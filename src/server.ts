import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import express, { Request, Response, NextFunction } from "express";
import * as path from "path";
import { z } from "zod";

import { query, runSqlFile } from "./db";
import { insertMeal, listMeals, getDailyTotals, estimateMacros, PLANO_METAS } from "./meals";
import { insertMetric, listMetrics } from "./metrics";
import { insertWorkout, insertWorkoutSet, upsertExercise, listWorkouts, progressionAnalysis, searchExercises } from "./workouts";
import { generateDailySummary, weeklyRetrospective } from "./reports";
import { checkAllReminders } from "./reminders";

// ──────────────────────────────────────────────────────────────
// CONFIG OAuth
// ──────────────────────────────────────────────────────────────
const CLIENT_ID = process.env.MCP_CLIENT_ID || "tracker-nutricao";
const CLIENT_SECRET = process.env.MCP_CLIENT_SECRET || "changeme";
const ACCESS_TOKEN = process.env.MCP_ACCESS_TOKEN || "tracker-access-token";

// codigos de autorizacao temporarios: code -> true
const authCodes = new Map<string, string>();

const server = new McpServer({ name: "tracker-nutricao-mcp", version: "1.0.0" });

// ──────────────────────────────────────────────────────────────
// TOOLS MCP
// ──────────────────────────────────────────────────────────────

server.tool("inicializar_banco", "Cria todas as tabelas e popula o catalogo de exercicios. Idempotente.", {}, async () => {
  const dbDir = path.join(__dirname, "..", "db");
  await runSqlFile(path.join(dbDir, "schema.sql"));
  await runSqlFile(path.join(dbDir, "seed.sql"));
  const countRes = await query("SELECT COUNT(*) AS n FROM exercises");
  const tableRes = await query(`SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name`);
  const tables = tableRes.rows.map((r: any) => r.table_name).join(", ");
  return { content: [{ type: "text", text: `Banco inicializado!\nTabelas: ${tables}\nExercicios: ${countRes.rows[0].n}` }] };
});

server.tool("verificar_lembretes", "Verifica lembretes pendentes (ex: mais de 7 dias sem registrar peso).", {}, async () => {
  const reminders = await checkAllReminders();
  if (reminders.length === 0) return { content: [{ type: "text", text: "Nenhum lembrete pendente." }] };
  return { content: [{ type: "text", text: reminders.map(r => `[${r.priority.toUpperCase()}] ${r.message}`).join("\n") }] };
});

server.tool("registrar_refeicao", "Registra refeicao com estimativa de macros/micros e feedback vs plano da Helena.", {
  descricao: z.string().describe("O que foi comido"),
  tipo: z.enum(["pre_treino","cafe_manha","almoco","lanche","jantar","ceia","outro"]).optional(),
  horario: z.string().optional().describe("ex: '08:30' ou '2026-06-09T08:30:00'"),
  seguiu_plano: z.boolean().optional(),
  notas: z.string().optional().describe("ex: 'viagem', 'restaurante'"),
}, async ({ descricao, tipo, horario, seguiu_plano, notas }) => {
  const now = new Date();
  let mealTime: string | undefined;
  if (horario) {
    mealTime = horario.includes("T") || horario.includes("-") ? horario : `${now.toISOString().split("T")[0]}T${horario}:00`;
  }
  const macros = estimateMacros(descricao);
  const id = await insertMeal({ meal_time: mealTime, meal_type: tipo, description: descricao, is_on_plan: seguiu_plano, notes: notas, macros: macros.calories !== null ? macros : undefined });
  const lines = [`Refeicao registrada (ID ${id})`];
  if (macros.calories !== null) lines.push(`Estimativa: ${macros.calories?.toFixed(0)} kcal | Prot: ${macros.protein_g?.toFixed(1)}g | Carbs: ${macros.carbs_g?.toFixed(1)}g | Gord: ${macros.fat_g?.toFixed(1)}g`);
  else lines.push("Macros nao estimados — alimento nao reconhecido.");
  return { content: [{ type: "text", text: lines.join("\n") }] };
});

server.tool("listar_refeicoes", "Lista refeicoes de um dia.", { data: z.string().optional().describe("YYYY-MM-DD. Padrao: hoje em Lisboa") }, async ({ data }) => {
  const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
  const meals = await listMeals(dateStr);
  if (meals.length === 0) return { content: [{ type: "text", text: `Nenhuma refeicao em ${dateStr}.` }] };
  const lines = meals.map((m: any) => {
    const hora = m.meal_time_local ? new Date(m.meal_time_local).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" }) : "--:--";
    const plan = m.is_on_plan === true ? "sim" : m.is_on_plan === false ? "nao" : "—";
    const kcal = m.calories ? `${parseFloat(m.calories).toFixed(0)} kcal` : "sem est.";
    return `${hora} [${m.meal_type || "—"}] ${m.description} | ${kcal} | plano: ${plan}`;
  });
  return { content: [{ type: "text", text: `Refeicoes em ${dateStr}:\n${lines.join("\n")}` }] };
});

