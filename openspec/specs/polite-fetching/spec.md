# polite-fetching Specification

## Purpose
Baixar páginas das fontes de forma respeitosa e resiliente: obedece ao robots.txt, se identifica, espaça as requisições e limita timeout e número de páginas. Trata respostas 429 e falhas do navegador sem depender de rede nos testes.

## Requirements

### Requirement: Respeitar o robots.txt
Antes de baixar qualquer URL, a coleta MUST ler o `robots.txt` do domínio uma vez por execução e conferir a regra do `USER_AGENT` configurado. URL proibida MUST NOT ser baixada e MUST gerar `ROBOTS_DISALLOWED`. Se o `robots.txt` responder 4xx, tudo é permitido; se estiver inacessível ou responder 5xx, tudo é proibido.

#### Scenario: Página de evento proibida
- **WHEN** o `robots.txt` proíbe a URL de um evento
- **THEN** a URL não é requisitada, o item falha com `ROBOTS_DISALLOWED` e a execução segue

#### Scenario: Listagem proibida
- **WHEN** o `robots.txt` proíbe a listagem da cidade
- **THEN** a execução encerra com `ROBOTS_DISALLOWED` sem baixar nenhum evento

#### Scenario: robots.txt inacessível
- **WHEN** o `robots.txt` responde 503
- **THEN** nenhuma página é baixada e a execução encerra com `ROBOTS_DISALLOWED`

### Requirement: Identificação e ritmo
Toda requisição MUST enviar o `USER_AGENT` da configuração. Entre duas requisições ao mesmo domínio MUST haver ao menos `REQUEST_DELAY_SECONDS` de intervalo, incluindo o `robots.txt`.

#### Scenario: Pausa entre requisições
- **WHEN** duas páginas do mesmo domínio são baixadas em sequência com `REQUEST_DELAY_SECONDS = 2`
- **THEN** o intervalo entre as duas requisições é de ao menos 2 segundos

#### Scenario: Cabeçalho de identificação
- **WHEN** qualquer requisição é enviada
- **THEN** o cabeçalho `User-Agent` é o valor de `USER_AGENT`

### Requirement: Timeout e limite de páginas
Cada requisição MUST respeitar `REQUEST_TIMEOUT_SECONDS`; estouro ou resposta HTTP de erro MUST gerar `FETCH_FAILED`. Uma execução MUST baixar no máximo `MAX_PAGES_PER_RUN` páginas (listagens e eventos, sem contar o `robots.txt`) e parar ao atingir o limite.

#### Scenario: Timeout num evento
- **WHEN** a página de um evento excede o timeout
- **THEN** o item falha com `FETCH_FAILED` e a execução segue

#### Scenario: Limite de páginas atingido
- **WHEN** `MAX_PAGES_PER_RUN = 5` e a listagem tem 50 eventos
- **THEN** no máximo 5 páginas são baixadas e os itens restantes não são processados

### Requirement: Testes sem rede
O acesso à rede MUST ficar atrás de uma interface de busca de páginas, de modo que os testes usem páginas salvas e nenhum teste faça requisição real.

#### Scenario: Busca falsa
- **WHEN** um teste injeta uma busca que serve fixtures HTML
- **THEN** o fluxo roda sem abrir socket

### Requirement: Resposta 429 do servidor
Resposta HTTP 429 MUST gerar `RATE_LIMITED` sem esperar o fim do carregamento da página, e o domínio MUST esperar antes do próximo pedido: o `Retry-After` (máximo 120 s) ou, sem ele, 30 s dobrando a cada 429 seguido. Três páginas seguidas com `RATE_LIMITED` MUST interromper a execução com `RATE_LIMITED`, fechando o `ingestion_run` como `FAILED`; o restante fica pendente.

#### Scenario: Retry-After
- **WHEN** o servidor responde 429 com `Retry-After: 45`
- **THEN** o item falha com `RATE_LIMITED` e o próximo pedido ao domínio espera 45 s

#### Scenario: Sem Retry-After
- **WHEN** o servidor responde 429 três vezes seguidas sem `Retry-After`
- **THEN** as esperas são 30 s, 60 s e 120 s, e a execução é interrompida com `RATE_LIMITED`

#### Scenario: Página que nunca fica ociosa
- **WHEN** a página de 429 (ou com rastreadores) nunca atinge `networkidle`
- **THEN** o 429 é detectado em segundos, sem consumir o timeout

#### Scenario: Sucesso zera a sequência
- **WHEN** uma página é baixada com sucesso entre dois 429
- **THEN** a contagem de 429 seguidos recomeça do primeiro

### Requirement: Navegador resiliente
Erro inesperado do navegador (ex.: `TargetClosedError`) MUST descartar o navegador, e o próximo evento MUST abrir um novo, sem contaminar as páginas seguintes.

#### Scenario: Navegador fechado
- **WHEN** o navegador cai durante uma página
- **THEN** o item falha com `FETCH_FAILED` e o evento seguinte é baixado num navegador novo
