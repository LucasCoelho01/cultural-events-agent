# Design

## Context

O repo tem só o esqueleto do `uv init` (`src/cultural_events_agent/__init__.py` com um `main` de exemplo e o script `cultural-events-agent` no `pyproject.toml`). O banco é do `cultural-events-app`. Pelas migrations V001–V008 e pelo `AGENT_CONTRACT.md` (contrato 1.0):

- `schema_contract(version VARCHAR(10))` guarda as versões; a maior se obtém ordenando por `string_to_array(version, '.')::int[]`.
- `ingestion_run` tem `source_id` (FK para `source`), `status` (`RUNNING|SUCCESS|PARTIAL|FAILED`), contadores `created/updated/unchanged/failed` (>= 0), `errors JSONB` e `finished_at`.
- A role `agent_writer` lê tudo e escreve só em `event`, `occurrence`, `venue`, `source`, `event_source_link` e `ingestion_run`. Escrita em `schema_contract` dá `42501`.
- A V007 já insere a fonte `sympla`, que serve de `source_id` nos testes.

Ver `proposal.md` para a motivação e os requisitos em `specs/`.

## Goals / Non-Goals

**Goals:**
- Camadas pequenas e independentes (config, banco, LLM, CLI) que as fontes futuras reutilizem sem mudar.
- Todo erro conhecido vira uma exceção com código explícito, mapeada para um código de saída só na borda (CLI).
- Testes de banco contra o schema real, sem mocks do Postgres.

**Non-Goals:**
- Pool de conexões, async, retentativas e migração de schema. O agente nunca faz DDL.
- Escrita em `event`, `occurrence`, `venue` ou `event_source_link`.
- Uso de httpx, Playwright e LangGraph. Só ficam instalados.

## Decisions

**Layout.** Pacote `cultural_events_agent` com `config.py`, `errors.py`, `logs.py`, `db.py`, `llm.py` e `cli.py`; `pyproject.toml` aponta o script para `cultural_events_agent.cli:main`. *Alternativa:* subpacotes por camada; descartada, pois são poucos arquivos.

**Erros.** `errors.py` define `AgentError(code, message)` e subclasses `ConfigInvalid`, `DbUnavailable`, `SchemaContractMismatch`, `DbContractViolation`, `LlmUnavailable`. Cada classe carrega seu `code` (o nome em maiúsculas dos specs) e seu código de saída (2, 3, 4, 1 e 5; `DB_CONTRACT_VIOLATION` sai com 1, por ser um erro de programação ou permissão, não de ambiente). A tradução para o processo fica só no `cli.py`.

**Configuração.** `Settings(BaseSettings)` do pydantic-settings, lendo o `.env`, com `DATABASE_URL` (`PostgresDsn`, esquema `postgresql://`), `ANTHROPIC_API_KEY` (`SecretStr`), `ANTHROPIC_MODEL`, `USER_AGENT` e `REQUEST_DELAY_SECONDS` (`float >= 0`). `load_settings()` captura `ValidationError` e levanta `ConfigInvalid` com só `loc` e `msg` de cada erro (nunca `input`, que traria o valor secreto). A URL do banco é mascarada ao exibir (`***` no lugar da senha). *Alternativa:* `str(ValidationError)`; descartada porque inclui o valor recebido.

