-- Tracker de Nutricao e Treino - Felipe
-- Schema PostgreSQL

CREATE TABLE IF NOT EXISTS meals (
    id SERIAL PRIMARY KEY,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    meal_time TIMESTAMPTZ,
    meal_type VARCHAR(50),
    description TEXT NOT NULL,
    is_on_plan BOOLEAN,
    deviation_notes TEXT,
    calories NUMERIC(7,2),
    protein_g NUMERIC(6,2),
    carbs_g NUMERIC(6,2),
    fat_g NUMERIC(6,2),
    fiber_g NUMERIC(6,2),
    calcium_mg NUMERIC(8,2),
    iron_mg NUMERIC(7,2),
    magnesium_mg NUMERIC(7,2),
    potassium_mg NUMERIC(8,2),
    sodium_mg NUMERIC(8,2),
    vitamin_c_mg NUMERIC(7,2),
    vitamin_d_mcg NUMERIC(7,2),
    vitamin_b12_mcg NUMERIC(7,2),
    zinc_mg NUMERIC(7,2),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS daily_summary (
    id SERIAL PRIMARY KEY,
    summary_date DATE NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_calories NUMERIC(7,2),
    total_protein_g NUMERIC(6,2),
    total_carbs_g NUMERIC(6,2),
    total_fat_g NUMERIC(6,2),
    total_fiber_g NUMERIC(6,2),
    target_calories NUMERIC(7,2) DEFAULT 2253,
    target_protein_g NUMERIC(6,2) DEFAULT 183.3,
    target_carbs_g NUMERIC(6,2) DEFAULT 231.9,
    target_fat_g NUMERIC(6,2) DEFAULT 70.7,
    calcium_mg NUMERIC(8,2),
    iron_mg NUMERIC(7,2),
    magnesium_mg NUMERIC(7,2),
    potassium_mg NUMERIC(8,2),
    vitamin_c_mg NUMERIC(7,2),
    vitamin_d_mcg NUMERIC(7,2),
    vitamin_b12_mcg NUMERIC(7,2),
    zinc_mg NUMERIC(7,2),
    meals_on_plan INTEGER,
    meals_total INTEGER,
    adherence_pct NUMERIC(5,2),
    trained BOOLEAN DEFAULT FALSE,
    workout_notes TEXT,
    report_text TEXT,
    feedback_text TEXT
);

CREATE TABLE IF NOT EXISTS body_metrics (
    id SERIAL PRIMARY KEY,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    measurement_date DATE NOT NULL,
    weight_kg NUMERIC(5,2),
    waist_cm NUMERIC(5,2),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS exercises (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    muscle_group VARCHAR(100),
    secondary_muscles TEXT,
    equipment VARCHAR(100),
    movement_pattern VARCHAR(100),
    difficulty VARCHAR(20),
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS workouts (
    id SERIAL PRIMARY KEY,
    workout_date DATE NOT NULL,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    workout_type VARCHAR(100),
    location VARCHAR(100),
    notes TEXT,
    skipped BOOLEAN DEFAULT FALSE,
    skip_reason TEXT
);

CREATE TABLE IF NOT EXISTS workout_sets (
    id SERIAL PRIMARY KEY,
    workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise_id INTEGER REFERENCES exercises(id),
    exercise_name VARCHAR(150),
    set_number INTEGER,
    reps INTEGER,
    weight_kg NUMERIC(6,2),
    rpe NUMERIC(3,1),
    notes TEXT,
    is_alternative BOOLEAN DEFAULT FALSE,
    alternative_for VARCHAR(150)
);

CREATE TABLE IF NOT EXISTS nutrient_alerts (
    id SERIAL PRIMARY KEY,
    nutrient VARCHAR(50) NOT NULL,
    first_flagged_at DATE NOT NULL,
    weeks_below INTEGER DEFAULT 1,
    escalation_level VARCHAR(20) DEFAULT 'sugestao',
    escalation_threshold INTEGER NOT NULL,
    last_suggestion TEXT,
    last_reviewed_at DATE,
    resolved_at DATE,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS training_plan (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    days_per_week_min INTEGER NOT NULL DEFAULT 3,
    days_per_week_max INTEGER NOT NULL DEFAULT 4,
    split_type VARCHAR(50) DEFAULT 'PPL',
    cardio_days INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT
);

INSERT INTO training_plan (days_per_week_min, days_per_week_max, split_type, cardio_days, notes)
SELECT 3, 4, 'PPL', 1, 'PPL + dia D de cardio LISS. Academia semi-completa, sem crucifixo máquina.'
WHERE NOT EXISTS (SELECT 1 FROM training_plan WHERE is_active = TRUE);

ALTER TABLE workouts ADD COLUMN IF NOT EXISTS energy_level INTEGER;
ALTER TABLE workouts ADD COLUMN IF NOT EXISTS sleep_quality INTEGER;
ALTER TABLE workouts ADD COLUMN IF NOT EXISTS split_day VARCHAR(20);

ALTER TABLE daily_summary ADD COLUMN IF NOT EXISTS water_estimate_ml INTEGER;

-- Indices para performance
CREATE INDEX IF NOT EXISTS idx_meals_meal_time ON meals(meal_time);
CREATE INDEX IF NOT EXISTS idx_nutrient_alerts_nutrient ON nutrient_alerts(nutrient);
CREATE INDEX IF NOT EXISTS idx_nutrient_alerts_active ON nutrient_alerts(is_active);
CREATE INDEX IF NOT EXISTS idx_meals_logged_at ON meals(logged_at);
CREATE INDEX IF NOT EXISTS idx_daily_summary_date ON daily_summary(summary_date);
CREATE INDEX IF NOT EXISTS idx_body_metrics_date ON body_metrics(measurement_date);
CREATE INDEX IF NOT EXISTS idx_workouts_date ON workouts(workout_date);
CREATE INDEX IF NOT EXISTS idx_workout_sets_workout ON workout_sets(workout_id);
CREATE INDEX IF NOT EXISTS idx_workout_sets_exercise ON workout_sets(exercise_id);
