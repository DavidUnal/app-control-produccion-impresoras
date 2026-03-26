# backend/routers/inventario.py
from __future__ import annotations

# --- eventos en tiempo real (no requiere pip install) ---
try:
    from backend.events import notify
except Exception:
    # fallback silencioso si alguien ejecuta el archivo suelto
    def notify(*_args, **_kwargs):
        pass

from fastapi import APIRouter, Depends, Query, Body, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection
from backend.db import get_conn

# sin prefix aquí; lo añade app.include_router(..., prefix="/inventario")
router = APIRouter(tags=["Inventario"])

# ---------- utilidades de introspección ----------
def _col_exists(conn: Connection, table: str, col: str) -> bool:
    q = text("""
        SELECT 1
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :t AND COLUMN_NAME = :c
        LIMIT 1
    """)
    return conn.execute(q, {"t": table, "c": col}).first() is not None

def _first_existing(conn: Connection, table: str, candidates: list[str]) -> str | None:
    for c in candidates:
        if _col_exists(conn, table, c):
            return c
    return None

def _get_em_cols(conn: Connection) -> dict:
    """
    Tabla de existencias de MATERIAL.
    Retorna: table, mat, marca (opcional), ancho, stock, reservado (opcional)

    OJO:  'marca_id0' es una columna generada en MySQL, así que
    NUNCA debemos intentar insertar en ella.  Por eso aquí
    priorizamos siempre la columna normal ('id_marca' / 'marca_id').
    """
    table = "existencias_material"
    mat   = _first_existing(conn, table, [
        "material_id", "id_material", "id_material_final",
        "id_material_FINAL", "fk_id_material",
    ])
    # Priorizar columnas "normales" que sí admiten INSERT
    marca = _first_existing(conn, table, [
        "id_marca", "marca_id", "fk_id_marca", "marca_id0",
    ])
    ancho = _first_existing(conn, table, ["ancho_cm", "ANCHO", "ancho"])
    stock = _first_existing(conn, table, ["stock_cm", "STOCK_CM", "stock"])
    reserv= _first_existing(conn, table, ["reservado_cm", "RESERVADO_CM", "reservado"])

    if not (mat and ancho and stock):
        raise HTTPException(
            status_code=500,
            detail="No se pudieron resolver columnas de existencias_material.",
        )
    return {"table": table, "mat": mat, "marca": marca,
            "ancho": ancho, "stock": stock, "reserv": reserv}


def _get_el_cols(conn: Connection) -> dict:
    """
    Tabla de existencias de LAMINADO.
    Retorna: table, tipo, marca (opcional), ancho, stock, reservado (opcional)
    """
    table = "existencias_laminado"
    # nombres reales compatibles (incluye tus variantes lam_id_tipo, lam_id_marca, lam_marca_id0)
    tipo  = _first_existing(conn, table, ["lam_tipo_id","fk_tipo_laminado","id_tipo_laminado","lam_id_tipo","id_tipo","tipo_id"])
    marca = _first_existing(conn, table, ["lam_marca_id","fk_marca_laminado","id_marca_laminado","id_marca","lam_id_marca","lam_marca_id0"])
    ancho = _first_existing(conn, table, ["ancho_cm","ANCHO","ancho"])
    stock = _first_existing(conn, table, ["stock_cm","STOCK_CM","stock"])
    reserv= _first_existing(conn, table, ["reservado_cm","RESERVADO_CM","reservado"])

    if not (tipo and ancho and stock):
        raise HTTPException(status_code=500, detail="No se pudieron resolver columnas de existencias_laminado.")
    return {"table": table, "tipo": tipo, "marca": marca, "ancho": ancho, "stock": stock, "reserv": reserv}