server.tool("resumo_nutricional", "Totais do dia vs metas do plano.", { data: z.string().optional() }, async ({ data }) => {
  const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
  const totals = await getDailyTotals(dateStr);
  const m = PLANO_METAS;
  const pct = (v: number, t: number) => `${Math.round((v / t) * 100)}%`;
  const text = [
    `Resumo nutricional — ${dateStr}`,
    `Calorias: ${(totals.calories as number).toFixed(0)} / ${m.calories} kcal (${pct(totals.calories as number, m.calories)})`,
    `Proteina: ${(totals.protein_g as number).toFixed(1)} / ${m.protein_g} g (${pct(totals.protein_g as number, m.protein_g)})`,
    `Carbs:    ${(totals.carbs_g as number).toFixed(1)} / ${m.carbs_g} g (${pct(totals.carbs_g as number, m.carbs_g)})`,
    `Gordura:  ${(totals.fat_g as number).toFixed(1)} / ${m.fat_g} g (${pct(totals.fat_g as number, m.fat_g)})`,
    `Calcio: ${(totals.calcium_mg as number).toFixed(0)}/${m.calcium_mg}mg | Mg: ${(totals.magnesium_mg as number).toFixed(0)}/${m.magnesium_mg}mg | Fe: ${(totals.iron_mg as number).toFixed(1)}/${m.iron_mg}mg`,
    `Pot: ${(totals.potassium_mg as number).toFixed(0)}/${m.potassium_mg}mg | VitC: ${(totals.vitamin_c_mg as number).toFixed(1)}/${m.vitamin_c_mg}mg | VitD: ${(totals.vitamin_d_mcg as number).toFixed(1)}/${m.vitamin_d_mcg}mcg`,
    `Refeicoes: ${totals.meals_total} (${totals.meals_on_plan} no plano)`,
  ].join("\n");
  return { content: [{ type: "text", text }] };
});

server.tool("registrar_metricas_corporais", "Registra peso (kg) e/ou cintura (cm).", {
  peso_kg: z.number().optional(),
  cintura_cm: z.number().optional(),
  data: z.string().optional(),
  notas: z.string().optional(),
}, async ({ peso_kg, cintura_cm, data, notas }) => {
  const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
  const id = await insertMetric({ measurement_date: dateStr, weight_kg: peso_kg, waist_cm: cintura_cm, notes: notas });
  const parts = [peso_kg ? `Peso: ${peso_kg}kg` : null, cintura_cm ? `Cintura: ${cintura_cm}cm` : null].filter(Boolean);
  return { content: [{ type: "text", text: `Metricas registradas (ID ${id}) em ${dateStr}: ${parts.join(" | ")}` }] };
});

server.tool("registrar_treino", "Registra sessao de treino com exercicios, series, reps e carga.", {
  data: z.string().optional(),
  tipo: z.string().optional(),
  local: z.string().optional(),
  exercicios: z.array(z.object({
    nome: z.string(),
    series: z.array(z.object({ reps: z.number().optional(), carga_kg: z.number().optional(), rpe: z.number().optional(), notas: z.string().optional() })),
    alternativa_de: z.string().optional(),
    grupo_muscular: z.string().optional(),
    equipamento: z.string().optional(),
  })),
  notas: z.string().optional(),
  pulado: z.boolean().optional(),
  motivo_pulo: z.string().optional(),
}, async ({ data, tipo, local, exercicios, notas, pulado, motivo_pulo }) => {
  const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
  const workoutId = await insertWorkout({ workout_date: dateStr, workout_type: tipo, location: local, notes: notas, skipped: pulado, skip_reason: motivo_pulo });
  if (pulado) return { content: [{ type: "text", text: `Treino registrado como pulado (ID ${workoutId}). Motivo: ${motivo_pulo || "nao informado"}` }] };
  let totalSets = 0;
  for (const ex of exercicios) {
    const exerciseId = await upsertExercise(ex.nome, ex.grupo_muscular, ex.equipamento);
    for (let i = 0; i < ex.series.length; i++) {
      const s = ex.series[i];
      await insertWorkoutSet({ workout_id: workoutId, exercise_id: exerciseId, exercise_name: ex.nome, set_number: i + 1, reps: s.reps, weight_kg: s.carga_kg, rpe: s.rpe, notes: s.notas, is_alternative: !!ex.alternativa_de, alternative_for: ex.alternativa_de });
      totalSets++;
    }
  }
  return { content: [{ type: "text", text: `Treino registrado (ID ${workoutId}) em ${dateStr}\n${exercicios.length} exercicios | ${totalSets} series` }] };
});

