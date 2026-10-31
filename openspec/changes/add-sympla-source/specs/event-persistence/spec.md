## ADDED Requirements

### Requirement: Idempotência por vínculo e hash
Cada item MUST ter um `content_hash` (SHA-256 em hex) calculado sobre os dados brutos da fonte (`__NEXT_DATA__` do evento, JSON-LD e texto da seção de ingressos), normalizados de forma determinística, antes de qualquer chamada ao modelo. Com o vínculo `(source_id, external_id)` lido com `FOR UPDATE`: sem vínculo, cria; hash igual, só atualiza `last_seen_at` e `last_run_id`, conta em `unchanged` e não chama o modelo; hash diferente, atualiza e conta em `updated`.

#### Scenario: Primeira execução
- **WHEN** o item não tem vínculo
- **THEN** o modelo é chamado, são criados evento, sessões e vínculo (com `status = 'PUBLISHED'` e `origin = 'AGENT'`), e conta em `created`

#### Scenario: Segunda execução sem mudança
- **WHEN** o mesmo item é processado de novo com os mesmos dados brutos
- **THEN** o modelo não é chamado, nada muda no evento, `last_seen_at` e `last_run_id` são atualizados e conta em `unchanged`

#### Scenario: Mudança de preço
- **WHEN** o texto da seção de ingressos muda o preço mínimo
- **THEN** o hash muda, o modelo é chamado, o evento é atualizado, `version` sobe 1 e conta em `updated`

### Requirement: Hash estável sobre dados brutos
A normalização do hash MUST usar uma lista fixa de campos relevantes ao conteúdo, ordenar chaves, normalizar espaços e entidades HTML, e incluir uma versão da regra. Campos voláteis ou de rastreio (integrações, visitas recentes, parâmetros de URL) MUST NOT entrar no hash.

#### Scenario: Campo volátil muda
- **WHEN** só um campo ignorado muda entre duas execuções (ex.: `cardPreviousView`)
- **THEN** o hash é o mesmo e o item conta em `unchanged`

#### Scenario: Espaços e entidades
- **WHEN** a descrição difere só por espaços extras ou `&nbsp;`
- **THEN** o hash é o mesmo

#### Scenario: Seção de ingressos ausente
- **WHEN** a página não tem a seção de ingressos
- **THEN** o hash usa texto vazio nessa parte e um aviso `pricing_section_missing` é logado

### Requirement: Uma transação por evento
Cada item MUST ser gravado em uma transação própria: local, evento, sessões e vínculo juntos. Qualquer falha MUST fazer rollback do item inteiro, registrá-lo em `errors` e `failed`, e seguir. Nenhuma transação MUST cobrir a execução toda.

#### Scenario: Falha no meio do item
- **WHEN** a inserção das sessões viola um check do banco (`DB_CONTRACT_VIOLATION`)
- **THEN** evento, venue e vínculo do item não existem após o rollback e o item entra em `failed`

#### Scenario: Itens independentes
- **WHEN** o segundo de três itens falha
- **THEN** o primeiro e o terceiro permanecem gravados

### Requirement: Atualização com versão e locks
A atualização MUST ler `version` e `locked_fields` com `FOR UPDATE`, gravar com `version = version + 1 ... WHERE version = :version` e MUST NOT escrever em coluna listada em `locked_fields`. Com `occurrences` listado, as sessões não são reescritas; lock em outra coluna não as bloqueia. O agente MUST NOT alterar `locked_fields` nem `status`.

#### Scenario: Campo travado preservado
- **WHEN** `locked_fields` contém `title` e o título na fonte mudou
- **THEN** o título do evento permanece o corrigido à mão e as demais colunas são atualizadas

#### Scenario: Sessões travadas
- **WHEN** `locked_fields` contém `occurrences` e as datas na fonte mudaram
- **THEN** as sessões do evento não são alteradas

#### Scenario: Conflito de versão
- **WHEN** o `UPDATE` afeta 0 linhas porque `version` mudou
- **THEN** o item é relido e reavaliado, e após 3 tentativas falha com `VERSION_CONFLICT`

### Requirement: Lápide não recria
Vínculo com `event_id` nulo MUST ser tratado como lápide: o agente MUST NOT criar evento nem vínculo para o par, mesmo que o conteúdo mude, e conta em `unchanged`. A lápide MUST ser conferida antes de baixar a página.

#### Scenario: Evento excluído à mão
- **WHEN** o vínculo do item tem `event_id` nulo
- **THEN** nenhum evento nem vínculo é criado e o item conta em `unchanged`

#### Scenario: Lápide antes do download
- **WHEN** o item é uma lápide
- **THEN** a página do evento não é baixada

### Requirement: Deduplicação entre fontes
Antes de criar, o agente MUST procurar evento da mesma cidade com `similarity(title) >= 0.6` e sessão em até 1 dia da primeira sessão do item. Se achar, MUST criar apenas o vínculo da fonte apontando para ele, sem sobrescrevê-lo, e contar em `unchanged`.

#### Scenario: Evento de outra fonte
- **WHEN** já existe um evento de outra fonte com título parecido e sessão no mesmo dia
- **THEN** nenhum evento novo é criado e o vínculo da Sympla aponta para o existente

#### Scenario: Sem correspondência
- **WHEN** nenhum evento da cidade atinge o limiar na janela de 1 dia
- **THEN** o evento é criado normalmente

### Requirement: Escrita tipada e parametrizada
Toda escrita MUST usar SQL parametrizado em funções tipadas; o modelo MUST NOT gerar SQL. Violação de constraint (`23514`, `23505` em `event_city_slug_key`) MUST falhar só o item, com `DB_CONTRACT_VIOLATION`.

#### Scenario: Slug duplicado na cidade
- **WHEN** `event_city_slug_key` rejeita o evento com `23505`
- **THEN** o item falha com `DB_CONTRACT_VIOLATION` e a execução segue

### Requirement: Evento gravado reclassificado como não cultural
Se um evento já gravado passar a ser classificado como não cultural, o agente MUST NOT apagá-lo, despublicá-lo nem alterá-lo (nem o vínculo): MUST apenas registrar o aviso `existing_event_not_cultural` com o `event_id` e o motivo, para decisão humana.

#### Scenario: Reclassificação
- **WHEN** o conteúdo de um evento publicado muda e o modelo o classifica como não cultural
- **THEN** o evento segue `PUBLISHED`, com a mesma `version`, e o log traz `existing_event_not_cultural` com o `event_id`

#### Scenario: Evento novo não cultural
- **WHEN** um item sem vínculo é classificado como não cultural
- **THEN** nenhum aviso `existing_event_not_cultural` é emitido
