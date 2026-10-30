# Tasks

## 1. Configuração e erros

- [ ] 1.1 Adicionar `request_timeout_seconds` (float > 0, padrão 30) e `max_pages_per_run` (int ≥ 1, padrão 50) a `Settings`, com `CONFIG_INVALID` citando só o nome da variável; incluir as duas linhas no `.env.example` e na tabela do README
- [ ] 1.2 Em `errors.py`, criar `CityNotFound` (6), `CityInactive` (7), `SourceNotFound` (8), `RobotsDisallowed` (9), `FetchFailed` (10), `ExtractionInvalid` e `VersionConflict` (estes dois só por item)
- [ ] 1.3 Testes: padrões e valores inválidos das novas variáveis; código e exit code de cada novo erro

## 2. LlmClient com saída estruturada

- [ ] 2.1 Adicionar `extract(prompt, schema)` ao protocolo `LlmClient` e a `AnthropicLlm` (`with_structured_output`, temperatura 0; `ValidationError` ou saída vazia → `ExtractionInvalid`; `anthropic.AnthropicError` → `LlmUnavailable` com segredos mascarados)
- [ ] 2.2 Estender `FakeLlm` com `structured` (instância, dicionário ou lista consumida em ordem), validação no esquema pedido e registro dos prompts
- [ ] 2.3 Testes de `llm.py`: resposta válida, resposta inválida, provedor indisponível sem expor a chave, falso com dicionário válido e inválido

## 3. Coleta educada

- [ ] 3.1 Criar `fetching.py` com a interface `PageFetcher` e o ritmo por domínio (relógio injetável): pausa de `REQUEST_DELAY_SECONDS`, `USER_AGENT` em toda requisição e `FetchFailed` em timeout ou HTTP de erro
- [ ] 3.2 Implementar a leitura do `robots.txt` uma vez por domínio (4xx permite, 5xx ou rede proíbe) e a checagem por URL com `RobotsDisallowed`
- [ ] 3.3 Implementar `HttpxPlaywrightFetcher`: listagem por `httpx`, evento por Playwright com um navegador reaproveitado, ambos com `REQUEST_TIMEOUT_SECONDS`, e o contador de `MAX_PAGES_PER_RUN`
- [ ] 3.4 Testes sem rede (servidor `httpx.MockTransport` e relógio falso): robots proíbe, robots 404, robots 503, pausa entre requisições, cabeçalho `User-Agent`, timeout e limite de páginas

## 4. Fixtures da Sympla

- [ ] 4.1 Salvar em `tests/fixtures/sympla/` a listagem e duas páginas de evento já renderizadas (`page.content()`), reduzidas ao JSON-LD, ao `__NEXT_DATA__` sem blocos de integração e ao texto com preços; criar variações pequenas à mão para evento gratuito, local a definir, evento privado e evento sem sessão futura
- [ ] 4.2 Documentar no README de `tests/fixtures/sympla/` a data e a URL de origem de cada fixture

## 5. Interface Source e Sympla

- [ ] 5.1 Criar `sources/base.py` com o protocolo `Source`, `DiscoveredItem` e `StructuredEvent`
- [ ] 5.2 Criar `sources/sympla.py`: `listing_url` (`<slug>-<uf>`, `?page=N`), `parse_listing` (ids, URL canônica sem parâmetros de rastreio, sem duplicados), `structured_data` (`__NEXT_DATA__` e JSON-LD, descrição mais completa, horário sem fuso) e `page_text`
- [ ] 5.3 Testes de `SymplaSource` sobre as fixtures: links da listagem, URL canônica, JSON-LD e `__NEXT_DATA__`, descrição truncada, evento privado e local a definir

## 6. Extração

- [ ] 6.1 Criar `extraction.py` com os modelos pydantic `EventCompletion` e `NormalizedEvent` (invariantes de preço, datas, gratuito, título e sessão) e a montagem do prompt com o texto limitado e os slugs de categoria
- [ ] 6.2 Implementar as camadas: dados estruturados primeiro, chamada ao modelo só para campos faltantes, conversão de fuso com `city.timezone`, preço em centavos e `ExtractionInvalid` por item
- [ ] 6.3 Implementar a regra de ignorar item sem sessão futura, cancelado, não publicado ou privado
- [ ] 6.4 Testes com `FakeLlm`: modelo não chamado quando nada falta, preço em centavos, gratuito, horário sem fuso, resposta fora do esquema, preço incoerente, evento já iniciado e evento privado

