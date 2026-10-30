## ADDED Requirements

### Requirement: Dados estruturados primeiro
A extração MUST ler antes os dados estruturados da página: JSON-LD `Event` e, na Sympla, o `__NEXT_DATA__`. O modelo MUST ser chamado só para os campos que faltarem ou que precisarem de normalização, e MUST NOT ser chamado quando os dados estruturados já cobrirem todos os campos.

#### Scenario: JSON-LD presente
- **WHEN** a página tem JSON-LD com nome, datas, imagem e local
- **THEN** esses campos vêm do JSON-LD e não do modelo

#### Scenario: Descrição truncada
- **WHEN** a descrição do JSON-LD termina em reticências
- **THEN** a descrição completa é obtida do `__NEXT_DATA__` ou do texto da página

#### Scenario: Nada a completar
- **WHEN** os dados estruturados cobrem todos os campos obrigatórios e o preço
- **THEN** nenhuma chamada ao modelo é feita para o item

### Requirement: Campos extraídos
O item normalizado MUST ter: título, descrição, sessões (início com fuso e fim opcional), preço mínimo e máximo em centavos, `is_free`, nome e endereço do local, `ticket_url`, `image_url` e classificação etária opcional. Horário sem fuso MUST usar o fuso da cidade (`city.timezone`).

#### Scenario: Preço em centavos
- **WHEN** a página mostra ingressos de R$ 100,00 e R$ 110,00
- **THEN** `price_min_cents = 10000` e `price_max_cents = 11000`

#### Scenario: Evento gratuito
- **WHEN** a página indica ingresso gratuito
- **THEN** `is_free = true` e os preços são nulos ou zero

#### Scenario: Horário sem fuso
- **WHEN** a fonte informa `2026-12-12 10:30:00` sem fuso e a cidade é `America/Sao_Paulo`
- **THEN** a sessão começa em `2026-12-12T10:30:00-03:00`

### Requirement: Saída inválida do modelo
Saída do modelo que não validar no modelo pydantic, ou item que violar as invariantes (preço máximo menor que o mínimo, fim antes do início, gratuito com preço positivo, título vazio, sem sessão), MUST falhar só aquele item com `EXTRACTION_INVALID`. A execução MUST seguir com os demais.

#### Scenario: Resposta fora do esquema
- **WHEN** o modelo devolve um objeto que não valida no esquema
- **THEN** o item falha com `EXTRACTION_INVALID` e nada é gravado para ele

#### Scenario: Preço incoerente
- **WHEN** o item extraído tem `price_max_cents` menor que `price_min_cents`
- **THEN** o item falha com `EXTRACTION_INVALID`

### Requirement: Evento sem sessão futura
Item sem ao menos uma sessão com início no futuro MUST NOT ser publicado nem contar como falha: é ignorado e conta em `unchanged`. O mesmo vale para evento cancelado, não publicado ou privado na fonte.

#### Scenario: Evento já iniciado
- **WHEN** todas as sessões do item começam antes de agora
- **THEN** o item é ignorado, conta em `unchanged` e nada é gravado

#### Scenario: Evento privado
- **WHEN** o `__NEXT_DATA__` marca `privateEvent = true`
- **THEN** o item é ignorado e conta em `unchanged`
