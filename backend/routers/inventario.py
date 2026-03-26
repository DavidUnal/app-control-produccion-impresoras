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
    Retorna: table, mat, marca (opcional), ancho, stock, costo (opcional), reservado (opcional)

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
    costo = _first_existing(conn, table, ["costo_cm", "COSTO_CM", "costo"])
    reserv= _first_existing(conn, table, ["reservado_cm", "RESERVADO_CM", "reservado"])

    if not (mat and ancho and stock):
        raise HTTPException(
            status_code=500,
            detail="No se pudieron resolver columnas de existencias_material.",
        )

    return {
        "table": table,
        "mat": mat,
        "marca": marca,
        "ancho": ancho,
        "stock": stock,
        "costo": costo,
        "reserv": reserv,
    }



def _get_el_cols(conn: Connection) -> dict:
    """
    Tabla de existencias de LAMINADO.
    Retorna: table, tipo, marca (opcional), ancho, stock, costo (opcional), reservado (opcional)
    """
    table = "existencias_laminado"

    tipo  = _first_existing(conn, table, [
        "lam_tipo_id", "fk_tipo_laminado", "id_tipo_laminado",
        "lam_id_tipo", "id_tipo", "tipo_id"
    ])

    marca = _first_existing(conn, table, [
        "lam_marca_id", "fk_marca_laminado", "id_marca_laminado",
        "id_marca", "lam_id_marca", "lam_marca_id0"
    ])

    ancho = _first_existing(conn, table, ["ancho_cm", "ANCHO", "ancho"])
    stock = _first_existing(conn, table, ["stock_cm", "STOCK_CM", "stock"])
    costo = _first_existing(conn, table, ["costo_cm", "COSTO_CM", "costo"])
    reserv= _first_existing(conn, table, ["reservado_cm", "RESERVADO_CM", "reservado"])

    if not (tipo and ancho and stock):
        raise HTTPException(
            status_code=500,
            detail="No se pudieron resolver columnas de existencias_laminado.",
        )

    return {
        "table": table,
        "tipo": tipo,
        "marca": marca,
        "ancho": ancho,
        "stock": stock,
        "costo": costo,
        "reserv": reserv,
    }

# ---------- helpers ----------
def _ensure_row(conn: Connection, table: str, where: str, insert_cols: str, insert_vals: str, params: dict) -> bool:
    """Crea la fila si no existe. Devuelve True si insertó."""
    exists = conn.execute(text(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1"), params).first()
    if not exists:
        conn.execute(text(f"INSERT INTO {table} ({insert_cols}) VALUES ({insert_vals})"), params)
        return True
    return False

def _select_stock(
    conn: Connection,
    table: str,
    stock_col: str,
    reserv_col: str | None,
    where: str,
    params: dict,
    costo_col: str | None = None,
) -> dict | None:
    sql = f"""
        SELECT {stock_col} AS stock_cm,
               {('COALESCE(' + reserv_col + ',0)') if reserv_col else '0'} AS reservado_cm
               {(', COALESCE(' + costo_col + ',0) AS costo_cm') if costo_col else ''}
        FROM {table}
        WHERE {where}
        LIMIT 1
    """
    row = conn.execute(text(sql), params).mappings().first()

    if row is None:
        return None

    out = {
        "stock_cm": float(row.get("stock_cm") or 0.0),
        "reservado_cm": float(row.get("reservado_cm") or 0.0),
    }
    if costo_col:
        out["costo_cm"] = float(row.get("costo_cm") or 0.0)
    return out



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
        costo_col=em.get("costo"),
    )

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
        if em.get("costo"):
            insert_cols = f"{em['mat']}, {em['marca']}, {em['ancho']}, {em['stock']}, {em['costo']}"
            insert_vals = ":mid, :mk, :ancho, 0, 0"
        else:
            insert_cols = f"{em['mat']}, {em['marca']}, {em['ancho']}, {em['stock']}"
            insert_vals = ":mid, :mk, :ancho, 0"
    else:
        where = f"""{em['mat']}=:mid AND {em['ancho']}=:ancho"""
        if em.get("costo"):
            insert_cols = f"{em['mat']}, {em['ancho']}, {em['stock']}, {em['costo']}"
            insert_vals = ":mid, :ancho, 0, 0"
        else:
            insert_cols = f"{em['mat']}, {em['ancho']}, {em['stock']}"
            insert_vals = ":mid, :ancho, 0"

    created = _ensure_row(
        conn,
        em["table"],
        where,
        insert_cols,
        insert_vals,
        {"mid": mid, "mk": mk, "ancho": ancho},
    )
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
        costo_col=el.get("costo"),
    )

    if data is None:
        return []

    return [{
        "lam_tipo_id": lam_tipo_id,
        "lam_marca_id": lam_marca_id,
        "ancho_cm": float(ancho_cm),
        **data,
    }]




