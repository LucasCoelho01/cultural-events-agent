# Tasks

## 1. Configuração e erros

- [x] 1.1 Adicionar `request_timeout_seconds` (float > 0, padrão 30) e `max_pages_per_run` (int ≥ 1, padrão 50) a `Settings`, com `CONFIG_INVALID` citando só o nome da variável; incluir as duas linhas no `.env.example` e na tabela do README
- [x] 1.2 Em `errors.py`, criar `CityNotFound` (6), `CityInactive` (7), `SourceNotFound` (8), `RobotsDisallowed` (9), `FetchFailed` (10), `ExtractionInvalid` e `VersionConflict` (estes dois só por item)
- [x] 1.3 Testes: padrões e valores inválidos das novas variáveis; código e exit code de cada novo erro

## 2. LlmClient com saída estruturada

- [x] 2.1 Adicionar `extract(prompt, schema)` ao protocolo `LlmClient` e a `AnthropicLlm` (`with_structured_output`; `ValidationError` ou saída vazia → `ExtractionInvalid`; `anthropic.AnthropicError` → `LlmUnavailable` com segredos mascarados)
- [x] 2.2 Estender `FakeLlm` com `structured` (instância, dicionário ou lista consumida em ordem), validação no esquema pedido e registro dos prompts
- [x] 2.3 Testes de `llm.py`: resposta válida, resposta inválida, provedor indisponível sem expor a chave, falso com dicionário válido e inválido

## 3. Coleta educada

- [x] 3.1 Criar `fetching.py` com a interface `PageFetcher` e o ritmo por domínio (relógio injetável): pausa de `REQUEST_DELAY_SECONDS`, `USER_AGENT` em toda requisição e `FetchFailed` em timeout ou HTTP de erro
- [x] 3.2 Implementar a leitura do `robots.txt` uma vez por domínio (4xx permite, 5xx ou rede proíbe) e a checagem por URL com `RobotsDisallowed`
- [x] 3.3 Implementar `HttpxPlaywrightFetcher`: listagem por `httpx`, evento por Playwright com um navegador reaproveitado (`close` fecha context, browser e playwright cada um no seu try e zera as referências), ambos com `REQUEST_TIMEOUT_SECONDS`, e o contador de `MAX_PAGES_PER_RUN`
- [x] 3.4 Testes sem rede (servidor `httpx.MockTransport` e relógio falso): robots proíbe, robots 404, robots 503, pausa entre requisições, cabeçalho `User-Agent`, timeout e limite de páginas

## 4. Fixtures da Sympla

- [x] 4.1 Salvar em `tests/fixtures/sympla/` a listagem e duas páginas de evento já renderizadas (`page.content()`), reduzidas ao JSON-LD, ao `__NEXT_DATA__` sem blocos de integração e ao texto com preços; criar variações pequenas à mão para evento gratuito, local a definir, evento privado e evento sem sessão futura
- [x] 4.2 Documentar no README de `tests/fixtures/sympla/` a data e a URL de origem de cada fixture

## 5. Interface Source e Sympla

- [x] 5.1 Criar `sources/base.py` com o protocolo `Source` (incluindo `pricing_text` e `hash_input`), `DiscoveredItem` e `StructuredEvent`
- [x] 5.2 Criar `sources/sympla.py`: `listing_url` (`<slug>-<uf>`, `?page=N`), `parse_listing` (ids, URL canônica sem parâmetros de rastreio, sem duplicados), `structured_data` (`__NEXT_DATA__` e JSON-LD, descrição mais completa, horário sem fuso), `pricing_text` (seção de ingressos), `hash_input` (lista fixa de campos, sem voláteis) e `page_text`
- [x] 5.3 Testes de `SymplaSource` sobre as fixtures: links da listagem, URL canônica, JSON-LD e `__NEXT_DATA__`, descrição truncada, evento privado, local a definir, e seção de ingressos achada na fixture (falha se o seletor não achar)

## 5b. Hash dos dados brutos e eventos de teste

- [x] 5b.1 Criar `fingerprint.py`: `content_hash(hash_input)` (JSON canônico com `v`, `next`, `ld`, `pricing`; `html.unescape`, NFC, espaços colapsados; SHA-256 em hex) e `is_test_event(text)` (palavra inteira `teste`, sem diferenciar maiúsculas)
- [x] 5b.2 Testes sem banco: hash estável com chaves em outra ordem, espaços e `&nbsp;`; hash igual com mudança só em campo volátil; hash diferente com preço diferente no texto de ingressos; `pricing_section_missing`; `is_test_event` para slug, título, "TESTE" e "Testemunho"

## 6. Extração

