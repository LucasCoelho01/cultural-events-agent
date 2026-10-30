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
