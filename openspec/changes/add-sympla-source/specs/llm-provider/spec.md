## ADDED Requirements

### Requirement: Saída estruturada
A interface do modelo MUST oferecer uma chamada que recebe um prompt e um modelo pydantic e devolve uma instância validada, usando `with_structured_output` na implementação do Claude. Resposta que não valide MUST ser sinalizada como `EXTRACTION_INVALID`, distinta de `LLM_UNAVAILABLE`.

#### Scenario: Resposta válida
- **WHEN** o modelo devolve dados que validam no esquema
- **THEN** a chamada devolve a instância do modelo pydantic

#### Scenario: Resposta inválida
- **WHEN** a saída não valida no esquema
- **THEN** a chamada falha com `EXTRACTION_INVALID`

#### Scenario: Provedor indisponível
- **WHEN** o provedor recusa a chave durante a saída estruturada
- **THEN** a chamada falha com `LLM_UNAVAILABLE`, sem expor a chave

### Requirement: Modelo falso com saída estruturada
O modelo falso MUST aceitar instâncias ou dicionários pré-definidos para a saída estruturada, validá-los no esquema pedido e registrar os prompts recebidos, sem rede e sem chave.

#### Scenario: Resposta pré-definida
- **WHEN** um teste configura o falso com um dicionário válido
- **THEN** a chamada devolve a instância validada e o prompt fica registrado

#### Scenario: Dicionário inválido
- **WHEN** o teste configura um dicionário que não valida
- **THEN** a chamada falha com `EXTRACTION_INVALID`