@router.get("/materiales", summary="Materiales visibles en inventario (activos)")
def materiales_inventario(conn: Connection = Depends(get_conn)):
    em = _get_em_cols(conn)

    # columnas reales del catálogo
    # (asumo: id_catalogo, Material, deleted_at, is_active existen)
    sql = f"""
        SELECT DISTINCT
            m.id_catalogo AS id,
            m.Material AS nombre
        FROM {em["table"]} em
        JOIN catalogo_materiales m
          ON m.id_catalogo = em.{em["mat"]}
        WHERE m.deleted_at IS NULL
          AND m.is_active = 1
          AND TRIM(COALESCE(m.Material,'')) <> ''
        ORDER BY m.Material
    """
    rows = conn.execute(text(sql)).mappings().all()
    return [dict(r) for r in rows]




@router.get("/laminados/tipos", summary="Tipos de laminado visibles en inventario (activos)")
def tipos_laminado_inventario(conn: Connection = Depends(get_conn)):
    el = _get_el_cols(conn)

    sql = f"""
        SELECT DISTINCT
            tl.id_laminado AS id,
            tl.nombre_laminado AS nombre
        FROM {el["table"]} el
        JOIN catalogo_tipos_laminado tl
          ON tl.id_laminado = el.{el["tipo"]}
        WHERE tl.deleted_at IS NULL
          AND tl.is_active = 1
          AND TRIM(COALESCE(tl.nombre_laminado,'')) <> ''
        ORDER BY tl.nombre_laminado
    """
    rows = conn.execute(text(sql)).mappings().all()
    return [dict(r) for r in rows]







