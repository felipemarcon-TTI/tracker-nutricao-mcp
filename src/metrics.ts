import { query } from "./db";

export async function insertMetric(params: {
  measurement_date: string;
  weight_kg?: number | null;
  waist_cm?: number | null;
  notes?: string | null;
}): Promise<number> {
  const res = await query(
    `INSERT INTO body_metrics (measurement_date, weight_kg, waist_cm, notes)
     VALUES ($1, $2, $3, $4) RETURNING id`,
    [params.measurement_date, params.weight_kg ?? null, params.waist_cm ?? null, params.notes ?? null]
  );
  return res.rows[0].id;
}

export async function lastMetricDate(): Promise<Date | null> {
  const res = await query(`SELECT MAX(measurement_date) AS last_date FROM body_metrics`);
  return res.rows[0].last_date ? new Date(res.rows[0].last_date) : null;
}

export async function needsReminderBodyMetrics(): Promise<boolean> {
  const last = await lastMetricDate();
  if (!last) return true;
  const diffDays = (Date.now() - last.getTime()) / (1000 * 60 * 60 * 24);
  return diffDays > 7;
}

export async function listMetrics(limit = 30): Promise<any[]> {
  const res = await query(
    `SELECT measurement_date, weight_kg, waist_cm, notes
     FROM body_metrics ORDER BY measurement_date DESC LIMIT $1`,
    [limit]
  );
  return res.rows;
}
