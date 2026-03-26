# backend/routers/ordenes.py
from __future__ import annotations

# ---- eventos en tiempo real (no requiere pip install) ----
try:
    from backend.events import notify
except Exception:
    from ..events import notify

from datetime import date, datetime
from typing import Optional, Dict, List
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.db import get_conn
from .catalogos import _is_sin_laminar

router = APIRouter(prefix="/ordenes", tags=["Órdenes"])





# ================== Modelos ==================
class OrdenCrear(BaseModel):
    fecha: Optional[date] = None
    material_id: int
    marca_id: Optional[int] = None

    ancho_orden_cm: Optional[float] = None
    ancho_rollo_cm: float

    largo: float
    rep: int

    consecutivo: str
    ruta: Optional[str] = None
    observaciones: Optional[str] = None

    lam_tipo_id: Optional[int] = None
    lam_marca_id: Optional[int] = None
    lam_ancho_cm: Optional[float] = None
    fecha_entrega: Optional[date] = None


class OrdenEnProceso(BaseModel):
    id_orden: int
    ancho_objetivo_cm: Optional[float] = None
    usuario: Optional[str] = None


class OrdenFinalizar(BaseModel):
    id_orden: int
    total_largo_impreso_cm: float
    desp_largo_cm: float
    desp_ancho_cm: float
    repeticiones: int
    usuario_impresor: Optional[str] = None
    estado_requerido: Optional[str] = "EN PROCESO"
    # laminado
    lam_tipo_id: int
    lam_marca_id: Optional[int] = None
    lam_ancho_cm: Optional[float] = None
    lam_consumo_cm: Optional[float] = None
    # compat
    ancho_objetivo_cm: Optional[float] = None


class OrdenCancelar(BaseModel):
    id_orden: int
    desp_largo_cm: float | None = None   # alias histórico
    merma_cancel_cm: float | None = None # nuevo nombre explícito
    usuario: str | None = None
    estado_permitido: list[str] | None = None
    ancho_objetivo_cm: float | None = None
    borrar_si_merma_cero: bool = False 


class OrdenSetEspacio(BaseModel):
    id_orden: int
    espacio_reps_cm: float


class EditConsec(BaseModel):
    id_orden: int
    consecutivo: str


class LimpiarFinalizadasReq(BaseModel):
    dias: Optional[int] = None  # si None, archiva todas las finalizadas no archivadas


# ================== Utils ==================
def _rows(conn: Connection, sql: str, params: dict | None = None):
    res = conn.execute(text(sql), params or {})
    return [dict(r) for r in res.mappings().all()]

def _one(conn: Connection, sql: str, params: dict | None = None):
    res = conn.execute(text(sql), params or {})
    row = res.mappings().first()
    return dict(row) if row else None

def _as_date(v):
    if not v:
        return date.today()
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date()
    except Exception:
        return date.today()

def _get_orden_lam_consumo_col(conn: Connection) -> str | None:
    return _first_existing(conn, "ordenes_de_impresion", [
        "lam_consumo_cm",
        "LAM_CONSUMO_CM",
        "lam_consumo",
        "consumo_laminado_cm",
    ])




# --------- Resolución defensiva de columnas (introspección) ---------
_EM_CACHE: Dict[str, str] | None = None
_EL_CACHE: Dict[str, str] | None = None
_MOVI_CACHE = None
_MOVL_CACHE = None

def _col_exists(conn: Connection, table: str, col: str) -> bool:
    q = text("""
        SELECT 1
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :t
          AND COLUMN_NAME = :c
        LIMIT 1
    """)
    return conn.execute(q, {"t": table, "c": col}).first() is not None



_EM_CACHE: dict | None = None

def _col_exists(conn, table: str, col: str) -> bool:
    q = text("""
        SELECT 1
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :t
          AND COLUMN_NAME = :c
        LIMIT 1
    """)
    return conn.execute(q, {"t": table, "c": col}).first() is not None






def _first_existing(conn, table: str, candidates: list[str]) -> str | None:
    for c in candidates:
        if _col_exists(conn, table, c):
            return c
    return None




def _get_em_cols(conn):
    """
    existencias_material: table, mat, marca, ancho, stock, costo
    - costo = costo por cm (o equivalente)
    """
    global _EM_CACHE
    if _EM_CACHE:
        return _EM_CACHE

    table = "existencias_material"

    mat   = _first_existing(conn, table, ["material_id","id_material","id_material_final","id_material_FINAL","fk_id_material"])
    marca = _first_existing(conn, table, ["marca_id","id_marca","fk_id_marca"])
    ancho = _first_existing(conn, table, ["ancho_cm","ANCHO","ancho"])
    stock = _first_existing(conn, table, ["stock_cm","STOCK_CM","stock"])

    # 👇 ESTA ES LA CLAVE: resolver la columna real de costo
    costo = _first_existing(conn, table, [
        "costo_cm", "COSTO_CM",
        "costo", "COSTO",
        "precio_cm", "PRECIO_CM",
        "precio", "PRECIO",
        "valor_cm", "VALOR_CM",
        "valor", "VALOR",
    ])

    if not (mat and ancho and stock):
        raise HTTPException(status_code=500, detail="No se pudieron resolver columnas de existencias_material.")

    # costo puede ser None si tu tabla aún no lo tiene, pero entonces costo_mat será 0
    _EM_CACHE = {"table": table, "mat": mat, "marca": marca, "ancho": ancho, "stock": stock, "costo": costo}
    return _EM_CACHE


