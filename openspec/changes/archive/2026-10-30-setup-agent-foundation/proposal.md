# Proposal

## Why

O projeto tem só o esqueleto do `uv init`. Antes de qualquer fonte, o agente precisa de uma base verificável: configuração segura, conexão com o Postgres do `cultural-events-app` com a checagem do contrato de schema, registro de execuções e acesso ao LLM. Assim, as fontes seguintes só acrescentam coleta e extração sobre uma fundação já testada.

## What Changes

- Declarar as dependências de runtime (langchain, langchain-anthropic, langgraph, psycopg[binary] 3, pydantic, pydantic-settings, httpx, playwright) e de desenvolvimento (pytest, ruff, testcontainers[postgres]) com `uv`.
- Configuração tipada lida do `.env` (`DATABASE_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `USER_AGENT`, `REQUEST_DELAY_SECONDS`), com falha `CONFIG_INVALID` que não expõe segredos, aviso `CONFIG_WEAK_SECRET` para segredos curtos (menos de 8 caracteres) e `.env.example` versionado.
- Módulo de banco: conexão psycopg, checagem do `schema_contract` na partida (`SCHEMA_CONTRACT_MISMATCH`, `DB_UNAVAILABLE`) e funções tipadas, com SQL parametrizado, para abrir e fechar um `ingestion_run`.
- Fábrica do LLM atrás de uma interface, para os testes usarem um modelo falso.
- Comando `uv run cultural-events-agent check` com códigos de saída distintos por erro e logs estruturados.
- Testes com Testcontainers (Postgres 16) aplicando as migrations do `cultural-events-app`, e README com setup e comandos.

**Fora de escopo:** qualquer fonte, scraping, `robots.txt`, extração de eventos e escrita em `event`, `occurrence`, `venue` ou `event_source_link`. O Playwright e o httpx são só instalados, sem uso ainda.

## Capabilities

### New Capabilities
- `agent-configuration`: carga e validação da configuração do ambiente, sem vazar segredos.
- `database-contract`: conexão com o Postgres, checagem da versão do contrato de schema e registro de execuções (`ingestion_run`).
- `llm-provider`: obtenção do modelo de linguagem por uma interface substituível.
- `agent-cli`: comando `check` de diagnóstico, com códigos de saída e logs estruturados.

### Modified Capabilities

Nenhuma. Não há specs anteriores.

## Impact

- **Código:** novo código em `src/cultural_events_agent/` (config, banco, LLM, CLI) e testes em `tests/`. O `main` do `__init__.py` é substituído pelo ponto de entrada da CLI.
- **Dependências:** as listadas acima, em `pyproject.toml` e `uv.lock`.
- **Contrato com o banco:** não altera o contrato de schema 1.0 nem as permissões da `agent_writer`. Só usa o que já é concedido: `SELECT` em `schema_contract` e `INSERT`/`UPDATE` em `ingestion_run`. Nenhuma outra tabela é escrita.
- **Dependência externa:** os testes exigem Docker e o checkout do `cultural-events-app` (`MIGRATIONS_DIR`, padrão `../cultural-events-app/database/migrations`).
