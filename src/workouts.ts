import { query } from "./db";

export async function upsertExercise(name: string, muscleGroup?: string, equipment?: string): Promise<number> {
  const res = await query(
    `INSERT INTO exercises (name, muscle_group, equipment, is_active)
     VALUES ($1, $2, $3, true)
     ON CONFLICT DO NOTHING
     RETURNING id`,
    [name, muscleGroup || null, equipment || null]
  );
  if (res.rows.length > 0) return res.rows[0].id;
  const existing = await query(`SELECT id FROM exercises WHERE LOWER(name) = LOWER($1)`, [name]);
  return existing.rows[0]?.id || null;
}

export async function insertWorkout(params: {
  workout_date: string;
  started_at?: string | null;
  ended_at?: string | null;
  workout_type?: string | null;
  location?: string | null;
  notes?: string | null;
  skipped?: boolean;
  skip_reason?: string | null;
}): Promise<number> {
  const res = await query(
    `INSERT INTO workouts (workout_date, started_at, ended_at, workout_type, location, notes, skipped, skip_reason)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id`,
    [
      params.workout_date, params.started_at || null, params.ended_at || null,
      params.workout_type || null, params.location || null, params.notes || null,
      params.skipped || false, params.skip_reason || null,
    ]
  );
  return res.rows[0].id;
}

export async function insertWorkoutSet(params: {
  workout_id: number;
  exercise_name: string;
  exercise_id?: number | null;
  set_number?: number;
  reps?: number | null;
  weight_kg?: number | null;
  rpe?: number | null;
  notes?: string | null;
  is_alternative?: boolean;
  alternative_for?: string | null;
}): Promise<number> {
  const res = await query(
    `INSERT INTO workout_sets
       (workout_id, exercise_id, exercise_name, set_number, reps, weight_kg, rpe, notes, is_alternative, alternative_for)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id`,
    [
      params.workout_id, params.exercise_id || null, params.exercise_name,
      params.set_number || null, params.reps || null, params.weight_kg ?? null,
      params.rpe || null, params.notes || null,
      params.is_alternative || false, params.alternative_for || null,
    ]
  );
  return res.rows[0].id;
}

export async function listWorkouts(startDate: string, endDate: string): Promise<any[]> {
  const res = await query(
    `SELECT w.id, w.workout_date, w.workout_type, w.location, w.notes, w.skipped, w.skip_reason,
            COUNT(ws.id) AS total_sets,
            COALESCE(SUM(ws.reps * NULLIF(ws.weight_kg, 0)), 0) AS total_volume_kg
     FROM workouts w
     LEFT JOIN workout_sets ws ON ws.workout_id = w.id
     WHERE w.workout_date BETWEEN $1 AND $2
     GROUP BY w.id ORDER BY w.workout_date DESC`,
    [startDate, endDate]
  );
  return res.rows;
}

export async function getWorkoutSets(workoutId: number): Promise<any[]> {
  const res = await query(
    `SELECT ws.set_number, ws.exercise_name, ws.reps, ws.weight_kg, ws.rpe, ws.notes,
            ws.is_alternative, ws.alternative_for
     FROM workout_sets ws WHERE ws.workout_id = $1 ORDER BY ws.set_number`,
    [workoutId]
  );
  return res.rows;
}

export async function progressionAnalysis(exerciseName: string): Promise<string> {
  const res = await query(
    `SELECT DATE_TRUNC('week', w.workout_date::timestamptz) AS week,
            MAX(ws.weight_kg) AS max_weight,
            MAX(ws.reps) AS max_reps
     FROM workout_sets ws
     JOIN workouts w ON w.id = ws.workout_id
     WHERE LOWER(ws.exercise_name) LIKE LOWER($1)
     GROUP BY 1 ORDER BY 1 DESC LIMIT 8`,
    [`%${exerciseName}%`]
  );
  if (res.rows.length === 0) return `Nenhum registro encontrado para "${exerciseName}".`;

  const rows = res.rows;
  const lines = rows.map((r: any) => {
    const week = new Date(r.week).toLocaleDateString("pt-PT");
    return `  Semana ${week}: ${r.max_weight}kg x ${r.max_reps} reps`;
  });

  // Verifica plateau: ultimas 2 semanas com mesmo peso
  let plateauMsg = "";
  if (rows.length >= 2 && rows[0].max_weight === rows[1].max_weight) {
    plateauMsg = "\n⚠️  Plateau detectado — mesmo peso nas ultimas 2+ semanas.";
  }

  return `Progressao em "${exerciseName}":\n${lines.join("\n")}${plateauMsg}`;
}

export async function searchExercises(term: string): Promise<any[]> {
  const res = await query(
    `SELECT id, name, muscle_group, secondary_muscles, equipment, movement_pattern, difficulty
     FROM exercises
     WHERE is_active = true AND (
       LOWER(name) LIKE LOWER($1) OR
       LOWER(COALESCE(muscle_group,'')) LIKE LOWER($1) OR
       LOWER(COALESCE(equipment,'')) LIKE LOWER($1)
     )
     ORDER BY muscle_group, name LIMIT 20`,
    [`%${term}%`]
  );
  return res.rows;
}