def _get_el_cols(conn: Connection) -> Dict[str, str]:
    """existencias_laminado: table, tipo, marca, ancho, stock"""
    global _EL_CACHE
    if _EL_CACHE:
        return _EL_CACHE
    table = "existencias_laminado"
    tipo  = _first_existing(conn, table, ["lam_tipo_id","fk_tipo_laminado","id_tipo_laminado","lam_id_tipo","id_tipo","tipo_id"])
    marca = _first_existing(conn, table, ["lam_marca_id","fk_marca_laminado","id_marca_laminado","id_marca","lam_id_marca","lam_marca_id0"])
    ancho = _first_existing(conn, table, ["ancho_cm","ANCHO","ancho"])
    stock = _first_existing(conn, table, ["stock_cm","STOCK_CM","stock"])
    if not (tipo and ancho and stock):
        raise HTTPException(status_code=500, detail="No se pudieron resolver columnas de existencias_laminado.")
    _EL_CACHE = {"table": table, "tipo": tipo, "marca": marca, "ancho": ancho, "stock": stock}
    return _EL_CACHE

def _get_mov_inv_cols(conn: Connection):
    """movimientos_inventario: resuelve nombres reales de columnas."""
    global _MOVI_CACHE
    if _MOVI_CACHE:
        return _MOVI_CACHE
    tbl = "movimientos_inventario"
    def pick(cands): return _first_existing(conn, tbl, cands)
    orden    = pick(["id_orden","orden_id","fk_id_orden"])
    mat      = pick(["material_id","id_material","id_material_FINAL","id_material_final","fk_id_material"])
    marca    = pick(["marca_id","id_marca","fk_id_marca"])
    ancho    = pick(["ancho_cm","ANCHO","ancho"])
    cantidad = pick(["cantidad_cm","CANTIDAD_CM","cantidad","delta_cm"])
    tipo     = pick(["tipo","mov_tipo","tipo_movimiento"])
    obs      = pick(["observaciones","obs","comentario","comentarios","nota"])
    usuario  = pick(["usuario","user","username","id_usuario"])
    if not all([orden, mat, ancho, cantidad]):
        raise HTTPException(status_code=500, detail="No se pudieron resolver columnas de movimientos_inventario.")
    _MOVI_CACHE = {"table": tbl, "orden": orden, "mat": mat, "marca": marca,
                   "ancho": ancho, "cantidad": cantidad, "tipo": tipo,
                   "obs": obs, "usuario": usuario}
    return _MOVI_CACHE

def _get_mov_lam_cols(conn: Connection):
    global _MOVL_CACHE
    if _MOVL_CACHE:
        return _MOVL_CACHE
    tbl = "movimientos_laminado"
    def pick(c): return _first_existing(conn, tbl, c)
    orden    = pick(["id_orden","orden_id","fk_id_orden"])
    tipo_id  = pick(["lam_tipo_id","fk_tipo_laminado","id_tipo_laminado","lam_id_tipo","tipo_id"])
    marca    = pick(["lam_marca_id","fk_marca_laminado","id_marca_laminado","id_marca","lam_id_marca"])
    ancho    = pick(["ancho_cm","ANCHO","ancho"])
    cantidad = pick(["cantidad_cm","CANTIDAD_CM","cantidad","delta_cm"])
    tipo     = pick(["tipo","mov_tipo","tipo_movimiento"])
    obs      = pick(["observaciones","obs","comentario","comentarios","nota"])
    usuario  = pick(["usuario","user","username","id_usuario"])
    if not all([orden, tipo_id, ancho, cantidad]):
        raise HTTPException(status_code=500, detail="No se pudieron resolver columnas de movimientos_laminado.")
    _MOVL_CACHE = {"table": tbl, "orden": orden, "tipo_id": tipo_id, "marca": marca,
                   "ancho": ancho, "cantidad": cantidad, "tipo": tipo,
                   "obs": obs, "usuario": usuario}
    return _MOVL_CACHE


# -------- Helpers de inventario --------
def _ensure_exist_material(conn: Connection, material_id: int, marca_id: int | None, ancho_cm: float):
    em = _get_em_cols(conn)
    params = {"mid": material_id, "mk": marca_id, "ancho": float(ancho_cm)}
    if em["marca"]:
        where = f"""{em['mat']} = :mid AND ((:mk IS NULL AND {em['marca']} IS NULL) OR {em['marca']} = :mk) AND {em['ancho']} = :ancho"""
        insert_cols = f"{em['mat']}, {em['marca']}, {em['ancho']}, {em['stock']}"
        insert_vals = ":mid, :mk, :ancho, 0"
    else:
        where = f"""{em['mat']} = :mid AND {em['ancho']} = :ancho"""
        insert_cols = f"{em['mat']}, {em['ancho']}, {em['stock']}"
        insert_vals = ":mid, :ancho, 0"
    exists = conn.execute(text(f"SELECT 1 FROM {em['table']} WHERE {where} LIMIT 1"), params).first()
    if not exists:
        conn.execute(text(f"INSERT INTO {em['table']} ({insert_cols}) VALUES ({insert_vals})"), params)

