# llm-provider Specification

## Purpose
Define como o agente obtém o modelo de linguagem por uma interface substituível, para que o código que o usa seja testável sem chamar a API real.

## Requirements

### Requirement: Modelo por interface
O agente MUST obter o modelo de linguagem por uma interface, e o código que o consome MUST depender só dela. A implementação padrão MUST usar o Claude com o modelo de `ANTHROPIC_MODEL`.

#### Scenario: Modelo padrão
- **WHEN** a fábrica é criada com a configuração padrão
- **THEN** o modelo usado é `claude-sonnet-5-5`

#### Scenario: Modelo configurado
- **WHEN** `ANTHROPIC_MODEL` define outro modelo
- **THEN** a fábrica usa esse modelo

### Requirement: Substituição por modelo falso
A interface MUST permitir injetar um modelo falso, de modo que testes rodem sem rede e sem `ANTHROPIC_API_KEY` válida.

#### Scenario: Teste com modelo falso
- **WHEN** um teste injeta um modelo falso com resposta fixa
- **THEN** o código consumidor recebe essa resposta e nenhuma chamada de rede é feita

### Requirement: Falha do provedor
Falha ao chamar o modelo (chave recusada, rede ou limite de uso) MUST encerrar com `LLM_UNAVAILABLE`, sem expor a chave de API.

#### Scenario: Chave recusada
- **WHEN** o provedor rejeita a `ANTHROPIC_API_KEY`
- **THEN** o agente falha com `LLM_UNAVAILABLE` e a mensagem não contém a chave

#### Scenario: Falha de rede
- **WHEN** a chamada ao provedor não completa
- **THEN** o agente falha com `LLM_UNAVAILABLE`

### Requirement: Saída estruturada
A interface do modelo MUST oferecer uma chamada que recebe um prompt e um modelo pydantic e devolve uma instância validada, usando `with_structured_output(schema, method="json_schema", include_raw=True)` na implementação do Claude (resposta bruta em `last_raw`). Resposta que não valide, inclusive `OutputParserException` e `ValueError` do parser, MUST ser sinalizada como `EXTRACTION_INVALID`, distinta de `LLM_UNAVAILABLE`, com tipo e mensagem da exceção no detalhe (segredos mascarados).

#### Scenario: Resposta válida
- **WHEN** o modelo devolve dados que validam no esquema
- **THEN** a chamada devolve a instância do modelo pydantic

#### Scenario: Resposta inválida
- **WHEN** a saída não valida no esquema
- **THEN** a chamada falha com `EXTRACTION_INVALID`

#### Scenario: Detalhe do erro de validação
- **WHEN** a saída não valida no esquema
- **THEN** o detalhe de `EXTRACTION_INVALID` traz `loc`, `type` e `msg`, sem o valor recebido, e `last_raw` guarda a resposta bruta

#### Scenario: Mensagem da exceção
- **WHEN** a exceção é um `ValueError` com a chave de API na mensagem
- **THEN** o detalhe traz `ValueError` e a mensagem, com a chave mascarada

#### Scenario: Esquema simples
- **WHEN** o JSON Schema do esquema de extração é gerado
- **THEN** não contém `minimum`, `maximum`, `pattern`, `format` nem outras restrições

#### Scenario: Erro do parser
- **WHEN** o parser levanta `OutputParserException` ou `ValueError`
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

### Requirement: Parâmetros aceitos pelo modelo
O cliente do Claude MUST NOT enviar parâmetros que o modelo configurado recusa, como `temperature` com valor diferente do padrão. A montagem do pedido MUST ser verificada por teste sem chamar a API.

#### Scenario: Pedido válido
- **WHEN** o cliente padrão monta o pedido para `claude-sonnet-5-5`
- **THEN** a montagem não levanta `ValueError` e `temperature` não é definida