server.tool("listar_treinos", "Lista treinos de um periodo.", { data_inicio: z.string().optional(), data_fim: z.string().optional() }, async ({ data_inicio, data_fim }) => {
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
  const start = data_inicio || new Date(Date.now() - 30 * 24 * 3600 * 1000).toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
  const workouts = await listWorkouts(start, data_fim || today);
  if (workouts.length === 0) return { content: [{ type: "text", text: "Nenhum treino no periodo." }] };
  const lines = workouts.map((w: any) => `${w.workout_date} [${w.workout_type || "—"}] ${w.skipped ? "Pulado" : `${w.total_sets} series`} | Volume: ${parseFloat(w.total_volume_kg || 0).toFixed(0)}kg`);
  return { content: [{ type: "text", text: lines.join("\n") }] };
});

server.tool("buscar_exercicios", "Busca no catalogo por nome, grupo muscular ou equipamento.", { termo: z.string() }, async ({ termo }) => {
  const exercises = await searchExercises(termo);
  if (exercises.length === 0) return { content: [{ type: "text", text: `Nenhum exercicio encontrado para "${termo}".` }] };
  const lines = exercises.map((e: any) => `[${e.muscle_group}] ${e.name} | ${e.equipment || "—"} | ${e.difficulty || "—"}`);
  return { content: [{ type: "text", text: lines.join("\n") }] };
});

server.tool("progressao_exercicio", "Historico de carga semanal e deteccao de plateau.", { nome_exercicio: z.string() }, async ({ nome_exercicio }) => {
  const result = await progressionAnalysis(nome_exercicio);
  return { content: [{ type: "text", text: result }] };
});

server.tool("gerar_resumo_diario", "Gera e salva o daily_summary com totais e aderencia.", { data: z.string().optional(), treinou: z.boolean().optional(), notas_treino: z.string().optional() }, async ({ data, treinou, notas_treino }) => {
  const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
  const text = await generateDailySummary(dateStr, treinou, notas_treino);
  return { content: [{ type: "text", text }] };
});

server.tool("retrospectiva_semanal", "Analise semanal com medias, tendencias e micros em falta.", { data_domingo: z.string().optional() }, async ({ data_domingo }) => {
  let sundayDate = data_domingo;
  if (!sundayDate) {
    const d = new Date();
    const day = d.getDay();
    d.setDate(d.getDate() - day);
    sundayDate = d.toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
  }
  const text = await weeklyRetrospective(sundayDate);
  return { content: [{ type: "text", text }] };
});

server.tool("inserir_dados_historicos", "Insere as 3 refeicoes do dia 08/06/2026 (viagem Napoles).", {}, async () => {
  const ids: number[] = [];
  ids.push(await insertMeal({ meal_time: "2026-06-08T06:00:00Z", meal_type: "cafe_manha", description: "2 ovos, 1 fatia de pao, bacon", is_on_plan: false, notes: "viagem em Napoles, Italia", macros: { calories: 453, protein_g: 27.5, carbs_g: 14.5, fat_g: 28.3, fiber_g: 0.8, calcium_mg: 54, iron_mg: 1.7, magnesium_mg: 16, potassium_mg: 258, sodium_mg: 592, vitamin_c_mg: 0, vitamin_d_mcg: 2.3, vitamin_b12_mcg: 1.4, zinc_mg: 1.4 } }));
  ids.push(await insertMeal({ meal_time: "2026-06-08T11:00:00Z", meal_type: "almoco", description: "pizza de queijo individual napolitana", is_on_plan: false, notes: "viagem em Napoles, Italia", macros: { calories: 798, protein_g: 30, carbs_g: 100, fat_g: 28, fiber_g: 5, calcium_mg: 450, iron_mg: 4.5, magnesium_mg: 54, potassium_mg: 516, sodium_mg: 1800, vitamin_c_mg: 6, vitamin_d_mcg: 0.3, vitamin_b12_mcg: 0.9, zinc_mg: 3.6 } }));
  ids.push(await insertMeal({ meal_time: "2026-06-08T13:00:00Z", meal_type: "lanche", description: "sorvete de limao (gelato)", is_on_plan: false, notes: "viagem em Napoles, Italia", macros: { calories: 200, protein_g: 3.5, carbs_g: 35, fat_g: 5, fiber_g: 0, calcium_mg: 100, iron_mg: 0.1, magnesium_mg: 10, potassium_mg: 150, sodium_mg: 50, vitamin_c_mg: 2, vitamin_d_mcg: 0.2, vitamin_b12_mcg: 0.1, zinc_mg: 0.3 } }));
  return { content: [{ type: "text", text: `Dados historicos inseridos (IDs: ${ids.join(", ")})\n08/06/2026 — viagem Napoles\nTotal: 1451 kcal | Prot: 61g | Carbs: 149.5g | Gord: 61.3g` }] };
});