def _ensure_exist_lam(conn: Connection, lam_tipo_id: int, lam_marca_id: int | None, ancho_cm: float):
    el = _get_el_cols(conn)
    params = {"tipo": lam_tipo_id, "mk": lam_marca_id, "ancho": float(ancho_cm)}
    if el["marca"]:
        where = f"""{el['tipo']} = :tipo AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']} = :mk) AND {el['ancho']} = :ancho"""
        insert_cols = f"{el['tipo']}, {el['marca']}, {el['ancho']}, {el['stock']}"
        insert_vals = ":tipo, :mk, :ancho, 0"
    else:
        where = f"""{el['tipo']} = :tipo AND {el['ancho']} = :ancho"""
        insert_cols = f"{el['tipo']}, {el['ancho']}, {el['stock']}"
        insert_vals = ":tipo, :ancho, 0"
    exists = conn.execute(text(f"SELECT 1 FROM {el['table']} WHERE {where} LIMIT 1"), params).first()
    if not exists:
        conn.execute(text(f"INSERT INTO {el['table']} ({insert_cols}) VALUES ({insert_vals})"), params)

def _tipo_value_for(conn: Connection, table: str, column: str, salida: bool = True):
    """Genera valor compatible para columna 'tipo' según su tipo real."""
    meta = _one(conn, """
        SELECT DATA_TYPE AS t, CHARACTER_MAXIMUM_LENGTH AS l
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :t AND COLUMN_NAME = :c
    """, {"t": table, "c": column}) or {}
    dt = (meta.get("t") or "").lower()
    length = meta.get("l")
    def _enum_options() -> list[str]:
        row = _one(conn, """
            SELECT COLUMN_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c
        """, {"t": table, "c": column}) or {}
        ct = row.get("COLUMN_TYPE") or ""
        return [re.sub(r"\\'", "'", m) for m in re.findall(r"'((?:\\'|[^'])*)'", ct)]
    if dt == "enum":
        opts = _enum_options()
        upper = [o.upper() for o in opts]
        wanted = ["SALIDA","EGRESO","OUT","S"] if salida else ["ENTRADA","INGRESO","IN","E"]
        for w in wanted:
            for i, up in enumerate(upper):
                if up == w:
                    return opts[i]
        needles = ["SALID","EGRES"] if salida else ["ENTRAD","INGRES"]
        for i, up in enumerate(upper):
            if any(n in up for n in needles):
                return opts[i]
        return opts[0] if opts else ("SALIDA" if salida else "ENTRADA")
    if dt in ("char","varchar"):
        if length is not None and int(length) <= 1:
            return "S" if salida else "E"
        return "SALIDA" if salida else "ENTRADA"
    if dt in ("tinyint","smallint","mediumint","int","bigint","decimal","numeric"):
        return 2 if salida else 1
    return "SALIDA" if salida else "ENTRADA"

def _ajuste_stock_material(conn: Connection, material_id: int, marca_id: int | None, ancho_cm: float,
                           delta_cm: float, id_orden: int, motivo: str | None = None, usuario: str | None = None):
    em = _get_em_cols(conn)
    _ensure_exist_material(conn, material_id, marca_id, ancho_cm)
    params = {"delta": float(delta_cm), "mid": material_id, "mk": marca_id, "ancho": float(ancho_cm)}
    if em["marca"]:
        where = f"""{em['mat']} = :mid AND ((:mk IS NULL AND {em['marca']} IS NULL) OR {em['marca']} = :mk) AND {em['ancho']} = :ancho"""
    else:
        where = f"""{em['mat']} = :mid AND {em['ancho']} = :ancho"""
    upd = text(f"UPDATE {em['table']} SET {em['stock']} = {em['stock']} - :delta WHERE {where} LIMIT 1")
    conn.execute(upd, params)

    # movimiento (SALIDA)
    mi = _get_mov_inv_cols(conn)
    cols = [mi["orden"], mi["mat"], mi["ancho"], mi["cantidad"]]
    vals = [":oid", ":mid", ":ancho", ":cant"]
    p = {"oid": id_orden, "mid": material_id, "ancho": float(ancho_cm),
         "cant": float(delta_cm), "obs": motivo, "usr": usuario}
    if mi["marca"]:
        cols.insert(2, mi["marca"]); vals.insert(2, ":mk"); p["mk"] = marca_id
    if mi["tipo"]:
        cols.append(mi["tipo"]); vals.append(":tipo_val"); p["tipo_val"] = _tipo_value_for(conn, mi["table"], mi["tipo"], salida=True)
    if mi["obs"]:
        cols.append(mi["obs"]); vals.append(":obs")
    if mi["usuario"]:
        cols.append(mi["usuario"]); vals.append(":usr")
    ins = text(f"INSERT INTO {mi['table']} ({', '.join(cols)}) VALUES ({', '.join(vals)})")
    conn.execute(ins, p)

