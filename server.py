import os, re, json, secrets, time, pathlib
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    LISBOA = ZoneInfo("Europe/Lisbon")
except Exception:
    LISBOA = timezone.utc

import psycopg2, psycopg2.extras
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

PORT = int(os.environ.get("PORT", 8000))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "tracker-nutricao-token")
CLIENT_ID = os.environ.get("MCP_OAUTH_CLIENT_ID", "tracker-nutricao")
CLIENT_SECRET = os.environ.get("MCP_OAUTH_CLIENT_SECRET", "tracker-nutricao")

mcp = FastMCP("Tracker Nutricao e Treino")

METAS = {
    "cal": 1721, "prot": 170.9, "carbs": 146.4, "fat": 53.4, "fibra": 32.6,
    "ca": 1000, "mg": 420, "fe": 8, "k": 3400, "na": 885.7,
    "vit_c": 90, "vit_d": 15.0, "vit_b12": 2.4, "zn": 11,
}

# Semanas consecutivas abaixo da meta para atingir red_flag (sodio: acima da meta)
ESCALATION_THRESHOLDS = {
    "zinco_mg": 2, "potassio_mg": 2, "vitamina_c_mg": 2,
    "ferro_mg": 3, "magnesio_mg": 3, "calcio_mg": 3,
    "vitamina_d_mcg": 5, "vitamina_b12_mcg": 6,
    "sodio_mg": 3,
}

def _nivel_escalacao(weeks_below: int, threshold: int) -> str:
    if weeks_below >= threshold + 2: return "encaminhar"
    if weeks_below >= threshold:     return "red_flag"
    if weeks_below >= 2:             return "reforco"
    return "sugestao"

def _db():
    url = DATABASE_URL
    if "railway" in url and "sslmode" not in url:
        url += "?sslmode=require"
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)

def db_q(sql, params=None):
    c = _db()
    try:
        with c.cursor() as cur:
            cur.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]
    finally:
        c.close()

def db_e(sql, params=None):
    c = psycopg2.connect(DATABASE_URL + ("?sslmode=require" if "railway" in DATABASE_URL and "sslmode" not in DATABASE_URL else ""))
    try:
        with c.cursor() as cur:
            cur.execute(sql, params or [])
            c.commit()
            try:
                return cur.fetchone()[0]
            except Exception:
                return cur.rowcount
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def _hoje(): return datetime.now(LISBOA).strftime("%Y-%m-%d")

@mcp.tool()
def inicializar_banco() -> str:
    """Cria todas as tabelas e popula o catalogo de exercicios. Seguro rodar multiplas vezes."""
    base = pathlib.Path(__file__).parent / "db"
    for f in ["schema.sql","seed.sql"]:
        sql = (base / f).read_text()
        for stmt in [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]:
            try: db_e(stmt + ";")
            except Exception as ex:
                if "already exists" not in str(ex).lower() and "duplicate" not in str(ex).lower(): raise
    # Migration v2 -- body_metrics expansion (idempotente)
    _cols = [
        "height_cm NUMERIC(5,2)","bmi NUMERIC(5,2)","body_fat_pct NUMERIC(5,2)",
        "fat_mass_kg NUMERIC(5,2)","fat_free_mass_kg NUMERIC(5,2)","residual_mass_kg NUMERIC(5,2)",
        "body_density NUMERIC(7,4)","sum_skinfolds_mm NUMERIC(6,2)",
        "waist_hip_ratio NUMERIC(5,3)","arm_muscle_circ_cm NUMERIC(5,2)",
        "skinfold_triceps_mm NUMERIC(5,2)","skinfold_biceps_mm NUMERIC(5,2)",
        "skinfold_abdominal_mm NUMERIC(5,2)","skinfold_subscapular_mm NUMERIC(5,2)",
        "skinfold_midaxillary_mm NUMERIC(5,2)","skinfold_thigh_mm NUMERIC(5,2)",
        "skinfold_chest_mm NUMERIC(5,2)","skinfold_suprailiac_mm NUMERIC(5,2)",
        "circ_waist_cm NUMERIC(5,2)","circ_hip_cm NUMERIC(5,2)",
        "circ_abdomen_cm NUMERIC(5,2)","circ_arm_relaxed_cm NUMERIC(5,2)",
        "circ_thigh_medial_cm NUMERIC(5,2)",
        "bmi_class VARCHAR(50)","metabolic_risk VARCHAR(50)",
        "body_fat_class VARCHAR(50)","amc_class VARCHAR(50)",
    ]
    for _col in _cols:
        try: db_e(f"ALTER TABLE body_metrics ADD COLUMN IF NOT EXISTS {_col};")
        except Exception: pass
    r = db_q("SELECT COUNT(*) as n FROM exercises")
    t = db_q("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
    return "Banco inicializado!\nTabelas: " + ", ".join(x["table_name"] for x in t) + "\nExercicios: " + str(r[0]["n"])

@mcp.tool()
def verificar_lembretes() -> str:
    """Verifica lembretes pendentes — se ha mais de 7 dias sem registrar peso."""
    rows = db_q("SELECT MAX(measurement_date) as last FROM body_metrics")
    last = rows[0]["last"] if rows and rows[0]["last"] else None
    if last is None: return "[ALTA] Nenhum registro de peso ou cintura. Regista as tuas metricas corporais!"
    diff = (datetime.now(timezone.utc).date() - last).days
    if diff > 7: return f"[ALTA] Ultimo registro foi ha {diff} dias ({last}). Hora de medir!"
    return "Nenhum lembrete pendente."

@mcp.tool()
def registrar_refeicao(
    descricao: str,
    tipo: str = None, horario: str = None, seguiu_plano: bool = None, notas: str = None,
    calorias: float = None, proteina_g: float = None, carbs_g: float = None,
    gordura_g: float = None, fibra_g: float = None,
    calcio_mg: float = None, ferro_mg: float = None, magnesio_mg: float = None,
    potassio_mg: float = None, sodio_mg: float = None,
    vitamina_c_mg: float = None, vitamina_d_mcg: float = None,
    vitamina_b12_mcg: float = None, zinco_mg: float = None,
) -> str:
    """Registra refeicao. Macros/micros opcionais — enviar valores calculados pelo Claude. tipo: cafe_manha|almoco|lanche|jantar|ceia|pre_treino|outro"""
    mt = None
    if horario:
        mt = horario if ("T" in horario or "-" in horario) else _hoje() + "T" + horario + ":00"
    rid = db_e(
        "INSERT INTO meals (meal_time,meal_type,description,is_on_plan,notes,"
        "calories,protein_g,carbs_g,fat_g,fiber_g,calcium_mg,iron_mg,magnesium_mg,"
        "potassium_mg,sodium_mg,vitamin_c_mg,vitamin_d_mcg,vitamin_b12_mcg,zinc_mg) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        [mt, tipo, descricao, seguiu_plano, notas,
         calorias, proteina_g, carbs_g, gordura_g, fibra_g,
         calcio_mg, ferro_mg, magnesio_mg, potassio_mg, sodio_mg,
         vitamina_c_mg, vitamina_d_mcg, vitamina_b12_mcg, zinco_mg]
    )
    partes = [f"Refeicao registrada (ID {rid})"]
    if calorias is not None:
        partes.append(f"{calorias:.0f} kcal | Prot:{proteina_g or 0:.1f}g | Carbs:{carbs_g or 0:.1f}g | Gord:{gordura_g or 0:.1f}g")
    else:
        partes.append("Macros nao informados.")
    return "\n".join(partes)

