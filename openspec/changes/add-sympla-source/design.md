# Design

## Context

A fundação (`setup-agent-foundation`, arquivada) entrega config, `db.py` (contrato, `open_run`/`close_run`, `translate_db_errors`), `llm.py` (`LlmClient` com `invoke(prompt) -> str` e `FakeLlm`), `logs.py` e a CLI `check`. Falta coletar. O banco já tem `source(code='sympla', city_id NULL)` (V007), a cidade `SP/sao-paulo` ativa (V002) e as categorias `shows, teatro, exposicoes, danca, cinema, outros` (V003). O `AGENT_CONTRACT.md` define a gravação; o design só o aplica.

### O que existe na Sympla (explorado com Playwright em 2026-10-30)

Exploração feita com `USER_AGENT` identificado, pausa de 2 s entre requisições e depois de ler o `robots.txt`, que responde `User-agent: *` / `Allow: /`.

**Listagem** `https://www.sympla.com.br/eventos/sao-paulo-sp` (redireciona para `?category=city`):
- Renderizada no servidor: um `GET` simples com `httpx`, sem JavaScript, já traz 50 links `/evento/<slug>/<id>` (o DOM do navegador mostrou 43). Não há JSON-LD nem `__NEXT_DATA__` nela.
- O slug da cidade na Sympla é `<slug>-<uf>` (`sao-paulo-sp`), derivado de `city.slug` e `city.state_uf`.
- Paginação por `?page=N` funciona (páginas 2 e 3 trazem 59 e 60 ids novos). A listagem não traz datas.
- O `<id>` numérico da URL é estável e vira o `external_id`. Os links vêm com parâmetros de rastreio (`?mp_rloc=...`), que devem ser removidos para formar a URL canônica.
- Há eventos de teste da própria Sympla na listagem (`evento-teste-565`, `blu-mp-juninho-teste-copia`).

**Página de evento** (duas abertas: `.../vamos-falar-de-agatha-christie-luiz-alfredo-garcia-roza/3298539` e `.../gouvea-pass/3346586`):

| Dado | Sem JavaScript (`httpx`) | Renderizado (Playwright) |
|---|---|---|
| JSON-LD `schema.org/Event` | **não** | sim: `name`, `startDate`/`endDate` com fuso (`-03:00`), `image`, `location` (nome e endereço), `eventStatus`, `organizer` |
| `__NEXT_DATA__` | sim | sim |
| Preço / ingressos | não | sim, só no DOM (`R$ 100,00`, `R$ 110,00`), carregado por XHR de uma API interna (`event-bff/.../tickets/grouped`) |

Detalhes que moldam o design:
- **JSON-LD é incompleto**: `offers` vem vazio; a `description` vem truncada em ~700 caracteres, com reticências e entidades HTML (`&nbsp;`, `&amp;`); `streetAddress` junta rua, número e bairro; o CEP pode vir vazio.
- **`__NEXT_DATA__`** (`props.pageProps.hydrationData.eventHydration.event`) tem: `id`, `name`, `startDate`/`endDate` **sem fuso** (`2026-12-12 10:30:00`; há `startDateMultiFormat.ISO8601` com fuso), `strippedDetail` (descrição completa em texto), `images.logoUrl`/`logoLarge`, `eventsAddress` (`name`, `address`, `addressNum`, `neighborhood`, `zipCode`, `geolocation.latitude/longitude`), `eventsCategory` (categoria da Sympla, ex.: `sociedade-e-cultura`), `cancelled`, `published`, `visible`, `privateEvent`, `paymentEventType` (`paid`).
- **Local a definir**: o segundo evento tem `eventsAddress.name = "Local a definir"` e endereço vazio.
- **Evento longo**: o `GOUVEA PASS` começa em 2026-03-23 (passado) e termina em 2027-12-31. Pelo contrato ("ao menos uma sessão futura") ele seria ignorado.
- Cada página examinada tinha uma única sessão (início e fim). Eventos com várias datas **não foram observados** nas duas páginas; as sessões múltiplas dependem do complemento pelo modelo a partir do texto e ficam como risco.
- O preço **não** está no JSON-LD nem no `__NEXT_DATA__`: só aparece depois de o JavaScript rodar. Por isso a página de evento é baixada com Playwright.