def _ajuste_stock_laminado(conn: Connection, lam_tipo_id: int, lam_marca_id: int | None, ancho_cm: float,
                           delta_cm: float, id_orden: int, motivo: str | None = None, usuario: str | None = None):
    el = _get_el_cols(conn)
    _ensure_exist_lam(conn, lam_tipo_id, lam_marca_id, ancho_cm)
    params = {"delta": float(delta_cm), "tipo": lam_tipo_id, "mk": lam_marca_id, "ancho": float(ancho_cm)}
    if el["marca"]:
        where = f"""{el['tipo']} = :tipo AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']} = :mk) AND {el['ancho']} = :ancho"""
    else:
        where = f"""{el['tipo']} = :tipo AND {el['ancho']} = :ancho"""
    upd = text(f"UPDATE {el['table']} SET {el['stock']} = {el['stock']} - :delta WHERE {where} LIMIT 1")
    conn.execute(upd, params)

    # movimiento (SALIDA)
    ml = _get_mov_lam_cols(conn)
    cols = [ml["orden"], ml["tipo_id"], ml["ancho"], ml["cantidad"]]
    vals = [":oid",    ":tipo",        ":ancho",    ":cant"]
    p = {"oid": id_orden, "tipo": lam_tipo_id, "ancho": float(ancho_cm),
         "cant": float(delta_cm), "obs": motivo, "usr": usuario}
    if ml["marca"]:
        cols.insert(2, ml["marca"]); vals.insert(2, ":mk"); p["mk"] = lam_marca_id
    if ml["tipo"]:
        cols.append(ml["tipo"]); vals.append(":tipo_val"); p["tipo_val"] = _tipo_value_for(conn, ml["table"], ml["tipo"], salida=True)
    if ml["obs"]:
        cols.append(ml["obs"]); vals.append(":obs")
    if ml["usuario"]:
        cols.append(ml["usuario"]); vals.append(":usr")
    ins = text(f"INSERT INTO {ml['table']} ({', '.join(cols)}) VALUES ({', '.join(vals)})")
    conn.execute(ins, p)


# ================== Endpoints ==================
@router.post("", summary="Crear Orden")
def crear_orden(payload: OrdenCrear, conn: Connection = Depends(get_conn)):
    fecha_val = _as_date(payload.fecha)
    fecha_entrega = _as_date(payload.fecha_entrega) if payload.fecha_entrega else fecha_val

    hay_lam   = bool(payload.lam_tipo_id)
    lam_tipo  = int(payload.lam_tipo_id) if hay_lam else None
    lam_marca = int(payload.lam_marca_id) if payload.lam_marca_id else None
    lam_ancho = float(payload.ancho_rollo_cm) if hay_lam else 0.0

    sql = text("""
        INSERT INTO ordenes_de_impresion
        (FECHA, id_material_FINAL, fk_id_marca,
         ANCHO, `MEDIDA DE ROLLO`, LARGO, REP, espacio_reps_cm,
         CONSECUTIVO, RUTA, OBSERVACIONES,
         fk_tipo_laminado, fk_marca_laminado, ancho_laminado_cm,
         fecha_entrega, fk_id_estado)
        VALUES
        (:FECHA, :id_material_FINAL, :fk_id_marca,
         :ANCHO, :MEDIDA_ROLLO, :LARGO, :REP, :ESPACIO_REPS_CM,
         :CONSECUTIVO, :RUTA, :OBS,
         :LAM_TIPO, :LAM_MARCA, :LAM_ANCHO,
         :FECHA_ENTREGA, 1)
    """)

    params = {
        "FECHA": fecha_val,
        "id_material_FINAL": payload.material_id,
        "fk_id_marca": payload.marca_id,
        "ANCHO": payload.ancho_orden_cm,
        "MEDIDA_ROLLO": payload.ancho_rollo_cm,
        "LARGO": payload.largo,
        "REP": payload.rep,
        "ESPACIO_REPS_CM": 0.0,
        "CONSECUTIVO": (payload.consecutivo or "").strip() or None,
        "RUTA": payload.ruta,
        "OBS": payload.observaciones,
        "LAM_TIPO": lam_tipo,
        "LAM_MARCA": lam_marca,
        "LAM_ANCHO": lam_ancho,
        "FECHA_ENTREGA": fecha_entrega,
    }

    res = conn.execute(sql, params)
    oid = res.lastrowid
    conn.commit()
    notify({"type": "order.created", "payload": {"id_orden": oid}})
    return {"id_orden": oid, "msg": "creada"}


@router.post("/set-espacio-reps", summary="Actualizar espacio entre reps (Impresión)")
def set_espacio_reps(payload: OrdenSetEspacio, conn: Connection = Depends(get_conn)):
    upd = text("""
        UPDATE ordenes_de_impresion
        SET espacio_reps_cm = :v
        WHERE id_orden = :oid AND fk_id_estado IN (1,2)
    """)
    res = conn.execute(upd, {"v": float(payload.espacio_reps_cm or 0.0), "oid": payload.id_orden})
    conn.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=409, detail="No se pudo actualizar (estado inválido o no existe).")
    notify({"type": "order.edited", "payload": {"id_orden": payload.id_orden, "espacio_reps_cm": float(payload.espacio_reps_cm or 0.0)}})
    return {"ok": True}








