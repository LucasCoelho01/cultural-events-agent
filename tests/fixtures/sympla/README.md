# Fixtures da Sympla

Páginas **já renderizadas** (`page.content()` do Playwright), reduzidas ao que o agente lê: o JSON-LD
`Event`, o `__NEXT_DATA__` (só os campos do evento, sem integrações, políticas e descrição em HTML) e a
seção de ingressos (`data-testid="ticket-grid"`, com o texto como a página mostra). Os testes não usam rede.

Origem: coletadas em 2026-10-30 com `USER_AGENT` identificado, depois de ler o `robots.txt`
(`User-agent: *` / `Allow: /`).

| Arquivo | Origem |
|---|---|
| `listagem_sp.html` | `https://www.sympla.com.br/eventos/sao-paulo-sp` **como o servidor entrega** (sem JavaScript): os eventos vêm num JSON dentro de `<script>`, sem `<a>`. Reconstruída com os 14 primeiros eventos reais (slug e id), mais um repetido; inclui dois eventos de teste (`evento-teste-565`, `blu-mp-juninho-teste-copia`). O DOM renderizado, que tem `<a>`, é coberto por um teste com HTML inline. |
| `evento_agatha.html` | `https://www.sympla.com.br/evento/vamos-falar-de-agatha-christie-luiz-alfredo-garcia-roza/3298539` (uma sessão, ingresso de R$ 100,00 + taxa). |
| `evento_gouvea.html` | `https://www.sympla.com.br/evento/gouvea-pass/3346586` ("Local a definir", quatro ingressos, começa no passado e termina em 2027). |

Variações feitas a partir de `evento_agatha.html` por troca de texto (não são páginas reais):

| Arquivo | O que muda |
|---|---|
| `evento_agatha_preco110.html` | ingresso de R$ 110,00 (+ taxa) |
| `evento_agatha_volatil.html` | só campos ignorados pelo hash (`cardPreviousView`, `buildId`) |
| `evento_gratuito.html` | id 3500001, ingresso "Gratuito" |
| `evento_privado.html` | id 3500002, `privateEvent = true` |
| `evento_passado.html` | id 3500003, datas em 2020 |
| `evento_teste_titulo.html` | id 3500004, título "SHOW TESTE DE SOM" e slug neutro |
| `evento_testemunho.html` | id 3500005, título "Testemunho de fé" (não é evento de teste) |

As datas das fixtures são futuras em relação a 2026-10-30; os testes injetam o "agora" em vez de ler o relógio.
