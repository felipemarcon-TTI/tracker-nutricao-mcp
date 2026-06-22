# Tracker de Nutricao e Treino — Felipe

Servidor MCP TypeScript para acompanhamento alimentar e de treino via claude.ai.

## Stack

- **MCP Server**: TypeScript + `@modelcontextprotocol/sdk`
- **Banco**: PostgreSQL no Railway
- **Deploy**: Railway (auto-deploy a partir do GitHub)
- **Interface**: claude.ai

## Setup Railway

### 1. Criar o projeto
1. Acesse [railway.app](https://railway.app) e crie um novo projeto
2. Em "Deploy from GitHub repo" selecione `felipemarcon-TTI/tracker-nutricao-mcp`
3. Clique em "+ New" > "Database" > "Add PostgreSQL"
4. O Railway cria a variavel `DATABASE_URL` automaticamente

### 2. Variáveis de ambiente
Apenas `DATABASE_URL` e `PORT` sao necessarias — ambas sao definidas automaticamente pelo Railway.

### 3. Inicializar o banco
Apos o primeiro deploy, use a tool `inicializar_banco` no claude.ai para criar as tabelas e popular o catalogo de exercicios.

## Adicionar ao claude.ai

Apos o deploy, copie a URL do servico no Railway (ex: `https://tracker-nutricao-mcp-production.up.railway.app`) e adicione no `mcp.json`:

```json
{
  "mcpServers": {
    "tracker-nutricao": {
      "type": "sse",
      "url": "https://<sua-url-railway>/sse"
    }
  }
}
```

## Tools disponíveis (14)

| Tool | Descricao |
|---|---|
| `inicializar_banco` | Cria tabelas e popula exercicios (setup inicial) |
| `verificar_lembretes` | Verifica lembretes pendentes |
| `registrar_refeicao` | Registra refeicao com macros/micros + estado de coccao (opcional) |
| `atualizar_refeicao` | Corrige campos de uma refeicao por ID (UPDATE com commit) |
| `listar_refeicoes` | Lista refeicoes de um dia (mostra estado de coccao quando informado) |
| `resumo_nutricional` | Totais do dia vs metas da nutricionista |
| `registrar_metricas_corporais` | Registra peso e cintura |
| `registrar_treino` | Registra sessao de treino completa |
| `listar_treinos` | Lista treinos de um periodo |
| `buscar_exercicios` | Busca no catalogo de exercicios |
| `progressao_exercicio` | Historico de carga e deteccao de plateau |
| `gerar_resumo_diario` | Gera daily_summary para uma data |
| `retrospectiva_semanal` | Analise semanal com medias e tendencias |
| `inserir_dados_historicos` | Insere refeicoes de 08/06/2026 (viagem Napoles) |
| `executar_sql` | SQL ad-hoc para analises e correcoes |

## Metas do plano alimentar (Helena Ferretti S. Proenica, CRN 5545N)

| Macro | Meta diaria |
|---|---|
| Calorias | 2253 kcal |
| Proteina | 183.3 g |
| Carboidratos | 231.9 g |
| Gordura | 70.7 g |

Micronutrientes: Calcio 1145mg, Magnesio 218mg, Ferro 7.3mg, Potassio 3199mg,
Vitamina C 39.9mg, Vitamina D 1.9mcg, Vitamina B12 1.8mcg, Zinco 6.6mg.

## Estado de cocção e perda/ganho de água (auditoria)

O peso de um alimento muda conforme o preparo, e isso afeta os macros. O **cálculo
continua sendo feito pelo cliente (Claude)** — o servidor só **armazena** o estado
informado, como metadado de auditoria. Campos opcionais em `registrar_refeicao` /
`atualizar_refeicao` (e persistidos na tabela `meals`):

- `estado_coccao` → `cooking_state`: `cru` | `cozido` | `congelado_glaze` | `assado` | `null`
- `peso_porcao_g` → `portion_weight_g`: peso relatado pelo usuário (g)
- `base_peso` → `portion_basis`: `peso_cru` | `peso_cozido` | `peso_congelado`

### Regra de cálculo (para o cliente/Claude, ANTES de enviar os macros)

- **Proteína** (carne, frango, peixe, camarão) **perde água**: 100g cru → ~70–80g cozido;
  proteína/100g sobe no cozido. Peso **cru** → tabela de cru; peso **cozido** → tabela de cozido.
- **Amido** (arroz, macarrão, batata, leguminosas) **absorve água**: 100g cru → ~250–300g
  cozido (batata incha menos); carbo/100g cai no cozido. Mesma lógica de base de peso.
- **Congelado com glaze** (camarão, peixe): descontar **~10–20% de gelo** antes de calcular.
- Se o usuário não especificar cru/cozido: **perguntar** ou assumir o padrão mais provável
  do alimento e **registrar a suposição** em `estado_coccao`/`base_peso`.

> Persistência: edições de refeição usam `atualizar_refeicao` (UPDATE com commit) e refletem
> em `resumo_diario`/`gerar_resumo_diario`. `executar_sql` também commita writes
> (SELECT/WITH/SHOW/EXPLAIN/TABLE/VALUES = leitura; o resto roda com commit).

## Estrutura do projeto

```
db/
  schema.sql       # 6 tabelas PostgreSQL
  seed.sql         # 70+ exercicios de musculacao
src/
  db.ts            # Conexao com banco
  meals.ts         # Refeicoes e estimativa de macros (~45 alimentos)
  metrics.ts       # Metricas corporais
  workouts.ts      # Treinos e exercicios
  reports.ts       # Resumos e retrospectiva
  reminders.ts     # Lembretes
  server.ts        # Servidor MCP (entry point)
```

<!-- redeploy: 2026-06-09 14:09 -->