@router.get("/finalizadas", summary="Órdenes finalizadas")
def ordenes_finalizadas(
    limite: int = Query(300, ge=1, le=2000),
    conn: Connection = Depends(get_conn)
):
    lam_col = _get_orden_lam_consumo_col(conn)
    lam_sel = f"o.{lam_col} AS lam_consumo_cm" if lam_col else "NULL AS lam_consumo_cm"

    sql = f"""
    SELECT
        o.id_orden,
        o.FECHA,
        o.fecha_entrega,
        o.CONSECUTIVO,

        o.id_material_FINAL AS material_id,
        m.Material          AS material,
        o.fk_id_marca       AS marca_id,
        mm.nombre_marca     AS marca,

        o.`MEDIDA DE ROLLO` AS ancho_rollo_cm,
        o.LARGO,
        o.REP,

        o.total_largo_impreso_cm AS IMPRESO_CM,
        o.`DESP LARGO`           AS DESP_LARGO_CM,
        o.`DESP ANCHO`           AS DESP_ANCHO_CM,

        tl.nombre_laminado   AS lam_tipo,
        ml.nombre_marca      AS lam_marca,
        o.ancho_laminado_cm  AS lam_ancho_cm,
        {lam_sel},

        o.RUTA            AS ruta,
        o.espacio_reps_cm AS espacio_reps_cm
    FROM ordenes_de_impresion o
    LEFT JOIN catalogo_materiales m
           ON m.id_catalogo = o.id_material_FINAL
    LEFT JOIN catalogo_marcas_material mm
           ON mm.id_marca = o.fk_id_marca
    LEFT JOIN catalogo_tipos_laminado tl
           ON tl.id_laminado = o.fk_tipo_laminado
    LEFT JOIN catalogo_marcas_laminado ml
           ON ml.id_marca = o.fk_marca_laminado
    WHERE o.fk_id_estado = 3
    ORDER BY o.id_orden DESC
    LIMIT :lim
    """

    return _rows(conn, sql, {"lim": limite})


@router.get("/materiales")
def materiales(conn=Depends(get_conn)):
    rows = conn.execute(text("""
        SELECT id_catalogo AS id, Material AS nombre
        FROM catalogo_materiales
        ORDER BY Material
    """)).mappings().all()
    return [dict(r) for r in rows]



@router.get("/laminados/tipos")
def laminados_tipos(conn=Depends(get_conn)):
    rows = conn.execute(text("""
        SELECT id_laminado AS id, nombre_laminado AS nombre
        FROM catalogo_tipos_laminado
        ORDER BY nombre_laminado
    """)).mappings().all()
    return [dict(r) for r in rows]




@router.get("/laminados/marcas")
def laminados_marcas(conn=Depends(get_conn)):
    rows = conn.execute(text("""
        SELECT id_marca AS id, nombre_marca AS nombre
        FROM catalogo_marcas_laminado
        ORDER BY nombre_marca
    """)).mappings().all()
    return [dict(r) for r in rows]





@router.get("/panel_impresion", summary="Panel Impresión")
def panel_impresion(incluir_finalizadas: int = Query(0, ge=0, le=1), conn: Connection = Depends(get_conn)):
    estados = (1, 2) if not incluir_finalizadas else (1, 2, 3)
    sql = f"""
    SELECT
        o.id_orden,
        e.nombre_estado AS estado,
        o.FECHA,
        o.fecha_entrega,
        m.Material AS material,
        o.`MEDIDA DE ROLLO` AS ancho_rollo_cm,
        o.LARGO,
        o.REP,
        o.ANCHO AS ancho_orden_cm,
        o.RUTA AS ruta,
        o.espacio_reps_cm,
        o.OBSERVACIONES AS observaciones,
        o.fk_tipo_laminado AS lam_tipo_id,
        COALESCE(tl.nombre_laminado,'SIN LAMINAR') AS lam_tipo,
        COALESCE(o.ancho_laminado_cm, 0) AS lam_ancho_cm
    FROM ordenes_de_impresion o
    LEFT JOIN catalogo_estados_orden  e  ON e.id_estado  = o.fk_id_estado
    LEFT JOIN catalogo_materiales     m  ON m.id_catalogo = o.id_material_FINAL
    LEFT JOIN catalogo_tipos_laminado tl ON tl.id_laminado = o.fk_tipo_laminado
    WHERE o.fk_id_estado IN ({",".join(map(str, estados))})
      AND (o.archivado IS NULL OR o.archivado = 0)
    ORDER BY o.id_orden DESC
    LIMIT 300
    """
    return _rows(conn, sql)


@router.get("/canceladas", summary="Órdenes canceladas")
def ordenes_canceladas(
    limite: int = Query(300, ge=1, le=2000),
    conn: Connection = Depends(get_conn)
):
    sql = """
    SELECT
        o.id_orden,
        o.FECHA,
        o.fecha_entrega,
        o.CONSECUTIVO,

        o.id_material_FINAL AS material_id,
        m.Material          AS material,
        o.fk_id_marca       AS marca_id,
        mm.nombre_marca     AS marca,

        o.`MEDIDA DE ROLLO` AS ancho_rollo_cm,
        o.LARGO,
        o.REP,

        o.`DESP LARGO` AS DESP_LARGO_CM,
        o.`DESP ANCHO` AS DESP_ANCHO_CM,

        tl.nombre_laminado  AS lam_tipo,
        ml.nombre_marca     AS lam_marca,
        o.ancho_laminado_cm AS lam_ancho_cm,
        NULL                AS lam_consumo_cm,

        o.RUTA            AS ruta,
        o.espacio_reps_cm AS espacio_reps_cm
    FROM ordenes_de_impresion o
    LEFT JOIN catalogo_materiales m
           ON m.id_catalogo = o.id_material_FINAL
    LEFT JOIN catalogo_marcas_material mm
           ON mm.id_marca = o.fk_id_marca
    LEFT JOIN catalogo_tipos_laminado tl
           ON tl.id_laminado = o.fk_tipo_laminado
    LEFT JOIN catalogo_marcas_laminado ml
           ON ml.id_marca = o.fk_marca_laminado
    WHERE o.fk_id_estado = 4
    ORDER BY o.id_orden DESC
    LIMIT :lim
    """
    return _rows(conn, sql, {"lim": limite})