## Goals / Non-Goals

**Goals:**
- Fluxo `StateGraph` por item, reaproveitável por outras fontes via `Source`.
- Coleta educada e verificável por testes sem rede.
- Gravação idempotente e fiel ao contrato 1.0, com falha isolada por item.
- `--dry-run` que prova não escrever (conexão somente leitura).

**Non-Goals:**
- Outras fontes, agendamento, tela de monitoramento.
- Chamar as APIs internas da Sympla (`event-bff`, `discovery-bff`): não são contrato público.
- Paginar a listagem além do necessário para `--limit` e `MAX_PAGES_PER_RUN`.
- Filtrar eventos de teste da Sympla.

## Decisions

**1. Módulos.** `sources/base.py` (`Source`, `DiscoveredItem`, `RawEvent`), `sources/sympla.py` (`SymplaSource`), `fetching.py` (`PageFetcher`, `HttpxPlaywrightFetcher`, robots, ritmo), `extraction.py` (modelos pydantic, camadas, validação), `mapping.py` (categoria e local), `writer.py` (SQL tipado), `graph.py` (grafo e estado), `cli.py` (comando `run`). Segue o padrão plano da fundação; `sources/` é pacote porque abrigará as próximas fontes.

**2. Grafo.** Nós: `discover` → `fetch` → `extract` → `map` → `dedupe` → `write`, com aresta condicional de volta a `fetch` enquanto houver itens e para o fim ao esgotar a fila, o `--limit` ou `MAX_PAGES_PER_RUN`. Qualquer nó pode marcar o item como `failed` (com código) ou `skipped`; a aresta pula para o próximo item. Estado em um `TypedDict` (fila, item atual, contadores, erros). Com `--dry-run`, o nó `write` é trocado por `report` (imprime JSON). *Alternativa descartada:* um nó por etapa processando a lista inteira — guardaria todas as páginas em memória e dificultaria a transação por item.

**3. `Source`.** Protocolo com `code`, `listing_url(city, page)`, `parse_listing(html) -> list[DiscoveredItem]`, `structured_data(html) -> StructuredEvent` e `page_text(html) -> str`. A Sympla implementa tudo; o grafo não importa `sympla.py`. Uma `Source` falsa nos testes prova isso.

**4. Download híbrido.** Listagem por `httpx` (renderizada no servidor, mais barata e mais educada que abrir navegador); evento por Playwright (JSON-LD e preços só existem renderizados). Ambos atrás de `PageFetcher`, que os testes substituem por fixtures. Um único navegador por execução, reaproveitado entre itens.

**5. Coleta educada.** `robots.txt` lido uma vez por domínio com `urllib.robotparser` (o texto é baixado pelo nosso cliente, com `USER_AGENT`, pois o parser da stdlib não envia cabeçalho próprio): 4xx = permitido, 5xx ou falha de rede = proibido (conservador). Ritmo: relógio monotônico por domínio, dormindo o restante de `REQUEST_DELAY_SECONDS` desde a última requisição, incluindo o `robots.txt`. `REQUEST_TIMEOUT_SECONDS` vale para `httpx` e para a navegação do Playwright. `MAX_PAGES_PER_RUN` conta listagens e eventos, não o `robots.txt`. `--limit` limita itens; o menor dos dois limites vence. A lápide é conferida **antes** do download, economizando a requisição.

**6. Extração em camadas** (`extraction.py`):
1. `__NEXT_DATA__` e JSON-LD são lidos por código (sem modelo): título, datas, imagem, local, endereço, coordenadas, categoria da Sympla, flags de cancelado/privado. Para a descrição, vale a mais completa: `strippedDetail`; o JSON-LD só se não terminar em reticências.
2. Se faltar algo (preço, sessões adicionais, classificação etária, `ticket_url`, `is_free`), o modelo é chamado com o texto da página (limitado a ~12 mil caracteres) e o que já foi extraído, pedindo só os campos faltantes, via `with_structured_output` num modelo pydantic `EventCompletion`.
3. O resultado final é validado num modelo pydantic `NormalizedEvent` com as invariantes do banco (preço, datas, gratuito, título, ao menos uma sessão). Falha de esquema ou de invariante vira `EXTRACTION_INVALID`.

