import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import express, { Request, Response } from "express";
import * as path from "path";
import { z } from "zod";

import { query, runSqlFile } from "./db";
import { insertMeal, listMeals, getDailyTotals, estimateMacros, PLANO_METAS } from "./meals";
import { insertMetric, listMetrics } from "./metrics";
import { insertWorkout, insertWorkoutSet, upsertExercise, listWorkouts, progressionAnalysis, searchExercises } from "./workouts";
import { generateDailySummary, weeklyRetrospective } from "./reports";
import { checkAllReminders } from "./reminders";

const server = new McpServer({
  name: "tracker-nutricao-mcp",
  version: "1.0.0",
});

// ──────────────────────────────────────────────────────────────
// 1. INICIALIZAR BANCO
// ──────────────────────────────────────────────────────────────
server.tool(
  "inicializar_banco",
  "Cria todas as tabelas (schema) e popula o catalogo de exercicios. Seguro rodar multiplas vezes (idempotente).",
  {},
  async () => {
    const dbDir = path.join(__dirname, "..", "db");
    await runSqlFile(path.join(dbDir, "schema.sql"));
    await runSqlFile(path.join(dbDir, "seed.sql"));
    const countRes = await query("SELECT COUNT(*) AS n FROM exercises");
    const tableRes = await query(
      `SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name`
    );
    const tables = tableRes.rows.map((r: any) => r.table_name).join(", ");
    return { content: [{ type: "text", text: `Banco inicializado com sucesso!\nTabelas: ${tables}\nExercicios no catalogo: ${countRes.rows[0].n}` }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 2. VERIFICAR LEMBRETES
// ──────────────────────────────────────────────────────────────
server.tool(
  "verificar_lembretes",
  "Verifica lembretes pendentes — principalmente se ha mais de 7 dias sem registrar peso ou cintura.",
  {},
  async () => {
    const reminders = await checkAllReminders();
    if (reminders.length === 0) {
      return { content: [{ type: "text", text: "Nenhum lembrete pendente. Tudo em dia!" }] };
    }
    const text = reminders.map(r => `[${r.priority.toUpperCase()}] ${r.message}`).join("\n");
    return { content: [{ type: "text", text } ] };
  }
);

// ──────────────────────────────────────────────────────────────
// 3. REGISTRAR REFEICAO
// ──────────────────────────────────────────────────────────────
server.tool(
  "registrar_refeicao",
  "Registra uma refeicao com estimativa automatica de macros/micros e feedback vs plano da nutricionista Helena.",
  {
    descricao: z.string().describe("O que foi comido, em linguagem natural"),
    tipo: z.enum(["pre_treino", "cafe_manha", "almoco", "lanche", "jantar", "ceia", "outro"]).optional().describe("Tipo da refeicao"),
    horario: z.string().optional().describe("Horario da refeicao, ex: '08:30' ou '2026-06-09T08:30:00'"),
    seguiu_plano: z.boolean().optional().describe("Se seguiu o plano da nutricionista"),
    notas: z.string().optional().describe("Observacoes livres, ex: 'viagem', 'restaurante', 'dia atipico'"),
  },
  async ({ descricao, tipo, horario, seguiu_plano, notas }) => {
    const now = new Date();
    let mealTime: string | undefined;

    if (horario) {
      if (horario.includes("T") || horario.includes("-")) {
        mealTime = horario;
      } else {
        const today = now.toISOString().split("T")[0];
        mealTime = `${today}T${horario}:00`;
      }
    }

    const macros = estimateMacros(descricao);
    const id = await insertMeal({
      meal_time: mealTime,
      meal_type: tipo,
      description: descricao,
      is_on_plan: seguiu_plano,
      notes: notas,
      macros: macros.calories !== null ? macros : undefined,
    });

    // Monta feedback rapido
    const lines = [`✅ Refeicao registrada (ID ${id})`];
    if (macros.calories !== null) {
      lines.push(`Estimativa: ${macros.calories?.toFixed(0)} kcal | Prot: ${macros.protein_g?.toFixed(1)}g | Carbs: ${macros.carbs_g?.toFixed(1)}g | Gord: ${macros.fat_g?.toFixed(1)}g`);
    } else {
      lines.push("Nao consegui estimar os macros — os campos ficaram em branco. Pode corrigir manualmente se quiser.");
    }

    if (seguiu_plano === false && notas) {
      lines.push(`📝 Dia atipico anotado: ${notas}`);
    }

    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 4. LISTAR REFEICOES
// ──────────────────────────────────────────────────────────────
server.tool(
  "listar_refeicoes",
  "Lista as refeicoes de um dia especifico (padrao: hoje em horario de Lisboa).",
  {
    data: z.string().optional().describe("Data no formato YYYY-MM-DD. Padrao: hoje em horario de Lisboa"),
  },
  async ({ data }) => {
    const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
    const meals = await listMeals(dateStr);
    if (meals.length === 0) {
      return { content: [{ type: "text", text: `Nenhuma refeicao registrada em ${dateStr}.` }] };
    }
    const lines = meals.map((m: any) => {
      const hora = m.meal_time_local ? new Date(m.meal_time_local).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" }) : "--:--";
      const plan = m.is_on_plan === true ? "✅" : m.is_on_plan === false ? "❌" : "—";
      const kcal = m.calories ? `${parseFloat(m.calories).toFixed(0)} kcal` : "sem est.";
      return `${hora} [${m.meal_type || "—"}] ${m.description} | ${kcal} | Plano: ${plan}`;
    });
    return { content: [{ type: "text", text: `Refeicoes em ${dateStr}:\n${lines.join("\n")}` }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 5. RESUMO NUTRICIONAL
// ──────────────────────────────────────────────────────────────
server.tool(
  "resumo_nutricional",
  "Mostra totais de macros e micros do dia vs metas do plano da Helena.",
  {
    data: z.string().optional().describe("Data YYYY-MM-DD. Padrao: hoje"),
  },
  async ({ data }) => {
    const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
    const totals = await getDailyTotals(dateStr);
    const m = PLANO_METAS;
    const pct = (v: number, t: number) => `${Math.round((v / t) * 100)}%`;

    const text = [
      `📊 Resumo nutricional — ${dateStr}`,
      ``,
      `Macros:`,
      `  Calorias: ${(totals.calories as number).toFixed(0)} / ${m.calories} kcal (${pct(totals.calories as number, m.calories)})`,
      `  Proteina: ${(totals.protein_g as number).toFixed(1)} / ${m.protein_g} g (${pct(totals.protein_g as number, m.protein_g)})`,
      `  Carbs:    ${(totals.carbs_g as number).toFixed(1)} / ${m.carbs_g} g (${pct(totals.carbs_g as number, m.carbs_g)})`,
      `  Gordura:  ${(totals.fat_g as number).toFixed(1)} / ${m.fat_g} g (${pct(totals.fat_g as number, m.fat_g)})`,
      ``,
      `Micros:`,
      `  Calcio:     ${(totals.calcium_mg as number).toFixed(0)} / ${m.calcium_mg} mg`,
      `  Magnesio:   ${(totals.magnesium_mg as number).toFixed(0)} / ${m.magnesium_mg} mg`,
      `  Ferro:      ${(totals.iron_mg as number).toFixed(1)} / ${m.iron_mg} mg`,
      `  Potassio:   ${(totals.potassium_mg as number).toFixed(0)} / ${m.potassium_mg} mg`,
      `  Vitamina C: ${(totals.vitamin_c_mg as number).toFixed(1)} / ${m.vitamin_c_mg} mg`,
      `  Vitamina D: ${(totals.vitamin_d_mcg as number).toFixed(1)} / ${m.vitamin_d_mcg} mcg`,
      `  Vitamina B12: ${(totals.vitamin_b12_mcg as number).toFixed(1)} / ${m.vitamin_b12_mcg} mcg`,
      `  Zinco:      ${(totals.zinc_mg as number).toFixed(1)} / ${m.zinc_mg} mg`,
      ``,
      `Refeicoes registradas: ${totals.meals_total} (${totals.meals_on_plan} no plano)`,
    ].join("\n");

    return { content: [{ type: "text", text }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 6. REGISTRAR METRICAS CORPORAIS
// ──────────────────────────────────────────────────────────────
server.tool(
  "registrar_metricas_corporais",
  "Registra peso (kg) e/ou circunferencia abdominal (cm).",
  {
    peso_kg: z.number().optional().describe("Peso em quilogramas, ex: 82.5"),
    cintura_cm: z.number().optional().describe("Circunferencia abdominal em cm, ex: 88"),
    data: z.string().optional().describe("Data da medicao YYYY-MM-DD. Padrao: hoje"),
    notas: z.string().optional().describe("Observacoes, ex: 'apos treino', 'em jejum'"),
  },
  async ({ peso_kg, cintura_cm, data, notas }) => {
    const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
    const id = await insertMetric({ measurement_date: dateStr, weight_kg: peso_kg, waist_cm: cintura_cm, notes: notas });
    const parts = [];
    if (peso_kg) parts.push(`Peso: ${peso_kg} kg`);
    if (cintura_cm) parts.push(`Cintura: ${cintura_cm} cm`);
    return { content: [{ type: "text", text: `✅ Metricas registradas (ID ${id}) em ${dateStr}: ${parts.join(" | ")}` }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 7. REGISTRAR TREINO
// ──────────────────────────────────────────────────────────────
server.tool(
  "registrar_treino",
  "Registra uma sessao de treino com exercicios, series, reps e carga. Adiciona automaticamente exercicios novos ao catalogo.",
  {
    data: z.string().optional().describe("Data do treino YYYY-MM-DD. Padrao: hoje"),
    tipo: z.string().optional().describe("Tipo do treino, ex: 'musculacao', 'cardio', 'funcional'"),
    local: z.string().optional().describe("Local, ex: 'academia', 'casa', 'parque'"),
    exercicios: z.array(z.object({
      nome: z.string().describe("Nome do exercicio"),
      series: z.array(z.object({
        reps: z.number().optional(),
        carga_kg: z.number().optional().describe("Carga em kg, 0 para peso corporal"),
        rpe: z.number().optional().describe("RPE 1-10"),
        notas: z.string().optional(),
      })),
      alternativa_de: z.string().optional().describe("Se substituiu outro exercicio, qual"),
      grupo_muscular: z.string().optional(),
      equipamento: z.string().optional(),
    })).describe("Lista de exercicios realizados"),
    notas: z.string().optional(),
    pulado: z.boolean().optional(),
    motivo_pulo: z.string().optional(),
  },
  async ({ data, tipo, local, exercicios, notas, pulado, motivo_pulo }) => {
    const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
    const workoutId = await insertWorkout({
      workout_date: dateStr,
      workout_type: tipo,
      location: local,
      notes: notas,
      skipped: pulado,
      skip_reason: motivo_pulo,
    });

    if (pulado) {
      return { content: [{ type: "text", text: `Treino registrado como pulado (ID ${workoutId}). Motivo: ${motivo_pulo || "nao informado"}` }] };
    }

    let totalSets = 0;
    for (const ex of exercicios) {
      const exerciseId = await upsertExercise(ex.nome, ex.grupo_muscular, ex.equipamento);
      for (let i = 0; i < ex.series.length; i++) {
        const s = ex.series[i];
        await insertWorkoutSet({
          workout_id: workoutId,
          exercise_id: exerciseId,
          exercise_name: ex.nome,
          set_number: i + 1,
          reps: s.reps,
          weight_kg: s.carga_kg,
          rpe: s.rpe,
          notes: s.notas,
          is_alternative: !!ex.alternativa_de,
          alternative_for: ex.alternativa_de,
        });
        totalSets++;
      }
    }

    return {
      content: [{
        type: "text",
        text: `✅ Treino registrado (ID ${workoutId}) em ${dateStr}\n${exercicios.length} exercicios | ${totalSets} series no total`,
      }],
    };
  }
);

// ──────────────────────────────────────────────────────────────
// 8. LISTAR TREINOS
// ──────────────────────────────────────────────────────────────
server.tool(
  "listar_treinos",
  "Lista treinos de um periodo.",
  {
    data_inicio: z.string().optional().describe("Data inicio YYYY-MM-DD. Padrao: 30 dias atras"),
    data_fim: z.string().optional().describe("Data fim YYYY-MM-DD. Padrao: hoje"),
  },
  async ({ data_inicio, data_fim }) => {
    const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
    const start = data_inicio || new Date(Date.now() - 30 * 24 * 3600 * 1000).toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
    const workouts = await listWorkouts(start, data_fim || today);
    if (workouts.length === 0) return { content: [{ type: "text", text: "Nenhum treino no periodo." }] };
    const lines = workouts.map((w: any) => {
      const status = w.skipped ? "❌ Pulado" : `✅ ${w.total_sets} series`;
      return `${w.workout_date} [${w.workout_type || "—"}] ${status} | Volume: ${parseFloat(w.total_volume_kg || 0).toFixed(0)}kg`;
    });
    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 9. BUSCAR EXERCICIOS
// ──────────────────────────────────────────────────────────────
server.tool(
  "buscar_exercicios",
  "Busca no catalogo de exercicios por nome, grupo muscular ou equipamento.",
  {
    termo: z.string().describe("Termo de busca, ex: 'peito', 'barra', 'agachamento'"),
  },
  async ({ termo }) => {
    const exercises = await searchExercises(termo);
    if (exercises.length === 0) return { content: [{ type: "text", text: `Nenhum exercicio encontrado para "${termo}".` }] };
    const lines = exercises.map((e: any) =>
      `[${e.muscle_group}] ${e.name} | Equip: ${e.equipment || "—"} | Dificuldade: ${e.difficulty || "—"}`
    );
    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 10. PROGRESSAO DE EXERCICIO
// ──────────────────────────────────────────────────────────────
server.tool(
  "progressao_exercicio",
  "Mostra historico de carga de um exercicio semana a semana e sinaliza plateau.",
  {
    nome_exercicio: z.string().describe("Nome do exercicio, ex: 'supino', 'agachamento'"),
  },
  async ({ nome_exercicio }) => {
    const result = await progressionAnalysis(nome_exercicio);
    return { content: [{ type: "text", text: result }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 11. GERAR RESUMO DIARIO
// ──────────────────────────────────────────────────────────────
server.tool(
  "gerar_resumo_diario",
  "Gera e salva o resumo diario (daily_summary) com totais, aderencia e micronutrientes.",
  {
    data: z.string().optional().describe("Data YYYY-MM-DD. Padrao: hoje"),
    treinou: z.boolean().optional().describe("Se treinou neste dia"),
    notas_treino: z.string().optional(),
  },
  async ({ data, treinou, notas_treino }) => {
    const dateStr = data || new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
    const text = await generateDailySummary(dateStr, treinou, notas_treino);
    return { content: [{ type: "text", text }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 12. RETROSPECTIVA SEMANAL
// ──────────────────────────────────────────────────────────────
server.tool(
  "retrospectiva_semanal",
  "Analisa os daily_summary da semana: medias, tendencias e micronutrientes cronicamente em falta.",
  {
    data_domingo: z.string().optional().describe("Data do domingo encerrando a semana YYYY-MM-DD. Padrao: domingo mais recente"),
  },
  async ({ data_domingo }) => {
    let sundayDate = data_domingo;
    if (!sundayDate) {
      const d = new Date();
      const day = d.getDay();
      const diff = day === 0 ? 0 : 7 - day;
      d.setDate(d.getDate() - diff + (diff === 0 ? 0 : 0) - (day === 0 ? 0 : day));
      sundayDate = d.toLocaleDateString("sv-SE", { timeZone: "Europe/Lisbon" });
    }
    const text = await weeklyRetrospective(sundayDate);
    return { content: [{ type: "text", text }] };
  }
);

// ──────────────────────────────────────────────────────────────
// 13. INSERIR DADOS HISTORICOS
// ──────────────────────────────────────────────────────────────
server.tool(
  "inserir_dados_historicos",
  "Insere as 3 refeicoes historicas do dia 08/06/2026 (dia de viagem Napoles → Lisboa).",
  {},
  async () => {
    const ids: number[] = [];

    // Refeicao 1: cafe da manha — 08:00 hora Napoles = 06:00 UTC
    ids.push(await insertMeal({
      meal_time: "2026-06-08T06:00:00Z",
      meal_type: "cafe_manha",
      description: "2 ovos, 1 fatia de pao, bacon",
      is_on_plan: false,
      notes: "viagem em Napoles, Italia",
      macros: {
        calories: 453, protein_g: 27.5, carbs_g: 14.5, fat_g: 28.3, fiber_g: 0.8,
        calcium_mg: 54, iron_mg: 1.7, magnesium_mg: 16, potassium_mg: 258, sodium_mg: 592,
        vitamin_c_mg: 0, vitamin_d_mcg: 2.3, vitamin_b12_mcg: 1.4, zinc_mg: 1.4,
      },
    }));

    // Refeicao 2: almoco — 13:00 hora Napoles = 11:00 UTC
    ids.push(await insertMeal({
      meal_time: "2026-06-08T11:00:00Z",
      meal_type: "almoco",
      description: "pizza de queijo individual napolitana",
      is_on_plan: false,
      notes: "viagem em Napoles, Italia",
      macros: {
        calories: 798, protein_g: 30, carbs_g: 100, fat_g: 28, fiber_g: 5,
        calcium_mg: 450, iron_mg: 4.5, magnesium_mg: 54, potassium_mg: 516, sodium_mg: 1800,
        vitamin_c_mg: 6, vitamin_d_mcg: 0.3, vitamin_b12_mcg: 0.9, zinc_mg: 3.6,
      },
    }));

    // Refeicao 3: lanche — 15:00 hora Napoles = 13:00 UTC
    ids.push(await insertMeal({
      meal_time: "2026-06-08T13:00:00Z",
      meal_type: "lanche",
      description: "sorvete de limao (gelato)",
      is_on_plan: false,
      notes: "viagem em Napoles, Italia",
      macros: {
        calories: 200, protein_g: 3.5, carbs_g: 35, fat_g: 5, fiber_g: 0,
        calcium_mg: 100, iron_mg: 0.1, magnesium_mg: 10, potassium_mg: 150, sodium_mg: 50,
        vitamin_c_mg: 2, vitamin_d_mcg: 0.2, vitamin_b12_mcg: 0.1, zinc_mg: 0.3,
      },
    }));

    const total = { kcal: 453 + 798 + 200, prot: 27.5 + 30 + 3.5, carbs: 14.5 + 100 + 35, fat: 28.3 + 28 + 5 };
    return {
      content: [{
        type: "text",
        text: [
          `✅ Dados historicos inseridos — 08/06/2026 (viagem Napoles → Lisboa)`,
          `IDs: ${ids.join(", ")}`,
          ``,
          `Total do dia:`,
          `  Calorias: ${total.kcal} kcal`,
          `  Proteina: ${total.prot}g`,
          `  Carbs: ${total.carbs}g`,
          `  Gordura: ${total.fat}g`,
          ``,
          `Todas as refeicoes marcadas como is_on_plan=false (dia de viagem).`,
        ].join("\n"),
      }],
    };
  }
);

// ──────────────────────────────────────────────────────────────
// 14. EXECUTAR SQL
// ──────────────────────────────────────────────────────────────
server.tool(
  "executar_sql",
  "Executa uma query SQL diretamente no banco. Use para analises, correcoes e consultas ad-hoc.",
  {
    sql: z.string().describe("Query SQL a executar"),
  },
  async ({ sql }) => {
    const normalized = sql.trim().toUpperCase();
    const allowed = ["SELECT", "WITH", "EXPLAIN"];
    const isReadOnly = allowed.some(kw => normalized.startsWith(kw));

    if (!isReadOnly) {
      // Permite DDL/DML mas avisa
      console.warn("executar_sql: rodando query de escrita:", sql.substring(0, 80));
    }

    try {
      const res = await query(sql);
      const text = res.rows.length > 0
        ? JSON.stringify(res.rows, null, 2)
        : `Operacao concluida. ${res.rowCount} linha(s) afetadas.`;
      return { content: [{ type: "text", text }] };
    } catch (err: any) {
      return { content: [{ type: "text", text: `Erro SQL: ${err.message}` }] };
    }
  }
);

// ──────────────────────────────────────────────────────────────
// SERVIDOR HTTP + SSE
// ──────────────────────────────────────────────────────────────
const app = express();
const PORT = parseInt(process.env.PORT || "8000", 10);

const transports = new Map<string, SSEServerTransport>();

app.get("/sse", async (req: Request, res: Response) => {
  const transport = new SSEServerTransport("/messages", res);
  transports.set(transport.sessionId, transport);
  res.on("close", () => transports.delete(transport.sessionId));
  await server.connect(transport);
});

app.post("/messages", express.json(), async (req: Request, res: Response) => {
  const sessionId = req.query.sessionId as string;
  const transport = transports.get(sessionId);
  if (!transport) {
    res.status(404).send("Session not found");
    return;
  }
  await transport.handlePostMessage(req, res);
});

app.get("/", (_req: Request, res: Response) => {
  res.json({ status: "ok", service: "tracker-nutricao-mcp", version: "1.0.0" });
});

app.listen(PORT, () => {
  console.log(`Tracker Nutricao MCP rodando na porta ${PORT}`);
  console.log(`SSE endpoint: http://localhost:${PORT}/sse`);
});
