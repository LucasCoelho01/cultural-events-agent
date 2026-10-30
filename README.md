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
| 64 | uso incorreto da linha de comando |

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