JSON-LD primeiro (pedido); o `__NEXT_DATA__` entra como segunda fonte estruturada porque a exploração mostrou que ele é mais completo e determinístico que o JSON-LD, e porque o JSON-LD só existe após renderizar. O preço quase sempre vem do modelo, a partir do texto renderizado.

*Fusos:* horário sem fuso usa `city.timezone`; `ZoneInfo` converte para `timestamptz`. *Preço:* o modelo devolve centavos inteiros; "gratuito" vira `is_free` com preços nulos.

**7. `LlmClient`.** Ganha `extract(prompt, schema: type[T]) -> T`. `AnthropicLlm` usa `chat.with_structured_output(schema)`, temperatura 0; `ValidationError` ou ausência de saída → `ExtractionInvalid`; `anthropic.AnthropicError` → `LlmUnavailable` (aborta a execução, pois todos os itens falhariam). `invoke` não muda. `FakeLlm(structured=...)` aceita instâncias, dicionários ou uma lista de respostas (consumidas em ordem) e valida no esquema pedido.

**8. Categoria e local** (`mapping.py`). Categorias lidas uma vez no início. O prompt de extração inclui a lista de slugs e a categoria da Sympla como dica; slug fora da lista vira `outros`. Local: `SELECT id, similarity(name, %s) ... WHERE city_id = %s ORDER BY 2 DESC LIMIT 1` com limiar 0,6 (o mesmo sugerido pelo contrato para título; calibrar com dados reais). Nomes genéricos (`Local a definir`, vazio) → sem venue. Na criação, `postal_code` fora de `\d{5}-\d{3}` e coordenadas fora do intervalo viram nulos. O endereço é montado de `address`, `addressNum` e `addressAlt`.

**9. Gravação** (`writer.py`). Uma função por passo, todas com SQL parametrizado, executadas na transação do item (`with conn.transaction()`):
- `SELECT … FROM event_source_link … FOR UPDATE` → decisão: lápide / sem vínculo / hash igual / hash diferente.
- Sem vínculo: dedupe entre fontes (contrato §8, limiar 0,6, janela de 1 dia); se achar, só insere o vínculo (conta `unchanged`); senão, `venue` (se preciso), `event` (`PUBLISHED`, `AGENT`), `occurrence` por sessão e vínculo (`created`).
- Hash diferente: `SELECT version, locked_fields FROM event … FOR UPDATE`; `UPDATE` só das colunas não travadas, com `version = version + 1 … WHERE version = %s`; sessões reescritas por `ON CONFLICT (event_id, starts_at)` e `DELETE … <> ALL(%s)` salvo `occurrences` travado; até 3 tentativas, depois `VERSION_CONFLICT`. A lista de colunas atualizáveis é fixa em código (`title`, `description`, `category_id`, `venue_id`, `age_rating`, `price_min_cents`, `price_max_cents`, `is_free`, `ticket_url`, `image_url`), de modo que nomes de coluna nunca vêm de dados externos. `status` e `locked_fields` nunca são escritos na atualização.
- `content_hash` = SHA-256 do JSON canônico (chaves ordenadas, UTF-8) do `NormalizedEvent` mapeado (slug da categoria, nome e endereço do local, sessões em UTC).
- Erros do banco passam por `translate_db_errors`; `DbContractViolation` falha só o item, `DbUnavailable` aborta a execução.
- Evento sem sessão futura (início ≥ agora) ou cancelado/privado: ignorado, conta `unchanged`, sem gravar.