@router.get("/marcas", summary="Marcas de material (según existencias)")
def marcas_material(material_id: int = Query(..., ge=1), conn: Connection = Depends(get_conn)):
    """
    Devuelve marcas del MATERIAL disponibles para ese material_id.
    Se obtiene desde existencias_material para que coincida con el lookup real de costos.
    """
    sql = text("""
        SELECT DISTINCT
            mm.id_marca AS id,
            mm.nombre_marca AS nombre
        FROM existencias_material em
        JOIN catalogo_marcas_material mm
          ON mm.id_marca = em.marca_id
        WHERE em.material_id = :mid
        ORDER BY mm.nombre_marca
    """)
    rows = conn.execute(sql, {"mid": int(material_id)}).mappings().all()
    return [dict(r) for r in rows]


@router.post("/en-proceso", summary="Marcar EN PROCESO")
def en_proceso(payload: OrdenEnProceso, conn: Connection = Depends(get_conn)):
    upd = text("""
        UPDATE ordenes_de_impresion
        SET fk_id_estado = 2, en_proceso_at = NOW()
        WHERE id_orden = :oid AND fk_id_estado = 1
    """)
    res = conn.execute(upd, {"oid": payload.id_orden})
    conn.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=409, detail="No es posible marcar EN PROCESO (estado inválido).")
    notify({"type": "order.in_process", "payload": {"id_orden": payload.id_orden}})
    return {"ok": True}


@router.post("/finalizar", summary="Finalizar orden")
def finalizar(payload: OrdenFinalizar, conn: Connection = Depends(get_conn)):
    # 1) Datos base
    row = _one(conn, """
        SELECT id_orden,
               id_material_FINAL    AS material_id,
               fk_id_marca          AS marca_id,
               `MEDIDA DE ROLLO`    AS ancho_rollo_cm,
               LARGO,
               REP,
               espacio_reps_cm,
               fk_tipo_laminado     AS lam_tipo_id,
               fk_marca_laminado    AS lam_marca_id,
               ancho_laminado_cm    AS lam_ancho_cm
        FROM ordenes_de_impresion
        WHERE id_orden = :oid
    """, {"oid": payload.id_orden})
    if not row:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")

    # 2) Actualiza a FINALIZADA (solo si está EN PROCESO)
    upd = text("""
        UPDATE ordenes_de_impresion
           SET fk_id_estado           = 3,
               finalizado_at           = NOW(),
               total_largo_impreso_cm = :impreso,
               `DESP LARGO`           = :desp_l,
               `DESP ANCHO`           = :desp_a,
               REP                    = :rep,
               fk_tipo_laminado       = :lam_tipo,
               fk_marca_laminado      = :lam_marca,
               ancho_laminado_cm      = :lam_ancho
         WHERE id_orden = :oid
           AND fk_id_estado = 2
    """)
    res = conn.execute(
        upd,
        {
            "impreso":   float(payload.total_largo_impreso_cm or 0.0),
            "desp_l":    float(payload.desp_largo_cm or 0.0),
            "desp_a":    float(payload.desp_ancho_cm or 0.0),
            "rep":       int(payload.repeticiones or 0),
            "lam_tipo":  payload.lam_tipo_id,
            "lam_marca": payload.lam_marca_id,
            "lam_ancho": float(payload.lam_ancho_cm or 0.0),
            "oid":       payload.id_orden,
        },
    )
    if res.rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail="No es posible FINALIZAR: la orden no está EN PROCESO."
        )

    # 3) Cálculo de consumos
    rep    = int(payload.repeticiones or 0)
    imp    = float(payload.total_largo_impreso_cm or 0.0)
    desp_l = float(payload.desp_largo_cm or 0.0)
    esp    = float(row.get("espacio_reps_cm") or 0.0)

    # Consumo de MATERIAL:
    #   (largo_impreso * repeticiones) + (espacio_entre_reps * repeticiones) + desperdicio_a_lo_largo
    pasadas = rep + (1 if imp > 0 else 0)
    consumo_mat = max(0.0, (imp * pasadas) + (esp * rep) + desp_l)


    # Datos de laminado (pueden venir del payload o de la orden original)
    lam_tipo_id  = payload.lam_tipo_id  if payload.lam_tipo_id  is not None else row.get("lam_tipo_id")
    lam_marca_id = payload.lam_marca_id if payload.lam_marca_id is not None else row.get("lam_marca_id")
    lam_ancho    = float(payload.lam_ancho_cm or row.get("lam_ancho_cm") or 0.0)
    lam_consumo  = float(payload.lam_consumo_cm or 0.0)
    if lam_consumo <= 0:
        # Si no se especifica consumo de laminado, asumimos el mismo que el material
        lam_consumo = consumo_mat

    # ¿REALMENTE lleva laminado? (descartamos tipos "SIN LAMINAR / SIN LAMINADO")
    requiere_lam = (
        bool(lam_tipo_id) and
        lam_ancho > 0 and
        not _is_sin_laminar(conn, lam_tipo_id, None)
    )

    # === VALIDAR STOCK DE MATERIAL ANTES DE DESCONTAR ===
    em = _get_em_cols(conn)

    params_mat = {
        "mid":   row["material_id"],
        "mk":    row["marca_id"],
        "ancho": float(row["ancho_rollo_cm"] or 0.0),
    }

    if em["marca"]:
        where_mat = (
            f"{em['mat']} = :mid "
            f"AND ((:mk IS NULL AND {em['marca']} IS NULL) OR {em['marca']} = :mk) "
            f"AND {em['ancho']} = :ancho"
        )
    else:
        where_mat = f"{em['mat']} = :mid AND {em['ancho']} = :ancho"

    stock_actual_mat = conn.execute(
        text(f"SELECT {em['stock']} FROM {em['table']} WHERE {where_mat} LIMIT 1"),
        params_mat,
    ).scalar()

    if stock_actual_mat is None:
        stock_actual_mat = 0.0

    if consumo_mat > stock_actual_mat:
        raise HTTPException(
            400,
            detail=(
                f"Stock insuficiente de MATERIAL: se requieren {consumo_mat} cm "
                f"y solo hay {stock_actual_mat} cm disponibles."
            ),
        )

    # === VALIDAR STOCK DE LAMINADO (solo si realmente lleva laminado) ===
    if requiere_lam:
        el = _get_el_cols(conn)
        params_lam = {
            "tipo":  lam_tipo_id,
            "mk":    lam_marca_id,
            "ancho": lam_ancho,
        }
        if el["marca"]:
            where_lam = (
                f"{el['tipo']} = :tipo "
                f"AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']} = :mk) "
                f"AND {el['ancho']} = :ancho"
            )
        else:
            where_lam = f"{el['tipo']} = :tipo AND {el['ancho']} = :ancho"

        stock_actual_lam = conn.execute(
            text(f"SELECT {el['stock']} FROM {el['table']} WHERE {where_lam} LIMIT 1"),
            params_lam,
        ).scalar()

        if stock_actual_lam is None:
            stock_actual_lam = 0.0

        if lam_consumo > stock_actual_lam:
            raise HTTPException(
                400,
                detail=(
                    f"Stock insuficiente de LAMINADO: se requieren {lam_consumo} cm "
                    f"y solo hay {stock_actual_lam} cm."
                ),
            )

    # 4) Ajustes de inventario (descuento real)
    _ajuste_stock_material(
        conn,
        material_id=row["material_id"],
        marca_id=row["marca_id"],
        ancho_cm=float(row["ancho_rollo_cm"] or 0.0),
        delta_cm=consumo_mat,
        id_orden=payload.id_orden,
        motivo="Finalización impresión",
    )

    if requiere_lam:
        _ajuste_stock_laminado(
            conn,
            lam_tipo_id=lam_tipo_id,
            lam_marca_id=lam_marca_id,
            ancho_cm=lam_ancho,
            delta_cm=lam_consumo,
            id_orden=payload.id_orden,
            motivo="Finalización laminado",
        )

    lam_col = _get_orden_lam_consumo_col(conn)
    if lam_col:
        conn.execute(
            text(f"UPDATE ordenes_de_impresion SET {lam_col} = :v WHERE id_orden = :oid"),
            {"v": float(lam_consumo if requiere_lam else 0.0), "oid": payload.id_orden},
        )


    conn.commit()
    notify({"type": "order.finalized", "payload": {"id_orden": payload.id_orden}})
    return {
        "ok": True,
        "consumo_material_cm": consumo_mat,
        "consumo_laminado_cm": (lam_consumo if requiere_lam else 0.0),
    }





