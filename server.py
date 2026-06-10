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
    "cal": 2253, "prot": 183.3, "carbs": 231.9, "fat": 70.7, "fibra": 17.7,
    "ca": 1145.1, "mg": 218.5, "fe": 7.3, "k": 3199.0, "na": 885.7,
    "vit_c": 39.9, "vit_d": 1.9, "vit_b12": 1.8, "zn": 6.6,
}

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
        "SUM(zn) as zn_t, AVG(zn) as zn_a FROM ("
        "  SELECT COALESCE(SUM(calcium_mg),0) as ca, COALESCE(SUM(iron_mg),0) as fe, "
        "  COALESCE(SUM(magnesium_mg),0) as mg, COALESCE(SUM(potassium_mg),0) as k, "
        "  COALESCE(SUM(sodium_mg),0) as na, COALESCE(SUM(vitamin_c_mg),0) as vitc, "
        "  COALESCE(SUM(vitamin_d_mcg),0) as vitd, COALESCE(SUM(vitamin_b12_mcg),0) as vitb12, "
        "  COALESCE(SUM(zinc_mg),0) as zn "
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
def registrar_metricas_corporais(peso_kg: float = None, cintura_cm: float = None, data: str = None, notas: str = None) -> str:
    """Registra peso (kg) e/ou cintura (cm)."""
    d = data or _hoje()
    rid = db_e("INSERT INTO body_metrics (measurement_date,weight_kg,waist_cm,notes) VALUES (%s,%s,%s,%s) RETURNING id",[d,peso_kg,cintura_cm,notas])
    parts = [x for x in [f"Peso:{peso_kg}kg" if peso_kg else None, f"Cintura:{cintura_cm}cm" if cintura_cm else None] if x]
    return f"Metricas (ID {rid}) em {d}: {' | '.join(parts)}"

@mcp.tool()
def registrar_treino(exercicios: list, data: str = None, tipo: str = None, local: str = None, notas: str = None, pulado: bool = False, motivo_pulo: str = None) -> str:
    """Registra sessao de treino. exercicios: [{nome, series:[{reps,carga_kg,rpe,notas}], grupo_muscular?, equipamento?, alternativa_de?}]"""
    d = data or _hoje()
    wid = db_e("INSERT INTO workouts (workout_date,workout_type,location,notes,skipped,skip_reason) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",[d,tipo,local,notas,pulado,motivo_pulo])
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
def gerar_resumo_diario(data: str = None, treinou: bool = None, notas_treino: str = None) -> str:
    """Gera e salva o daily_summary."""
    d = data or _hoje()
    r = db_q("SELECT COALESCE(SUM(calories),0) as cal,COALESCE(SUM(protein_g),0) as prot,COALESCE(SUM(carbs_g),0) as carbs,COALESCE(SUM(fat_g),0) as fat,COALESCE(SUM(fiber_g),0) as fiber,COALESCE(SUM(calcium_mg),0) as ca,COALESCE(SUM(iron_mg),0) as fe,COALESCE(SUM(magnesium_mg),0) as mg_,COALESCE(SUM(potassium_mg),0) as k,COALESCE(SUM(vitamin_c_mg),0) as vitc,COALESCE(SUM(vitamin_d_mcg),0) as vitd,COALESCE(SUM(vitamin_b12_mcg),0) as vitb12,COALESCE(SUM(zinc_mg),0) as zn,COUNT(*) as total,COUNT(*) FILTER (WHERE is_on_plan) as on_plan FROM meals WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date=%s OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date=%s)",[d,d])[0]
    adh = int(float(r["on_plan"])/max(float(r["total"]),1)*100)
    db_e("INSERT INTO daily_summary (summary_date,total_calories,total_protein_g,total_carbs_g,total_fat_g,total_fiber_g,calcium_mg,iron_mg,magnesium_mg,potassium_mg,vitamin_c_mg,vitamin_d_mcg,vitamin_b12_mcg,zinc_mg,meals_on_plan,meals_total,adherence_pct,trained,workout_notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (summary_date) DO UPDATE SET total_calories=EXCLUDED.total_calories,total_protein_g=EXCLUDED.total_protein_g,adherence_pct=EXCLUDED.adherence_pct,trained=EXCLUDED.trained",
        [d,r["cal"],r["prot"],r["carbs"],r["fat"],r["fiber"],r["ca"],r["fe"],r["mg_"],r["k"],r["vitc"],r["vitd"],r["vitb12"],r["zn"],r["on_plan"],r["total"],adh,treinou,notas_treino])
    m = METAS
    return "\n".join([f"Resumo {d}",f"Calorias: {float(r['cal']):.0f}/{m['cal']} ({float(r['cal'])/m['cal']*100:.0f}%)",f"Proteina: {float(r['prot']):.1f}/{m['prot']}g",f"Aderencia: {adh}% ({r['on_plan']}/{r['total']})",f"Treino: {'Sim' if treinou else ('Nao' if treinou is False else '-')}"])

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