## 7. Mapeamento

- [ ] 7.1 Criar `mapping.py`: leitura das categorias do banco, escolha por slug com fallback `outros`, e normalização do endereço (CEP e coordenadas inválidos viram nulos)
- [ ] 7.2 Implementar a busca de local por `similarity()` na cidade (limiar 0,6), nome genérico sem venue e a criação do `venue` quando nada atinge o limiar
- [ ] 7.3 Testes com Testcontainers: slug válido, slug inventado, local existente com grafia diferente, local novo, "Local a definir", venue de outra cidade e CEP inválido

## 8. Gravação conforme o contrato

- [ ] 8.1 Criar `writer.py` com `content_hash` (JSON canônico, SHA-256) e a busca do vínculo com `FOR UPDATE` (lápide, sem vínculo, hash igual, hash diferente), com SQL parametrizado
- [ ] 8.2 Implementar a criação: venue, evento `PUBLISHED`/`AGENT`, sessões e vínculo, numa transação por item, com rollback e `DbContractViolation` por item
- [ ] 8.3 Implementar a deduplicação entre fontes (`similarity` ≥ 0,6, janela de 1 dia): só o vínculo, sem sobrescrever o evento existente
- [ ] 8.4 Implementar a atualização: colunas não travadas, `version = version + 1 … WHERE version`, sessões por `ON CONFLICT` e `DELETE … <> ALL`, `occurrences` travado preserva as sessões, até 3 tentativas e `VersionConflict`
- [ ] 8.5 Testes com Testcontainers como `agent_writer`: criar, repetir sem mudança (`unchanged`), mudança de preço (`updated`, `version` +1), `title` travado, `occurrences` travado, lápide (nada criado), rollback do item inteiro, dois itens independentes, dedupe entre fontes e `23505` em `event_city_slug_key`

## 9. Grafo e execução

- [ ] 9.1 Criar `graph.py` com o `StateGraph` (descobrir → baixar → extrair → mapear → deduplicar → gravar), o laço por item, a falha isolada, a conferência da lápide antes do download e o respeito a `--limit` e `MAX_PAGES_PER_RUN`
- [ ] 9.2 Implementar a validação de cidade (`CITY_NOT_FOUND`, `CITY_INACTIVE`) e de fonte (`SOURCE_NOT_FOUND`), o `ingestion_run` (`SUCCESS`/`PARTIAL`/`FAILED`, fechado num `finally`) e os contadores
- [ ] 9.3 Implementar o `--dry-run`: conexão `readonly=True`, sem `open_run`, nó `report` que imprime um JSON por item
- [ ] 9.4 Testes de fluxo com `Source` falsa, `FakePageFetcher`, `FakeLlm` e Testcontainers: ordem das etapas, item com `EXTRACTION_INVALID` e execução seguindo, `ROBOTS_DISALLOWED` num evento e na listagem, `FETCH_FAILED` na listagem fechando como `FAILED`, cidade inexistente e inativa, `--dry-run` sem nenhuma linha nova e com escrita recusada pelo banco

## 10. CLI e documentação

- [ ] 10.1 Adicionar o comando `run` em `cli.py` (`--source`, `--city uf/slug`, `--limit`, `--dry-run`), o registro de fontes, os novos códigos de saída 6–10 e o log final com os contadores
- [ ] 10.2 Testes da CLI: execução normal com contadores no log, falha parcial com saída 0, fonte desconhecida (64), cidade inexistente (6), cidade inativa (7), listagem bloqueada (9)
- [ ] 10.3 Atualizar o README: comando `run`, `--dry-run`, tabela de códigos de saída 6–10 e as variáveis novas
- [ ] 10.4 Rodar `uv run pytest` e `uv run ruff check` / `ruff format --check` e deixar tudo verde antes de marcar a mudança como pronta
