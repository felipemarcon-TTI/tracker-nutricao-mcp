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
| `registrar_refeicao` | Registra refeicao com estimativa de macros/micros |
| `listar_refeicoes` | Lista refeicoes de um dia |
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
