import { query } from "./db";

// Metas do plano da Helena Ferretti S. Proenica (CRN 5545N, 06/10/2025)
export const PLANO_METAS = {
  calories: 2253,
  protein_g: 183.3,
  carbs_g: 231.9,
  fat_g: 70.7,
  // Micronutrientes-alvo diarios
  calcium_mg: 1145,
  magnesium_mg: 218,
  iron_mg: 7.3,
  potassium_mg: 3199,
  vitamin_c_mg: 39.9,
  vitamin_d_mcg: 1.9,
  vitamin_b12_mcg: 1.8,
  zinc_mg: 6.6,
};

// Tabela nutricional simplificada por 100g ou por unidade tipica
// Formato: [calorias, proteina_g, carbs_g, gordura_g, fibra_g, calcio_mg, ferro_mg, magnesio_mg, potassio_mg, sodio_mg, vitC_mg, vitD_mcg, vitB12_mcg, zinco_mg]
type NutriRow = [number, number, number, number, number, number, number, number, number, number, number, number, number, number];

const NUTRI_DB: Record<string, NutriRow> = {
  // [cal, prot, carb, fat, fiber, Ca, Fe, Mg, K, Na, vitC, vitD, vitB12, Zn]
  "ovo": [78, 6, 0.5, 5.3, 0, 25, 0.6, 6, 69, 62, 0, 1.1, 0.6, 0.6],
  "clara de ovo": [17, 3.6, 0.2, 0, 0, 2, 0, 4, 54, 55, 0, 0, 0, 0],
  "pao": [75, 2.5, 14, 1, 0.8, 30, 0.8, 8, 35, 150, 0, 0, 0, 0.2],
  "pao integral": [70, 3, 13, 1, 1.5, 25, 1, 10, 70, 130, 0, 0, 0, 0.3],
  "bacon": [110, 7, 0.3, 9, 0, 2, 0.2, 4, 90, 380, 0, 0.1, 0.2, 0.5],
  "frango peito": [165, 31, 0, 3.6, 0, 15, 1, 28, 300, 74, 0, 0.1, 0.3, 1],
  "frango coxa": [185, 22, 0, 10, 0, 12, 1, 22, 250, 80, 0, 0.1, 0.3, 1.5],
  "arroz branco": [130, 2.7, 28, 0.3, 0.4, 10, 0.2, 12, 35, 1, 0, 0, 0, 0.5],
  "arroz integral": [120, 2.5, 25, 1, 1.8, 10, 0.5, 43, 80, 5, 0, 0, 0, 0.6],
  "feijao": [135, 8, 24, 0.5, 7, 40, 2, 45, 400, 5, 1, 0, 0, 1],
  "batata doce": [90, 1.6, 21, 0.1, 3, 30, 0.6, 25, 440, 55, 22, 0, 0, 0.3],
  "batata": [80, 2, 18, 0.1, 1.5, 12, 0.8, 22, 420, 6, 20, 0, 0, 0.3],
  "massa": [150, 5, 30, 1, 1, 12, 1, 18, 44, 5, 0, 0, 0, 0.5],
  "pizza": [266, 11, 33, 10, 2, 150, 1.5, 18, 172, 600, 2, 0.1, 0.3, 1.2],
  "queijo": [100, 7, 0.5, 8, 0, 200, 0.1, 8, 25, 170, 0, 0.1, 0.3, 0.9],
  "iogurte grego": [130, 12, 6, 6, 0, 150, 0, 12, 160, 60, 0, 0, 0.5, 0.5],
  "proteina whey": [120, 24, 3, 1.5, 0, 120, 0.5, 30, 160, 80, 0, 0, 0.5, 1.2],
  "banana": [90, 1.1, 23, 0.3, 2.6, 5, 0.3, 27, 358, 1, 9, 0, 0, 0.2],
  "maca": [52, 0.3, 14, 0.2, 2.4, 6, 0.1, 5, 107, 1, 5, 0, 0, 0],
  "laranja": [47, 0.9, 12, 0.1, 2.4, 40, 0.1, 10, 181, 0, 53, 0, 0, 0.1],
  "salada": [20, 1.5, 3, 0.2, 2, 40, 1, 15, 200, 20, 15, 0, 0, 0.2],
  "tomate": [18, 0.9, 3.9, 0.2, 1.2, 10, 0.3, 11, 237, 5, 14, 0, 0, 0.2],
  "aveia": [380, 13, 68, 7, 10, 54, 4.7, 138, 430, 2, 0, 0, 0, 4],
  "gelato": [200, 3.5, 35, 5, 0, 100, 0.1, 10, 150, 50, 2, 0.2, 0.1, 0.3],
  "sorvete": [200, 3.5, 35, 5, 0, 100, 0.1, 10, 150, 50, 2, 0.2, 0.1, 0.3],
  "chocolate": [540, 6, 60, 31, 4, 50, 3.5, 65, 400, 20, 0, 0, 0, 1.5],
  "salmao": [200, 25, 0, 11, 0, 15, 0.5, 32, 490, 59, 3, 11, 3.2, 0.6],
  "atum": [110, 23, 0, 1.7, 0, 12, 0.7, 28, 280, 40, 0, 4, 2, 0.5],
  "amendoim": [567, 26, 16, 49, 8.5, 92, 4.6, 168, 705, 18, 0, 0, 0, 3.3],
  "amendoa": [580, 21, 20, 50, 12, 270, 3.7, 270, 730, 1, 0, 0, 0, 3.1],
  "leite": [60, 3.2, 4.8, 3.2, 0, 120, 0.1, 11, 150, 50, 0, 1.1, 0.4, 0.4],
  "cafe": [2, 0.3, 0.3, 0, 0, 4, 0.1, 7, 100, 5, 0, 0, 0, 0],
  "suco laranja": [45, 0.7, 10, 0.2, 0.4, 12, 0.2, 11, 200, 1, 50, 0, 0, 0.1],
  "tapioca": [130, 1, 32, 0.1, 0.5, 20, 0.5, 5, 30, 5, 0, 0, 0, 0.1],
  "peixe": [100, 20, 0, 2, 0, 15, 0.5, 27, 370, 75, 0, 3, 1.5, 0.5],
  "carne bovina": [215, 22, 0, 13, 0, 12, 2.5, 20, 290, 60, 0, 0.1, 2.4, 4.5],
  "carne suina": [190, 22, 0, 11, 0, 12, 1, 22, 360, 60, 0, 0.5, 0.6, 2.9],
  "brocolos": [35, 2.4, 7, 0.4, 2.6, 47, 0.7, 21, 316, 33, 90, 0, 0, 0.4],
  "espinafre": [23, 2.9, 3.6, 0.4, 2.2, 99, 2.7, 79, 558, 79, 28, 0, 0, 0.5],
  "cenoura": [41, 0.9, 10, 0.2, 2.8, 33, 0.3, 12, 320, 69, 6, 0, 0, 0.2],
  "pao de forma": [75, 2.5, 14, 1, 0.8, 30, 0.8, 8, 35, 150, 0, 0, 0, 0.2],
  "manteiga": [717, 0.9, 0.1, 81, 0, 24, 0, 2, 24, 576, 0, 1.5, 0.2, 0.1],
  "azeite": [884, 0, 0, 100, 0, 1, 0, 0, 1, 2, 0, 0, 0, 0],
  "mel": [304, 0.3, 82, 0, 0.2, 6, 0.4, 2, 52, 4, 0.5, 0, 0, 0.2],
  "granola": [450, 10, 65, 18, 6, 50, 3, 80, 300, 50, 0, 0, 0, 2],
};

