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
- **WHEN** os dados estruturados cobrem os campos obrigatórios, o preço, o veredito cultural e a categoria (um slug do banco)
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
Saída do modelo que não validar no modelo pydantic, ou item que violar as invariantes (preço máximo menor que o mínimo, fim antes do início, gratuito com preço positivo, título vazio, sem sessão), MUST falhar só aquele item com `EXTRACTION_INVALID`, cujo detalhe lista `loc`, `type` e `msg` de cada erro do pydantic, sem o valor recebido. A execução MUST seguir com os demais.

#### Scenario: Resposta fora do esquema
- **WHEN** o modelo devolve um objeto que não valida no esquema
- **THEN** o item falha com `EXTRACTION_INVALID` e nada é gravado para ele

#### Scenario: Detalhe sem o valor recebido
- **WHEN** o modelo devolve `price_min_cents` com o texto `caro-demais`
- **THEN** o detalhe traz `price_min_cents [int_parsing]` e a mensagem do pydantic, sem `caro-demais`

#### Scenario: Preço incoerente
- **WHEN** o item extraído tem `price_max_cents` menor que `price_min_cents`
- **THEN** o item falha com `EXTRACTION_INVALID`

### Requirement: Evento sem sessão futura
Item sem ao menos uma sessão com início no futuro MUST NOT ser publicado nem contar como falha: é `skipped` com motivo `NO_FUTURE_SESSION`. O mesmo vale para evento cancelado, não publicado ou privado (`EVENT_NOT_PUBLIC`). Item `skipped` conta em `unchanged` no `ingestion_run`.

#### Scenario: Evento já iniciado
- **WHEN** todas as sessões do item começam antes de agora
- **THEN** o item é `skipped` com `NO_FUTURE_SESSION` e nada é gravado

#### Scenario: Evento privado
- **WHEN** o `__NEXT_DATA__` marca `privateEvent = true`
- **THEN** o item é `skipped` com `EVENT_NOT_PUBLIC`

### Requirement: Resposta bruta em depuração
Com `LOG_LEVEL=DEBUG`, a resposta bruta do modelo de um item `EXTRACTION_INVALID` MUST ser gravada em `.debug/<external_id>.json`, com segredos mascarados. Sem DEBUG nada MUST ser gravado, e `.debug/` MUST constar no `.gitignore`. Item que falha sem ter chamado o modelo MUST NOT herdar a resposta de outro item.

#### Scenario: DEBUG ligado
- **WHEN** `LOG_LEVEL=DEBUG` e a saída do modelo de um item não valida
- **THEN** `.debug/<external_id>.json` traz o `external_id`, o erro e a resposta bruta

#### Scenario: DEBUG desligado
- **WHEN** `LOG_LEVEL` é `INFO` e um item falha com `EXTRACTION_INVALID`
- **THEN** nenhum arquivo é criado em `.debug/`

#### Scenario: Item sem chamada ao modelo
- **WHEN** um item falha com `EXTRACTION_INVALID` sem chamar o modelo, logo após outro que chamou
- **THEN** nenhum arquivo é gravado para ele

### Requirement: Classificação cultural na mesma chamada
A extração MUST classificar o evento em `is_cultural` e `cultural_reason` (frase curta) na mesma chamada ao modelo, sem chamada extra. Cultural: shows, teatro, dança, cinema, exposições, literatura, festas e festivais culturais, gastronomia cultural. Não cultural: cursos, workshops técnicos, networking, negócios, terapias, religião, esporte competitivo, webinars. A categoria da fonte MUST ir ao modelo só como dica.

#### Scenario: Mesma chamada
- **WHEN** um evento novo é extraído
- **THEN** uma única chamada devolve os campos faltantes e a classificação cultural

#### Scenario: Categoria da fonte como dica
- **WHEN** a fonte atribui a categoria `sociedade-e-cultura`
- **THEN** o prompt a traz como dica e o veredito continua sendo do modelo

#### Scenario: Modelo sem veredito
- **WHEN** a resposta não traz `is_cultural`
- **THEN** o evento não é descartado

### Requirement: Evento não cultural é ignorado
Evento classificado como não cultural MUST ser `skipped` com o motivo `NOT_CULTURAL`, o `cultural_reason` no log (`item_skipped`) e nada gravado, inclusive no `--dry-run`. A decisão MUST vir antes de qualquer outra validação do item.

#### Scenario: Workshop técnico
- **WHEN** o modelo devolve `is_cultural = false` com o motivo "workshop técnico"
- **THEN** o item é `skipped` com `NOT_CULTURAL`, o motivo vai ao log e nenhum evento, venue ou vínculo é criado

#### Scenario: Dry-run
- **WHEN** o comando roda com `--dry-run`
- **THEN** o item não cultural sai como `skipped` com `reason` e `detail`, e o banco não muda

#### Scenario: Preço incoerente num evento não cultural
- **WHEN** o item é não cultural e também traz preço máximo menor que o mínimo
- **THEN** o item é `skipped` e não falha com `EXTRACTION_INVALID`
