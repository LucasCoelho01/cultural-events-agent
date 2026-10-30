# Spec Delta

## Purpose

Define a conexão do agente com o Postgres do `cultural-events-app`, a checagem do contrato de schema na partida e o registro de execuções em `ingestion_run`, conforme `database/AGENT_CONTRACT.md` (contrato 1.0).

## ADDED Requirements

### Requirement: Conexão com o banco
O agente MUST conectar ao Postgres com a `DATABASE_URL` configurada, que usa a role `agent_writer`. Se o banco não puder ser alcançado ou a autenticação falhar, MUST encerrar com `DB_UNAVAILABLE`, sem expor a senha.

#### Scenario: Conexão bem-sucedida
- **WHEN** o banco está acessível com credenciais válidas
- **THEN** o agente obtém uma conexão utilizável

#### Scenario: Banco inacessível
- **WHEN** o host não responde ou as credenciais são recusadas
- **THEN** o agente falha com `DB_UNAVAILABLE` e a mensagem não contém a senha

### Requirement: Versão do contrato na partida
Antes de qualquer escrita, o agente MUST ler a maior versão de `schema_contract`, comparada numericamente por componente, e aceitar somente MAJOR igual a 1. MINOR maior MUST ser aceito.

#### Scenario: Contrato 1.0 compatível
- **WHEN** a maior versão em `schema_contract` é `1.0`
- **THEN** a checagem passa e informa a versão lida

#### Scenario: MINOR maior compatível
- **WHEN** a maior versão é `1.3`
- **THEN** a checagem passa

#### Scenario: Ordenação numérica
- **WHEN** as versões são `1.0` e `10.0`
- **THEN** a maior é `10.0` e a checagem falha

#### Scenario: MAJOR incompatível
- **WHEN** a maior versão é `2.0`
- **THEN** o agente falha com `SCHEMA_CONTRACT_MISMATCH`, informa a versão lida e a esperada, e não grava nada

### Requirement: Contrato ausente ou ilegível
Se `schema_contract` não existir ou estiver vazia, o agente MUST encerrar com `SCHEMA_CONTRACT_MISMATCH` e não gravar nada.

#### Scenario: Tabela vazia
- **WHEN** `schema_contract` não tem nenhuma linha
- **THEN** o agente falha com `SCHEMA_CONTRACT_MISMATCH`

### Requirement: Abertura de execução
O agente MUST abrir uma execução inserindo uma linha em `ingestion_run` para um `source_id`, e retornar o `id` gerado. A linha nasce com status `RUNNING` e contadores zerados.

#### Scenario: Execução aberta
- **WHEN** a abertura é chamada com um `source_id` existente
- **THEN** existe uma linha com status `RUNNING`, contadores 0 e o `id` devolvido

#### Scenario: Fonte inexistente
- **WHEN** o `source_id` não existe em `source`
- **THEN** o agente falha com `DB_CONTRACT_VIOLATION` e nenhuma linha é criada

### Requirement: Fechamento de execução
O agente MUST fechar uma execução uma única vez, gravando status (`SUCCESS`, `PARTIAL` ou `FAILED`), `finished_at`, os contadores `created`, `updated`, `unchanged` e `failed` e a lista `errors` de `{external_id, message}`.

#### Scenario: Fechamento com erros
- **WHEN** a execução é fechada como `PARTIAL` com `failed` igual a 1 e um erro
- **THEN** a linha tem status `PARTIAL`, `finished_at` preenchido, contadores gravados e `errors` com o item

#### Scenario: Status inválido
- **WHEN** o fechamento recebe um status fora de `SUCCESS`, `PARTIAL` e `FAILED`
- **THEN** o agente rejeita antes de gravar, com `DB_CONTRACT_VIOLATION`

#### Scenario: Fechamento repetido
- **WHEN** uma execução que já saiu de `RUNNING` é fechada de novo
- **THEN** o agente falha com `DB_CONTRACT_VIOLATION` e a linha não muda

### Requirement: Escrita tipada e parametrizada
Toda instrução SQL do agente MUST ser executada com parâmetros, nunca por concatenação de valores no texto, e a escrita MUST se limitar a `ingestion_run` nesta mudança.

#### Scenario: Valor com aspas e SQL
- **WHEN** uma mensagem de erro contém `'; DROP TABLE source; --`
- **THEN** o texto é gravado literalmente em `errors` e as tabelas continuam intactas

#### Scenario: Escrita negada pelo banco
- **WHEN** o agente tenta escrever em uma tabela sem permissão, como `schema_contract`
- **THEN** o erro `42501` é reportado como `DB_CONTRACT_VIOLATION`
