## ADDED Requirements

### Requirement: Fluxo em grafo por item
A execução MUST seguir um `StateGraph` com as etapas descobrir, baixar, extrair, mapear, deduplicar e gravar. A descoberta ocorre uma vez; as demais etapas processam um item de cada vez. A falha de um item MUST ser registrada e MUST NOT interromper os demais.

#### Scenario: Ordem das etapas
- **WHEN** a execução processa um item
- **THEN** as etapas rodam na ordem descobrir, baixar, extrair, mapear, deduplicar e gravar

#### Scenario: Item com falha
- **WHEN** a extração de um item falha com `EXTRACTION_INVALID`
- **THEN** o item entra em `failed` e `errors`, e o próximo item é processado

### Requirement: Interface Source
O fluxo MUST depender só da interface `Source`: URL da listagem da cidade, itens descobertos (`external_id` e URL), dados estruturados da página e texto para o modelo. Uma fonte nova MUST ser adicionada implementando a interface, sem alterar o grafo.

#### Scenario: Fonte de teste no mesmo fluxo
- **WHEN** um teste executa o grafo com uma `Source` falsa
- **THEN** o fluxo completo roda sem referência à Sympla

### Requirement: Cidade e fonte válidas
Antes de coletar, a execução MUST resolver a cidade (`state_uf` e `slug`, ex.: `sp/sao-paulo`) e a fonte (`source.code`, ativa). Cidade inexistente MUST encerrar com `CITY_NOT_FOUND`, cidade com `active = false` com `CITY_INACTIVE` e fonte ausente ou inativa com `SOURCE_NOT_FOUND`. Nada é baixado nem gravado.

#### Scenario: Cidade inexistente
- **WHEN** a cidade pedida não existe em `city`
- **THEN** a execução encerra com `CITY_NOT_FOUND` sem requisição à fonte e sem `ingestion_run`

#### Scenario: Cidade inativa
- **WHEN** a cidade existe com `active = false`
- **THEN** a execução encerra com `CITY_INACTIVE` sem requisição à fonte

#### Scenario: Fonte inativa
- **WHEN** `source.active` é `false` para o código pedido
- **THEN** a execução encerra com `SOURCE_NOT_FOUND`

### Requirement: Registro da execução
Fora do `--dry-run`, a execução MUST abrir um `ingestion_run` da fonte antes de gravar e fechá-lo uma única vez com `created`, `updated`, `unchanged`, `failed` e `errors`. O status MUST ser `SUCCESS` sem falhas, `PARTIAL` com falhas parciais e `FAILED` se abortada.

#### Scenario: Execução com uma falha
- **WHEN** 9 itens são gravados e 1 falha com `EXTRACTION_INVALID`
- **THEN** o `ingestion_run` fecha como `PARTIAL`, com `failed = 1` e o `external_id` do item em `errors`

#### Scenario: Execução abortada
- **WHEN** a listagem falha com `FETCH_FAILED`
- **THEN** o `ingestion_run` fecha como `FAILED` e o comando sai com o código desse erro

### Requirement: Dry-run sem escrita
Com `--dry-run`, a execução MUST extrair e mapear os itens e exibir o resultado, sem abrir `ingestion_run` e sem nenhuma escrita no banco. A conexão MUST ser somente leitura, de modo que o próprio banco recuse qualquer escrita.

#### Scenario: Nada gravado
- **WHEN** o comando roda com `--dry-run` sobre itens novos
- **THEN** `event`, `venue`, `event_source_link` e `ingestion_run` permanecem sem linhas novas

#### Scenario: Resultado exibido
- **WHEN** o comando roda com `--dry-run`
- **THEN** cada item extraído é impresso como JSON, um por linha, na saída padrão