- [x] 6.1 Criar `extraction.py` com os modelos pydantic `EventCompletion` e `NormalizedEvent` (invariantes de preço, datas, gratuito, título e sessão) e a montagem do prompt com o texto limitado e os slugs de categoria
- [x] 6.2 Implementar as camadas: dados estruturados primeiro, chamada ao modelo só para campos faltantes, conversão de fuso com `city.timezone`, preço em centavos e `ExtractionInvalid` por item
- [x] 6.3 Implementar `skipped` com motivo (`NO_FUTURE_SESSION`, `EVENT_NOT_PUBLIC`, `TESTE_EVENT` pelo título) e o log `item_skipped`
- [x] 6.4 Testes com `FakeLlm`: modelo não chamado quando nada falta, preço em centavos, gratuito, horário sem fuso, resposta fora do esquema, preço incoerente, evento já iniciado, evento privado e título com "teste"

## 7. Mapeamento

- [x] 7.1 Criar `mapping.py`: leitura das categorias do banco, escolha por slug com fallback `outros`, e normalização do endereço (CEP e coordenadas inválidos viram nulos)
- [x] 7.2 Implementar a busca de local por `similarity()` na cidade (limiar 0,6), nome genérico sem venue e a criação do `venue` quando nada atinge o limiar
- [x] 7.3 Testes com Testcontainers: slug válido, slug inventado, local existente com grafia diferente, local novo, "Local a definir", venue de outra cidade e CEP inválido

## 8. Gravação conforme o contrato

- [x] 8.1 Criar `writer.py` com a busca do vínculo (lida na triagem e repetida com `FOR UPDATE` na gravação: lápide, sem vínculo, hash igual, hash diferente), o `touch` de `last_seen_at`/`last_run_id` e o hash dos dados brutos gravado no vínculo, com SQL parametrizado
- [x] 8.2 Implementar a criação: venue, evento `PUBLISHED`/`AGENT`, sessões e vínculo, numa transação por item, com rollback e `DbContractViolation` por item
- [x] 8.3 Implementar a deduplicação entre fontes (`similarity` ≥ 0,6, janela de 1 dia): só o vínculo, sem sobrescrever o evento existente; atualização e `--dry-run` só para o dono (`origin = 'AGENT'` e vínculo mais antigo desta fonte), senão só o vínculo anda
- [x] 8.4 Implementar a atualização: colunas não travadas, `version = version + 1 … WHERE version`, sessões por `ON CONFLICT` e `DELETE … <> ALL`, `occurrences` travado preserva as sessões, até 3 tentativas e `VersionConflict`
- [x] 8.5 Testes com Testcontainers como `agent_writer`: criar, repetir sem mudança (`unchanged`), mudança de preço no texto de ingressos (`updated`, `version` +1), `title` travado, `occurrences` travado, lápide (nada criado), rollback do item inteiro, dois itens independentes, dedupe entre fontes `23505` em `event_city_slug_key`, e evento admin/de outra fonte vinculado por duplicata intacto quando o hash muda

## 9. Grafo e execução

- [x] 9.1 Criar `graph.py` com o `StateGraph` (descobrir → baixar → triar → extrair → mapear → deduplicar → gravar), o laço por item, a falha isolada, o descarte de `teste` por slug e a conferência da lápide antes do download, a triagem por hash sem chamar o modelo e o respeito a `--limit` e `MAX_PAGES_PER_RUN`
- [x] 9.2 Implementar a validação de cidade (`CITY_NOT_FOUND`, `CITY_INACTIVE`) e de fonte (`SOURCE_NOT_FOUND`), o `ingestion_run` (`SUCCESS`/`PARTIAL`/`FAILED`, fechado num `finally`) e os contadores
- [x] 9.3 Implementar o `--dry-run`: sessão com `default_transaction_read_only = on`, sem `open_run`, nó `report` que imprime um JSON por item
- [x] 9.4 Testes de fluxo com `Source` falsa, `FakePageFetcher`, `FakeLlm` e Testcontainers: **rodar duas vezes chama o `FakeLlm` só na primeira**, eventos de teste `skipped` (slug sem download, título sem modelo) com `skipped` no log final, ordem das etapas, item com `EXTRACTION_INVALID` e execução seguindo, `ROBOTS_DISALLOWED` num evento e na listagem, `FETCH_FAILED` na listagem fechando como `FAILED`, cidade inexistente e inativa, `--dry-run` sem nenhuma linha nova e com escrita recusada pelo banco

## 10. CLI e documentação

