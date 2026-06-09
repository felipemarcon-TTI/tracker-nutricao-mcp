-- Seed: Catalogo de exercicios de musculacao
-- 70 exercicios cobrindo todos os grupos musculares

INSERT INTO exercises (name, muscle_group, secondary_muscles, equipment, movement_pattern, difficulty) VALUES

-- PEITO
('Supino Reto com Barra', 'peito', 'tríceps, ombro anterior', 'barra', 'empurrar', 'intermediário'),
('Supino Inclinado com Barra', 'peito', 'tríceps, ombro anterior', 'barra', 'empurrar', 'intermediário'),
('Supino Declinado com Barra', 'peito', 'tríceps', 'barra', 'empurrar', 'intermediário'),
('Supino Reto com Halteres', 'peito', 'tríceps, ombro anterior', 'haltere', 'empurrar', 'iniciante'),
('Supino Inclinado com Halteres', 'peito', 'tríceps, ombro anterior', 'haltere', 'empurrar', 'iniciante'),
('Crucifixo Reto com Halteres', 'peito', 'ombro anterior', 'haltere', 'empurrar', 'iniciante'),
('Crucifixo Inclinado com Halteres', 'peito', 'ombro anterior', 'haltere', 'empurrar', 'iniciante'),
('Crossover no Cabo', 'peito', 'ombro anterior', 'cabo', 'empurrar', 'iniciante'),
('Flexao de Braco', 'peito', 'tríceps, ombro anterior, core', 'peso_corporal', 'empurrar', 'iniciante'),
('Mergulho em Paralelas (Peito)', 'peito', 'tríceps, ombro anterior', 'peso_corporal', 'empurrar', 'intermediário'),

-- COSTAS
('Barra Fixa Pronada', 'costas', 'bíceps, romboides', 'peso_corporal', 'puxar', 'intermediário'),
('Barra Fixa Supinada', 'costas', 'bíceps', 'peso_corporal', 'puxar', 'intermediário'),
('Remada Curvada com Barra', 'costas', 'bíceps, romboides, trapézio', 'barra', 'puxar', 'intermediário'),
('Remada Unilateral com Haltere', 'costas', 'bíceps, romboides', 'haltere', 'puxar', 'iniciante'),
('Puxada Alta Pronada', 'costas', 'bíceps, romboides', 'máquina', 'puxar', 'iniciante'),
('Puxada Alta Supinada', 'costas', 'bíceps', 'máquina', 'puxar', 'iniciante'),
('Remada Baixa no Cabo', 'costas', 'bíceps, romboides', 'cabo', 'puxar', 'iniciante'),
('Levantamento Terra', 'costas', 'glúteos, isquiotibiais, core', 'barra', 'dobrar', 'avançado'),
('Remada com Apoio no Peito (Chest Supported)', 'costas', 'bíceps, romboides', 'haltere', 'puxar', 'iniciante'),
('Pullover com Haltere', 'costas', 'tríceps, peito', 'haltere', 'puxar', 'iniciante'),

-- OMBROS
('Desenvolvimento com Barra', 'ombro', 'tríceps, trapézio', 'barra', 'empurrar', 'intermediário'),
('Desenvolvimento com Halteres', 'ombro', 'tríceps, trapézio', 'haltere', 'empurrar', 'iniciante'),
('Elevacao Lateral com Halteres', 'ombro', 'trapézio', 'haltere', 'empurrar', 'iniciante'),
('Elevacao Frontal com Halteres', 'ombro', 'peito anterior', 'haltere', 'empurrar', 'iniciante'),
('Elevacao Posterior com Halteres', 'ombro', 'romboides, trapézio médio', 'haltere', 'puxar', 'iniciante'),
('Desenvolvimento Arnold', 'ombro', 'tríceps, trapézio', 'haltere', 'empurrar', 'intermediário'),
('Elevacao Lateral no Cabo', 'ombro', 'trapézio', 'cabo', 'empurrar', 'iniciante'),
('Encolhimento de Ombros com Halteres', 'ombro', 'trapézio superior', 'haltere', 'carregar', 'iniciante'),

-- BÍCEPS
('Rosca Direta com Barra', 'bíceps', 'braquial, braquiorradial', 'barra', 'puxar', 'iniciante'),
('Rosca Alternada com Halteres', 'bíceps', 'braquial, braquiorradial', 'haltere', 'puxar', 'iniciante'),
('Rosca Concentrada com Haltere', 'bíceps', 'braquial', 'haltere', 'puxar', 'iniciante'),
('Rosca Martelo', 'bíceps', 'braquiorradial, braquial', 'haltere', 'puxar', 'iniciante'),
('Rosca Scott com Barra', 'bíceps', 'braquial', 'barra', 'puxar', 'intermediário'),
('Rosca no Cabo (Polia Baixa)', 'bíceps', 'braquial', 'cabo', 'puxar', 'iniciante'),