server.tool("executar_sql", "Executa SQL ad-hoc no banco.", { sql: z.string() }, async ({ sql }) => {
  try {
    const res = await query(sql);
    const text = res.rows.length > 0 ? JSON.stringify(res.rows, null, 2) : `${res.rowCount} linha(s) afetadas.`;
    return { content: [{ type: "text", text }] };
  } catch (err: any) {
    return { content: [{ type: "text", text: `Erro SQL: ${err.message}` }] };
  }
});

// ──────────────────────────────────────────────────────────────
// EXPRESS + OAUTH + SSE
// ──────────────────────────────────────────────────────────────

const app = express();
app.set("trust proxy", 1);
const PORT = parseInt(process.env.PORT || "8000", 10);
const transports = new Map<string, SSEServerTransport>();

// Bearer token middleware para /sse e /messages
function requireAuth(req: Request, res: Response, next: NextFunction): void {
  const auth = req.headers.authorization || "";
  if (auth === `Bearer ${ACCESS_TOKEN}`) { next(); return; }
  res.status(401).json({ error: "unauthorized" });
}

// OAuth: metadados do servidor
app.get("/.well-known/oauth-authorization-server", (req: Request, res: Response) => {
  const proto = (req.headers["x-forwarded-proto"] as string) || req.protocol;
  const host = process.env.RAILWAY_PUBLIC_DOMAIN || req.get("host") || "localhost";
  const base = proto + "://" + host;
  res.json({
    issuer: base,
    authorization_endpoint: `${base}/authorize`,
    token_endpoint: `${base}/token`,
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code", "client_credentials"],
    code_challenge_methods_supported: ["S256", "plain"],
  });
});

// OAuth: authorization endpoint — auto-aprova e redireciona com code
app.get("/authorize", (req: Request, res: Response) => {
  const { redirect_uri, state, client_id } = req.query as Record<string, string>;
  if (client_id !== CLIENT_ID) { res.status(400).send("invalid client_id"); return; }
  const code = `code-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  authCodes.set(code, redirect_uri);
  const url = new URL(redirect_uri);
  url.searchParams.set("code", code);
  if (state) url.searchParams.set("state", state);
  res.redirect(url.toString());
});

// OAuth: token endpoint — troca code ou client_credentials por access_token
app.post("/token", express.urlencoded({ extended: true }), express.json(), (req: Request, res: Response) => {
  const { client_id, client_secret, grant_type } = req.body;
  if (client_id !== CLIENT_ID || client_secret !== CLIENT_SECRET) {
    res.status(401).json({ error: "invalid_client" }); return;
  }
  if (grant_type === "authorization_code" || grant_type === "client_credentials") {
    res.json({ access_token: ACCESS_TOKEN, token_type: "Bearer", expires_in: 31536000 });
  } else {
    res.status(400).json({ error: "unsupported_grant_type" });
  }
});

// SSE — requer Bearer token
app.get("/sse", requireAuth, async (req: Request, res: Response) => {
  const transport = new SSEServerTransport("/messages", res);
  transports.set(transport.sessionId, transport);
  res.on("close", () => transports.delete(transport.sessionId));
  await server.connect(transport);
});

app.post("/messages", express.json(), requireAuth, async (req: Request, res: Response) => {
  const sessionId = req.query.sessionId as string;
  const transport = transports.get(sessionId);
  if (!transport) { res.status(404).send("Session not found"); return; }
  await transport.handlePostMessage(req, res);
});

app.get("/", (_req: Request, res: Response) => {
  res.json({ status: "ok", service: "tracker-nutricao-mcp", version: "1.0.0" });
});

app.listen(PORT, () => {
  console.log(`Tracker Nutricao MCP na porta ${PORT}`);
  console.log(`CLIENT_ID: ${CLIENT_ID}`);
});
