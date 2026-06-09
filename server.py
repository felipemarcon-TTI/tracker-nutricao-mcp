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
CLIENT_ID = os.environ.get("MCP_CLIENT_ID", "tracker-nutricao")
CLIENT_SECRET = os.environ.get("MCP_CLIENT_SECRET", "changeme")

mcp = FastMCP("Tracker Nutricao e Treino")

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

NUTRI = {
    "ovo": [78,6,0.5,5.3,0,25,0.6,6,69,62,0,1.1,0.6,0.6],
    "pao": [75,2.5,14,1,0.8,30,0.8,8,35,150,0,0,0,0.2],
    "bacon": [110,7,0.3,9,0,2,0.2,4,90,380,0,0.1,0.2,0.5],
    "pizza": [266,11,33,10,2,150,1.5,18,172,600,2,0.1,0.3,1.2],
    "queijo": [100,7,0.5,8,0,200,0.1,8,25,170,0,0.1,0.3,0.9],
    "frango": [165,31,0,3.6,0,15,1,28,300,74,0,0.1,0.3,1],
    "arroz": [130,2.7,28,0.3,0.4,10,0.2,12,35,1,0,0,0,0.5],
    "feijao": [135,8,24,0.5,7,40,2,45,400,5,1,0,0,1],
    "batata": [80,2,18,0.1,1.5,12,0.8,22,420,6,20,0,0,0.3],
    "massa": [150,5,30,1,1,12,1,18,44,5,0,0,0,0.5],
    "salmao": [200,25,0,11,0,15,0.5,32,490,59,3,11,3.2,0.6],
    "atum": [110,23,0,1.7,0,12,0.7,28,280,40,0,4,2,0.5],
    "carne": [215,22,0,13,0,12,2.5,20,290,60,0,0.1,2.4,4.5],
    "banana": [90,1.1,23,0.3,2.6,5,0.3,27,358,1,9,0,0,0.2],
    "aveia": [380,13,68,7,10,54,4.7,138,430,2,0,0,0,4],
    "iogurte": [130,12,6,6,0,150,0,12,160,60,0,0,0.5,0.5],
    "leite": [60,3.2,4.8,3.2,0,120,0.1,11,150,50,0,1.1,0.4,0.4],
    "whey": [120,24,3,1.5,0,120,0.5,30,160,80,0,0,0.5,1.2],
    "amendoim": [567,26,16,49,8.5,92,4.6,168,705,18,0,0,0,3.3],
    "sorvete": [200,3.5,35,5,0,100,0.1,10,150,50,2,0.2,0.1,0.3],
    "gelato": [200,3.5,35,5,0,100,0.1,10,150,50,2,0.2,0.1,0.3],
    "salada": [20,1.5,3,0.2,2,40,1,15,200,20,15,0,0,0.2],
    "brocolos": [35,2.4,7,0.4,2.6,47,0.7,21,316,33,90,0,0,0.4],
    "tapioca": [130,1,32,0.1,0.5,20,0.5,5,30,5,0,0,0,0.1],
    "cafe": [2,0.3,0.3,0,0,4,0.1,7,100,5,0,0,0,0],
    "chocolate": [540,6,60,31,4,50,3.5,65,400,20,0,0,0,1.5],
    "mel": [304,0.3,82,0,0.2,6,0.4,2,52,4,0.5,0,0,0.2],
    "cenoura": [41,0.9,10,0.2,2.8,33,0.3,12,320,69,6,0,0,0.2],
    "laranja": [47,0.9,12,0.1,2.4,40,0.1,10,181,0,53,0,0,0.1],
}

METAS = {"cal":2253,"prot":183.3,"carbs":231.9,"fat":70.7,
         "ca":1145,"mg":218,"fe":7.3,"k":3199,"vitc":39.9,"vitd":1.9,"vitb12":1.8,"zn":6.6}

def _estimar(desc):
    d = desc.lower()
    t = [0.0]*14
    ok = False
    for kw, v in NUTRI.items():
        if kw in d:
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:g|ml)?\s*" + re.escape(kw), d)
            mult = float(m.group(1).replace(",",".")) / 100 if m else 1.0
            for i in range(14): t[i] += v[i] * mult
            ok = True
    return [round(x,2) for x in t] if ok else None

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
def registrar_refeicao(descricao: str, tipo: str = None, horario: str = None, seguiu_plano: bool = None, notas: str = None) -> str:
    """Registra refeicao com estimativa de macros. tipo: cafe_manha|almoco|lanche|jantar|ceia|pre_treino|outro"""
    mt = None
    if horario:
        mt = horario if ("T" in horario or "-" in horario) else _hoje() + "T" + horario + ":00"
    m = _estimar(descricao)
    macros = m if m else [None]*14
    rid = db_e(
        "INSERT INTO meals (meal_time,meal_type,description,is_on_plan,notes,calories,protein_g,carbs_g,fat_g,fiber_g,calcium_mg,iron_mg,magnesium_mg,potassium_mg,sodium_mg,vitamin_c_mg,vitamin_d_mcg,vitamin_b12_mcg,zinc_mg) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        [mt,tipo,descricao,seguiu_plano,notas] + macros
    )
    linhas = [f"Refeicao registrada (ID {rid})"]
    if m: linhas.append(f"Estimativa: {m[0]:.0f} kcal | Prot: {m[1]:.1f}g | Carbs: {m[2]:.1f}g | Gord: {m[3]:.1f}g")
    else: linhas.append("Macros nao estimados — alimento nao reconhecido.")
    return "\n".join(linhas)

