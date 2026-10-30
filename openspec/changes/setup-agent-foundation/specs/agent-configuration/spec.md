# Spec Delta

## Purpose

Define como o agente obtém e valida sua configuração a partir do ambiente e do arquivo `.env`, falhando cedo e sem expor segredos.

## ADDED Requirements

### Requirement: Variáveis de configuração
O agente MUST ler `DATABASE_URL` e `ANTHROPIC_API_KEY` (obrigatórias), `USER_AGENT` (obrigatória) e, opcionalmente, `ANTHROPIC_MODEL` (padrão `claude-sonnet-5-5`) e `REQUEST_DELAY_SECONDS` (padrão `2`) do ambiente ou do `.env`.

#### Scenario: Configuração completa com padrões
- **WHEN** apenas as variáveis obrigatórias estão definidas
- **THEN** a configuração é carregada com `ANTHROPIC_MODEL` igual a `claude-sonnet-5-5` e `REQUEST_DELAY_SECONDS` igual a 2

#### Scenario: Valores opcionais sobrescritos
- **WHEN** `ANTHROPIC_MODEL` e `REQUEST_DELAY_SECONDS` estão definidos
- **THEN** a configuração usa esses valores

#### Scenario: Variável obrigatória ausente
- **WHEN** `DATABASE_URL`, `ANTHROPIC_API_KEY` ou `USER_AGENT` não está definida
- **THEN** o agente falha com o código `CONFIG_INVALID` citando o nome da variável

### Requirement: Validação dos valores
O agente MUST rejeitar com `CONFIG_INVALID` valores inválidos: `REQUEST_DELAY_SECONDS` não numérico ou negativo, `DATABASE_URL` que não seja uma URL `postgresql://` e variáveis de texto vazias.

#### Scenario: Atraso inválido
- **WHEN** `REQUEST_DELAY_SECONDS` vale `abc` ou `-1`
- **THEN** o agente falha com `CONFIG_INVALID` citando `REQUEST_DELAY_SECONDS`

#### Scenario: URL do banco inválida
- **WHEN** `DATABASE_URL` não começa com `postgresql://`
- **THEN** o agente falha com `CONFIG_INVALID` citando `DATABASE_URL`

### Requirement: Segredos não aparecem em mensagens
Mensagens de erro e logs MUST NOT conter o valor de `ANTHROPIC_API_KEY` nem a senha de `DATABASE_URL`. Só os nomes das variáveis e o motivo do erro podem aparecer.

#### Scenario: Erro com valor secreto inválido
- **WHEN** `DATABASE_URL` contém senha e é inválida
- **THEN** a mensagem de `CONFIG_INVALID` cita `DATABASE_URL` e não contém a senha

#### Scenario: Chave presente em configuração válida
- **WHEN** a configuração é impressa ou registrada em log
- **THEN** `ANTHROPIC_API_KEY` e a senha do banco aparecem mascaradas

### Requirement: Exemplo de configuração versionado
O repositório MUST ter um `.env.example` listando todas as variáveis com valores fictícios, e o `.env` MUST ficar fora do controle de versão.

#### Scenario: Exemplo cobre todas as variáveis
- **WHEN** se compara o `.env.example` com os campos da configuração
- **THEN** cada variável da configuração aparece no `.env.example`, sem segredo real
