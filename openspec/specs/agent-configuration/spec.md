# agent-configuration Specification

## Purpose
Define como o agente obtém e valida sua configuração a partir do ambiente e do arquivo `.env`, falhando cedo e sem expor segredos.

## Requirements

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
Mensagens de erro e logs MUST NOT conter o valor de `ANTHROPIC_API_KEY` nem a senha de `DATABASE_URL`. Só os nomes das variáveis e o motivo do erro podem aparecer. O filtro de logs mascara apenas segredos com 8 ou mais caracteres, para não alterar texto comum.

#### Scenario: Erro com valor secreto inválido
- **WHEN** `DATABASE_URL` contém senha e é inválida
- **THEN** a mensagem de `CONFIG_INVALID` cita `DATABASE_URL` e não contém a senha

#### Scenario: Chave presente em configuração válida
- **WHEN** a configuração é impressa ou registrada em log
- **THEN** `ANTHROPIC_API_KEY` e a senha do banco aparecem mascaradas

#### Scenario: Segredo com 8 caracteres é mascarado
- **WHEN** um segredo tem exatamente 8 caracteres e aparece numa mensagem de log
- **THEN** o filtro o troca por `***`

#### Scenario: Segredo curto não é mascarado
- **WHEN** um segredo tem menos de 8 caracteres e o mesmo texto aparece numa mensagem de log
- **THEN** o filtro não altera a mensagem, e nenhuma mensagem do agente inclui o valor do segredo, só o nome da variável

### Requirement: Aviso de segredo fraco
Ao carregar a configuração, o agente MUST emitir um log WARNING com `error_code` `CONFIG_WEAK_SECRET` e o nome da variável para cada segredo com menos de 8 caracteres (`ANTHROPIC_API_KEY` ou a senha de `DATABASE_URL`), sem o valor, e MUST seguir a execução normalmente.

#### Scenario: Segredos curtos geram aviso
- **WHEN** `ANTHROPIC_API_KEY` ou a senha de `DATABASE_URL` tem menos de 8 caracteres
- **THEN** um log WARNING com `error_code` `CONFIG_WEAK_SECRET` cita a variável, não contém o valor e a execução continua

#### Scenario: Segredos fortes não geram aviso
- **WHEN** os dois segredos têm 8 ou mais caracteres
- **THEN** nenhum log `CONFIG_WEAK_SECRET` é emitido

#### Scenario: URL do banco sem senha
- **WHEN** `DATABASE_URL` não contém senha
- **THEN** não há aviso para `DATABASE_URL`

### Requirement: Exemplo de configuração versionado
O repositório MUST ter um `.env.example` listando todas as variáveis com valores fictícios, e o `.env` MUST ficar fora do controle de versão.

#### Scenario: Exemplo cobre todas as variáveis
- **WHEN** se compara o `.env.example` com os campos da configuração
- **THEN** cada variável da configuração aparece no `.env.example`, sem segredo real