interface MacroEstimate {
  calories: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  calcium_mg: number | null;
  iron_mg: number | null;
  magnesium_mg: number | null;
  potassium_mg: number | null;
  sodium_mg: number | null;
  vitamin_c_mg: number | null;
  vitamin_d_mcg: number | null;
  vitamin_b12_mcg: number | null;
  zinc_mg: number | null;
}

function zero(): MacroEstimate {
  return { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0, calcium_mg: 0, iron_mg: 0, magnesium_mg: 0, potassium_mg: 0, sodium_mg: 0, vitamin_c_mg: 0, vitamin_d_mcg: 0, vitamin_b12_mcg: 0, zinc_mg: 0 };
}

function addRow(acc: MacroEstimate, row: NutriRow, multiplier: number): void {
  acc.calories! += row[0] * multiplier;
  acc.protein_g! += row[1] * multiplier;
  acc.carbs_g! += row[2] * multiplier;
  acc.fat_g! += row[3] * multiplier;
  acc.fiber_g! += row[4] * multiplier;
  acc.calcium_mg! += row[5] * multiplier;
  acc.iron_mg! += row[6] * multiplier;
  acc.magnesium_mg! += row[7] * multiplier;
  acc.potassium_mg! += row[8] * multiplier;
  acc.sodium_mg! += row[9] * multiplier;
  acc.vitamin_c_mg! += row[10] * multiplier;
  acc.vitamin_d_mcg! += row[11] * multiplier;
  acc.vitamin_b12_mcg! += row[12] * multiplier;
  acc.zinc_mg! += row[13] * multiplier;
}