@router.get("/reportes/produccion", summary="Reporte Producción (Costos)")
def reporte_produccion(
    desde: date = Query(...),
    hasta: date = Query(...),
    conn: Connection = Depends(get_conn),
):
    em = _get_em_cols(conn)

    # Si no existe columna de costo, devolvemos 0 para costo_mat
    costo_expr = f"COALESCE(em.{em['costo']}, 0)" if em.get("costo") else "0"

    # JOIN defensivo a existencias_material por (material_id, marca_id, ancho_cm)
    # ✅ FIX: tolerancia por decimales en el ancho (105 vs 105.0 vs 105.00)
    join_em = f"""
        LEFT JOIN {em['table']} em
          ON em.{em['mat']} = o.id_material_FINAL
         AND ROUND(em.{em['ancho']}, 2) = ROUND(o.`MEDIDA DE ROLLO`, 2)
    """
    if em.get("marca"):
        join_em += f"""
         AND (
               (o.fk_id_marca IS NULL AND em.{em['marca']} IS NULL)
            OR  (o.fk_id_marca = em.{em['marca']})
         )
        """

    sql = f"""
    SELECT
        o.id_orden,
        e.nombre_estado AS estado,
        o.FECHA AS fecha,
        o.fecha_entrega AS entrega,

        m.Material AS material,
        o.fk_id_marca AS marca_id,
        o.`MEDIDA DE ROLLO` AS ancho_rollo_cm,
        o.LARGO,
        o.REP,

        -- ✅ Consumo esperado (Consumo.esp.(cm))
        (
            COALESCE(o.total_largo_impreso_cm, o.LARGO) * (o.REP + 1)
          + COALESCE(o.espacio_reps_cm, 0) * o.REP
          + COALESCE(o.`DESP LARGO`, 0)
        ) AS consumo_esp_cm,

        -- ✅ DEBUG: ¿Encontró fila en existencias?
        CASE WHEN em.{em['mat']} IS NULL THEN 0 ELSE 1 END AS match_existencias,

        -- ✅ DEBUG: costo por cm usado (si esto sale 0 o NULL, tu costo no está en existencias o no hizo match)
        {costo_expr} AS costo_cm_usado,

        -- ✅ Costo Mat = consumo esperado * costo_cm
        (
            (
                COALESCE(o.total_largo_impreso_cm, o.LARGO) * (o.REP + 1)
              + COALESCE(o.espacio_reps_cm, 0) * o.REP
              + COALESCE(o.`DESP LARGO`, 0)
            ) * {costo_expr}
        ) AS costo_mat

    FROM ordenes_de_impresion o
    LEFT JOIN catalogo_estados_orden e ON e.id_estado = o.fk_id_estado
    LEFT JOIN catalogo_materiales m ON m.id_catalogo = o.id_material_FINAL
    {join_em}
    WHERE o.FECHA BETWEEN :desde AND :hasta
      AND (o.archivado IS NULL OR o.archivado = 0)
    ORDER BY o.id_orden DESC
    LIMIT 2000
    """

    rows = conn.execute(text(sql), {"desde": desde, "hasta": hasta}).mappings().all()
    return [dict(r) for r in rows]




