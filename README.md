# cultural-events-agent

Agente de ingestão de eventos culturais. Coleta páginas de fontes culturais,
usa um LLM (Claude, via LangChain/LangGraph) para extrair eventos em formato
estruturado e grava direto no Postgres do app `cultural-events-app`.

> Estado atual: só a fundação (configuração, banco, LLM e o comando `check`).
> Ainda não há fontes, scraping nem escrita de eventos.

## Princípios

- O LLM só extrai dados estruturados; nunca gera SQL.
- Toda escrita no banco passa por código tipado com SQL parametrizado, seguindo
  `database/AGENT_CONTRACT.md` do `cultural-events-app` (contrato de schema 1.0,
  role `agent_writer`).
- Respeita `robots.txt` e limita a taxa de requisições.

## Stack

Python 3.13, uv, LangChain/LangGraph, langchain-anthropic, Playwright,
psycopg 3, pydantic, pytest, ruff.

## Configuração

```
uv sync
uv run playwright install chromium
```

Copie o exemplo e preencha (o `.env` não é versionado):

```
cp .env.example .env          # Git Bash / Linux / macOS
copy .env.example .env        # Windows (cmd)
Copy-Item .env.example .env   # Windows (PowerShell)
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DATABASE_URL` | sim | `postgresql://agent_writer:SENHA@host:5432/banco` (role `agent_writer`) |
| `ANTHROPIC_API_KEY` | sim | chave da API da Anthropic |
| `USER_AGENT` | sim | identifica o agente para as fontes; inclua um contato |
| `ANTHROPIC_MODEL` | não | modelo do Claude (padrão `claude-sonnet-5-5`) |
| `REQUEST_DELAY_SECONDS` | não | pausa entre requisições, em segundos (padrão `2`) |
| `REQUEST_TIMEOUT_SECONDS` | não | tempo máximo de cada requisição, em segundos (padrão `30`) |
| `MAX_PAGES_PER_RUN` | não | máximo de páginas baixadas por execução, listagens e eventos (padrão `50`) |
| `LOG_LEVEL` | não | `DEBUG`, `INFO` (padrão), `WARNING` ou `ERROR`; `DEBUG` inclui o traceback de erros inesperados |
| `MIGRATIONS_DIR` | não | só para os testes: migrations do `cultural-events-app` (padrão `../cultural-events-app/database/migrations`) |

Variável ausente ou inválida encerra com `CONFIG_INVALID`, sem mostrar segredos.

## Verificar o ambiente

```
uv run cultural-events-agent check             # config, banco, contrato e uma chamada mínima ao modelo
uv run cultural-events-agent check --skip-llm  # o mesmo, sem chamar o modelo (não gasta tokens)
```

Os logs saem em JSON, um por linha, no `stderr`. Códigos de saída:

| Código | Significado |
|---|---|
| 0 | tudo certo |
| 1 | erro inesperado (ou `DB_CONTRACT_VIOLATION`) |
| 2 | `CONFIG_INVALID` |
| 3 | `DB_UNAVAILABLE` |
| 4 | `SCHEMA_CONTRACT_MISMATCH` (MAJOR diferente de 1) |
| 5 | `LLM_UNAVAILABLE` |
| 6 | `CITY_NOT_FOUND` (só no `run`) |
| 7 | `CITY_INACTIVE` (só no `run`) |
| 8 | `SOURCE_NOT_FOUND` (só no `run`: a fonte não existe em `source` ou está inativa) |
| 9 | `ROBOTS_DISALLOWED` na listagem (só no `run`) |
| 10 | `FETCH_FAILED` na listagem (só no `run`) |
| 11 | `RATE_LIMITED`: a fonte respondeu 429 na listagem ou em três páginas seguidas (só no `run`) |
| 64 | uso incorreto da linha de comando |

## Coletar eventos

```
uv run cultural-events-agent run --source sympla --city sp/sao-paulo
uv run cultural-events-agent run --source sympla --city sp/sao-paulo --limit 10
uv run cultural-events-agent run --source sympla --city sp/sao-paulo --dry-run
```