// Extrai quantidade e unidade da descricao
function parseQuantity(token: string): { qty: number; unit: string } {
  const m = token.match(/^(\d+(?:[.,]\d+)?)\s*(g|ml|kg|unid?|un|x|porcao|colher|xic|fatia|copo|dose)?$/i);
  if (m) {
    const qty = parseFloat(m[1].replace(",", "."));
    const unit = (m[2] || "unid").toLowerCase();
    return { qty, unit };
  }
  return { qty: 1, unit: "unid" };
}

export function estimateMacros(description: string): MacroEstimate {
  const desc = description.toLowerCase();
  const acc = zero();
  let matched = false;

  for (const [keyword, row] of Object.entries(NUTRI_DB)) {
    if (desc.includes(keyword)) {
      // Tenta extrair quantidade numerica antes do keyword
      const beforeKeyword = desc.substring(0, desc.indexOf(keyword)).trim();
      const tokens = beforeKeyword.split(/\s+/);
      const lastToken = tokens[tokens.length - 1] || "1";
      const { qty, unit } = parseQuantity(lastToken);

      // Normaliza: se for gramas, divide por 100 (tabela e por 100g); se for unidade, usa 1x
      let multiplier = 1;
      if (unit === "g" || unit === "ml") {
        multiplier = qty / 100;
      } else {
        multiplier = qty;
      }

      addRow(acc, row, multiplier);
      matched = true;
    }
  }

  if (!matched) {
    return { calories: null, protein_g: null, carbs_g: null, fat_g: null, fiber_g: null, calcium_mg: null, iron_mg: null, magnesium_mg: null, potassium_mg: null, sodium_mg: null, vitamin_c_mg: null, vitamin_d_mcg: null, vitamin_b12_mcg: null, zinc_mg: null };
  }

  // Arredonda tudo para 2 casas
  const round = (v: number | null) => v !== null ? Math.round(v * 100) / 100 : null;
  return {
    calories: round(acc.calories),
    protein_g: round(acc.protein_g),
    carbs_g: round(acc.carbs_g),
    fat_g: round(acc.fat_g),
    fiber_g: round(acc.fiber_g),
    calcium_mg: round(acc.calcium_mg),
    iron_mg: round(acc.iron_mg),
    magnesium_mg: round(acc.magnesium_mg),
    potassium_mg: round(acc.potassium_mg),
    sodium_mg: round(acc.sodium_mg),
    vitamin_c_mg: round(acc.vitamin_c_mg),
    vitamin_d_mcg: round(acc.vitamin_d_mcg),
    vitamin_b12_mcg: round(acc.vitamin_b12_mcg),
    zinc_mg: round(acc.zinc_mg),
  };
}

export interface InsertMealParams {
  meal_time?: string;
  meal_type?: string;
  description: string;
  is_on_plan?: boolean;
  deviation_notes?: string;
  notes?: string;
  macros?: MacroEstimate;
}