# ---------- helpers ----------
def _ensure_row(conn: Connection, table: str, where: str, insert_cols: str, insert_vals: str, params: dict) -> bool:
    """Crea la fila si no existe. Devuelve True si insertó."""
    exists = conn.execute(text(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1"), params).first()
    if not exists:
        conn.execute(text(f"INSERT INTO {table} ({insert_cols}) VALUES ({insert_vals})"), params)
        return True
    return False

def _select_stock(conn: Connection, table: str, stock_col: str, reserv_col: str | None,
                  where: str, params: dict) -> dict | None:
    sql = f"""
        SELECT {stock_col} AS stock_cm,
               {('COALESCE(' + reserv_col + ',0)') if reserv_col else '0'} AS reservado_cm
        FROM {table}
        WHERE {where}
        LIMIT 1
    """
    row = conn.execute(text(sql), params).mappings().first()

    # ⬅️ CAMBIO IMPORTANTE:
    # Si no hay fila en la tabla, devolvemos None (en lugar de 0/0)
    if row is None:
        return None

    return {
        "stock_cm": float(row.get("stock_cm") or 0.0),
        "reservado_cm": float(row.get("reservado_cm") or 0.0),
    }


# =========================================================
# ===================   MATERIALES   ======================
# =========================================================
@router.get("/existencias")
def existencias_material(
    material_id: int,
    ancho_cm: float,
    marca_id: int | None = Query(None),
    conn: Connection = Depends(get_conn),
):
    em = _get_em_cols(conn)
    if em["marca"]:
        where = (
            f"{em['mat']}=:mid "
            f"AND ((:mk IS NULL AND {em['marca']} IS NULL) OR {em['marca']}=:mk) "
            f"AND {em['ancho']}=:ancho"
        )
    else:
        where = f"{em['mat']}=:mid AND {em['ancho']}=:ancho"

    data = _select_stock(
        conn,
        em["table"],
        em["stock"],
        em["reserv"],
        where,
        {"mid": material_id, "mk": marca_id, "ancho": float(ancho_cm)},
    )

    # ⬅️ SI NO EXISTE FILA, NO DEVOLVEMOS NADA
    if data is None:
        return []

    return [{
        "material_id": material_id,
        "marca_id": marca_id,
        "ancho_cm": float(ancho_cm),
        **data,
    }]


@router.post("/ensure")
def ensure_material(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    mid   = int(payload["material_id"])
    ancho = float(payload["ancho_cm"])
    mk    = int(payload["marca_id"]) if payload.get("marca_id") is not None else None

    em = _get_em_cols(conn)
    if em["marca"]:
        where = f"""{em['mat']}=:mid AND ((:mk IS NULL AND {em['marca']} IS NULL) OR {em['marca']}=:mk) AND {em['ancho']}=:ancho"""
        insert_cols = f"{em['mat']}, {em['marca']}, {em['ancho']}, {em['stock']}"
        insert_vals = ":mid, :mk, :ancho, 0"
    else:
        where = f"""{em['mat']}=:mid AND {em['ancho']}=:ancho"""
        insert_cols = f"{em['mat']}, {em['ancho']}, {em['stock']}"
        insert_vals = ":mid, :ancho, 0"

    created = _ensure_row(conn, em["table"], where, insert_cols, insert_vals, {"mid": mid, "mk": mk, "ancho": ancho})
    conn.commit()

    if created:
        notify({"type": "inventory.material.created",
                "payload": {"material_id": mid, "marca_id": mk, "ancho_cm": ancho}})

    return {"ok": True, "created": bool(created)}

@router.post("/ajuste")
def ajuste_material(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    mid   = int(payload["material_id"])
    ancho = float(payload["ancho_cm"])
    delta = float(payload.get("delta_cm", 0.0))
    mk    = int(payload["marca_id"]) if payload.get("marca_id") is not None else None

    em = _get_em_cols(conn)

    # Asegurar fila
    if em["marca"]:
        where = f"""{em['mat']}=:mid AND ((:mk IS NULL AND {em['marca']} IS NULL) OR {em['marca']}=:mk) AND {em['ancho']}=:ancho"""
    else:
        where = f"""{em['mat']}=:mid AND {em['ancho']}=:ancho"""

    # Obtener stock actual
    row = conn.execute(
        text(f"SELECT {em['stock']} FROM {em['table']} WHERE {where} LIMIT 1"),
        {"mid": mid, "mk": mk, "ancho": ancho}
    ).scalar()

    actual = float(row or 0)
    nuevo = actual + delta

    # ❌ PROHIBIR STOCK NEGATIVO
    if nuevo < 0:
        raise HTTPException(400, detail="No se puede dejar el stock negativo.")

    # Aplicar ajuste
    conn.execute(
        text(f"UPDATE {em['table']} SET {em['stock']}=:nuevo WHERE {where} LIMIT 1"),
        {"nuevo": nuevo, "mid": mid, "mk": mk, "ancho": ancho}
    )

    conn.commit()

    notify({"type": "inventory.material.adjusted",
            "payload": {"material_id": mid, "marca_id": mk, "ancho_cm": ancho,
                        "delta_cm": delta, "stock_cm": nuevo}})

    return {"ok": True, "stock_cm": nuevo}


@router.delete("/delete")
def eliminar_combinacion_material(
    material_id: int,
    ancho_cm: float,
    marca_id: int | None = Query(None),
    conn: Connection = Depends(get_conn),
):
    """Elimina una combinación de material/marca/ancho en existencias_material."""
    em = _get_em_cols(conn)
    if em["marca"]:
        where = f"""{em['mat']}=:mid AND ((:mk IS NULL AND {em['marca']} IS NULL) OR {em['marca']}=:mk) AND {em['ancho']}=:an"""
    else:
        where = f"""{em['mat']}=:mid AND {em['ancho']}=:an"""

    res = conn.execute(text(f"DELETE FROM {em['table']} WHERE {where}"), {
        "mid": material_id,
        "mk": marca_id,
        "an": float(ancho_cm)
    })
    conn.commit()
    if res.rowcount == 0:
        raise HTTPException(404, detail="Combinación no encontrada.")

    notify({"type": "inventory.material.deleted",
            "payload": {"material_id": material_id, "marca_id": marca_id, "ancho_cm": float(ancho_cm)}})
    return {"ok": True, "deleted": int(res.rowcount)}

@router.get("/movimientos")
def movimientos_material(material_id: int, ancho_cm: float, limit: int = 20, marca_id: int | None = Query(None)):
    # (por ahora) tu UI solo lo usa informativo
    return []

# =========================================================
# ====================   LAMINADOS   ======================
# =========================================================
@router.get("/laminados/existencias")
def existencias_laminado(
    lam_tipo_id: int,
    ancho_cm: float,
    lam_marca_id: int | None = Query(None),
    conn: Connection = Depends(get_conn),
):
    el = _get_el_cols(conn)
    if el["marca"]:
        where = (
            f"{el['tipo']}=:tipo "
            f"AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']}=:mk) "
            f"AND {el['ancho']}=:ancho"
        )
    else:
        where = f"{el['tipo']}=:tipo AND {el['ancho']}=:ancho"

    data = _select_stock(
        conn,
        el["table"],
        el["stock"],
        el["reserv"],
        where,
        {"tipo": lam_tipo_id, "mk": lam_marca_id, "ancho": float(ancho_cm)},
    )

    # ⬅️ igual que en materiales
    if data is None:
        return []

    return [{
        "lam_tipo_id": lam_tipo_id,
        "lam_marca_id": lam_marca_id,
        "ancho_cm": float(ancho_cm),
        **data,
    }]

@router.post("/laminados/ensure")
def ensure_laminado(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    tipo  = int(payload.get("lam_tipo_id") or payload.get("tipo") or payload.get("laminado_tipo_id"))
    ancho = float(payload["ancho_cm"])
    mk    = payload.get("lam_marca_id")
    mk    = int(mk) if mk is not None else None

    el = _get_el_cols(conn)
    if el["marca"]:
        where = f"""{el['tipo']}=:tipo AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']}=:mk) AND {el['ancho']}=:ancho"""
        insert_cols = f"{el['tipo']}, {el['marca']}, {el['ancho']}, {el['stock']}"
        insert_vals = ":tipo, :mk, :ancho, 0"
    else:
        where = f"""{el['tipo']}=:tipo AND {el['ancho']}=:ancho"""
        insert_cols = f"{el['tipo']}, {el['ancho']}, {el['stock']}"
        insert_vals = ":tipo, :ancho, 0"

    created = _ensure_row(conn, el["table"], where, insert_cols, insert_vals, {"tipo": tipo, "mk": mk, "ancho": ancho})
    conn.commit()

    if created:
        notify({"type": "inventory.laminate.created",
                "payload": {"lam_tipo_id": tipo, "lam_marca_id": mk, "ancho_cm": ancho}})

    return {"ok": True, "created": bool(created)}

@router.post("/laminados/ajuste")
def ajuste_laminado(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    tipo  = int(payload.get("lam_tipo_id") or payload.get("tipo") or payload.get("laminado_tipo_id"))
    ancho = float(payload["ancho_cm"])
    delta = float(payload.get("delta_cm", 0.0))
    mk    = payload.get("lam_marca_id")
    mk    = int(mk) if mk is not None else None

    el = _get_el_cols(conn)
    if el["marca"]:
        where = f"""{el['tipo']}=:tipo AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']}=:mk) AND {el['ancho']}=:ancho"""
        insert_cols = f"{el['tipo']}, {el['marca']}, {el['ancho']}, {el['stock']}"
        insert_vals = ":tipo, :mk, :ancho, 0"
    else:
        where = f"""{el['tipo']}=:tipo AND {el['ancho']}=:ancho"""
        insert_cols = f"{el['tipo']}, {el['ancho']}, {el['stock']}"
        insert_vals = ":tipo, :ancho, 0"

    _ensure_row(conn, el["table"], where, insert_cols, insert_vals, {"tipo": tipo, "mk": mk, "ancho": ancho})

    conn.execute(text(f"UPDATE {el['table']} SET {el['stock']}={el['stock']} + :d WHERE {where} LIMIT 1"),
                 {"d": delta, "tipo": tipo, "mk": mk, "ancho": ancho})

    data = _select_stock(conn, el["table"], el["stock"], el["reserv"], where,
                         {"tipo": tipo, "mk": mk, "ancho": ancho})
    conn.commit()

    notify({"type": "inventory.laminate.adjusted",
            "payload": {"lam_tipo_id": tipo, "lam_marca_id": mk, "ancho_cm": ancho,
                        "delta_cm": delta, **data}})
    return {"ok": True, **data}

@router.delete("/laminados/delete")
def eliminar_combinacion_laminado(
    lam_tipo_id: int,
    ancho_cm: float,
    lam_marca_id: int | None = Query(None),
    conn: Connection = Depends(get_conn),
):
    """Elimina una combinación de laminado/tipo/marca/ancho en existencias_laminado."""
    el = _get_el_cols(conn)
    if el["marca"]:
        where = f"""{el['tipo']}=:tipo AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']}=:mk) AND {el['ancho']}=:an"""
    else:
        where = f"""{el['tipo']}=:tipo AND {el['ancho']}=:an"""

    res = conn.execute(text(f"DELETE FROM {el['table']} WHERE {where}"), {
        "tipo": lam_tipo_id,
        "mk": lam_marca_id,
        "an": float(ancho_cm)
    })
    conn.commit()
    if res.rowcount == 0:
        raise HTTPException(404, detail="Combinación no encontrada.")

    notify({"type": "inventory.laminate.deleted",
            "payload": {"lam_tipo_id": lam_tipo_id, "lam_marca_id": lam_marca_id, "ancho_cm": float(ancho_cm)}})
    return {"ok": True, "deleted": int(res.rowcount)}

@router.get("/laminados/movimientos")
def movimientos_laminado(lam_tipo_id: int, ancho_cm: float, limit: int = 20, lam_marca_id: int | None = Query(None)):
    # (por ahora) la UI lo usa informativo
    return []