@mcp.tool()
def atualizar_refeicao(
    id: int,
    descricao: str = None, tipo: str = None, horario: str = None,
    seguiu_plano: bool = None, notas: str = None,
    calorias: float = None, proteina_g: float = None, carbs_g: float = None,
    gordura_g: float = None, fibra_g: float = None,
    calcio_mg: float = None, ferro_mg: float = None, magnesio_mg: float = None,
    potassio_mg: float = None, sodio_mg: float = None,
    vitamina_c_mg: float = None, vitamina_d_mcg: float = None,
    vitamina_b12_mcg: float = None, zinco_mg: float = None,
) -> str:
    """Atualiza campos de uma refeicao existente por ID. Apenas campos informados sao alterados."""
    campos = {
        "description": descricao, "meal_type": tipo, "is_on_plan": seguiu_plano, "notes": notas,
        "calories": calorias, "protein_g": proteina_g, "carbs_g": carbs_g, "fat_g": gordura_g,
        "fiber_g": fibra_g, "calcium_mg": calcio_mg, "iron_mg": ferro_mg,
        "magnesium_mg": magnesio_mg, "potassium_mg": potassio_mg, "sodium_mg": sodio_mg,
        "vitamin_c_mg": vitamina_c_mg, "vitamin_d_mcg": vitamina_d_mcg,
        "vitamin_b12_mcg": vitamina_b12_mcg, "zinc_mg": zinco_mg,
    }
    if horario:
        campos["meal_time"] = horario if ("T" in horario or "-" in horario) else _hoje() + "T" + horario + ":00"
    campos = {k: v for k, v in campos.items() if v is not None}
    if not campos:
        return "Nenhum campo para atualizar."
    sets = ", ".join(f"{k}=%s" for k in campos)
    n = db_e(f"UPDATE meals SET {sets} WHERE id=%s", list(campos.values()) + [id])
    return f"Refeicao ID {id} nao encontrada." if n == 0 else f"Refeicao ID {id} atualizada ({len(campos)} campo(s))."

@mcp.tool()
def listar_refeicoes(data: str = None, data_inicio: str = None, data_fim: str = None) -> str:
    """Lista refeicoes filtrando por meal_time. data: dia exato; data_inicio/data_fim: range. Padrao: hoje."""
    sel = (
        "SELECT id, meal_time AT TIME ZONE 'Europe/Lisbon' as t, meal_type, description, "
        "is_on_plan, calories, protein_g, carbs_g, fat_g, fiber_g, "
        "calcium_mg, iron_mg, magnesium_mg, potassium_mg, sodium_mg, "
        "vitamin_c_mg, vitamin_d_mcg, vitamin_b12_mcg, zinc_mg, notes "
        "FROM meals "
    )
    if data:
        rows = db_q(sel + "WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date = %s ORDER BY meal_time", [data])
        label = data
    else:
        inicio = data_inicio or _hoje()
        fim = data_fim or inicio
        rows = db_q(sel + "WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s ORDER BY meal_time", [inicio, fim])
        label = inicio if inicio == fim else f"{inicio} a {fim}"
    if not rows:
        return f"Nenhuma refeicao em {label}."
    linhas = [f"Refeicoes {label} ({len(rows)} registros):"]
    for r in rows:
        h = r["t"].strftime("%H:%M") if r["t"] else "--:--"
        kcal = f"{float(r['calories']):.0f}kcal" if r["calories"] else "?"
        prot = f"{float(r['protein_g']):.1f}g" if r["protein_g"] else "?"
        carbs = f"{float(r['carbs_g']):.1f}g" if r["carbs_g"] else "?"
        fat = f"{float(r['fat_g']):.1f}g" if r["fat_g"] else "?"
        linhas.append(f"ID:{r['id']} {h} [{r['meal_type'] or '-'}] {r['description']}")
        linhas.append(f"  {kcal} | P:{prot} | C:{carbs} | G:{fat}")
    return "\n".join(linhas)

@mcp.tool()
def resumo_diario(data: str = None) -> str:
    """Totais nutricionais de um dia (padrao: hoje)."""
    d = data or _hoje()
    r = db_q(
        "SELECT COUNT(*) as n, "
        "COALESCE(SUM(calories),0) as cal, COALESCE(SUM(protein_g),0) as prot, "
        "COALESCE(SUM(carbs_g),0) as carbs, COALESCE(SUM(fat_g),0) as fat, "
        "COALESCE(SUM(fiber_g),0) as fiber "
        "FROM meals "
        "WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date=%s "
        "OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date=%s)", [d, d])[0]
    m = METAS
    def pct(v, g): return f"({float(v)/g*100:.0f}%)" if g else ""
    return "\n".join([
        f"Resumo {d} ({r['n']} refeicoes)",
        f"Calorias: {float(r['cal']):.0f}/{m['cal']} kcal {pct(r['cal'], m['cal'])}",
        f"Proteina: {float(r['prot']):.1f}/{m['prot']}g {pct(r['prot'], m['prot'])}",
        f"Carbs:    {float(r['carbs']):.1f}/{m['carbs']}g {pct(r['carbs'], m['carbs'])}",
        f"Gordura:  {float(r['fat']):.1f}/{m['fat']}g {pct(r['fat'], m['fat'])}",
        f"Fibra:    {float(r['fiber']):.1f}/{m['fibra']}g {pct(r['fiber'], m['fibra'])}",
    ])

