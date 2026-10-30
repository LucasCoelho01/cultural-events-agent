## ADDED Requirements

### Requirement: Comando run
O comando `cultural-events-agent run --source <código> --city <uf>/<slug> [--limit N] [--dry-run]` MUST validar a configuração, conectar, conferir o contrato e executar o fluxo da fonte para a cidade. `--limit` MUST limitar os itens processados. Ao final, MUST registrar um log com os contadores.

#### Scenario: Execução normal
- **WHEN** o comando roda com `--source sympla --city sp/sao-paulo --limit 10`
- **THEN** no máximo 10 itens são processados e o log final traz `created`, `updated`, `unchanged` e `failed`

#### Scenario: Falhas parciais
- **WHEN** alguns itens falham mas a execução termina
- **THEN** o código de saída é 0 e as falhas constam no log e em `ingestion_run.errors`

#### Scenario: Fonte desconhecida
- **WHEN** `--source` não é uma fonte registrada
- **THEN** o código de saída é 64

### Requirement: Códigos de saída do run
O comando `run` MUST sair com: 6 `CITY_NOT_FOUND`, 7 `CITY_INACTIVE`, 8 `SOURCE_NOT_FOUND`, 9 `ROBOTS_DISALLOWED` (listagem) e 10 `FETCH_FAILED` (listagem). Os códigos 2, 3, 4 e 5 MUST valer como no `check`. Falha de item MUST NOT alterar o código de saída.

#### Scenario: Cidade inexistente
- **WHEN** `--city xx/lugar-nenhum` não existe
- **THEN** o código de saída é 6 e o log traz `CITY_NOT_FOUND`

#### Scenario: Cidade inativa
- **WHEN** a cidade existe com `active = false`
- **THEN** o código de saída é 7

#### Scenario: Listagem bloqueada
- **WHEN** o `robots.txt` proíbe a listagem
- **THEN** o código de saída é 9
