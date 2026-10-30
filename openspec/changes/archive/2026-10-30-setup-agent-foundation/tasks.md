# Tasks

## 1. Dependências e estrutura

- [x] 1.1 Adicionar com `uv add` langchain, langchain-anthropic, langgraph, `psycopg[binary]`, pydantic, pydantic-settings, httpx e playwright; verificar que `uv sync` conclui e `uv run python -c "import psycopg, langchain_anthropic, playwright"` roda sem erro
- [x] 1.2 Adicionar com `uv add --dev` pytest, ruff e `testcontainers[postgres]`; configurar ruff e pytest (`testpaths = ["tests"]`) no `pyproject.toml`; verificar `uv run ruff check` e `uv run pytest --collect-only` sem erro
- [x] 1.3 Criar `errors.py` com `AgentError` e as subclasses `ConfigInvalid`, `DbUnavailable`, `SchemaContractMismatch`, `DbContractViolation` e `LlmUnavailable`, cada uma com `code` e `exit_code`; verificar com teste unitário que os códigos de saída são 2, 3, 4, 1 e 5

## 2. Configuração

- [x] 2.1 Criar `config.py` com `Settings` (pydantic-settings, `.env`) e `load_settings()` que levanta `ConfigInvalid` só com nome e motivo; verificar com testes de padrões (`claude-sonnet-5-5`, 2), valores sobrescritos e variável obrigatória ausente
- [x] 2.2 Validar `REQUEST_DELAY_SECONDS` (número >= 0), `DATABASE_URL` (`postgresql://`) e textos vazios; verificar com testes que cada caso levanta `CONFIG_INVALID` citando a variável
- [x] 2.3 Mascarar `ANTHROPIC_API_KEY` e a senha do banco em `repr`, mensagens de erro e logs (nos logs, só segredos com 8 ou mais caracteres; os menores geram o aviso `CONFIG_WEAK_SECRET`); verificar com teste que nenhum valor secreto aparece na mensagem de `CONFIG_INVALID` nem na saída impressa da configuração
- [x] 2.4 Criar `.env.example` com todas as variáveis (incl. `MIGRATIONS_DIR`) e valores fictícios; verificar com teste que cada campo de `Settings` aparece no arquivo e que o `.env` está no `.gitignore`

## 3. Módulo de banco

- [x] 3.1 Montar a infraestrutura de testes em `tests/conftest.py`: Postgres 16 por Testcontainers, migrations de `MIGRATIONS_DIR` em ordem numérica, fixture com `SET ROLE agent_writer` e skip com mensagem clara quando o diretório não existir; verificar com um teste que consulta `schema_contract` e retorna `1.0`, e com `MIGRATIONS_DIR` inexistente o teste é pulado com a mensagem
- [x] 3.2 Criar `db.py` com `connect()` e o mapeamento de `OperationalError` para `DB_UNAVAILABLE`; verificar com teste de porta fechada que a falha vem como `DB_UNAVAILABLE` sem a senha na mensagem
- [x] 3.3 Implementar `check_schema_contract()` com a consulta da seção 2 do contrato e a regra de MAJOR 1; verificar com testes de `1.0` ok, `1.3` ok, `2.0` recusado (`SCHEMA_CONTRACT_MISMATCH`), `10.0` recusado e tabela vazia recusada
- [x] 3.4 Implementar `open_run()` e `close_run()` com `RunStatus`, `RunCounts` e `RunError`, SQL parametrizado e fechamento único; verificar com testes de abrir (status `RUNNING`, contadores 0), fechar como `PARTIAL` com erro, status inválido, fechamento repetido e `source_id` inexistente (`DB_CONTRACT_VIOLATION`)
- [x] 3.5 Cobrir a parametrização e as permissões; verificar com teste que uma mensagem com `'; DROP TABLE source; --` é gravada literalmente e outro que a escrita em `schema_contract` como `agent_writer` vira `DB_CONTRACT_VIOLATION` (`42501`)

## 4. Fábrica do LLM

- [x] 4.1 Criar `llm.py` com o `Protocol` `LlmClient`, `AnthropicLlm` (`ChatAnthropic` com o modelo da configuração), `build_llm()` e `FakeLlm` para testes; verificar com testes que o modelo padrão é `claude-sonnet-5-5`, que `ANTHROPIC_MODEL` o sobrescreve e que o `FakeLlm` responde sem rede
- [x] 4.2 Mapear falhas do provedor para `LLM_UNAVAILABLE` sem expor a chave; verificar com teste que simula erro de autenticação e de conexão e confere o código e a ausência da chave na mensagem

## 5. CLI e logs

- [x] 5.1 Criar `logs.py` com o formatter JSON por linha (`timestamp`, `level`, `event`, `error_code`) e o mascaramento de segredos com 8 ou mais caracteres (`MIN_SECRET_LENGTH`), mais o aviso `CONFIG_WEAK_SECRET` da CLI para os menores; verificar com testes que a linha é JSON válido, não contém a chave nem a senha, que segredo curto não é mascarado e que o aviso cita a variável sem o valor
- [x] 5.2 Criar `cli.py` com o subcomando `check`, a flag `--skip-llm`, a ordem config, banco, contrato e modelo (parando na primeira falha) e o mapeamento de erros para códigos de saída; apontar o script do `pyproject.toml` para `cultural_events_agent.cli:main` e remover o `main` de exemplo; verificar com testes de saída 0, 2, 3, 4, 5 e 1, de `--skip-llm` sem chamada ao modelo e de "config inválida não acessa o banco"
- [x] 5.3 Verificar de ponta a ponta com `uv run cultural-events-agent check --skip-llm` contra o Postgres de teste (ou o banco local do app): saída 0 com logs JSON por etapa

## 6. Documentação e verificação final

- [x] 6.1 Escrever no `README.md` o setup (`uv sync`, `uv run playwright install chromium`, cópia do `.env.example` para `.env`) e os comandos `uv run cultural-events-agent check [--skip-llm]`, `uv run pytest` (requer Docker e `MIGRATIONS_DIR`) e `uv run ruff check` / `ruff format`; verificar executando os comandos como escritos
- [x] 6.2 Rodar `uv run ruff check`, `uv run ruff format --check` e `uv run pytest`; verificar que os três passam e que nenhum segredo aparece nos arquivos versionados (`git grep` pela chave de exemplo)
