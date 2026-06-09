import { query } from "./db";
import { getDailyTotals, PLANO_METAS } from "./meals";

function pct(val: number, target: number): string {
  if (!target) return "—";
  return `${Math.round((val / target) * 100)}%`;
}

function statusEmoji(val: number, target: number): string {
  const ratio = val / target;
  if (ratio >= 0.9 && ratio <= 1.1) return "✅";
  if (ratio < 0.9) return "⬇️";
  return "⬆️";
}

export async function generateDailySummary(dateStr: string, trainedFlag?: boolean, workoutNotes?: string): Promise<string> {
  const totals = await getDailyTotals(dateStr);
  const adherencePct = totals.meals_total > 0
    ? Math.round((totals.meals_on_plan / totals.meals_total) * 100)
    : 0;

  await query(
    `INSERT INTO daily_summary
       (summary_date, total_calories, total_protein_g, total_carbs_g, total_fat_g, total_fiber_g,
        calcium_mg, iron_mg, magnesium_mg, potassium_mg,
        vitamin_c_mg, vitamin_d_mcg, vitamin_b12_mcg, zinc_mg,
        meals_on_plan, meals_total, adherence_pct, trained, workout_notes)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
     ON CONFLICT (summary_date) DO UPDATE SET
       total_calories = EXCLUDED.total_calories,
       total_protein_g = EXCLUDED.total_protein_g,
       total_carbs_g = EXCLUDED.total_carbs_g,
       total_fat_g = EXCLUDED.total_fat_g,
       total_fiber_g = EXCLUDED.total_fiber_g,
       calcium_mg = EXCLUDED.calcium_mg,
       iron_mg = EXCLUDED.iron_mg,
       magnesium_mg = EXCLUDED.magnesium_mg,
       potassium_mg = EXCLUDED.potassium_mg,
       vitamin_c_mg = EXCLUDED.vitamin_c_mg,
       vitamin_d_mcg = EXCLUDED.vitamin_d_mcg,
       vitamin_b12_mcg = EXCLUDED.vitamin_b12_mcg,
       zinc_mg = EXCLUDED.zinc_mg,
       meals_on_plan = EXCLUDED.meals_on_plan,
       meals_total = EXCLUDED.meals_total,
       adherence_pct = EXCLUDED.adherence_pct,
       trained = EXCLUDED.trained,
       workout_notes = EXCLUDED.workout_notes`,
    [
      dateStr,
      totals.calories, totals.protein_g, totals.carbs_g, totals.fat_g, totals.fiber_g,
      totals.calcium_mg, totals.iron_mg, totals.magnesium_mg, totals.potassium_mg,
      totals.vitamin_c_mg, totals.vitamin_d_mcg, totals.vitamin_b12_mcg, totals.zinc_mg,
      totals.meals_on_plan, totals.meals_total, adherencePct,
      trainedFlag ?? false, workoutNotes ?? null,
    ]
  );

  // Gera texto do resumo
  const m = PLANO_METAS;
  const cal = totals.calories as number;
  const prot = totals.protein_g as number;
  const carbs = totals.carbs_g as number;
  const fat = totals.fat_g as number;

  const lines = [
    `📊 Resumo do dia ${dateStr}`,
    ``,
    `Macronutrientes:`,
    `  Calorias:    ${cal.toFixed(0)} / ${m.calories} kcal ${statusEmoji(cal, m.calories)} (${pct(cal, m.calories)})`,
    `  Proteina:    ${prot.toFixed(1)} / ${m.protein_g} g ${statusEmoji(prot, m.protein_g)} (${pct(prot, m.protein_g)})`,
    `  Carboidratos: ${carbs.toFixed(1)} / ${m.carbs_g} g ${statusEmoji(carbs, m.carbs_g)} (${pct(carbs, m.carbs_g)})`,
    `  Gordura:     ${fat.toFixed(1)} / ${m.fat_g} g ${statusEmoji(fat, m.fat_g)} (${pct(fat, m.fat_g)})`,
    ``,
    `Micronutrientes:`,
    `  Calcio:     ${(totals.calcium_mg as number).toFixed(0)} / ${m.calcium_mg} mg ${statusEmoji(totals.calcium_mg as number, m.calcium_mg)}`,
    `  Magnesio:   ${(totals.magnesium_mg as number).toFixed(0)} / ${m.magnesium_mg} mg ${statusEmoji(totals.magnesium_mg as number, m.magnesium_mg)}`,
    `  Ferro:      ${(totals.iron_mg as number).toFixed(1)} / ${m.iron_mg} mg ${statusEmoji(totals.iron_mg as number, m.iron_mg)}`,
    `  Potassio:   ${(totals.potassium_mg as number).toFixed(0)} / ${m.potassium_mg} mg ${statusEmoji(totals.potassium_mg as number, m.potassium_mg)}`,
    `  Vitamina C: ${(totals.vitamin_c_mg as number).toFixed(1)} / ${m.vitamin_c_mg} mg ${statusEmoji(totals.vitamin_c_mg as number, m.vitamin_c_mg)}`,
    `  Vitamina D: ${(totals.vitamin_d_mcg as number).toFixed(1)} / ${m.vitamin_d_mcg} mcg ${statusEmoji(totals.vitamin_d_mcg as number, m.vitamin_d_mcg)}`,
    `  Vitamina B12: ${(totals.vitamin_b12_mcg as number).toFixed(1)} / ${m.vitamin_b12_mcg} mcg ${statusEmoji(totals.vitamin_b12_mcg as number, m.vitamin_b12_mcg)}`,
    `  Zinco:      ${(totals.zinc_mg as number).toFixed(1)} / ${m.zinc_mg} mg ${statusEmoji(totals.zinc_mg as number, m.zinc_mg)}`,
    ``,
    `Aderencia ao plano: ${adherencePct}% (${totals.meals_on_plan}/${totals.meals_total} refeicoes)`,
    trainedFlag !== undefined ? `Treino: ${trainedFlag ? "✅ Realizado" : "❌ Nao realizado"}` : "",
  ].filter(l => l !== undefined);

  return lines.join("\n");
}