**10. Contadores e status.** `RunCounts` e `close_run` da fundação são reaproveitados. `PARTIAL` se `failed > 0` e houve ao menos um item processado; `SUCCESS` se `failed = 0`; `FAILED` se abortada, com o erro em `errors`. Execução abortada ainda fecha o `ingestion_run` num `finally`.

**11. `--dry-run`.** Abre a conexão com `readonly=True`; sem `open_run`; categorias, cidade, venues e deduplicação continuam sendo lidos; imprime um JSON por item (`external_id`, `url`, item normalizado, `category_slug`, `venue` resolvido ou "criaria", ação prevista: `create`/`update`/`unchanged`/`skip`). Qualquer escrita acidental falha no próprio banco.

**12. Erros e saída.** Novos em `errors.py`: `CityNotFound` (6), `CityInactive` (7), `SourceNotFound` (8), `RobotsDisallowed` (9 na listagem), `FetchFailed` (10 na listagem), `ExtractionInvalid` e `VersionConflict` (só por item, sem código de saída próprio). Falha de item não muda o código de saída; ela aparece no log final e em `ingestion_run.errors`. `--source` usa um registro `{"sympla": SymplaSource}`; nome desconhecido é erro de uso (64).

**13. Config.** `REQUEST_TIMEOUT_SECONDS` (float > 0, padrão 30) e `MAX_PAGES_PER_RUN` (int ≥ 1, padrão 50) em `Settings`, com validação no mesmo padrão e linhas no `.env.example`.

**14. Fixtures e testes.** Duas páginas de evento e a listagem são salvas **já renderizadas** (`page.content()`), reduzidas ao necessário (JSON-LD, `__NEXT_DATA__` sem blocos de integração, texto com preços), em `tests/fixtures/sympla/`. Sem rede nos testes: `FakePageFetcher` serve as fixtures e registra as URLs pedidas. Banco com Testcontainers como `agent_writer`, como na fundação. Cenários: criar, repetir sem mudança, mudança de preço, campo travado, `occurrences` travado, lápide, robots bloqueado, extração inválida, dedupe entre fontes, venue por similaridade, cidade inativa/inexistente, `--dry-run` (nenhuma linha e escrita recusada), limite de páginas e pausa entre requisições (relógio injetado).

## Risks / Trade-offs

- **Instabilidade do hash por causa do modelo.** Categoria e campos completados pelo modelo entram no hash; se o modelo variar entre execuções, o evento é regravado (`updated`) sem mudança real. Mitigações: temperatura 0, dados estruturados por código sempre que possível, e a categoria só muda de `outros` para outra se o slug for válido. Se aparecer na prática, o hash passa a usar só os campos da fonte e a categoria é fixada na criação.
- **Custo de reextração.** Item sem lápide é baixado e passa pelo modelo a cada execução para calcular o hash. Aceito agora; uma otimização futura é guardar o hash da página bruta.
- **HTML da Sympla muda.** `__NEXT_DATA__` e JSON-LD são detalhes de implementação. Mitigação: o modelo completa o que faltar e, se ambos falharem, o item vira `EXTRACTION_INVALID`, nunca dado errado. As fixtures documentam o formato esperado.
- **Sessões múltiplas não observadas.** Só foram vistas páginas com uma sessão; datas múltiplas dependem do modelo. A calibração com dados reais fica para depois (limiar e janela do contrato são recomendações).
- **Evento longo (passe de temporada)** começa no passado e é ignorado ("sessão futura" = início ≥ agora). Eventos em andamento deixam de ser publicados; revisar se virar problema.
- **Eventos de teste da Sympla** podem ser publicados. Sem filtro por ora.
- **Playwright é lento e pesado.** Um navegador reaproveitado e `MAX_PAGES_PER_RUN` limitam o impacto; no Windows o `connect` já mostrou lentidões, então todos os timeouts são explícitos.
- **`robots.txt` conservador** (5xx = proibido) pode bloquear execuções por instabilidade passageira; é o lado seguro.
- **Variáveis novas:** `REQUEST_TIMEOUT_SECONDS` e `MAX_PAGES_PER_RUN`, com padrão e no `.env.example`. Sem segredos novos.