@mcp.tool()
def resumo_micronutrientes(dias: int = 7) -> str:
    """Totais e medias diarias de micronutrientes dos ultimos N dias."""
    fim = _hoje()
    inicio = (datetime.now(LISBOA) - timedelta(days=dias - 1)).strftime("%Y-%m-%d")
    r = db_q(
        "SELECT SUM(ca) as ca_t, AVG(ca) as ca_a, SUM(fe) as fe_t, AVG(fe) as fe_a, "
        "SUM(mg) as mg_t, AVG(mg) as mg_a, SUM(k) as k_t, AVG(k) as k_a, "
        "SUM(na) as na_t, AVG(na) as na_a, SUM(vitc) as vitc_t, AVG(vitc) as vitc_a, "
        "SUM(vitd) as vitd_t, AVG(vitd) as vitd_a, "
        "SUM(vitb12) as vitb12_t, AVG(vitb12) as vitb12_a, "
        "SUM(zn) as zn_t, AVG(zn) as zn_a, "
        "SUM(fibra) as fibra_t, AVG(fibra) as fibra_a FROM ("
        "  SELECT COALESCE(SUM(calcium_mg),0) as ca, COALESCE(SUM(iron_mg),0) as fe, "
        "  COALESCE(SUM(magnesium_mg),0) as mg, COALESCE(SUM(potassium_mg),0) as k, "
        "  COALESCE(SUM(sodium_mg),0) as na, COALESCE(SUM(vitamin_c_mg),0) as vitc, "
        "  COALESCE(SUM(vitamin_d_mcg),0) as vitd, COALESCE(SUM(vitamin_b12_mcg),0) as vitb12, "
        "  COALESCE(SUM(zinc_mg),0) as zn, COALESCE(SUM(fiber_g),0) as fibra "
        "  FROM meals "
        "  WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s "
        "  OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s) "
        "  GROUP BY COALESCE((meal_time AT TIME ZONE 'Europe/Lisbon')::date, "
        "           (logged_at AT TIME ZONE 'Europe/Lisbon')::date)"
        ") daily",
        [inicio, fim, inicio, fim])[0]
    f = lambda v: float(v or 0)
    m = METAS
    return "\n".join([
        f"Micronutrientes {inicio} a {fim} ({dias} dias)",
        f"Fibra:    total {f(r['fibra_t']):.1f}g    | media {f(r['fibra_a']):.1f}g/dia    | meta {m['fibra']}g",
        f"Calcio:   total {f(r['ca_t']):.0f}mg  | media {f(r['ca_a']):.0f}mg/dia  | meta {m['ca']}mg",
        f"Ferro:    total {f(r['fe_t']):.1f}mg  | media {f(r['fe_a']):.1f}mg/dia  | meta {m['fe']}mg",
        f"Magnesio: total {f(r['mg_t']):.0f}mg  | media {f(r['mg_a']):.0f}mg/dia  | meta {m['mg']}mg",
        f"Potassio: total {f(r['k_t']):.0f}mg  | media {f(r['k_a']):.0f}mg/dia  | meta {m['k']}mg",
        f"Sodio:    total {f(r['na_t']):.0f}mg  | media {f(r['na_a']):.0f}mg/dia  | meta {m['na']}mg",
        f"Vit C:    total {f(r['vitc_t']):.0f}mg  | media {f(r['vitc_a']):.0f}mg/dia  | meta {m['vit_c']}mg",
        f"Vit D:    total {f(r['vitd_t']):.1f}mcg | media {f(r['vitd_a']):.1f}mcg/dia | meta {m['vit_d']}mcg",
        f"Vit B12:  total {f(r['vitb12_t']):.1f}mcg | media {f(r['vitb12_a']):.1f}mcg/dia | meta {m['vit_b12']}mcg",
        f"Zinco:    total {f(r['zn_t']):.1f}mg  | media {f(r['zn_a']):.1f}mg/dia  | meta {m['zn']}mg",
    ])

@mcp.tool()
def verificar_alertas() -> str:
    """Retorna alertas de micronutrientes ativos e resolvidos nos ultimos 30 dias."""
    ativos = db_q(
        "SELECT id, nutrient, weeks_below, escalation_level, escalation_threshold, "
        "last_suggestion, first_flagged_at, last_reviewed_at "
        "FROM nutrient_alerts WHERE is_active = TRUE "
        "ORDER BY CASE escalation_level WHEN 'encaminhar' THEN 0 WHEN 'red_flag' THEN 1 "
        "WHEN 'reforco' THEN 2 ELSE 3 END, weeks_below DESC"
    )
    recentes = db_q(
        "SELECT id, nutrient, weeks_below, escalation_level, resolved_at "
        "FROM nutrient_alerts WHERE is_active = FALSE AND resolved_at >= CURRENT_DATE - 30 "
        "ORDER BY resolved_at DESC"
    )
    resultado = {
        "alertas_ativos": [dict(r) for r in ativos],
        "alertas_resolvidos_recentes": [dict(r) for r in recentes],
    }
    return json.dumps(resultado, default=str, ensure_ascii=False, indent=2)

@mcp.tool()
def registrar_alerta(nutrient: str, suggestion: str, avg_daily_intake: float, target: float) -> str:
    """Cria ou incrementa alerta de micronutriente. Chame quando analise semanal detecta valor fora da meta.
    Para sodio, chame quando avg_daily_intake > target. Para os demais, quando < target."""
    threshold = ESCALATION_THRESHOLDS.get(nutrient, 3)
    hoje = _hoje()
    existing = db_q(
        "SELECT id, weeks_below FROM nutrient_alerts WHERE nutrient=%s AND is_active=TRUE", [nutrient]
    )
    if existing:
        weeks = existing[0]["weeks_below"] + 1
        nivel = _nivel_escalacao(weeks, threshold)
        db_e(
            "UPDATE nutrient_alerts SET weeks_below=%s, escalation_level=%s, "
            "last_suggestion=%s, last_reviewed_at=%s WHERE id=%s",
            [weeks, nivel, suggestion, hoje, existing[0]["id"]]
        )
        msg = f"Alerta '{nutrient}' atualizado: semana {weeks}, nivel '{nivel}'"
        if nivel == "encaminhar":
            msg += " — ENCAMINHAR para nutricionista."
        elif nivel == "red_flag":
            msg += " — RED FLAG."
        return msg
    else:
        db_e(
            "INSERT INTO nutrient_alerts "
            "(nutrient, first_flagged_at, weeks_below, escalation_level, escalation_threshold, "
            "last_suggestion, last_reviewed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [nutrient, hoje, 1, "sugestao", threshold, suggestion, hoje]
        )
        return f"Alerta '{nutrient}' criado: semana 1, nivel 'sugestao'. Meta:{target}, media:{avg_daily_intake:.1f}."

@mcp.tool()
def resolver_alerta(nutrient: str) -> str:
    """Marca alerta de micronutriente como resolvido quando o nutriente volta a meta."""
    hoje = _hoje()
    n = db_e(
        "UPDATE nutrient_alerts SET is_active=FALSE, resolved_at=%s "
        "WHERE nutrient=%s AND is_active=TRUE",
        [hoje, nutrient]
    )
    if n == 0:
        return f"Nenhum alerta ativo para '{nutrient}'."
    return f"Alerta '{nutrient}' resolvido em {hoje}."