- `--city` é `<uf>/<slug>` da tabela `city`; a cidade precisa existir e estar ativa.
- `--limit N` limita os eventos processados; sem ele, usa a primeira página da listagem.
- `--dry-run` extrai e mostra o resultado (um JSON por linha, no `stdout`) sem gravar nada: não abre
  `ingestion_run` e a conexão com o banco fica somente leitura.
- Os eventos são gravados conforme o `database/AGENT_CONTRACT.md`: uma transação por evento, upsert por
  `(source_id, external_id)`, `locked_fields` respeitados e eventos excluídos à mão não voltam.
- O `content_hash` é calculado sobre os dados brutos da página, antes de qualquer chamada ao modelo:
  evento inalterado não gasta tokens (só o download).
- Eventos com a palavra `teste` no título ou no slug são ignorados (`skipped`, motivo `TESTE_EVENT`).
- Só eventos culturais são gravados. O modelo classifica na mesma chamada da extração: cultural é
  show, teatro, dança, cinema, exposição, literatura, festa ou festival cultural e gastronomia
  cultural; não é cultural curso, workshop técnico, networking, negócios, terapia, religião, esporte
  competitivo e webinar. Os não culturais são `skipped` (`NOT_CULTURAL`), com o motivo no log
  (`item_skipped`, campo `detail`) e no `--dry-run`, e nada é gravado. Um evento já gravado que
  passe a ser classificado como não cultural **não** é apagado nem despublicado: só sai o aviso
  `existing_event_not_cultural` no log, para decisão humana.
- A coleta respeita o `robots.txt`, usa `USER_AGENT`, espera `REQUEST_DELAY_SECONDS` entre requisições
  ao mesmo domínio e baixa no máximo `MAX_PAGES_PER_RUN` páginas.
- Falha de um evento (`EXTRACTION_INVALID`, `ROBOTS_DISALLOWED`, `FETCH_FAILED`, `DB_CONTRACT_VIOLATION`,
  `VERSION_CONFLICT`) não interrompe a execução nem muda o código de saída; ela fica no log e em
  `ingestion_run.errors`. Toda execução termina com o log `run_finished` (também no `--dry-run` e quando abortada), com
  `items_discovered` e um balde por item: `items_created`, `items_updated`, `items_unchanged`,
  `items_skipped` e `items_failed`; `items_pending` são os descobertos não processados (limite de
  páginas ou execução abortada). No `ingestion_run`, `unchanged` inclui os `skipped`.
- `EXTRACTION_INVALID` traz no detalhe o `loc`, o `type` e a `msg` de cada erro de validação (sem o
  valor recebido). Com `LOG_LEVEL=DEBUG`, a resposta bruta do modelo desses itens é gravada em
  `.debug/<external_id>.json` (pasta ignorada pelo git).

### Limite de taxa da fonte

Se a fonte responde HTTP 429, o agente espera (`Retry-After`, ou 30 s dobrando até 120 s) antes do
próximo pedido ao domínio, não repete o item e, com três 429 seguidos, encerra a execução com
`RATE_LIMITED` (saída 11); o que já foi gravado fica e o resto aparece em `items_pending`. Para
`--limit` alto, aumente `REQUEST_DELAY_SECONDS` (o padrão é 2 s) e tente de novo mais tarde.

### Limitações conhecidas

- Itens não culturais (`NOT_CULTURAL`) e demais `skipped` não têm vínculo gravado, então são
  baixados e reavaliados pelo modelo **a cada execução** (e o veredito pode variar).

Os eventos da Sympla são abertos com Playwright (`uv run playwright install chromium`); a listagem é
baixada com `httpx`.

## Desenvolvimento

```
uv run pytest             # testes de banco exigem Docker e as migrations do cultural-events-app
uv run ruff check
uv run ruff format
```

Os testes de banco sobem um Postgres 16 com Testcontainers, aplicam em ordem os
`.sql` de `MIGRATIONS_DIR` e rodam como `agent_writer` (via `SET ROLE`). Sem o
diretório de migrations, esses testes são pulados com uma mensagem indicando o
caminho esperado; sem Docker em execução, também são pulados. Os demais testes
rodam sempre.

Mudanças são especificadas com OpenSpec em `openspec/`.
