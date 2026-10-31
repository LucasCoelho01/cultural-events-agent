# source-ingestion Specification

## Purpose
Orquestrar a execução de uma fonte como um grafo que processa um item por vez, isolando falhas por item. Define a interface Source, o descarte de eventos de teste, a validação de cidade e fonte, o registro da execução, o dry-run e os contadores finais.

## Requirements

### Requirement: Fluxo em grafo por item
A execução MUST seguir um `StateGraph` com as etapas descobrir, baixar, triar, extrair, mapear, deduplicar e gravar. A descoberta ocorre uma vez; as demais processam um item de cada vez. A triagem compara o hash dos dados brutos com o vínculo e encerra o item sem chamar o modelo se nada mudou. A falha de um item MUST NOT interromper os demais.

#### Scenario: Ordem das etapas
- **WHEN** a execução processa um item novo
- **THEN** as etapas rodam na ordem descobrir, baixar, triar, extrair, mapear, deduplicar e gravar

#### Scenario: Triagem encerra o item
- **WHEN** o hash dos dados brutos é igual ao do vínculo
- **THEN** o item termina na triagem como `unchanged`, sem extrair, mapear ou chamar o modelo

#### Scenario: Item com falha
- **WHEN** a extração de um item falha com `EXTRACTION_INVALID`
- **THEN** o item entra em `failed` e `errors`, e o próximo item é processado

### Requirement: Exceção inesperada num item
Qualquer exceção inesperada ao processar UM item MUST falhar só aquele item com `ITEM_FAILED`, registrando em `ingestion_run.errors` o tipo e a mensagem com os segredos mascarados, e a execução MUST seguir. Só `DB_UNAVAILABLE`, `LLM_UNAVAILABLE` e os erros da listagem MUST abortar a execução.

#### Scenario: Exceção no item
- **WHEN** a leitura dos dados de um item levanta `KeyError` contendo a chave de API
- **THEN** o item entra em `failed` com `ITEM_FAILED: KeyError: ...` sem a chave, e os demais itens são gravados

#### Scenario: Banco indisponível
- **WHEN** um item levanta `DB_UNAVAILABLE`
- **THEN** a execução aborta, o `ingestion_run` fecha como `FAILED` e os itens seguintes não são tentados

#### Scenario: Modelo indisponível
- **WHEN** um item levanta `LLM_UNAVAILABLE`
- **THEN** a execução aborta e o `ingestion_run` fecha como `FAILED`

### Requirement: Interface Source
O fluxo MUST depender só da interface `Source`: URL da listagem da cidade, itens descobertos (`external_id` e URL), dados estruturados da página e texto para o modelo. Uma fonte nova MUST ser adicionada implementando a interface, sem alterar o grafo.

#### Scenario: Fonte de teste no mesmo fluxo
- **WHEN** um teste executa o grafo com uma `Source` falsa
- **THEN** o fluxo completo roda sem referência à Sympla

### Requirement: Eventos de teste ignorados
Item cujo título ou slug contenha a palavra `teste` (sem diferenciar maiúsculas, sem acentos obrigatórios e só como palavra inteira) MUST ser ignorado como `skipped`, com o motivo `TESTE_EVENT` registrado em log. O slug MUST ser conferido antes de baixar a página. Item `skipped` MUST NOT criar vínculo nem chamar o modelo e conta em `unchanged` no `ingestion_run`.

#### Scenario: Slug com teste
- **WHEN** a URL é `/evento/evento-teste-565/3430776`
- **THEN** a página não é baixada e o log traz `item_skipped` com `reason = TESTE_EVENT`

#### Scenario: Título com Teste
- **WHEN** o slug é neutro e o título é "Show TESTE de som"
- **THEN** o item é `skipped` antes de qualquer chamada ao modelo

#### Scenario: Palavra parecida
- **WHEN** o título é "Testemunho de fé"
- **THEN** o item não é ignorado

#### Scenario: Contagem
- **WHEN** a execução ignora 2 itens de teste
- **THEN** o log final traz `items_skipped = 2` e o `ingestion_run` os soma em `unchanged`

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

### Requirement: Fim da execução e contadores
Toda execução que passa da validação inicial MUST terminar emitindo o log `run_finished` com os contadores, inclusive no `--dry-run` e quando abortada (com `error_code`). Cada item descoberto MUST cair em exatamente um de `items_created`, `items_updated`, `items_unchanged`, `items_skipped` ou `items_failed`; os não processados aparecem em `items_pending`.

#### Scenario: Cinco itens, cinco baldes
- **WHEN** a execução descobre 5 itens: um novo, um com preço novo, um igual, um de teste e um com falha de download
- **THEN** `run_finished` traz `items_discovered = 5`, e 1 em cada balde, e `items_pending = 0`

#### Scenario: Dry-run
- **WHEN** a execução roda com `--dry-run`
- **THEN** `run_finished` sai com `dry_run = true`, `run_id = "-"` e os mesmos baldes

#### Scenario: Execução abortada
- **WHEN** o terceiro de cinco itens levanta `DB_UNAVAILABLE`
- **THEN** `run_finished` sai com `status = FAILED`, `error_code = DB_UNAVAILABLE` e `items_pending = 3`

#### Scenario: Validação inicial falha
- **WHEN** a cidade não existe
- **THEN** `run_finished` sai com `status = FAILED`, `error_code = CITY_NOT_FOUND` e contadores zero