@mcp.tool()
def historico_treino(dias: int = 30) -> str:
    """Volume, frequencia e progressao de treino dos ultimos N dias."""
    fim = _hoje()
    inicio = (datetime.now(LISBOA) - timedelta(days=dias - 1)).strftime("%Y-%m-%d")
    stats = db_q(
        "SELECT COUNT(DISTINCT w.id) as treinos, COUNT(ws.id) as series, "
        "COALESCE(SUM(ws.weight_kg * ws.reps), 0) as volume "
        "FROM workouts w LEFT JOIN workout_sets ws ON ws.workout_id=w.id "
        "WHERE w.workout_date BETWEEN %s AND %s AND NOT COALESCE(w.skipped, false)",
        [inicio, fim])[0]
    top = db_q(
        "SELECT ws.exercise_name, COUNT(*) as series, MAX(ws.weight_kg) as carga_max "
        "FROM workout_sets ws JOIN workouts w ON w.id=ws.workout_id "
        "WHERE w.workout_date BETWEEN %s AND %s "
        "GROUP BY ws.exercise_name ORDER BY series DESC LIMIT 5",
        [inicio, fim])
    linhas = [
        f"Treinos {inicio} a {fim} ({dias} dias)",
        f"Sessoes: {stats['treinos']} | Series: {stats['series']} | Volume total: {float(stats['volume']):.0f}kg",
        "",
        "Top exercicios (por series):",
    ] + [f"  {r['exercise_name']}: {r['series']} series, carga max {r['carga_max']}kg" for r in top]
    return "\n".join(linhas)

@mcp.tool()
def distribuicao_proteina(data: str = None) -> str:
    """Distribuicao de proteina ao longo do dia. Calcula maior janela sem proteina (>5g)."""
    d = data or _hoje()
    rows = db_q(
        "SELECT meal_time AT TIME ZONE 'Europe/Lisbon' as t, meal_type, protein_g "
        "FROM meals "
        "WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date = %s AND COALESCE(protein_g, 0) > 5 "
        "ORDER BY meal_time", [d])
    total_prot = db_q(
        "SELECT COALESCE(SUM(protein_g), 0) as total FROM meals "
        "WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date = %s", [d])[0]["total"]
    anchor_start = datetime.fromisoformat(d + "T06:00:00").replace(tzinfo=LISBOA)
    anchor_end = datetime.now(LISBOA) if d == _hoje() else datetime.fromisoformat(d + "T23:59:00").replace(tzinfo=LISBOA)
    times = [r["t"] for r in rows if r["t"]]
    if not times:
        maior_janela = round((anchor_end - anchor_start).total_seconds() / 3600, 1)
    else:
        pontos = [anchor_start] + sorted(times) + [anchor_end]
        maior_janela = round(max(max((pontos[i+1] - pontos[i]).total_seconds() / 3600, 0) for i in range(len(pontos)-1)), 1)
    resultado = {
        "data": d,
        "total_proteina_g": round(float(total_prot), 1),
        "meta_proteina_g": METAS["prot"],
        "refeicoes": [{"horario": r["t"].strftime("%H:%M") if r["t"] else "--:--", "tipo": r["meal_type"], "proteina_g": round(float(r["protein_g"] or 0), 1)} for r in rows],
        "maior_janela_sem_proteina_horas": maior_janela,
        "refeicoes_com_30g_ou_mais": sum(1 for r in rows if float(r["protein_g"] or 0) >= 30),
    }
    return json.dumps(resultado, default=str, ensure_ascii=False, indent=2)