**Variáveis de ambiente novas** (todas no `.env.example`, com valores fictícios): `DATABASE_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `USER_AGENT`, `REQUEST_DELAY_SECONDS`. Os testes usam também `MIGRATIONS_DIR` (padrão `../cultural-events-app/database/migrations`), lida só pelos testes e documentada no `.env.example`.

**Banco.** psycopg 3 síncrono, uma `Connection` por execução, com `autocommit` desligado e `commit()` explícito em cada função de escrita. O módulo `db.py` expõe `connect(settings)`, `check_schema_contract(conn)`, `open_run(conn, source_id) -> int` e `close_run(conn, run_id, status, counts, errors)`. Todo SQL usa placeholders `%s` com a lista de parâmetros, e `errors` vai como `Jsonb(...)`; nenhum valor entra no texto do SQL. Tipos: `RunStatus` (`StrEnum` com `SUCCESS|PARTIAL|FAILED`), `RunCounts` (modelo pydantic com `ge=0`) e `RunError` (`external_id`, `message`).

**Respeito ao AGENT_CONTRACT.md.** Lê só `schema_contract`; escreve só `ingestion_run`, nos moldes da seção 3 do contrato (`INSERT ... RETURNING id` e um único `UPDATE` ao fechar). A checagem usa a consulta da seção 2, comparando só o MAJOR (MINOR maior é aceito). `psycopg.OperationalError` vira `DbUnavailable`; `ForeignKeyViolation` (`23503`), `CheckViolation` (`23514`), `InsufficientPrivilege` (`42501`), `NotNullViolation` (`23502`), `UniqueViolation` (`23505`), `UndefinedTable` (`42P01`) e `UndefinedColumn` (`42703`) viram `DbContractViolation`. Nada disso exige permissão nova da `agent_writer`.

**Fechamento único.** O `UPDATE` leva `WHERE id = %s AND status = 'RUNNING'`; se afetar 0 linhas, levanta `DbContractViolation`. O status é validado pelo `RunStatus` antes de ir ao banco. Assim, fechar duas vezes não sobrescreve o resultado.

**LLM.** `llm.py` define um `Protocol` `LlmClient` com `invoke(prompt: str) -> str` e a implementação `AnthropicLlm`, que embrulha `ChatAnthropic(model=settings.anthropic_model, api_key=...)`. Exceções do provedor (`anthropic.APIError` e erros de conexão) viram `LlmUnavailable`. A fábrica `build_llm(settings)` devolve o `LlmClient`. Testes passam um `FakeLlm`. *Alternativa:* usar `BaseChatModel` do LangChain direto como interface; descartada, pois acopla os testes ao LangChain. O LLM só devolve texto aqui; a extração estruturada vem na mudança das fontes, e o LLM nunca gera SQL.

**CLI.** `argparse` (sem dependência nova), subcomando `check` e a flag `--skip-llm`. Os erros passam por um único `try/except AgentError` em `main`, que loga e devolve o código de saída; `Exception` genérica sai com 1. A função `run_check(settings, llm_factory)` recebe dependências por argumento, para ser testável.

**Logs.** Módulo `logs.py` (nome que não sombreia o `logging` da stdlib), que usa o `logging` da stdlib com um `Formatter` que emite JSON por linha no `stderr` (`timestamp`, `level`, `event`, `error_code`, extras). O próprio `Formatter` mascara a chave de API e a senha do banco conhecidas, só quando têm 8 ou mais caracteres (`MIN_SECRET_LENGTH`): mascarar um segredo curto como `abc` corromperia texto comum. A config sinaliza os segredos menores com um WARNING `CONFIG_WEAK_SECRET` (`Settings.weak_secret_names()`, emitido pela CLI), sem o valor. As mensagens de erro de conexão (`db.py`) e do provedor (`llm.py`) continuam mascarando qualquer tamanho, pois montam texto a partir de erros externos. *Alternativa:* structlog; descartada para não somar dependência fora da lista.

**Testes.** `tests/conftest.py` sobe `PostgresContainer("postgres:16")` por sessão e aplica os `.sql` de `MIGRATIONS_DIR` em ordem numérica do prefixo `V<n>` (e não só lexicográfica), com o usuário administrador do contêiner. Se o diretório não existir, a fixture chama `pytest.skip` com mensagem que cita o caminho esperado e a variável `MIGRATIONS_DIR`. Cada teste usa uma conexão com `SET ROLE agent_writer`; o teste de contrato 2.0 insere a linha `2.0` como administrador antes do `SET ROLE` e a remove no fim, pois a role não escreve em `schema_contract`. Cada teste de escrita roda em transação revertida ou limpa suas linhas. Os testes de configuração não usam Docker e rodam sempre.

## Risks / Trade-offs

- [Migrations dependem do checkout irmão do outro repo] → `MIGRATIONS_DIR` configurável e skip com mensagem clara; em CI o repo deve ser clonado antes.
- [Docker indisponível derruba os testes de banco] → mensagem de skip distingue "sem Docker" de "sem migrations"; testes de configuração seguem rodando.
- [`SET ROLE` só prova as permissões se o login for superusuário do contêiner] → aceito; o teste de `42501` confirma que a role realmente é restrita.
- [Erro de contrato sobre um MAJOR futuro quebra todos os agentes de uma vez] → é o comportamento desejado pelo contrato (seção 2).
- [`LLM_UNAVAILABLE` não estava na lista original de erros] → adicionado para a chamada do `check`; sem ele, essa falha cairia no código genérico 1.
- [Segredo curto passa sem máscara em texto livre dos logs] → nenhum caminho do código loga o valor (a URL do banco sai mascarada e as mensagens de erro são filtradas na origem); o aviso `CONFIG_WEAK_SECRET` incentiva segredos fortes.
- [Chamada real do `check` custa tokens] → uma chamada mínima e `--skip-llm` para o dia a dia.
