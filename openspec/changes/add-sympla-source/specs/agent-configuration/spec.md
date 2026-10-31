## ADDED Requirements

### Requirement: Limites de coleta
A configuração MUST aceitar `REQUEST_TIMEOUT_SECONDS` (número maior que 0, padrão 30) e `MAX_PAGES_PER_RUN` (inteiro maior ou igual a 1, padrão 50). Valor inválido MUST encerrar com `CONFIG_INVALID`, citando só o nome da variável. Ambas MUST constar no `.env.example`.

#### Scenario: Padrões
- **WHEN** as variáveis não estão definidas
- **THEN** o timeout é 30 segundos e o limite é 50 páginas

#### Scenario: Limite inválido
- **WHEN** `MAX_PAGES_PER_RUN=0`
- **THEN** a execução encerra com `CONFIG_INVALID` citando `MAX_PAGES_PER_RUN`

### Requirement: Nível de log
A configuração MUST aceitar `LOG_LEVEL` com `DEBUG`, `INFO`, `WARNING` ou `ERROR` (sem diferenciar maiúsculas, padrão `INFO`). Valor inválido MUST encerrar com `CONFIG_INVALID` citando só o nome da variável. A variável MUST constar no `.env.example`.

#### Scenario: Padrão
- **WHEN** `LOG_LEVEL` não está definida
- **THEN** o nível é `INFO`

#### Scenario: Valor inválido
- **WHEN** `LOG_LEVEL=barulhento`
- **THEN** a execução encerra com `CONFIG_INVALID` citando `LOG_LEVEL`