-- TRÍCEPS
('Tríceps Testa com Barra', 'tríceps', 'ombro posterior', 'barra', 'empurrar', 'intermediário'),
('Tríceps Testa com Halteres', 'tríceps', 'ombro posterior', 'haltere', 'empurrar', 'iniciante'),
('Tríceps Pulley com Corda', 'tríceps', null, 'cabo', 'empurrar', 'iniciante'),
('Tríceps Pulley com Barra', 'tríceps', null, 'cabo', 'empurrar', 'iniciante'),
('Mergulho em Paralelas (Tríceps)', 'tríceps', 'peito, ombro anterior', 'peso_corporal', 'empurrar', 'intermediário'),
('Supino Fechado', 'tríceps', 'peito, ombro anterior', 'barra', 'empurrar', 'intermediário'),
('Extensao de Tríceps Acima da Cabeca', 'tríceps', null, 'haltere', 'empurrar', 'iniciante'),

-- PERNAS — QUADRÍCEPS
('Agachamento Livre', 'pernas', 'glúteos, isquiotibiais, core', 'barra', 'agachar', 'intermediário'),
('Agachamento Hack', 'pernas', 'glúteos, isquiotibiais', 'máquina', 'agachar', 'iniciante'),
('Leg Press 45', 'pernas', 'glúteos, isquiotibiais', 'máquina', 'empurrar', 'iniciante'),
('Extensao de Joelho (Cadeira Extensora)', 'pernas', null, 'máquina', 'empurrar', 'iniciante'),
('Agachamento Goblet', 'pernas', 'glúteos, core', 'kettlebell', 'agachar', 'iniciante'),
('Avanço com Halteres', 'pernas', 'glúteos, isquiotibiais', 'haltere', 'agachar', 'iniciante'),
('Agachamento Búlgaro', 'pernas', 'glúteos, isquiotibiais', 'haltere', 'agachar', 'intermediário'),

-- PERNAS — POSTERIOR E GLÚTEOS
('Stiff com Barra', 'pernas', 'glúteos, coluna lombar', 'barra', 'dobrar', 'intermediário'),
('Stiff com Halteres', 'pernas', 'glúteos, coluna lombar', 'haltere', 'dobrar', 'iniciante'),
('Mesa Flexora', 'pernas', 'glúteos', 'máquina', 'dobrar', 'iniciante'),
('Cadeira Flexora', 'pernas', 'glúteos', 'máquina', 'dobrar', 'iniciante'),
('Hip Thrust com Barra', 'pernas', 'quadríceps, core', 'barra', 'dobrar', 'iniciante'),
('Elevacao Pelvica (Glute Bridge)', 'pernas', 'core', 'peso_corporal', 'dobrar', 'iniciante'),
('Afundo no Multipower', 'pernas', 'glúteos, isquiotibiais', 'máquina', 'agachar', 'iniciante'),

-- PANTURRILHA
('Panturrilha em Pe na Máquina', 'pernas', null, 'máquina', 'empurrar', 'iniciante'),
('Panturrilha Sentado', 'pernas', null, 'máquina', 'empurrar', 'iniciante'),
('Panturrilha no Leg Press', 'pernas', null, 'máquina', 'empurrar', 'iniciante'),
('Panturrilha Livre com Halteres', 'pernas', null, 'haltere', 'empurrar', 'iniciante'),

-- CORE / ABDOMEN
('Abdominal Crunch', 'core', null, 'peso_corporal', 'rotação', 'iniciante'),
('Prancha (Plank)', 'core', 'ombros, glúteos', 'peso_corporal', 'carregar', 'iniciante'),
('Abdominal Infra', 'core', null, 'peso_corporal', 'rotação', 'iniciante'),
('Oblíquo com Cabo', 'core', null, 'cabo', 'rotação', 'iniciante'),
('Prancha Lateral', 'core', 'ombros', 'peso_corporal', 'carregar', 'iniciante'),
('Abdominal na Roda (Ab Wheel)', 'core', 'ombros, costas', 'peso_corporal', 'carregar', 'avançado'),
('Vacuo Abdominal', 'core', null, 'peso_corporal', 'rotação', 'iniciante'),
('Dead Bug', 'core', 'quadríceps', 'peso_corporal', 'carregar', 'iniciante'),
('Elevacao de Pernas Reto', 'core', 'isquiotibiais', 'peso_corporal', 'rotação', 'intermediário'),
('Russian Twist', 'core', null, 'peso_corporal', 'rotação', 'iniciante')

ON CONFLICT DO NOTHING;