- [x] 10.1 Adicionar o comando `run` em `cli.py` (`--source`, `--city uf/slug`, `--limit`, `--dry-run`), o registro de fontes, os novos códigos de saída 6–10 e o log final com os contadores
- [x] 10.2 Testes da CLI: execução normal com contadores no log, falha parcial com saída 0, fonte desconhecida (64), cidade inexistente (6), cidade inativa (7), listagem bloqueada (9)
- [x] 10.3 Atualizar o README: comando `run`, `--dry-run`, tabela de códigos de saída 6–10 e as variáveis novas
- [x] 10.4 Rodar `uv run pytest` e `uv run ruff check` / `ruff format --check` e deixar tudo verde antes de marcar a mudança como pronta

## 11. Ajustes após a implementação

- [x] 11.1 `AnthropicLlm.extract` usa `with_structured_output(schema, method="json_schema")` e trata `OutputParserException` e `ValueError` do parser como `ExtractionInvalid`; teste atualizado
- [x] 11.2 Guarda por item no grafo: exceção inesperada vira `ITEM_FAILED` (tipo e mensagem mascarada em `ingestion_run.errors`) e a execução segue; só `DbUnavailable`, `LlmUnavailable` e erros da listagem abortam; testes de fluxo
- [x] 11.3 `UNEXPECTED_ERROR` loga a mensagem (filtrada) e, com `LOG_LEVEL=DEBUG`, o traceback; `LOG_LEVEL` em `Settings`, `.env.example` e README; testes

## 12. Diagnóstico de falhas e fim da execução

- [x] 12.1 `EXTRACTION_INVALID` com `loc [type] msg` de cada erro do pydantic (sem o input); `last_raw` no `AnthropicLlm` (`include_raw=True`) e no `FakeLlm`
- [x] 12.2 Com `LOG_LEVEL=DEBUG`, gravar a resposta bruta em `.debug/<external_id>.json` (segredos mascarados); `.debug/` no `.gitignore`
- [x] 12.3 `run_finished` sempre emitido (inclusive no `--dry-run`, abortado e validação inicial), com `items_discovered`, os cinco baldes e `items_pending`; teste com 5 itens

## 13. Falha real de `ValueError` na extração

- [x] 13.1 Reproduzir contra a API real: a causa era `temperature=0` (`ValueError: temperature is not supported for claude-sonnet-5-5 at non-default values`), recusado pelo `langchain_anthropic` antes da requisição; remover o `temperature` e testar que o chat padrão monta o payload
- [x] 13.2 `EventCompletion` só com tipos simples (datas em texto ISO 8601, sem `ge`); restrições no `NormalizedEvent`; teste que falha se o JSON Schema tiver palavras-chave proibidas
- [x] 13.3 `detail` de `EXTRACTION_INVALID` sempre com tipo e mensagem da exceção (mascarada) e, para `ValidationError`, os campos; mensagem do item também mascarada em `ingestion_run.errors`
- [x] 13.4 Dry-run real com `--limit 5` (Sympla, Claude e banco)

## 14. Filtro de eventos não culturais

- [x] 14.1 `EventCompletion` com `is_cultural` e `cultural_reason`, critério e categoria da fonte como dica no prompt, na mesma chamada; `StructuredEvent.is_cultural` opcional da fonte
- [x] 14.2 `NOT_CULTURAL` vira `skipped` com o motivo no log (`item_skipped`) e no `--dry-run`, antes de qualquer outra validação, sem gravar nada
- [x] 14.3 Evento já gravado reclassificado: não apagar, despublicar nem alterar; só o aviso `existing_event_not_cultural`
- [x] 14.4 Testes com `FakeLlm`: cultural, não cultural, `--dry-run`, evento já existente e a limitação conhecida (reavaliação a cada execução)
- [x] 14.5 Registrar a limitação no design (riscos) e no README; rodar `--dry-run` com `--limit 10`

## 15. Limite de taxa da fonte (HTTP 429)

- [x] 15.1 Diagnosticar o run real: os timeouts de 30 s eram 429 da Sympla (página de desafio que nunca fica ociosa), e o `TargetClosedError` deixava o navegador reaproveitado em mau estado
- [x] 15.2 Renderer: `domcontentloaded` + espera do ingresso (sem `networkidle`), 429 vira `RateLimited` com `Retry-After`, e o navegador é descartado em erro inesperado
- [x] 15.3 `PoliteFetcher`: espera por domínio depois do 429 (`Retry-After` até 120 s, ou 30 s dobrando), sucesso zera a sequência; 429 também na listagem e no `robots.txt`
- [x] 15.4 Grafo: três 429 seguidos interrompem a execução (`RATE_LIMITED`, saída 11, `ingestion_run` `FAILED`, resto em `items_pending`)
- [x] 15.5 Testes: backoff e teto, `Retry-After` inválido, sequência zerada, interrupção no grafo e Chromium real contra servidor local (429, HTTP 500, página sem idle, navegador fechado)