export async function insertMeal(params: InsertMealParams): Promise<number> {
  const macros = params.macros || estimateMacros(params.description);
  const res = await query(
    `INSERT INTO meals
      (meal_time, meal_type, description, is_on_plan, deviation_notes, notes,
       calories, protein_g, carbs_g, fat_g, fiber_g,
       calcium_mg, iron_mg, magnesium_mg, potassium_mg, sodium_mg,
       vitamin_c_mg, vitamin_d_mcg, vitamin_b12_mcg, zinc_mg)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
     RETURNING id`,
    [
      params.meal_time || null,
      params.meal_type || null,
      params.description,
      params.is_on_plan ?? null,
      params.deviation_notes || null,
      params.notes || null,
      macros.calories, macros.protein_g, macros.carbs_g, macros.fat_g, macros.fiber_g,
      macros.calcium_mg, macros.iron_mg, macros.magnesium_mg, macros.potassium_mg, macros.sodium_mg,
      macros.vitamin_c_mg, macros.vitamin_d_mcg, macros.vitamin_b12_mcg, macros.zinc_mg,
    ]
  );
  return res.rows[0].id;
}

export async function listMeals(dateStr: string): Promise<any[]> {
  // dateStr: YYYY-MM-DD em horario de Lisboa (UTC+0 ou UTC+1)
  const res = await query(
    `SELECT id, meal_time AT TIME ZONE 'Europe/Lisbon' AS meal_time_local,
            meal_type, description, is_on_plan,
            calories, protein_g, carbs_g, fat_g, fiber_g,
            calcium_mg, iron_mg, magnesium_mg, potassium_mg,
            vitamin_c_mg, vitamin_d_mcg, vitamin_b12_mcg, zinc_mg,
            notes, deviation_notes
     FROM meals
     WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date = $1
        OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date = $1)
     ORDER BY COALESCE(meal_time, logged_at)`,
    [dateStr]
  );
  return res.rows;
}

export async function getDailyTotals(dateStr: string): Promise<MacroEstimate & { meals_total: number; meals_on_plan: number }> {
  const res = await query(
    `SELECT
       COUNT(*) AS meals_total,
       COUNT(*) FILTER (WHERE is_on_plan = true) AS meals_on_plan,
       COALESCE(SUM(calories), 0) AS calories,
       COALESCE(SUM(protein_g), 0) AS protein_g,
       COALESCE(SUM(carbs_g), 0) AS carbs_g,
       COALESCE(SUM(fat_g), 0) AS fat_g,
       COALESCE(SUM(fiber_g), 0) AS fiber_g,
       COALESCE(SUM(calcium_mg), 0) AS calcium_mg,
       COALESCE(SUM(iron_mg), 0) AS iron_mg,
       COALESCE(SUM(magnesium_mg), 0) AS magnesium_mg,
       COALESCE(SUM(potassium_mg), 0) AS potassium_mg,
       COALESCE(SUM(vitamin_c_mg), 0) AS vitamin_c_mg,
       COALESCE(SUM(vitamin_d_mcg), 0) AS vitamin_d_mcg,
       COALESCE(SUM(vitamin_b12_mcg), 0) AS vitamin_b12_mcg,
       COALESCE(SUM(zinc_mg), 0) AS zinc_mg
     FROM meals
     WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date = $1
        OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date = $1)`,
    [dateStr]
  );
  const row = res.rows[0];
  return {
    meals_total: parseInt(row.meals_total),
    meals_on_plan: parseInt(row.meals_on_plan),
    calories: parseFloat(row.calories),
    protein_g: parseFloat(row.protein_g),
    carbs_g: parseFloat(row.carbs_g),
    fat_g: parseFloat(row.fat_g),
    fiber_g: parseFloat(row.fiber_g),
    calcium_mg: parseFloat(row.calcium_mg),
    iron_mg: parseFloat(row.iron_mg),
    magnesium_mg: parseFloat(row.magnesium_mg),
    potassium_mg: parseFloat(row.potassium_mg),
    sodium_mg: null,
    vitamin_c_mg: parseFloat(row.vitamin_c_mg),
    vitamin_d_mcg: parseFloat(row.vitamin_d_mcg),
    vitamin_b12_mcg: parseFloat(row.vitamin_b12_mcg),
    zinc_mg: parseFloat(row.zinc_mg),
  };
}
