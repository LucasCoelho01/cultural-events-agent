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