export async function weeklyRetrospective(sundayDate: string): Promise<string> {
  const res = await query(
    `SELECT summary_date, total_calories, total_protein_g, total_carbs_g, total_fat_g,
            calcium_mg, iron_mg, magnesium_mg, potassium_mg,
            vitamin_c_mg, vitamin_d_mcg, vitamin_b12_mcg, zinc_mg,
            adherence_pct, trained, meals_total
     FROM daily_summary
     WHERE summary_date BETWEEN ($1::date - INTERVAL '6 days') AND $1::date
     ORDER BY summary_date`,
    [sundayDate]
  );

  if (res.rows.length === 0) {
    return "Nenhum dado encontrado para esta semana. Use gerar_resumo_diario para cada dia primeiro.";
  }

  const rows = res.rows;
  const n = rows.length;
  const avg = (field: string) => rows.reduce((s: number, r: any) => s + parseFloat(r[field] || 0), 0) / n;

  const avgCal = avg("total_calories");
  const avgProt = avg("total_protein_g");
  const avgCarbs = avg("total_carbs_g");
  const avgFat = avg("total_fat_g");
  const trainedDays = rows.filter((r: any) => r.trained).length;
  const avgAdherence = avg("adherence_pct");

  const m = PLANO_METAS;

  const micros = [
    { name: "Calcio", field: "calcium_mg", target: m.calcium_mg, unit: "mg" },
    { name: "Magnesio", field: "magnesium_mg", target: m.magnesium_mg, unit: "mg" },
    { name: "Ferro", field: "iron_mg", target: m.iron_mg, unit: "mg" },
    { name: "Potassio", field: "potassium_mg", target: m.potassium_mg, unit: "mg" },
    { name: "Vitamina C", field: "vitamin_c_mg", target: m.vitamin_c_mg, unit: "mg" },
    { name: "Vitamina D", field: "vitamin_d_mcg", target: m.vitamin_d_mcg, unit: "mcg" },
    { name: "Vitamina B12", field: "vitamin_b12_mcg", target: m.vitamin_b12_mcg, unit: "mcg" },
    { name: "Zinco", field: "zinc_mg", target: m.zinc_mg, unit: "mg" },
  ];

  const microLines = micros
    .map(mic => {
      const avgVal = avg(mic.field);
      const ratio = avgVal / mic.target;
      const emoji = ratio < 0.7 ? "🔴" : ratio < 0.9 ? "🟡" : "🟢";
      return `  ${emoji} ${mic.name}: media ${avgVal.toFixed(1)} / ${mic.target} ${mic.unit} (${pct(avgVal, mic.target)})`;
    })
    .join("\n");

  return [
    `📅 Retrospectiva semanal — semana encerrada em ${sundayDate}`,
    ``,
    `Dias com dados: ${n}/7`,
    ``,
    `Medias diarias:`,
    `  Calorias:     ${avgCal.toFixed(0)} / ${m.calories} kcal ${statusEmoji(avgCal, m.calories)}`,
    `  Proteina:     ${avgProt.toFixed(1)} / ${m.protein_g} g ${statusEmoji(avgProt, m.protein_g)}`,
    `  Carboidratos: ${avgCarbs.toFixed(1)} / ${m.carbs_g} g ${statusEmoji(avgCarbs, m.carbs_g)}`,
    `  Gordura:      ${avgFat.toFixed(1)} / ${m.fat_g} g ${statusEmoji(avgFat, m.fat_g)}`,
    ``,
    `Treinos realizados: ${trainedDays}/${n} dias`,
    `Aderencia media ao plano: ${avgAdherence.toFixed(0)}%`,
    ``,
    `Micronutrientes (media diaria vs meta):`,
    microLines,
  ].join("\n");
}
