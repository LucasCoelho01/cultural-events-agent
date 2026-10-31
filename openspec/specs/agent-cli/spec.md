# agent-cli Specification

## Purpose
Define o comando de diagnóstico `check` do agente, que verifica o ambiente de ponta a ponta e sinaliza cada falha com um código de saída próprio e logs estruturados.

## Requirements

### Requirement: Comando check
O comando `cultural-events-agent check` MUST, em ordem: validar a configuração, conectar ao banco, conferir o contrato de schema e, sem `--skip-llm`, fazer uma chamada mínima ao modelo. Parar na primeira falha.

#### Scenario: Tudo certo
- **WHEN** configuração, banco, contrato e modelo estão válidos
- **THEN** o comando termina com código 0 e registra um log de sucesso por etapa

#### Scenario: Sem chamada ao modelo
- **WHEN** o comando roda com `--skip-llm`
- **THEN** nenhuma chamada ao modelo é feita e o código de saída é 0 se as demais etapas passam

#### Scenario: Para na primeira falha
- **WHEN** a configuração é inválida
- **THEN** o banco e o modelo não são acessados

### Requirement: Códigos de saída por erro
O comando MUST usar códigos de saída distintos: 0 sucesso, 2 `CONFIG_INVALID`, 3 `DB_UNAVAILABLE`, 4 `SCHEMA_CONTRACT_MISMATCH`, 5 `LLM_UNAVAILABLE`. Erro inesperado MUST sair com 1.

#### Scenario: Configuração inválida
- **WHEN** falta `DATABASE_URL`
- **THEN** o código de saída é 2

#### Scenario: Banco inacessível
- **WHEN** o banco não responde
- **THEN** o código de saída é 3

#### Scenario: Contrato incompatível
- **WHEN** o contrato lido tem MAJOR 2
- **THEN** o código de saída é 4

#### Scenario: Modelo indisponível
- **WHEN** a chamada ao modelo falha
- **THEN** o código de saída é 5

#### Scenario: Erro inesperado
- **WHEN** ocorre uma exceção não mapeada
- **THEN** o código de saída é 1 e o log registra o tipo do erro, sem segredos

### Requirement: Logs estruturados
O comando MUST emitir logs estruturados, um objeto JSON por linha no `stderr`, com ao menos `event`, `level` e `timestamp`. Falhas incluem o código de erro em `error_code`.

#### Scenario: Log de falha
- **WHEN** a checagem falha com `SCHEMA_CONTRACT_MISMATCH`
- **THEN** uma linha JSON tem `level` de erro e `error_code` igual a `SCHEMA_CONTRACT_MISMATCH`

#### Scenario: Log sem segredos
- **WHEN** qualquer etapa registra log
- **THEN** a linha não contém a chave de API nem a senha do banco

### Requirement: Comando run
O comando `cultural-events-agent run --source <código> --city <uf>/<slug> [--limit N] [--dry-run]` MUST validar a configuração, conectar, conferir o contrato e executar o fluxo da fonte para a cidade. `--limit` MUST limitar os itens processados. Ao final, MUST registrar um log com os contadores (prefixo `items_`, pois `created` é reservado no log).

#### Scenario: Execução normal
- **WHEN** o comando roda com `--source sympla --city sp/sao-paulo --limit 10`
- **THEN** no máximo 10 itens são processados e o log final `run_finished` traz `items_discovered`, `items_created`, `items_updated`, `items_unchanged`, `items_skipped`, `items_failed` e `items_pending`

#### Scenario: Falhas parciais
- **WHEN** alguns itens falham mas a execução termina
- **THEN** o código de saída é 0 e as falhas constam no log e em `ingestion_run.errors`

#### Scenario: Fonte desconhecida
- **WHEN** `--source` não é uma fonte registrada
- **THEN** o código de saída é 64

### Requirement: Códigos de saída do run
O comando `run` MUST sair com: 6 `CITY_NOT_FOUND`, 7 `CITY_INACTIVE`, 8 `SOURCE_NOT_FOUND`, 9 `ROBOTS_DISALLOWED` (listagem), 10 `FETCH_FAILED` (listagem) e 11 `RATE_LIMITED` (429 na listagem ou três seguidos nos eventos). Os códigos 2, 3, 4 e 5 MUST valer como no `check`. Falha de item MUST NOT alterar o código de saída.

#### Scenario: Cidade inexistente
- **WHEN** `--city xx/lugar-nenhum` não existe
- **THEN** o código de saída é 6 e o log traz `CITY_NOT_FOUND`

#### Scenario: Cidade inativa
- **WHEN** a cidade existe com `active = false`
- **THEN** o código de saída é 7

#### Scenario: Listagem bloqueada
- **WHEN** o `robots.txt` proíbe a listagem
- **THEN** o código de saída é 9

### Requirement: Log de erro inesperado
O log de `UNEXPECTED_ERROR` MUST incluir o tipo e a mensagem da exceção, passando pelo filtro de segredos. Com `LOG_LEVEL=DEBUG`, MUST incluir também o traceback, igualmente filtrado; nos demais níveis, o traceback MUST NOT aparecer.

#### Scenario: Mensagem sem segredos
- **WHEN** uma exceção inesperada contém a `ANTHROPIC_API_KEY` na mensagem
- **THEN** o log traz o tipo e a mensagem com a chave mascarada e o código de saída é 1

#### Scenario: Traceback em DEBUG
- **WHEN** `LOG_LEVEL=DEBUG` e ocorre uma exceção inesperada
- **THEN** o log traz o campo `traceback`, sem segredos

#### Scenario: Sem traceback em INFO
- **WHEN** `LOG_LEVEL` é `INFO` ou não está definido
- **THEN** o log não traz o campo `traceback`
