# event-mapping Specification

## Purpose
Traduzir o evento normalizado para o modelo do banco: resolve a categoria pelo slug e o local por similaridade (ou o cria). Garante que o endereço seja compatível com as restrições do banco.

## Requirements

### Requirement: Categoria por slug
O modelo MUST escolher a categoria entre os slugs lidos da tabela `category` no início da execução. Slug que não esteja na lista, ou ausência de escolha, MUST resultar em `outros`. O agente MUST NOT criar categorias.

#### Scenario: Slug válido
- **WHEN** o modelo escolhe `teatro` e `teatro` existe em `category`
- **THEN** o evento usa o `category_id` de `teatro`

#### Scenario: Slug inventado
- **WHEN** o modelo devolve `gastronomia`, que não existe em `category`
- **THEN** o evento usa o `category_id` de `outros`

### Requirement: Local por similaridade
O local MUST ser procurado só na cidade do evento, por `similarity(name, :nome) >= 0.6` (`pg_trgm`), escolhendo o mais parecido. Se nenhum atingir o limiar, o `venue` MUST ser criado na mesma cidade com nome e endereço extraídos. Nome genérico como "Local a definir" MUST resultar em evento sem `venue`.

#### Scenario: Local existente
- **WHEN** a cidade já tem o venue "Fundação Maria Luisa e Oscar Americano" e o item traz "Fundacao Maria Luisa e Oscar Americano"
- **THEN** o evento usa o `venue_id` existente e nenhum venue é criado

#### Scenario: Local novo
- **WHEN** nenhum venue da cidade atinge o limiar
- **THEN** um venue é criado na cidade do evento e usado no evento

#### Scenario: Local a definir
- **WHEN** o nome do local é "Local a definir"
- **THEN** o evento é gravado com `venue_id` nulo e nenhum venue é criado

#### Scenario: Venue de outra cidade
- **WHEN** existe um venue de nome idêntico em outra cidade
- **THEN** ele não é reutilizado

### Requirement: Endereço compatível com o banco
Os campos do `venue` MUST respeitar as constraints do banco: `postal_code` no formato `NNNNN-NNN` (senão nulo) e latitude e longitude nos intervalos válidos (senão nulas). Valor inválido MUST NOT causar falha do item.

#### Scenario: CEP inválido
- **WHEN** o CEP vem vazio ou fora do formato
- **THEN** o venue é criado com `postal_code` nulo