@router.post("/laminados/ensure")
def ensure_laminado(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    tipo  = int(payload.get("lam_tipo_id") or payload.get("tipo") or payload.get("laminado_tipo_id"))
    ancho = float(payload.get("ancho_cm", 0))
    
    # 👇 1. SOLUCIÓN AL ERROR 500: Blindaje contra textos vacíos ("") o nulos
    mk_raw = payload.get("lam_marca_id")
    if mk_raw in ("", "None", None, "0", 0):
        mk = None
    else:
        mk = int(mk_raw)

    el = _get_el_cols(conn)

    if el.get("marca"):
        where = f"""{el['tipo']}=:tipo AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']}=:mk) AND {el['ancho']}=:ancho"""
        if el.get("costo"):
            insert_cols = f"{el['tipo']}, {el['marca']}, {el['ancho']}, {el['stock']}, {el['costo']}"
            insert_vals = ":tipo, :mk, :ancho, 0, 0"
        else:
            insert_cols = f"{el['tipo']}, {el['marca']}, {el['ancho']}, {el['stock']}"
            insert_vals = ":tipo, :mk, :ancho, 0"
            
        # 👇 2. Inyección dinámica de la columna especial de laminados para evitar fallos SQL
        insert_cols += ", lam_marca_id0"
        insert_vals += ", :mk0"
        mk0 = 0 if mk is None else mk
        
    else:
        where = f"""{el['tipo']}=:tipo AND {el['ancho']}=:ancho"""
        if el.get("costo"):
            insert_cols = f"{el['tipo']}, {el['ancho']}, {el['stock']}, {el['costo']}"
            insert_vals = ":tipo, :ancho, 0, 0"
        else:
            insert_cols = f"{el['tipo']}, {el['ancho']}, {el['stock']}"
            insert_vals = ":tipo, :ancho, 0"
        mk0 = 0 # Fallback por si acaso

    # 3. Ejecución segura usando tus propios helpers
    created = _ensure_row(
        conn,
        el["table"],
        where,
        insert_cols,
        insert_vals,
        {"tipo": tipo, "mk": mk, "mk0": mk0, "ancho": ancho},
    )
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

    # where
    if el["marca"]:
        where = f"""{el['tipo']}=:tipo AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']}=:mk) AND {el['ancho']}=:ancho"""
    else:
        where = f"""{el['tipo']}=:tipo AND {el['ancho']}=:ancho"""

    # asegurar fila (si no existe)
    if el["marca"]:
        insert_cols = f"{el['tipo']}, {el['marca']}, {el['ancho']}, {el['stock']}"
        insert_vals = ":tipo, :mk, :ancho, 0"
    else:
        insert_cols = f"{el['tipo']}, {el['ancho']}, {el['stock']}"
        insert_vals = ":tipo, :ancho, 0"

    _ensure_row(conn, el["table"], where, insert_cols, insert_vals, {"tipo": tipo, "mk": mk, "ancho": ancho})

    # obtener stock actual
    row = conn.execute(
        text(f"SELECT {el['stock']} FROM {el['table']} WHERE {where} LIMIT 1"),
        {"tipo": tipo, "mk": mk, "ancho": ancho}
    ).scalar()

    actual = float(row or 0.0)
    nuevo = actual + delta

    # ❌ prohibir negativo
    if nuevo < 0:
        raise HTTPException(400, detail="No se puede dejar el stock negativo.")

    # aplicar ajuste
    conn.execute(
        text(f"UPDATE {el['table']} SET {el['stock']}=:nuevo WHERE {where} LIMIT 1"),
        {"nuevo": nuevo, "tipo": tipo, "mk": mk, "ancho": ancho}
    )

    conn.commit()

    notify({"type": "inventory.laminate.adjusted",
            "payload": {"lam_tipo_id": tipo, "lam_marca_id": mk, "ancho_cm": ancho,
                        "delta_cm": delta, "stock_cm": nuevo}})

    return {"ok": True, "stock_cm": nuevo}








@router.post("/costo")
def set_costo_material(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    mid   = int(payload["material_id"])
    ancho = float(payload["ancho_cm"])
    mk    = payload.get("marca_id")
    mk    = int(mk) if mk is not None else None
    costo = float(payload.get("costo_cm", 0.0))
    if costo < 0:
        raise HTTPException(400, detail="El costo_cm no puede ser negativo.")

    em = _get_em_cols(conn)
    if not em.get("costo"):
        raise HTTPException(500, detail="La columna costo_cm no existe en existencias_material.")

    # asegurar fila
    if em["marca"]:
        where = f"""{em['mat']}=:mid AND ((:mk IS NULL AND {em['marca']} IS NULL) OR {em['marca']}=:mk) AND {em['ancho']}=:ancho"""
        insert_cols = f"{em['mat']}, {em['marca']}, {em['ancho']}, {em['stock']}, {em['costo']}"
        insert_vals = ":mid, :mk, :ancho, 0, 0"
    else:
        where = f"""{em['mat']}=:mid AND {em['ancho']}=:ancho"""
        insert_cols = f"{em['mat']}, {em['ancho']}, {em['stock']}, {em['costo']}"
        insert_vals = ":mid, :ancho, 0, 0"

    _ensure_row(conn, em["table"], where, insert_cols, insert_vals, {"mid": mid, "mk": mk, "ancho": ancho})

    conn.execute(
        text(f"UPDATE {em['table']} SET {em['costo']}=:c WHERE {where} LIMIT 1"),
        {"c": costo, "mid": mid, "mk": mk, "ancho": ancho},
    )
    conn.commit()

    notify({"type": "inventory.material.cost.updated",
            "payload": {"material_id": mid, "marca_id": mk, "ancho_cm": ancho, "costo_cm": costo}})

    return {"ok": True, "costo_cm": costo}




@router.post("/laminados/costo")
def set_costo_laminado(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    tipo  = int(payload.get("lam_tipo_id") or payload.get("tipo") or payload.get("laminado_tipo_id"))
    ancho = float(payload["ancho_cm"])
    mk    = payload.get("lam_marca_id")
    mk    = int(mk) if mk is not None else None
    costo = float(payload.get("costo_cm", 0.0))
    if costo < 0:
        raise HTTPException(400, detail="El costo_cm no puede ser negativo.")

    el = _get_el_cols(conn)
    if not el.get("costo"):
        raise HTTPException(500, detail="La columna costo_cm no existe en existencias_laminado.")

    # asegurar fila
    if el["marca"]:
        where = f"""{el['tipo']}=:tipo AND ((:mk IS NULL AND {el['marca']} IS NULL) OR {el['marca']}=:mk) AND {el['ancho']}=:ancho"""
        insert_cols = f"{el['tipo']}, {el['marca']}, {el['ancho']}, {el['stock']}, {el['costo']}"
        insert_vals = ":tipo, :mk, :ancho, 0, 0"
    else:
        where = f"""{el['tipo']}=:tipo AND {el['ancho']}=:ancho"""
        insert_cols = f"{el['tipo']}, {el['ancho']}, {el['stock']}, {el['costo']}"
        insert_vals = ":tipo, :ancho, 0, 0"

    _ensure_row(conn, el["table"], where, insert_cols, insert_vals, {"tipo": tipo, "mk": mk, "ancho": ancho})

    conn.execute(
        text(f"UPDATE {el['table']} SET {el['costo']}=:c WHERE {where} LIMIT 1"),
        {"c": costo, "tipo": tipo, "mk": mk, "ancho": ancho},
    )
    conn.commit()

    notify({"type": "inventory.laminate.cost.updated",
            "payload": {"lam_tipo_id": tipo, "lam_marca_id": mk, "ancho_cm": ancho, "costo_cm": costo}})

    return {"ok": True, "costo_cm": costo}






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