@mcp.tool()
def listar_refeicoes(data: str = None) -> str:
    """Lista refeicoes de um dia (padrao: hoje em Lisboa)."""
    d = data or _hoje()
    rows = db_q(
        "SELECT meal_time AT TIME ZONE 'Europe/Lisbon' as t, meal_type, description, is_on_plan, calories FROM meals "
        "WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date = %s OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date = %s) "
        "ORDER BY COALESCE(meal_time,logged_at)", [d,d])
    if not rows: return f"Nenhuma refeicao em {d}."
    linhas = [f"Refeicoes em {d}:"]
    for r in rows:
        h = r["t"].strftime("%H:%M") if r["t"] else "--:--"
        p = "sim" if r["is_on_plan"] else ("nao" if r["is_on_plan"] is False else "-")
        k = f"{float(r['calories']):.0f} kcal" if r["calories"] else "sem est."
        linhas.append(f"{h} [{r['meal_type'] or '-'}] {r['description']} | {k} | plano:{p}")
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
    return JSONResponse({"issuer":b,"authorization_endpoint":b+"/oauth/authorize","token_endpoint":b+"/oauth/token","registration_endpoint":b+"/oauth/register","response_types_supported":["code"],"grant_types_supported":["authorization_code","client_credentials"],"code_challenge_methods_supported":["S256","plain"]})

@mcp.custom_route("/oauth/register", methods=["POST"])
async def oauth_reg(request: Request) -> JSONResponse:
    try: body = await request.json()
    except Exception: body = {}
    return JSONResponse({"client_id":CLIENT_ID,"client_secret":CLIENT_SECRET,"client_id_issued_at":int(time.time()),"client_secret_expires_at":0,"redirect_uris":body.get("redirect_uris",[]),"grant_types":["authorization_code"],"response_types":["code"],"token_endpoint_auth_method":"client_secret_post"},status_code=201)

@mcp.custom_route("/oauth/authorize", methods=["GET"])
async def oauth_auth(request: Request) -> Response:
    p = dict(request.query_params)
    if p.get("client_id","") != CLIENT_ID: return Response("invalid client_id",status_code=400)
    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {"uri":p.get("redirect_uri",""),"state":p.get("state",""),"exp":time.time()+300}
    from urllib.parse import urlencode
    q = urlencode({"code":code,"redirect_uri":p.get("redirect_uri",""),"state":p.get("state","")})
    html = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Tracker Nutricao</title><style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f0f4ff}.c{background:white;padding:2rem;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.1);text-align:center;max-width:400px;width:90%}h2{margin:0 0 .5rem}p{color:#555;margin:0 0 1.5rem}.b{display:inline-block;background:#2563eb;color:white;text-decoration:none;padding:.75rem 2rem;border-radius:8px;font-size:1rem}</style></head><body><div class='c'><h2>Tracker Nutricao</h2><p>O Claude.ai pretende aceder ao teu tracker de nutricao e treino.</p><a class='b' href='/oauth/authorize/confirm?" + q + "'>Autorizar Acesso</a></div></body></html>"
    return HTMLResponse(html)

@mcp.custom_route("/oauth/authorize/confirm", methods=["GET"])
async def oauth_confirm(request: Request) -> Response:
    from urllib.parse import urlparse,urlencode,urlunparse,parse_qs
    p = dict(request.query_params)
    code = p.get("code","")
    info = _auth_codes.get(code)
    if not info or time.time() > info["exp"]: return Response("Codigo invalido",status_code=400)
    uri = p.get("redirect_uri") or info["uri"]
    state = p.get("state") or info["state"]
    try:
        parts = list(urlparse(uri))
        q = parse_qs(parts[4])
        q["code"] = [code]
        if state: q["state"] = [state]
        parts[4] = urlencode({k:v[0] for k,v in q.items()})
        return RedirectResponse(urlunparse(parts))
    except Exception: return Response("redirect_uri invalido",status_code=400)

@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_tok(request: Request) -> JSONResponse:
    ct = request.headers.get("content-type","")
    if "json" in ct:
        body = await request.json()
    else:
        from urllib.parse import parse_qs
        raw = await request.body()
        body = {k:v[0] for k,v in parse_qs(raw.decode()).items()}
    cid = body.get("client_id","")
    csec = body.get("client_secret","")
    auth = request.headers.get("authorization","")
    if auth.lower().startswith("basic "):
        import base64 as b64m
        try:
            dec = b64m.b64decode(auth[6:]).decode()
            sep = dec.index(":")
            cid = cid or dec[:sep]; csec = csec or dec[sep+1:]
        except Exception: pass
    grant = body.get("grant_type","")
    if cid != CLIENT_ID: return JSONResponse({"error":"invalid_client"},status_code=401)
    if grant == "authorization_code":
        return JSONResponse({"access_token":AUTH_TOKEN,"token_type":"Bearer","expires_in":31536000})
    if grant == "client_credentials":
        if csec != CLIENT_SECRET: return JSONResponse({"error":"invalid_client"},status_code=401)
        return JSONResponse({"access_token":AUTH_TOKEN,"token_type":"Bearer","expires_in":31536000})
    return JSONResponse({"error":"unsupported_grant_type"},status_code=400)

_OPEN = {"/.well-known/oauth-authorization-server","/oauth/register","/oauth/authorize","/oauth/authorize/confirm","/oauth/token","/"}

class _Auth(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _OPEN: return await call_next(request)
        if request.headers.get("authorization","") == "Bearer " + AUTH_TOKEN: return await call_next(request)
        return JSONResponse({"error":"unauthorized"},status_code=401,headers={"WWW-Authenticate":"Bearer"})

if __name__ == "__main__":
    from starlette.middleware.cors import CORSMiddleware
    app = mcp.sse_app()
    app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
    app.add_middleware(_Auth)
    uvicorn.run(app,host="0.0.0.0",port=PORT,proxy_headers=True,forwarded_allow_ips="*")