import os, re, json, secrets, time, pathlib, threading, unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    LISBOA = ZoneInfo("Europe/Lisbon")
except Exception:
    LISBOA = timezone.utc
USER_TZ = LISBOA  # fuso do usuario para localizar horarios ingenuos antes de gravar

import psycopg2, psycopg2.extras, psycopg2.pool, psycopg2.extensions
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
    "cal": 1902, "prot": 185.5, "carbs": 194.8, "fat": 46.5, "fibra": 25.8,
    "ca": 1000, "mg": 420, "fe": 8, "k": 3400, "na": 885.7,
    "vit_c": 90, "vit_d": 15.0, "vit_b12": 2.4, "zn": 11,
}  # Plano Alimentar 5 (vigente desde 26/08/2026). Fallback de _metas_em() se plan_targets estiver vazia.

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

def _dsn() -> str:
    url = DATABASE_URL
    if "railway" in url and "sslmode" not in url:
        url += "?sslmode=require"
    return url

_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    """Pool lazy. O dashboard dispara dezenas de queries por request; abrir uma
    conexao TCP+TLS por query (comportamento anterior) nao escala."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    1, int(os.environ.get("DB_POOL_MAX", "8")), _dsn(),
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
    return _pool

@contextmanager
def _conn():
    p = _get_pool()
    c = p.getconn()
    try:
        yield c
    finally:
        # putconn faz rollback se a transacao nao estiver idle
        p.putconn(c)

def _db():
    """Compat: conexao avulsa fora do pool. Mantido para nao quebrar chamadas externas."""
    return psycopg2.connect(_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)

def _exec(cur, sql, params):
    """params vazio => execute SEM segundo argumento.

    Passar [] fazia o psycopg2 tratar qualquer '%' do SQL como placeholder,
    rebentando com 'IndexError: list index out of range' em qualquer
    LIKE '%x%'. Este era o bug do executar_sql.
    """
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)

def db_q(sql, params=None):
    with _conn() as c:
        with c.cursor() as cur:
            _exec(cur, sql, params)
            return [dict(r) for r in cur.fetchall()]

def db_e(sql, params=None):
    with _conn() as c:
        try:
            # cursor de tuplos: o fetchone()[0] abaixo depende disso
            with c.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
                _exec(cur, sql, params)
                rc = cur.rowcount  # capturar antes do commit (commit invalida o rowcount -> -1)
                try:
                    val = cur.fetchone()[0]  # statements com RETURNING
                except Exception:
                    val = rc
                c.commit()
                return val
        except Exception:
            c.rollback()
            raise

def db_tx(statements):
    """Executa varios statements numa unica transacao. statements: [(sql, params), ...].
    Devolve a lista de rowcounts. Rollback total em qualquer erro."""
    with _conn() as c:
        try:
            out = []
            with c.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
                for sql, params in statements:
                    _exec(cur, sql, params)
                    out.append(cur.rowcount)
            c.commit()
            return out
        except Exception:
            c.rollback()
            raise

def _hoje(): return datetime.now(LISBOA).strftime("%Y-%m-%d")

def _metas_em(data: str = None) -> dict:
    """Metas de macro (cal/prot/carbs/fat/fibra) vigentes na data informada, lidas de
    plan_targets por effective_from. Sem linha <= data -> usa o plano mais antigo cadastrado.
    Tabela vazia -> cai no dict METAS (micros do METAS sempre se aplicam, nao mudam por plano)."""
    d = data or _hoje()
    rows = db_q("SELECT calories,protein_g,carbs_g,fat_g,fiber_g FROM plan_targets "
                "WHERE effective_from <= %s ORDER BY effective_from DESC LIMIT 1", [d])
    if not rows:
        rows = db_q("SELECT calories,protein_g,carbs_g,fat_g,fiber_g FROM plan_targets "
                    "ORDER BY effective_from ASC LIMIT 1")
    if not rows:
        return dict(METAS)
    r = rows[0]
    m = dict(METAS)
    m.update({"cal": float(r["calories"]), "prot": float(r["protein_g"]), "carbs": float(r["carbs_g"]),
               "fat": float(r["fat_g"]), "fibra": float(r["fiber_g"])})
    return m

def _parse_horario(horario: str):
    """Converte o horario recebido em datetime tz-aware para gravar em meal_time.

    Sem tzinfo explicito no input -> localiza em USER_TZ (Europe/Lisbon) antes de
    converter, em vez de deixar o Postgres assumir o fuso da sessao (UTC). Isso corrige
    o bug em que refeicoes registradas entre 23:00-23:59 (hora local) apareciam no dia
    seguinte nos agregados: a string ingenua era gravada como se ja fosse UTC, adiantando
    o instante em 1h (Lisboa = UTC+1 no horario de verao) e cruzando a meia-noite local
    na hora de converter de volta para exibir/agrupar por dia.
    Com offset explicito no input (Z ou +HH:MM), o offset informado e respeitado."""
    s = horario if ("T" in horario or "-" in horario) else _hoje() + "T" + horario + ":00"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=USER_TZ)
    return dt

def slug(nome: str) -> str:
    """lowercase, sem acentos, nao-alfanumerico -> underscore.
    'Elevacao lateral' e 'Elevação lateral' colapsam no mesmo slug.
    'Supino Reto' e 'Supino reto com halteres' continuam distintos (de proposito)."""
    if not nome:
        return ""
    s = unicodedata.normalize("NFKD", nome)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", s.lower().strip()).strip("_")

# Peso da barra descarregada, por slug de exercicio. Usado para normalizar
# cargas anotadas 'por_lado' (anilhas de cada lado, sem a barra).
PESO_BARRA_KG = {
    "remada_curvada_na_barra": 20.0,
    "rosca_direta_barra_w": 7.5,
}

def carga_total(weight_kg, unidade: str = "total", exercise_slug: str = "") -> float:
    """Normaliza a carga registada para carga total movida.

    por_lado -> anilhas de cada lado + barra   (remada curvada 15kg/lado = 20 + 30 = 50kg)
    por_mao  -> halteres, um em cada mao       (supino inclinado 20kg/mao = 40kg)
    total    -> ja e o valor final
    """
    if weight_kg is None:
        return 0.0
    w = float(weight_kg)
    if unidade == "por_lado":
        return w * 2 + PESO_BARRA_KG.get(exercise_slug, 0.0)
    if unidade == "por_mao":
        return w * 2
    return w

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
    # Migration v3 -- estado de coccao / base do peso da porcao (metadado de auditoria, idempotente)
    for _col in ["cooking_state VARCHAR(20)", "portion_weight_g NUMERIC(7,2)", "portion_basis VARCHAR(20)"]:
        try: db_e(f"ALTER TABLE meals ADD COLUMN IF NOT EXISTS {_col};")
        except Exception: pass
    # Migration v4 -- Plano Alimentar 5, folato, suplementos, extensao body_metrics (idempotente)
    try: db_e("ALTER TABLE meals ADD COLUMN IF NOT EXISTS folate_mcg NUMERIC(7,2);")
    except Exception: pass
    for _col in ["circ_arm_flexed_cm NUMERIC(5,2)", "circ_forearm_cm NUMERIC(5,2)", "source VARCHAR(30)"]:
        try: db_e(f"ALTER TABLE body_metrics ADD COLUMN IF NOT EXISTS {_col};")
        except Exception: pass
    try:
        db_e(
            "CREATE TABLE IF NOT EXISTS supplement_log ("
            "id SERIAL PRIMARY KEY, taken_at TIMESTAMPTZ NOT NULL, supplement VARCHAR NOT NULL, "
            "dose_amount NUMERIC, dose_unit VARCHAR, prescribed_by VARCHAR, notes TEXT, "
            "created_at TIMESTAMPTZ DEFAULT NOW());"
        )
    except Exception: pass
    try:
        db_e(
            "CREATE TABLE IF NOT EXISTS plan_targets ("
            "id SERIAL PRIMARY KEY, effective_from DATE NOT NULL UNIQUE, plan_name VARCHAR(50), "
            "calories NUMERIC(7,2) NOT NULL, protein_g NUMERIC(6,2) NOT NULL, carbs_g NUMERIC(6,2) NOT NULL, "
            "fat_g NUMERIC(6,2) NOT NULL, fiber_g NUMERIC(6,2) NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW());"
        )
        db_e(
            "INSERT INTO plan_targets (effective_from, plan_name, calories, protein_g, carbs_g, fat_g, fiber_g) VALUES "
            "('2026-06-17','Plano Alimentar 4',1721,170.9,146.4,53.4,32.6), "
            "('2026-08-26','Plano Alimentar 5',1902,185.5,194.8,46.5,25.8) "
            "ON CONFLICT (effective_from) DO NOTHING;"
        )
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
    vitamina_b12_mcg: float = None, zinco_mg: float = None, folato_mcg: float = None,
    estado_coccao: str = None, peso_porcao_g: float = None, base_peso: str = None,
) -> str:
    """Registra refeicao. Macros/micros opcionais — enviar valores calculados pelo Claude. tipo: cafe_manha|almoco|lanche|jantar|ceia|pre_treino|outro

    ESTADO DE COCCAO (metadado de auditoria — o servidor so armazena, NAO converte):
      estado_coccao: 'cru' | 'cozido' | 'congelado_glaze' | 'assado' | None(desconhecido)
      peso_porcao_g: peso relatado pelo usuario (g)
      base_peso:     'peso_cru' | 'peso_cozido' | 'peso_congelado'

    REGRA DE CALCULO (vale para o cliente/Claude ao calcular os macros, ANTES de enviar):
      - Proteina (carne/frango/peixe/camarao) PERDE agua ao cozinhar: 100g cru -> ~70-80g cozido,
        proteina/100g sobe no cozido. Peso CRU -> tabela cru; peso COZIDO -> tabela cozido.
      - Amido (arroz/macarrao/batata/leguminosas) ABSORVE agua: 100g cru -> ~250-300g cozido
        (batata incha menos), carbo/100g cai no cozido. Peso CRU -> tabela cru; COZIDO -> tabela cozido.
      - Congelado com glaze (camarao/peixe): descontar ~10-20% de gelo antes de calcular.
      - Se o usuario nao especificar cru/cozido: perguntar ou assumir o padrao mais provavel do
        alimento e REGISTRAR a suposicao em estado_coccao/base_peso."""
    mt = _parse_horario(horario) if horario else None
    rid = db_e(
        "INSERT INTO meals (meal_time,meal_type,description,is_on_plan,notes,"
        "calories,protein_g,carbs_g,fat_g,fiber_g,calcium_mg,iron_mg,magnesium_mg,"
        "potassium_mg,sodium_mg,vitamin_c_mg,vitamin_d_mcg,vitamin_b12_mcg,zinc_mg,folate_mcg,"
        "cooking_state,portion_weight_g,portion_basis) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        [mt, tipo, descricao, seguiu_plano, notas,
         calorias, proteina_g, carbs_g, gordura_g, fibra_g,
         calcio_mg, ferro_mg, magnesio_mg, potassio_mg, sodio_mg,
         vitamina_c_mg, vitamina_d_mcg, vitamina_b12_mcg, zinco_mg, folato_mcg,
         estado_coccao, peso_porcao_g, base_peso]
    )
    partes = [f"Refeicao registrada (ID {rid})"]
    if calorias is not None:
        partes.append(f"{calorias:.0f} kcal | Prot:{proteina_g or 0:.1f}g | Carbs:{carbs_g or 0:.1f}g | Gord:{gordura_g or 0:.1f}g")
    else:
        partes.append("Macros nao informados.")
    if estado_coccao or base_peso or peso_porcao_g is not None:
        peso = f"{peso_porcao_g:.0f}g " if peso_porcao_g is not None else ""
        partes.append(f"Coccao: {peso}[{estado_coccao or '?'} / {base_peso or '?'}]")
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
    vitamina_b12_mcg: float = None, zinco_mg: float = None, folato_mcg: float = None,
    estado_coccao: str = None, peso_porcao_g: float = None, base_peso: str = None,
) -> str:
    """Atualiza campos de uma refeicao existente por ID. Apenas campos informados sao alterados.
    Via confiavel para corrigir uma refeicao (ex: proteina 42g -> 34g): faz UPDATE com commit
    e reflete em resumo_diario/gerar_resumo_diario. Para SQL ad-hoc use executar_sql (writes commitam).
    estado_coccao/base_peso/peso_porcao_g: metadado de coccao (ver registrar_refeicao)."""
    campos = {
        "description": descricao, "meal_type": tipo, "is_on_plan": seguiu_plano, "notes": notas,
        "calories": calorias, "protein_g": proteina_g, "carbs_g": carbs_g, "fat_g": gordura_g,
        "fiber_g": fibra_g, "calcium_mg": calcio_mg, "iron_mg": ferro_mg,
        "magnesium_mg": magnesio_mg, "potassium_mg": potassio_mg, "sodium_mg": sodio_mg,
        "vitamin_c_mg": vitamina_c_mg, "vitamin_d_mcg": vitamina_d_mcg,
        "vitamin_b12_mcg": vitamina_b12_mcg, "zinc_mg": zinco_mg, "folate_mcg": folato_mcg,
        "cooking_state": estado_coccao, "portion_weight_g": peso_porcao_g, "portion_basis": base_peso,
    }
    if horario:
        campos["meal_time"] = _parse_horario(horario)
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
        "vitamin_c_mg, vitamin_d_mcg, vitamin_b12_mcg, zinc_mg, notes, "
        "cooking_state, portion_weight_g, portion_basis "
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
        linha_macros = f"  {kcal} | P:{prot} | C:{carbs} | G:{fat}"
        if r.get("cooking_state") or r.get("portion_basis") or r.get("portion_weight_g") is not None:
            peso = f"{float(r['portion_weight_g']):.0f}g " if r.get("portion_weight_g") is not None else ""
            linha_macros += f" | coccao: {peso}[{r.get('cooking_state') or '?'}/{r.get('portion_basis') or '?'}]"
        linhas.append(linha_macros)
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
    m = _metas_em(d)
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
        "SUM(folato) as folato_t, AVG(folato) as folato_a, "
        "SUM(fibra) as fibra_t, AVG(fibra) as fibra_a FROM ("
        "  SELECT COALESCE(SUM(calcium_mg),0) as ca, COALESCE(SUM(iron_mg),0) as fe, "
        "  COALESCE(SUM(magnesium_mg),0) as mg, COALESCE(SUM(potassium_mg),0) as k, "
        "  COALESCE(SUM(sodium_mg),0) as na, COALESCE(SUM(vitamin_c_mg),0) as vitc, "
        "  COALESCE(SUM(vitamin_d_mcg),0) as vitd, COALESCE(SUM(vitamin_b12_mcg),0) as vitb12, "
        "  COALESCE(SUM(zinc_mg),0) as zn, COALESCE(SUM(folate_mcg),0) as folato, "
        "  COALESCE(SUM(fiber_g),0) as fibra "
        "  FROM meals "
        "  WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s "
        "  OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s) "
        "  GROUP BY COALESCE((meal_time AT TIME ZONE 'Europe/Lisbon')::date, "
        "           (logged_at AT TIME ZONE 'Europe/Lisbon')::date)"
        ") daily",
        [inicio, fim, inicio, fim])[0]
    f = lambda v: float(v or 0)
    m = _metas_em(fim)
    return "\n".join([
        f"Micronutrientes {inicio} a {fim} ({dias} dias)",
        f"Fibra:    total {f(r['fibra_t']):.1f}g    | media {f(r['fibra_a']):.1f}g/dia    | meta {m['fibra']}g",
        f"Folato:   total {f(r['folato_t']):.0f}mcg | media {f(r['folato_a']):.0f}mcg/dia | meta 400mcg",
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
        "meta_proteina_g": _metas_em(d)["prot"],
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
    # Media por DIA (soma diaria, depois media entre dias) — nao media por linha de refeicao.
    # AVG(sodium_mg) direto sobre `meals` diluia pelo numero de refeicoes/dia (~4,2x), gerando
    # medias ~4x menores que o real e invertendo sodio_acima_meta em dias de excesso.
    ctx = db_q(
        "WITH totais_dia AS ("
        "  SELECT (meal_time AT TIME ZONE 'Europe/Lisbon')::date AS dia, "
        "         SUM(sodium_mg) AS sodio, SUM(calories) AS kcal, SUM(carbs_g) AS carbs "
        "  FROM meals "
        "  WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s "
        "  GROUP BY dia"
        ") SELECT AVG(sodio) as na, AVG(kcal) as kcal, AVG(carbs) as carbs FROM totais_dia",
        [d3_ini, d3_fim])[0]
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
    m = _metas_em(d)
    def p(v,g): return f"{float(v)/g*100:.0f}%"
    return "\n".join([f"Resumo — {d}",
        f"Calorias: {float(r['cal']):.0f}/{m['cal']} kcal ({p(r['cal'],m['cal'])})",
        f"Proteina: {float(r['prot']):.1f}/{m['prot']}g ({p(r['prot'],m['prot'])})",
        f"Carbs: {float(r['carbs']):.1f}/{m['carbs']}g ({p(r['carbs'],m['carbs'])})",
        f"Gordura: {float(r['fat']):.1f}/{m['fat']}g ({p(r['fat'],m['fat'])})",
        f"Ca:{float(r['ca']):.0f}/{m['ca']}mg Mg:{float(r['mg_']):.0f}/{m['mg']}mg Fe:{float(r['fe']):.1f}/{m['fe']}mg",
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
    circ_arm_flexed_cm: float = None, circ_forearm_cm: float = None,
    circ_thigh_medial_cm: float = None,
    bmi_class: str = None, metabolic_risk: str = None,
    body_fat_class: str = None, amc_class: str = None,
    source: str = None,
) -> str:
    """Registra peso/cintura (update caseiro) ou composicao corporal completa (bioimpedancia).
    Campos opcionais. circ_waist_cm e espelhado em waist_cm para manter a serie continua.
    source: 'balanca_casa' | 'bioimpedancia_clinica' — importante registrar, pois os dois
    equipamentos divergem sistematicamente (ex: mesmo periodo, balanca de casa vs clinica
    ~1,7kg de diferenca); sem essa marca a serie historica mistura instrumentos diferentes."""
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
        "circ_arm_flexed_cm": circ_arm_flexed_cm, "circ_forearm_cm": circ_forearm_cm,
        "circ_thigh_medial_cm": circ_thigh_medial_cm,
        "bmi_class": bmi_class, "metabolic_risk": metabolic_risk,
        "body_fat_class": body_fat_class, "amc_class": amc_class,
        "source": source,
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
def registrar_suplemento(supplement: str, dose_amount: float = None, dose_unit: str = None,
                          taken_at: str = None, prescribed_by: str = None, notas: str = None) -> str:
    """Registra dose de suplemento (Vitamina D, Metilcobalamina, Metilfolato, etc.) em tabela
    dedicada (supplement_log) — separado de meals, para nao poluir contagem/aderencia de refeicoes.
    taken_at: data/hora (default agora). dose_unit: 'mcg'|'mg'|'UI'|'g'."""
    dt = _parse_horario(taken_at) if taken_at else datetime.now(USER_TZ)
    rid = db_e(
        "INSERT INTO supplement_log (taken_at,supplement,dose_amount,dose_unit,prescribed_by,notes) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        [dt, supplement, dose_amount, dose_unit, prescribed_by, notas]
    )
    dose = f" {dose_amount}{dose_unit}" if dose_amount is not None else ""
    return f"Suplemento registrado (ID {rid}): {supplement}{dose}"

@mcp.tool()
def listar_suplementos(dias: int = 7) -> str:
    """Lista doses de suplemento registradas nos ultimos N dias (padrao: 7)."""
    fim = _hoje()
    inicio = (datetime.now(USER_TZ) - timedelta(days=dias - 1)).strftime("%Y-%m-%d")
    rows = db_q(
        "SELECT id, taken_at AT TIME ZONE 'Europe/Lisbon' as t, supplement, dose_amount, dose_unit "
        "FROM supplement_log WHERE (taken_at AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s "
        "ORDER BY taken_at", [inicio, fim])
    if not rows:
        return f"Nenhum suplemento registrado entre {inicio} e {fim}."
    linhas = [f"Suplementos {inicio} a {fim} ({len(rows)} registros):"]
    for r in rows:
        h = r["t"].strftime("%d/%m %H:%M") if r["t"] else "?"
        dose = f" {r['dose_amount']}{r['dose_unit']}" if r["dose_amount"] is not None else ""
        linhas.append(f"  {h}  {r['supplement']}{dose}")
    return "\n".join(linhas)

@mcp.tool()
def aderencia_suplementos(dias: int = 30) -> str:
    """Percentual de dias com cada suplemento registrado nos ultimos N dias (padrao: 30) —
    metrica tipicamente pedida em consulta com a nutricionista."""
    fim = _hoje()
    inicio = (datetime.now(USER_TZ) - timedelta(days=dias - 1)).strftime("%Y-%m-%d")
    rows = db_q(
        "SELECT supplement, COUNT(DISTINCT (taken_at AT TIME ZONE 'Europe/Lisbon')::date) as dias_com_registro "
        "FROM supplement_log WHERE (taken_at AT TIME ZONE 'Europe/Lisbon')::date BETWEEN %s AND %s "
        "GROUP BY supplement ORDER BY supplement", [inicio, fim])
    if not rows:
        return f"Nenhum suplemento registrado entre {inicio} e {fim}."
    linhas = [f"Aderencia a suplementos {inicio} a {fim} ({dias} dias):"]
    for r in rows:
        pct = round(int(r["dias_com_registro"]) / dias * 100, 1)
        linhas.append(f"  {r['supplement']}: {r['dias_com_registro']}/{dias} dias ({pct}%)")
    return "\n".join(linhas)

LB_TO_KG = 0.45359237

@mcp.tool()
def registrar_treino(exercicios: list, data: str = None, tipo: str = None, local: str = None, notas: str = None, pulado: bool = False, motivo_pulo: str = None, energia: int = None, qualidade_sono: int = None, split_day: str = None) -> str:
    """Registra sessao de treino.
    exercicios: [{nome ou exercise_id, series:[{reps,carga_kg ou carga_lbs,rpe,notas}], grupo_muscular?, equipamento?, alternativa_de?}]
    split_day: 'push'|'pull'|'legs'|'cardio'

    Resolucao de exercicio: passe exercise_id quando souber o ID do catalogo (buscar_exercicios) —
    elimina ambiguidade. Por nome, o match e case/acento-insensitive contra o catalogo; so cria
    um exercicio novo se realmente nao houver correspondencia. A resposta informa, por exercicio,
    se houve match ou criacao — confira antes de assumir que o exercicio certo foi usado.
    carga_lbs: alternativa a carga_kg para maquinas calibradas em libras; convertida para kg
    (o valor original em lbs fica preservado em notes)."""
    d = data or _hoje()
    wid = db_e("INSERT INTO workouts (workout_date,workout_type,location,notes,skipped,skip_reason,energy_level,sleep_quality,split_day) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",[d,tipo,local,notas,pulado,motivo_pulo,energia,qualidade_sono,split_day])
    if pulado: return f"Treino pulado (ID {wid}). Motivo: {motivo_pulo or 'nao informado'}"
    sets = 0
    resolucoes = []
    catalogo = db_q("SELECT id, name FROM exercises")
    for ex in exercicios:
        nome = ex.get("nome") or ex.get("name", "")
        exercise_id_in = ex.get("exercise_id")
        if exercise_id_in:
            achado = next((c for c in catalogo if c["id"] == exercise_id_in), None)
            if not achado:
                return f"exercise_id {exercise_id_in} nao existe no catalogo. Use buscar_exercicios para conferir o ID."
            eid = achado["id"]
            nome = nome or achado["name"]
            status = "id explicito"
        else:
            alvo = slug(nome)
            achado = next((c for c in catalogo if slug(c["name"]) == alvo), None)
            if achado:
                eid = achado["id"]
                status = "match"
            else:
                eid = db_e("INSERT INTO exercises (name,muscle_group,equipment,is_active) VALUES (%s,%s,%s,true) RETURNING id",
                           [nome, ex.get("grupo_muscular"), ex.get("equipamento")])
                catalogo.append({"id": eid, "name": nome})
                status = "criado"
        resolucoes.append(f"{nome} (id {eid}, {status})")
        alternativa_de = ex.get("alternativa_de")
        for i, s in enumerate(ex.get("series", []), 1):
            carga_kg, carga_lbs, notas_set = s.get("carga_kg"), s.get("carga_lbs"), s.get("notas")
            if carga_kg is None and carga_lbs is not None:
                carga_kg = round(float(carga_lbs) * LB_TO_KG, 2)
                notas_set = (f"{notas_set} | " if notas_set else "") + f"carga original: {carga_lbs}lbs"
            db_e("INSERT INTO workout_sets (workout_id,exercise_id,exercise_name,set_number,reps,weight_kg,rpe,notes,is_alternative,alternative_for) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [wid, eid, nome, i, s.get("reps"), carga_kg, s.get("rpe"), notas_set, bool(alternativa_de), alternativa_de])
            sets += 1
    return f"Treino (ID {wid}) em {d} — {len(exercicios)} exercicios, {sets} series\n" + "\n".join(resolucoes)

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
    """Gera e salva o daily_summary do dia e retorna o fechamento completo num unico payload:
    calorias/proteina/carboidrato/gordura/fibra/sodio (consumido, meta, percentual — meta do
    plano vigente na data via plan_targets), bloco de micronutrientes completo (incl. folato),
    refeicoes_no_plano, aderencia_macro (100 - desvio medio absoluto vs meta em cal/prot/carbs/
    gordura/fibra — um dia 95-105% em tudo pontua alto mesmo com refeicoes fora do plano, e
    vice-versa), agua e treino. treinou: se omitido, deriva de haver treino nao-pulado no dia."""
    d = data or _hoje()
    r = db_q(
        "SELECT COALESCE(SUM(calories),0) as cal,COALESCE(SUM(protein_g),0) as prot,"
        "COALESCE(SUM(carbs_g),0) as carbs,COALESCE(SUM(fat_g),0) as fat,COALESCE(SUM(fiber_g),0) as fiber,"
        "COALESCE(SUM(sodium_mg),0) as na,"
        "COALESCE(SUM(calcium_mg),0) as ca,COALESCE(SUM(iron_mg),0) as fe,COALESCE(SUM(magnesium_mg),0) as mg_,"
        "COALESCE(SUM(potassium_mg),0) as k,COALESCE(SUM(vitamin_c_mg),0) as vitc,"
        "COALESCE(SUM(vitamin_d_mcg),0) as vitd,COALESCE(SUM(vitamin_b12_mcg),0) as vitb12,"
        "COALESCE(SUM(zinc_mg),0) as zn,COALESCE(SUM(folate_mcg),0) as folato,"
        "COUNT(*) as total,COUNT(*) FILTER (WHERE is_on_plan) as on_plan FROM meals "
        "WHERE (meal_time AT TIME ZONE 'Europe/Lisbon')::date=%s OR (meal_time IS NULL AND (logged_at AT TIME ZONE 'Europe/Lisbon')::date=%s)",
        [d, d])[0]
    m = _metas_em(d)
    if treinou is None:
        treinou = bool(db_q("SELECT id FROM workouts WHERE workout_date=%s AND NOT COALESCE(skipped,false)", [d]))
    total, on_plan = float(r["total"]), float(r["on_plan"])
    refeicoes_no_plano_pct = int(on_plan / max(total, 1) * 100)

    def bloco(consumido, meta):
        return {"consumido": round(float(consumido), 1), "meta": meta,
                "percentual": round(float(consumido) / meta * 100, 1) if meta else 0.0}

    macros = {
        "calorias": bloco(r["cal"], m["cal"]), "proteina_g": bloco(r["prot"], m["prot"]),
        "carboidrato_g": bloco(r["carbs"], m["carbs"]), "gordura_g": bloco(r["fat"], m["fat"]),
        "fibra_g": bloco(r["fiber"], m["fibra"]), "sodio_mg": bloco(r["na"], METAS["na"]),
    }
    aderencia_macro = round(100 - sum(abs(v["percentual"] - 100) for k, v in macros.items() if k != "sodio_mg") / 5, 1)
    micros = {
        "calcio_mg": bloco(r["ca"], METAS["ca"]), "ferro_mg": bloco(r["fe"], METAS["fe"]),
        "magnesio_mg": bloco(r["mg_"], METAS["mg"]), "potassio_mg": bloco(r["k"], METAS["k"]),
        "vitamina_c_mg": bloco(r["vitc"], METAS["vit_c"]), "vitamina_d_mcg": bloco(r["vitd"], METAS["vit_d"]),
        "vitamina_b12_mcg": bloco(r["vitb12"], METAS["vit_b12"]), "zinco_mg": bloco(r["zn"], METAS["zn"]),
        "folato_mcg": bloco(r["folato"], 400),
    }
    db_e(
        "INSERT INTO daily_summary (summary_date,total_calories,total_protein_g,total_carbs_g,total_fat_g,"
        "total_fiber_g,calcium_mg,iron_mg,magnesium_mg,potassium_mg,vitamin_c_mg,vitamin_d_mcg,vitamin_b12_mcg,"
        "zinc_mg,meals_on_plan,meals_total,adherence_pct,trained,workout_notes,water_estimate_ml) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (summary_date) DO UPDATE SET total_calories=EXCLUDED.total_calories,"
        "total_protein_g=EXCLUDED.total_protein_g,adherence_pct=EXCLUDED.adherence_pct,"
        "trained=EXCLUDED.trained,water_estimate_ml=EXCLUDED.water_estimate_ml",
        [d, r["cal"], r["prot"], r["carbs"], r["fat"], r["fiber"], r["ca"], r["fe"], r["mg_"], r["k"],
         r["vitc"], r["vitd"], r["vitb12"], r["zn"], r["on_plan"], r["total"], refeicoes_no_plano_pct,
         treinou, notas_treino, agua_ml]
    )
    resultado = {
        "data": d, "macros": macros, "micronutrientes": micros,
        "aderencia_macro_pct": aderencia_macro,
        "refeicoes_no_plano": {"pct": refeicoes_no_plano_pct, "on_plan": int(on_plan), "total": int(total)},
        "agua_ml": agua_ml, "treino": {"treinou": treinou, "notas": notas_treino},
    }
    return json.dumps(resultado, default=str, ensure_ascii=False, indent=2)

@mcp.tool()
def retrospectiva_semanal(data_domingo: str = None) -> str:
    """Analise semanal com medias e tendencias. Cada dia e avaliado contra a meta vigente
    NAQUELE dia (plan_targets) antes de entrar na media — uma semana que atravessa troca de
    plano (ex: 24 a 30/08/2026) nao fica distorcida comparando dias do plano antigo com a
    meta do plano novo."""
    d = data_domingo or _hoje()
    rows = db_q("SELECT * FROM daily_summary WHERE summary_date BETWEEN (%s::date-INTERVAL '6 days') AND %s::date ORDER BY summary_date",[d,d])
    if not rows: return "Nenhum dado. Usa gerar_resumo_diario para cada dia primeiro."
    n = len(rows)
    avg = lambda f: sum(float(r.get(f) or 0) for r in rows)/n
    metas_dia = [_metas_em(str(r["summary_date"])) for r in rows]
    pct_cal = sum(float(r.get("total_calories") or 0) / mt["cal"] * 100 for r, mt in zip(rows, metas_dia)) / n
    pct_prot = sum(float(r.get("total_protein_g") or 0) / mt["prot"] * 100 for r, mt in zip(rows, metas_dia)) / n
    return "\n".join([
        f"Retrospectiva ate {d} ({n}/7 dias)",
        f"Calorias media: {avg('total_calories'):.0f} kcal ({pct_cal:.0f}% da meta de cada dia)",
        f"Proteina media: {avg('total_protein_g'):.1f}g ({pct_prot:.0f}% da meta de cada dia)",
        f"Treinos: {sum(1 for r in rows if r.get('trained'))}/{n}",
        f"Aderencia media: {avg('adherence_pct'):.0f}%",
    ])

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
    """Executa SQL ad-hoc no banco. Reads (SELECT/WITH/SHOW/EXPLAIN/TABLE/VALUES) usam db_q;
    qualquer outra coisa (UPDATE/INSERT/DELETE/DDL) vai por db_e, que COMMITA — evita o bug
    em que edicoes via SQL direto pareciam reverter (db_q nunca commitava e fechava em rollback)."""
    primeiro = ""
    for tok in sql.strip().lstrip("(").strip().split():
        primeiro = tok.lower().strip("(")
        break
    if primeiro in ("select", "with", "show", "explain", "table", "values"):
        try:
            rows = db_q(sql)
            return json.dumps(rows, default=str, indent=2) if rows else "0 linhas."
        except Exception as ex:
            return f"Erro: {ex}"
    try:
        res = db_e(sql)
        return f"OK. {res} linha(s) afetadas (commit confirmado)."
    except Exception as ex:
        return f"Erro: {ex}"

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

