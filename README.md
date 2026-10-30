# cultural-events-agent

Agente de ingestão de eventos culturais. Coleta páginas de fontes culturais,
usa um LLM (Claude, via LangChain/LangGraph) para extrair eventos em formato
estruturado e grava direto no Postgres do app `cultural-events-app`.

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
copy .env.example .env   # preencha ANTHROPIC_API_KEY, DATABASE_URL etc.
uv run playwright install chromium
```

`ANTHROPIC_MODEL` define o modelo (padrão `claude-sonnet-5-5`). Segredos ficam
só no `.env`, que não é versionado.

## Desenvolvimento

```
uv run pytest      # testes de banco usam Testcontainers (requer Docker)
uv run ruff check
uv run ruff format
```

Os testes de banco aplicam as migrations do `cultural-events-app`. Mudanças são
especificadas com OpenSpec em `openspec/`.
