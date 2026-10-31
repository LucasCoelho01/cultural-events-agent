# Proposal

## Why

A fundação do agente já conecta, confere o contrato e registra execuções, mas ainda não coleta nada. A Sympla é a primeira fonte: a linha `sympla` já existe em `source` (migration V007) e a listagem de São Paulo é pública, com `robots.txt` liberado. Esta mudança entrega o fluxo completo de ponta a ponta e a interface `Source`, para que as próximas fontes reaproveitem o fluxo.

## What Changes

- Novo fluxo em LangGraph (`StateGraph`): descobrir URLs na listagem da cidade → baixar → extrair → mapear categoria e local → deduplicar → gravar. Um item de cada vez, com falha isolada por item.
- Nova interface `Source` (listagem, identificador externo, dados estruturados, texto da página) e a implementação `SymplaSource`.
- Coleta educada: `robots.txt` respeitado (`ROBOTS_DISALLOWED`), `USER_AGENT` da config, pausa `REQUEST_DELAY_SECONDS` por domínio, timeout e limite de páginas por execução.
- Extração em camadas: JSON-LD `Event` e `__NEXT_DATA__` primeiro; o Claude completa e normaliza o que faltar. Saída inválida vira `EXTRACTION_INVALID` só daquele item.
- `LlmClient` ganha saída estruturada (`with_structured_output` + modelo pydantic); `FakeLlm` acompanha.
- Mapeamento: categoria por slug entre as do banco (fallback `outros`); local por `similarity()` na cidade, criando o `venue` se não existir.
- `content_hash` sobre os dados brutos da fonte (`__NEXT_DATA__`, JSON-LD e texto de ingressos), normalizados antes de qualquer chamada ao modelo: hash igual = `unchanged` sem chamar o LLM.
- Eventos cuja palavra `teste` aparece no título ou no slug são `skipped`, com o motivo no log.
- Gravação conforme o `AGENT_CONTRACT.md`: `content_hash`, uma transação por evento, upsert por `(source_id, external_id)`, `locked_fields`, lápides, `version = version + 1`, deduplicação entre fontes e contadores em `ingestion_run`.
- CLI `run --source sympla --city sp/sao-paulo [--limit N] [--dry-run]`. O `--dry-run` extrai e mostra o resultado sem gravar nada.
- Novas variáveis `REQUEST_TIMEOUT_SECONDS` e `MAX_PAGES_PER_RUN` (e no `.env.example`); novos códigos de erro e de saída.
- Fixtures HTML da Sympla (sem rede nos testes), LLM falso e Testcontainers como `agent_writer`.

Não há **BREAKING**: o comando `check` e os códigos 0–5 e 64 não mudam. Esta mudança **não altera** o contrato de schema 1.0 nem as permissões da role `agent_writer`: usa só `event`, `occurrence`, `venue`, `event_source_link` e `ingestion_run` (escrita) e lê `source`, `city` e `category`.

## Capabilities

### New Capabilities
- `source-ingestion`: fluxo em grafo, interface `Source`, validação de cidade e fonte, contadores da execução e `--dry-run` sem escrita.
- `polite-fetching`: `robots.txt`, `USER_AGENT`, pausa por domínio, timeout e limite de páginas.
- `event-extraction`: dados estruturados primeiro, complemento pelo modelo, normalização e `EXTRACTION_INVALID`.
- `event-mapping`: categoria por slug com fallback e local por similaridade, com criação do `venue`.
- `event-persistence`: gravação conforme o contrato (hash, transação por evento, locks, lápides, versão, deduplicação).

### Modified Capabilities
- `agent-cli`: novo comando `run` e novos códigos de saída.
- `llm-provider`: saída estruturada na interface e no modelo falso.
- `agent-configuration`: `REQUEST_TIMEOUT_SECONDS` e `MAX_PAGES_PER_RUN`.

## Impact

- Código: `src/cultural_events_agent/` ganha o pacote `sources/` (`base.py`, `sympla.py`) e os módulos `graph.py`, `fetching.py`, `extraction.py`, `mapping.py` e `writer.py`; `llm.py`, `config.py`, `errors.py` e `cli.py` são estendidos.
- Dependências: nenhuma nova (httpx, playwright, langgraph e langchain-anthropic já estão no `pyproject.toml`).
- Banco: leitura de `source`, `city`, `category`, `venue`, `event`, `occurrence`; escrita nas tabelas permitidas à role. Sem migration.
- Rede: a Sympla (listagem por `httpx`, eventos por Playwright). Os testes não usam rede.
- Custo: uma chamada ao modelo só por evento novo ou com dados brutos alterados; o item inalterado custa apenas o download.
- Fora de escopo: outras fontes, agendamento e tela de monitoramento.