@router.post("/cancelar", summary="Cancelar / eliminar orden")
def cancelar(payload: OrdenCancelar, conn: Connection = Depends(get_conn)):
    # Verificación para evitar valores nulos
    if not payload.merma_cancel_cm and not payload.desp_largo_cm:
        raise HTTPException(status_code=400, detail="Debe proporcionar merma o desp_largo_cm.")

    # Tomar la merma desde merma_cancel_cm (nuevo) o, si no viene, desde desp_largo_cm (compatibilidad)
    merma_raw = payload.merma_cancel_cm or payload.desp_largo_cm
    merma = float(merma_raw)

    # Si no se proporciona material_id o marca_id, devolvemos un error
    if payload.id_orden is None:
        raise HTTPException(status_code=400, detail="Orden no encontrada")

    row = _one(conn, """
        SELECT id_material_FINAL AS material_id,
               fk_id_marca       AS marca_id,
               `MEDIDA DE ROLLO` AS ancho_rollo_cm
        FROM ordenes_de_impresion
        WHERE id_orden = :oid
    """, {"oid": payload.id_orden})

    if not row:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    material_id = row.get("material_id")
    marca_id = row.get("marca_id")

    if material_id is None:
        raise HTTPException(status_code=400, detail="Material no encontrado para la orden")


    # Lógica de cancelación
    # Solo ajustamos inventario si la merma es positiva
    if merma > 0:
        _ajuste_stock_material(
            conn,
            material_id=int(material_id),
            marca_id=int(marca_id) if marca_id else None,
            ancho_cm=float(row["ancho_rollo_cm"]),
            delta_cm=merma,
            id_orden=payload.id_orden,
            motivo="Cancelación (merma)",
        )

    # Actualiza estado de la orden a CANCELADA
    upd = text(""" 
        UPDATE ordenes_de_impresion
        SET fk_id_estado = 4,
            cancelado_at = NOW(),
            `DESP LARGO` = :merma
        WHERE id_orden = :oid AND fk_id_estado IN (1,2)
    """)
    res = conn.execute(upd, {"merma": merma, "oid": payload.id_orden})
    conn.commit()

    if res.rowcount == 0:
        conn.rollback()
        raise HTTPException(status_code=409, detail="No es posible CANCELAR (estado inválido).")

    notify({"type": "order.cancelled", "payload": {"id_orden": payload.id_orden, "merma_cancel_cm": merma}})
    return {"ok": True, "merma_cancel_cm": merma}




@router.post("/editar-consecutivo", summary="Editar consecutivo")
def editar_consecutivo(payload: EditConsec, conn: Connection = Depends(get_conn)):
    upd = text("""UPDATE ordenes_de_impresion SET CONSECUTIVO = :c WHERE id_orden = :oid""")
    res = conn.execute(upd, {"c": (payload.consecutivo or "").strip() or None, "oid": payload.id_orden})
    conn.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")
    notify({"type": "order.edited", "payload": {"id_orden": payload.id_orden, "consecutivo": payload.consecutivo}})
    return {"ok": True}


@router.post("/limpiar-finalizadas", summary="Archivar finalizadas (se ocultan del panel)")
def limpiar_finalizadas(req: LimpiarFinalizadasReq = LimpiarFinalizadasReq(), conn: Connection = Depends(get_conn)):
    if req.dias:
        sql = text("""
            UPDATE ordenes_de_impresion
            SET archivado = 1, archivado_at = NOW()
            WHERE fk_id_estado = 3
              AND (archivado IS NULL OR archivado = 0)
              AND FECHA < DATE_SUB(CURDATE(), INTERVAL :d DAY)
        """)
        res = conn.execute(sql, {"d": int(req.dias)})
    else:
        sql = text("""
            UPDATE ordenes_de_impresion
            SET archivado = 1, archivado_at = NOW()
            WHERE fk_id_estado = 3
              AND (archivado IS NULL OR archivado = 0)
        """)
        res = conn.execute(sql)
    conn.commit()
    n = res.rowcount or 0
    notify({"type": "orders.archived", "payload": {"count": int(n)}})
    return {"archivadas": int(n)}