@mcp.tool()
def dias_suspeitos(dias: int = 7) -> str:
    """Detecta dias com possivel subnotificacao nos ultimos N dias."""
    fim = _hoje()
    inicio = (datetime.now(LISBOA) - timedelta(days=dias - 1)).strftime("%Y-%m-%d")
    rows = db_q(
        "SELECT (meal_time AT TIME ZONE 'Europe/Lisbon')::date as data, "
        "COUNT(*) as n, COALESCE(SUM(calories), 0) as kcal, "
        "EXTRACT(EPOCH FROM (MAX(meal_time) - MIN(meal_time)))/3600 as janela_h "
        "FROM meals WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s "
        "GROUP BY 1 ORDER BY 1", [inicio, fim])
    dias_com_dados = {str(r["data"]): r for r in rows}
    all_days = [(datetime.now(LISBOA) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(dias - 1, -1, -1)]
    suspeitos = []
    sem_registro = []
    dias_ok = 0
    for d in all_days:
        if d not in dias_com_dados:
            sem_registro.append(d)
            continue
        r = dias_com_dados[d]
        flags = []
        if float(r["kcal"]) < 1352: flags.append("kcal_baixa")
        if int(r["n"]) < 3: flags.append("poucas_refeicoes")
        if r["janela_h"] is not None and float(r["janela_h"]) < 6: flags.append("janela_curta")
        if flags:
            suspeitos.append({"data": d, "total_kcal": round(float(r["kcal"])), "num_refeicoes": int(r["n"]), "flags": flags})
        else:
            dias_ok += 1
    resultado = {
        "periodo": f"{inicio} a {fim}",
        "dias_suspeitos": suspeitos,
        "dias_ok": dias_ok,
        "dias_sem_registro": sem_registro,
    }
    return json.dumps(resultado, default=str, ensure_ascii=False, indent=2)

@mcp.tool()
def comparativo_semana_fds(semanas: int = 2) -> str:
    """Compara medias de macros entre dias uteis (seg-sex) e fim de semana (sab-dom)."""
    fim = _hoje()
    inicio = (datetime.now(LISBOA) - timedelta(days=semanas * 7 - 1)).strftime("%Y-%m-%d")
    rows = db_q(
        "SELECT tipo_dia, COUNT(*) as dias, "
        "AVG(kcal) as media_kcal, AVG(prot) as media_prot, "
        "AVG(carbs) as media_carbs, AVG(fat) as media_fat, AVG(na) as media_sodio "
        "FROM ("
        "  SELECT CASE WHEN EXTRACT(DOW FROM (meal_time AT TIME ZONE 'Europe/Lisbon')::date) IN (0,6) "
        "         THEN 'fds' ELSE 'util' END as tipo_dia, "
        "  (meal_time AT TIME ZONE 'Europe/Lisbon')::date as data, "
        "  COALESCE(SUM(calories),0) as kcal, COALESCE(SUM(protein_g),0) as prot, "
        "  COALESCE(SUM(carbs_g),0) as carbs, COALESCE(SUM(fat_g),0) as fat, "
        "  COALESCE(SUM(sodium_mg),0) as na "
        "  FROM meals WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s "
        "  GROUP BY tipo_dia, data"
        ") t GROUP BY tipo_dia", [inicio, fim])
    grupos = {r["tipo_dia"]: r for r in rows}
    def grp(g):
        if g not in grupos: return None
        r = grupos[g]
        return {"media_kcal": round(float(r["media_kcal"] or 0)), "media_proteina_g": round(float(r["media_prot"] or 0), 1), "media_carbs_g": round(float(r["media_carbs"] or 0), 1), "media_gordura_g": round(float(r["media_fat"] or 0), 1), "media_sodio_mg": round(float(r["media_sodio"] or 0)), "dias_com_registro": int(r["dias"])}
    u = grp("util")
    f = grp("fds")
    gap = round((float(grupos["fds"]["media_kcal"] or 0) - float(grupos["util"]["media_kcal"] or 0)) / max(float(grupos["util"]["media_kcal"] or 1), 1) * 100, 1) if "util" in grupos and "fds" in grupos else None
    resultado = {"periodo_semanas": semanas, "dias_uteis": u, "fim_de_semana": f, "gap_percentual_kcal": gap}
    return json.dumps(resultado, default=str, ensure_ascii=False, indent=2)

@mcp.tool()
def aderencia_treino(semanas: int = 2) -> str:
    """Compara treinos realizados vs planejados. Usa split_day para detectar grupos negligenciados."""
    plano = db_q("SELECT days_per_week_min, days_per_week_max, split_type, cardio_days FROM training_plan WHERE is_active=TRUE ORDER BY id DESC LIMIT 1")
    if not plano:
        return "Nenhum plano de treino ativo. Crie um na tabela training_plan."
    p = plano[0]
    split_grupos = {"PPL": {"push", "pull", "legs"}, "upper_lower": {"upper", "lower"}, "fullbody": {"fullbody"}}.get(p["split_type"], set())
    resultado_semanas = []
    for i in range(semanas - 1, -1, -1):
        seg = (datetime.now(LISBOA) - timedelta(days=datetime.now(LISBOA).weekday() + 7 * i)).date()
        dom = seg + timedelta(days=6)
        seg_s, dom_s = str(seg), str(dom)
        realizados = db_q("SELECT split_day FROM workouts WHERE workout_date BETWEEN %s AND %s AND NOT COALESCE(skipped, false)", [seg_s, dom_s])
        pulados = db_q("SELECT COUNT(*) as n FROM workouts WHERE workout_date BETWEEN %s AND %s AND COALESCE(skipped, false)", [seg_s, dom_s])[0]["n"]
        grupos_treinados = list({r["split_day"] for r in realizados if r["split_day"]})
        grupos_neg = sorted(split_grupos - set(grupos_treinados)) if split_grupos else []
        n_real = len(realizados)
        ader = min(round(n_real / max(p["days_per_week_min"], 1) * 100, 1), 100.0)
        resultado_semanas.append({"semana": f"{seg_s} a {dom_s}", "treinos_realizados": n_real, "treinos_pulados": int(pulados), "aderencia_pct": ader, "grupos_treinados": grupos_treinados, "grupos_negligenciados": grupos_neg})
    resultado = {"plano": {"min_por_semana": p["days_per_week_min"], "max_por_semana": p["days_per_week_max"], "split": p["split_type"]}, "semanas": resultado_semanas}
    return json.dumps(resultado, default=str, ensure_ascii=False, indent=2)

@mcp.tool()
def contexto_peso(data: str = None) -> str:
    """Contexto para interpretar uma medicao de peso: sodio/carbs recentes e medias moveis."""
    d = data or _hoje()
    atual = db_q("SELECT weight_kg, measurement_date FROM body_metrics WHERE measurement_date <= %s AND weight_kg IS NOT NULL ORDER BY measurement_date DESC LIMIT 1", [d])
    if not atual:
        return json.dumps({"data_pesagem": d, "peso_registrado_kg": None, "mensagem": "Nenhum peso registrado ate esta data."}, ensure_ascii=False)
    peso_data = str(atual[0]["measurement_date"])
    anterior = db_q("SELECT weight_kg, measurement_date FROM body_metrics WHERE measurement_date < %s AND weight_kg IS NOT NULL ORDER BY measurement_date DESC LIMIT 1", [peso_data])
    d3_ini = (datetime.fromisoformat(d + "T00:00:00").replace(tzinfo=LISBOA) - timedelta(days=3)).strftime("%Y-%m-%d")
    d3_fim = (datetime.fromisoformat(d + "T00:00:00").replace(tzinfo=LISBOA) - timedelta(days=1)).strftime("%Y-%m-%d")
    ctx = db_q("SELECT AVG(sodium_mg) as na, AVG(carbs_g) as carbs, AVG(calories) as kcal FROM meals WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s", [d3_ini, d3_fim])[0]
    d7_ini = (datetime.fromisoformat(d + "T00:00:00").replace(tzinfo=LISBOA) - timedelta(days=6)).strftime("%Y-%m-%d")
    d14_ini = (datetime.fromisoformat(d + "T00:00:00").replace(tzinfo=LISBOA) - timedelta(days=13)).strftime("%Y-%m-%d")
    p7 = db_q("SELECT weight_kg FROM body_metrics WHERE measurement_date BETWEEN %s AND %s AND weight_kg IS NOT NULL", [d7_ini, d])
    p14 = db_q("SELECT weight_kg FROM body_metrics WHERE measurement_date BETWEEN %s AND %s AND weight_kg IS NOT NULL", [d14_ini, d])
    mm7 = round(sum(float(x["weight_kg"]) for x in p7) / len(p7), 2) if len(p7) >= 2 else None
    mm14 = round(sum(float(x["weight_kg"]) for x in p14) / len(p14), 2) if len(p14) >= 3 else None
    na_avg = float(ctx["na"] or 0)
    resultado = {
        "data_pesagem": d, "peso_registrado_kg": float(atual[0]["weight_kg"]),
        "peso_anterior": {"data": str(anterior[0]["measurement_date"]), "peso_kg": float(anterior[0]["weight_kg"])} if anterior else None,
        "contexto_3_dias_anteriores": {
            "media_sodio_mg": round(na_avg), "meta_sodio_mg": METAS["na"],
            "sodio_acima_meta": na_avg > METAS["na"],
            "media_carbs_g": round(float(ctx["carbs"] or 0), 1),
            "media_kcal": round(float(ctx["kcal"] or 0)),
        },
        "media_movel_7d_kg": mm7,
        "media_movel_14d_kg": mm14,
    }
    return json.dumps(resultado, default=str, ensure_ascii=False, indent=2)

@mcp.tool()
def resumo_nutricional(data: str = None) -> str:
    """Totais do dia vs metas do plano da Helena."""
    d = data or _hoje()
    r = db_q(
        "SELECT COALESCE(SUM(calories),0) as cal,COALESCE(SUM(protein_g),0) as prot,COALESCE(SUM(carbs_g),0) as carbs,COALESCE(SUM(fat_g),0) as fat,"
        "COALESCE(SUM(calcium_mg),0) as ca,COALESCE(SUM(iron_mg),0) as fe,COALESCE(SUM(magnesium_mg),0) as mg_,COALESCE(SUM(potassium_mg),0) as k,"
        "COALESCE(SUM(vitamin_c_mg),0) as vitc,COALESCE(SUM(vitamin_d_mcg),0) as vitd,COALESCE(SUM(vitamin_b12_mcg),0) as vitb12,COALESCE(SUM(zinc_mg),0) as zn,"
        "COUNT(*) as total,COUNT(*) FILTER (WHERE is_on_plan) as on_plan FROM meals "
        "WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date=%s OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date=%s)", [d,d])[0]
    def p(v,g): return f"{float(v)/g*100:.0f}%"
    return "\n".join([f"Resumo — {d}",
        f"Calorias: {float(r['cal']):.0f}/{METAS['cal']} kcal ({p(r['cal'],METAS['cal'])})",
        f"Proteina: {float(r['prot']):.1f}/{METAS['prot']}g ({p(r['prot'],METAS['prot'])})",
        f"Carbs: {float(r['carbs']):.1f}/{METAS['carbs']}g ({p(r['carbs'],METAS['carbs'])})",
        f"Gordura: {float(r['fat']):.1f}/{METAS['fat']}g ({p(r['fat'],METAS['fat'])})",
        f"Ca:{float(r['ca']):.0f}/{METAS['ca']}mg Mg:{float(r['mg_']):.0f}/{METAS['mg']}mg Fe:{float(r['fe']):.1f}/{METAS['fe']}mg",
        f"Refeicoes: {r['total']} ({r['on_plan']} no plano)"])

@mcp.tool()
def registrar_metricas_corporais(
    peso_kg: float = None, cintura_cm: float = None, data: str = None, notas: str = None,
    height_cm: float = None, bmi: float = None,
    body_fat_pct: float = None, fat_mass_kg: float = None,
    fat_free_mass_kg: float = None, residual_mass_kg: float = None,
    body_density: float = None, sum_skinfolds_mm: float = None,
    waist_hip_ratio: float = None, arm_muscle_circ_cm: float = None,
    skinfold_triceps_mm: float = None, skinfold_biceps_mm: float = None,
    skinfold_abdominal_mm: float = None, skinfold_subscapular_mm: float = None,
    skinfold_midaxillary_mm: float = None, skinfold_thigh_mm: float = None,
    skinfold_chest_mm: float = None, skinfold_suprailiac_mm: float = None,
    circ_waist_cm: float = None, circ_hip_cm: float = None,
    circ_abdomen_cm: float = None, circ_arm_relaxed_cm: float = None,
    circ_thigh_medial_cm: float = None,
    bmi_class: str = None, metabolic_risk: str = None,
    body_fat_class: str = None, amc_class: str = None,
) -> str:
    """Registra peso/cintura (update caseiro) ou composicao corporal completa (bioimpedancia).
    Campos opcionais. circ_waist_cm e espelhado em waist_cm para manter a serie continua."""
    d = data or _hoje()
    campos = {
        "measurement_date": d,
        "weight_kg": peso_kg, "waist_cm": cintura_cm, "notes": notas,
        "height_cm": height_cm, "bmi": bmi,
        "body_fat_pct": body_fat_pct, "fat_mass_kg": fat_mass_kg,
        "fat_free_mass_kg": fat_free_mass_kg, "residual_mass_kg": residual_mass_kg,
        "body_density": body_density, "sum_skinfolds_mm": sum_skinfolds_mm,
        "waist_hip_ratio": waist_hip_ratio, "arm_muscle_circ_cm": arm_muscle_circ_cm,
        "skinfold_triceps_mm": skinfold_triceps_mm, "skinfold_biceps_mm": skinfold_biceps_mm,
        "skinfold_abdominal_mm": skinfold_abdominal_mm, "skinfold_subscapular_mm": skinfold_subscapular_mm,
        "skinfold_midaxillary_mm": skinfold_midaxillary_mm, "skinfold_thigh_mm": skinfold_thigh_mm,
        "skinfold_chest_mm": skinfold_chest_mm, "skinfold_suprailiac_mm": skinfold_suprailiac_mm,
        "circ_waist_cm": circ_waist_cm, "circ_hip_cm": circ_hip_cm,
        "circ_abdomen_cm": circ_abdomen_cm, "circ_arm_relaxed_cm": circ_arm_relaxed_cm,
        "circ_thigh_medial_cm": circ_thigh_medial_cm,
        "bmi_class": bmi_class, "metabolic_risk": metabolic_risk,
        "body_fat_class": body_fat_class, "amc_class": amc_class,
    }
    if circ_waist_cm is not None and cintura_cm is None:
        campos["waist_cm"] = circ_waist_cm
    campos = {k: v for k, v in campos.items() if v is not None}
    cols = ", ".join(campos.keys())
    vals = ", ".join(["%s"] * len(campos))
    rid = db_e(f"INSERT INTO body_metrics ({cols}) VALUES ({vals}) RETURNING id", list(campos.values()))
    waist_display = campos.get("waist_cm")
    parts = [x for x in [f"Peso:{peso_kg}kg" if peso_kg else None, f"Cintura:{waist_display}cm" if waist_display else None] if x]
    extra = len(campos) - 1 - len(parts)
    return f"Metricas (ID {rid}) em {d}: {' | '.join(parts) or 'sem peso/cintura'}" + (f" + {extra} campos adicionais" if extra > 0 else "")

@mcp.tool()
def registrar_treino(exercicios: list, data: str = None, tipo: str = None, local: str = None, notas: str = None, pulado: bool = False, motivo_pulo: str = None, energia: int = None, qualidade_sono: int = None, split_day: str = None) -> str:
    """Registra sessao de treino. exercicios: [{nome, series:[{reps,carga_kg,rpe,notas}], grupo_muscular?, equipamento?, alternativa_de?}]. split_day: 'push'|'pull'|'legs'|'cardio'"""
    d = data or _hoje()
    wid = db_e("INSERT INTO workouts (workout_date,workout_type,location,notes,skipped,skip_reason,energy_level,sleep_quality,split_day) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",[d,tipo,local,notas,pulado,motivo_pulo,energia,qualidade_sono,split_day])
    if pulado: return f"Treino pulado (ID {wid}). Motivo: {motivo_pulo or 'nao informado'}"
    sets = 0
    for ex in exercicios:
        nome = ex.get("nome") or ex.get("name","")
        existing = db_q("SELECT id FROM exercises WHERE LOWER(name)=LOWER(%s)",[nome])
        eid = existing[0]["id"] if existing else db_e("INSERT INTO exercises (name,muscle_group,equipment,is_active) VALUES (%s,%s,%s,true) RETURNING id",[nome,ex.get("grupo_muscular"),ex.get("equipamento")])
        for i,s in enumerate(ex.get("series",[]),1):
            db_e("INSERT INTO workout_sets (workout_id,exercise_id,exercise_name,set_number,reps,weight_kg,rpe,notes,is_alternative,alternative_for) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [wid,eid,nome,i,s.get("reps"),s.get("carga_kg"),s.get("rpe"),s.get("notas"),bool(ex.get("alternativa_de")),ex.get("alternativa_de")])
            sets += 1
    return f"Treino (ID {wid}) em {d} — {len(exercicios)} exercicios, {sets} series"

@mcp.tool()
def listar_treinos(data_inicio: str = None, data_fim: str = None) -> str:
    """Lista treinos de um periodo (padrao: ultimos 30 dias)."""
    hoje = _hoje()
    inicio = data_inicio or (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = db_q("SELECT w.workout_date,w.workout_type,w.skipped,COUNT(ws.id) as sets FROM workouts w LEFT JOIN workout_sets ws ON ws.workout_id=w.id WHERE w.workout_date BETWEEN %s AND %s GROUP BY w.id ORDER BY w.workout_date DESC",[inicio,data_fim or hoje])
    if not rows: return "Nenhum treino no periodo."
    return "\n".join(f"{r['workout_date']} [{r['workout_type'] or '-'}] {'Pulado' if r['skipped'] else str(r['sets'])+' series'}" for r in rows)

@mcp.tool()
def buscar_exercicios(termo: str) -> str:
    """Busca no catalogo por nome ou grupo muscular."""
    rows = db_q("SELECT name,muscle_group,equipment,difficulty FROM exercises WHERE is_active=true AND (LOWER(name) LIKE LOWER(%s) OR LOWER(COALESCE(muscle_group,'')) LIKE LOWER(%s)) ORDER BY muscle_group,name LIMIT 20",[f"%{termo}%",f"%{termo}%"])
    if not rows: return f"Nenhum exercicio para '{termo}'."
    return "\n".join(f"[{r['muscle_group'] or '-'}] {r['name']} | {r['equipment'] or '-'}" for r in rows)

@mcp.tool()
def progressao_exercicio(nome_exercicio: str) -> str:
    """Historico semanal de carga. Sinaliza plateau se 2+ semanas sem progressao."""
    rows = db_q("SELECT DATE_TRUNC('week',w.workout_date::timestamptz) as sem,MAX(ws.weight_kg) as kg,MAX(ws.reps) as reps FROM workout_sets ws JOIN workouts w ON w.id=ws.workout_id WHERE LOWER(ws.exercise_name) LIKE LOWER(%s) GROUP BY 1 ORDER BY 1 DESC LIMIT 8",[f"%{nome_exercicio}%"])
    if not rows: return f"Nenhum registo para '{nome_exercicio}'."
    linhas = [f"Progressao '{nome_exercicio}':"] + [f"  {r['sem'].strftime('%d/%m/%Y')}: {r['kg']}kg x {r['reps']} reps" for r in rows]
    if len(rows)>=2 and rows[0]["kg"]==rows[1]["kg"]: linhas.append("Plateau detectado.")
    return "\n".join(linhas)

@mcp.tool()
def gerar_resumo_diario(data: str = None, treinou: bool = None, notas_treino: str = None, agua_ml: int = None) -> str:
    """Gera e salva o daily_summary. agua_ml: estimativa informal de hidratacao (opcional)."""
    d = data or _hoje()
    r = db_q("SELECT COALESCE(SUM(calories),0) as cal,COALESCE(SUM(protein_g),0) as prot,COALESCE(SUM(carbs_g),0) as carbs,COALESCE(SUM(fat_g),0) as fat,COALESCE(SUM(fiber_g),0) as fiber,COALESCE(SUM(calcium_mg),0) as ca,COALESCE(SUM(iron_mg),0) as fe,COALESCE(SUM(magnesium_mg),0) as mg_,COALESCE(SUM(potassium_mg),0) as k,COALESCE(SUM(vitamin_c_mg),0) as vitc,COALESCE(SUM(vitamin_d_mcg),0) as vitd,COALESCE(SUM(vitamin_b12_mcg),0) as vitb12,COALESCE(SUM(zinc_mg),0) as zn,COUNT(*) as total,COUNT(*) FILTER (WHERE is_on_plan) as on_plan FROM meals WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date=%s OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date=%s)",[d,d])[0]
    adh = int(float(r["on_plan"])/max(float(r["total"]),1)*100)
    db_e("INSERT INTO daily_summary (summary_date,total_calories,total_protein_g,total_carbs_g,total_fat_g,total_fiber_g,calcium_mg,iron_mg,magnesium_mg,potassium_mg,vitamin_c_mg,vitamin_d_mcg,vitamin_b12_mcg,zinc_mg,meals_on_plan,meals_total,adherence_pct,trained,workout_notes,water_estimate_ml) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (summary_date) DO UPDATE SET total_calories=EXCLUDED.total_calories,total_protein_g=EXCLUDED.total_protein_g,adherence_pct=EXCLUDED.adherence_pct,trained=EXCLUDED.trained,water_estimate_ml=EXCLUDED.water_estimate_ml",
        [d,r["cal"],r["prot"],r["carbs"],r["fat"],r["fiber"],r["ca"],r["fe"],r["mg_"],r["k"],r["vitc"],r["vitd"],r["vitb12"],r["zn"],r["on_plan"],r["total"],adh,treinou,notas_treino,agua_ml])
    m = METAS
    linhas = [f"Resumo {d}",f"Calorias: {float(r['cal']):.0f}/{m['cal']} ({float(r['cal'])/m['cal']*100:.0f}%)",f"Proteina: {float(r['prot']):.1f}/{m['prot']}g",f"Aderencia: {adh}% ({r['on_plan']}/{r['total']})",f"Treino: {'Sim' if treinou else ('Nao' if treinou is False else '-')}"]
    if agua_ml: linhas.append(f"Agua: {agua_ml}ml registrado.")
    return "\n".join(linhas)

@mcp.tool()
def retrospectiva_semanal(data_domingo: str = None) -> str:
    """Analise semanal com medias e tendencias."""
    d = data_domingo or _hoje()
    rows = db_q("SELECT * FROM daily_summary WHERE summary_date BETWEEN (%s::date-INTERVAL '6 days') AND %s::date ORDER BY summary_date",[d,d])
    if not rows: return "Nenhum dado. Usa gerar_resumo_diario para cada dia primeiro."
    n = len(rows)
    avg = lambda f: sum(float(r.get(f) or 0) for r in rows)/n
    m = METAS
    return "\n".join([f"Retrospectiva ate {d} ({n}/7 dias)",f"Calorias media: {avg('total_calories'):.0f}/{m['cal']} ({avg('total_calories')/m['cal']*100:.0f}%)",f"Proteina media: {avg('total_protein_g'):.1f}/{m['prot']}g",f"Treinos: {sum(1 for r in rows if r.get('trained'))}/{n}",f"Aderencia media: {avg('adherence_pct'):.0f}%"])

@mcp.tool()
def inserir_dados_historicos() -> str:
    """Insere as 3 refeicoes do dia 08/06/2026 (viagem Napoles -> Lisboa)."""
    refeicoes = [
        ["2026-06-08T06:00:00Z","cafe_manha","2 ovos, 1 fatia de pao, bacon",453,27.5,14.5,28.3,0.8,54,1.7,16,258,592,0,2.3,1.4,1.4],
        ["2026-06-08T11:00:00Z","almoco","pizza napolitana individual",798,30,100,28,5,450,4.5,54,516,1800,6,0.3,0.9,3.6],
        ["2026-06-08T13:00:00Z","lanche","sorvete de limao (gelato)",200,3.5,35,5,0,100,0.1,10,150,50,2,0.2,0.1,0.3],
    ]
    ids = []
    for row in refeicoes:
        rid = db_e("INSERT INTO meals (meal_time,meal_type,description,is_on_plan,notes,calories,protein_g,carbs_g,fat_g,fiber_g,calcium_mg,iron_mg,magnesium_mg,potassium_mg,sodium_mg,vitamin_c_mg,vitamin_d_mcg,vitamin_b12_mcg,zinc_mg) VALUES (%s,%s,%s,false,'viagem em Napoles, Italia',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",row)
        ids.append(str(rid))
    return "Dados historicos inseridos (IDs: " + ", ".join(ids) + ")\n08/06/2026 — Napoles\n1451 kcal total"

@mcp.tool()
def executar_sql(sql: str) -> str:
    """Executa SQL ad-hoc no banco."""
    try:
        rows = db_q(sql)
        return json.dumps(rows, default=str, indent=2) if rows else "0 linhas."
    except Exception:
        try: return f"{db_e(sql)} linha(s) afetadas."
        except Exception as ex: return f"Erro: {ex}"

_auth_codes: dict = {}

@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_meta(request: Request) -> JSONResponse:
    proto = request.headers.get("x-forwarded-proto") or request.headers.get("x-forwarded-scheme") or "https"
    host = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or request.headers.get("host", "localhost")
    b = proto + "://" + host
    return JSONResponse({"issuer":b,"authorization_endpoint":b+"/oauth/authorize","token_endpoint":b+"/oauth/token","response_types_supported":["code"],"grant_types_supported":["authorization_code","client_credentials"],"code_challenge_methods_supported":["S256","plain"],"token_endpoint_auth_methods_supported":["client_secret_post","none"]})

@mcp.custom_route("/oauth/authorize", methods=["GET"])
async def oauth_auth(request: Request) -> Response:
    from urllib.parse import urlencode as _ue
    p = dict(request.query_params)
    redirect_uri = p.get("redirect_uri", "")
    state = p.get("state", "")
    if not redirect_uri:
        return Response("redirect_uri obrigatorio", status_code=400)
    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {"uri": redirect_uri, "state": state, "exp": time.time() + 300, "code_challenge": p.get("code_challenge",""), "code_challenge_method": p.get("code_challenge_method","plain")}
    params = {"code": code}
    if state: params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{_ue(params)}")

@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_tok(request: Request) -> JSONResponse:
    ct = request.headers.get("content-type","")
    if "json" in ct:
        body = await request.json()
    else:
        from urllib.parse import parse_qs
        raw = await request.body()
        body = {k:v[0] for k,v in parse_qs(raw.decode()).items()}
    grant = body.get("grant_type","")
    if grant == "authorization_code":
        import hashlib, base64 as _b64
        stored = _auth_codes.pop(body.get("code",""), None)
        if not stored or time.time() > stored["exp"]:
            return JSONResponse({"error":"invalid_grant"},status_code=400)
        verifier = body.get("code_verifier","")
        challenge = stored.get("code_challenge","")
        if challenge and verifier:
            method = stored.get("code_challenge_method","plain")
            if method == "S256":
                digest = hashlib.sha256(verifier.encode()).digest()
                expected = _b64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            else:
                expected = verifier
            if expected != challenge:
                return JSONResponse({"error":"invalid_grant"},status_code=400)
        return JSONResponse({"access_token":AUTH_TOKEN,"token_type":"Bearer","expires_in":86400})
    if grant == "client_credentials":
        cid = body.get("client_id","")
        csec = body.get("client_secret","")
        auth = request.headers.get("authorization","")
        if auth.lower().startswith("basic "):
            import base64 as _b64
            try:
                dec = _b64.b64decode(auth[6:]).decode()
                sep = dec.index(":")
                cid = cid or dec[:sep]; csec = csec or dec[sep+1:]
            except Exception: pass
        if cid != CLIENT_ID: return JSONResponse({"error":"invalid_client"},status_code=401)
        if csec != CLIENT_SECRET: return JSONResponse({"error":"invalid_client"},status_code=401)
        return JSONResponse({"access_token":AUTH_TOKEN,"token_type":"Bearer","expires_in":86400})
    return JSONResponse({"error":"unsupported_grant_type"},status_code=400)

_OPEN = {"/.well-known/oauth-authorization-server","/oauth/authorize","/oauth/token","/","/sse"}

class _Auth(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _OPEN or request.url.path.startswith("/messages"): return await call_next(request)
        if request.headers.get("authorization","") == "Bearer " + AUTH_TOKEN: return await call_next(request)
        return JSONResponse({"error":"unauthorized"},status_code=401,headers={"WWW-Authenticate":"Bearer"})

class _CombinedApp:
    def __init__(self, http_app, sse_app):
        self.http_app = http_app
        self.sse_app = sse_app
    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path == "/sse" or path.startswith("/messages"):
            await self.sse_app(scope, receive, send)
        else:
            await self.http_app(scope, receive, send)

if __name__ == "__main__":
    from starlette.middleware.cors import CORSMiddleware
    http_app = mcp.streamable_http_app()
    http_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    sse_app = mcp.sse_app()
    combined = _CombinedApp(http_app, sse_app)
    uvicorn.run(_Auth(combined), host="0.0.0.0", port=PORT)

