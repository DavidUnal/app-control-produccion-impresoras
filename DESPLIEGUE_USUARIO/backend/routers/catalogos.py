from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.db import get_conn
from backend.events import notify
router = APIRouter(prefix="/catalogos", tags=["Catálogos"])


# ------------------------
# Helpers
# ------------------------
def _rows(conn: Connection, sql: str, params: Dict[str, Any] | None = None):
    res = conn.execute(text(sql), params or {})
    return [dict(r._mapping) for r in res]


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


def _count_refs(conn: Connection, table: str, candidates: list[str], value: int) -> tuple[int, str | None]:
    """
    Devuelve (conteo, columna_usada) contando filas en `table` donde alguna
    columna de `candidates` == value. Si no existe ninguna columna, (0, None).
    """
    col = _first_existing(conn, table, candidates)
    if not col:
        return 0, None
    n = conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {col}=:v"), {"v": value}).scalar() or 0
    return int(n), col


def _one(conn: Connection, sql: str, params: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    res = conn.execute(text(sql), params or {})
    m = res.mappings().first()
    return dict(m) if m else None

def _is_lona(conn: Connection, material_id: int) -> bool:
    """True si el material (por id) empieza por 'LONA'."""
    row = conn.execute(
        text("SELECT UPPER(TRIM(Material)) AS nombre FROM catalogo_materiales WHERE id_catalogo = :mid"),
        {"mid": material_id},
    ).fetchone()
    nombre = (row[0] if row else "") or ""
    return nombre.startswith("LONA")

def _lam_tipo_nombre(conn: Connection, tipo_id: int) -> Optional[str]:
    row = conn.execute(
        text("SELECT TRIM(UPPER(nombre_laminado)) FROM catalogo_tipos_laminado WHERE id_laminado = :tid"),
        {"tid": tipo_id},
    ).fetchone()
    return (row[0] if row else None)

def _is_sin_laminar(conn: Connection, tipo_id: Optional[int], tipo_nombre: Optional[str]) -> bool:
    """Detecta 'SIN LAMINAR' por id o por nombre."""
    if tipo_nombre is not None:
        nombre = tipo_nombre
    elif tipo_id is not None:
        nombre = _lam_tipo_nombre(conn, tipo_id)
    else:
        return False
    return (nombre or "").strip().upper() in {"SIN LAMINAR", "SIN LAMINADO"}


# ======================================
#   MATERIALES
# ======================================
@router.get("/materiales", summary="Catálogo Materiales")
def materiales(conn: Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
    sql = """
    SELECT id_catalogo AS id, Material AS nombre
    FROM catalogo_materiales
    WHERE TRIM(COALESCE(Material,'')) <> ''
    ORDER BY nombre
    """
    return _rows(conn, sql)

@router.get("/materiales/{material_id}", summary="Material por ID")
def material_por_id(material_id: int, conn: Connection = Depends(get_conn)) -> Dict[str, Any]:
    sql = """
    SELECT id_catalogo AS id, Material AS nombre
    FROM catalogo_materiales
    WHERE id_catalogo = :mid
    """
    row = _one(conn, sql, {"mid": material_id})
    if not row:
        raise HTTPException(status_code=404, detail="Material no encontrado")
    return row

@router.get("/materiales/buscar", summary="Buscar Materiales (LIKE)")
def buscar_materiales(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
    conn: Connection = Depends(get_conn)
) -> List[Dict[str, Any]]:
    sql = """
    SELECT id_catalogo AS id, Material AS nombre
    FROM catalogo_materiales
    WHERE TRIM(COALESCE(Material,'')) <> '' AND Material LIKE :q
    ORDER BY nombre
    LIMIT :lim
    """
    return _rows(conn, sql, {"q": f"%{q}%", "lim": limit})


# ======================================
#   MARCAS (Material) – global (opcional filtrar por material)
#   - Adhesivo: excluye ('10 OZ','12 OZ','PUBLIMASTER')
#   - Lona:     solo ('10 OZ','12 OZ','PUBLIMASTER')
# ======================================
@router.get("/marcas", summary="Catálogo Marcas (Material)")
def marcas_material(
    material_id: Optional[int] = Query(None, description="Id del material seleccionado"),
    conn: Connection = Depends(get_conn),
) -> List[Dict[str, Any]]:
    if material_id is not None and _is_lona(conn, material_id):
        sql = """
            SELECT id_marca AS id, nombre_marca AS nombre
            FROM catalogo_marcas_material
            WHERE TRIM(COALESCE(nombre_marca,'')) <> ''
              AND UPPER(TRIM(nombre_marca)) IN ('10 OZ','12 OZ','PUBLIMASTER')
            ORDER BY nombre
        """
        return _rows(conn, sql)

    sql = """
        SELECT id_marca AS id, nombre_marca AS nombre
        FROM catalogo_marcas_material
        WHERE TRIM(COALESCE(nombre_marca,'')) <> ''
          AND UPPER(TRIM(nombre_marca)) NOT IN ('10 OZ','12 OZ','PUBLIMASTER')
        ORDER BY nombre
    """
    return _rows(conn, sql)


# ======================================
#   MEDIDAS por MATERIAL (adhesivo vs lona)
# ======================================
def _es_lona(nombre_material: str) -> bool:
    return "LONA" in (nombre_material or "").upper()


@router.delete("/medidas", summary="Eliminar medida de adhesivo/lona por ancho")
def eliminar_medida(
    categoria: str = Query(..., regex="^(?i)(ADHESIVO|LONA)$"),
    ancho_cm: float = Query(..., gt=0),
    conn: Connection = Depends(get_conn),
):
    tabla = "medidas_lonas" if categoria.strip().upper() == "LONA" else "medidas_adhesivos"
    # Normaliza a dos decimales para evitar falsos negativos
    ancho = round(float(ancho_cm), 2)

    res = conn.execute(text(f"DELETE FROM {tabla} WHERE ROUND(ancho_cm, 2) = :w"), {"w": ancho})
    conn.commit()
    return {"ok": True, "categoria": categoria.upper(), "ancho_cm": ancho, "rows_affected": int(res.rowcount or 0)}


@router.delete("/materiales/{material_id}", summary="Eliminar material")
def eliminar_material(
    material_id: int,
    force: bool = Query(False, description="Si True, también elimina existencias asociadas"),
    conn: Connection = Depends(get_conn),
):
    # 1) Verifica que exista
    mat = _one(conn,
               "SELECT id_catalogo AS id, Material AS nombre FROM catalogo_materiales WHERE id_catalogo=:mid",
               {"mid": material_id})
    if not mat:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    # 2) ¿Está referenciado en existencias_material?
    refs, ref_col = _count_refs(
        conn,
        table="existencias_material",
        candidates=["id_material", "id_material_final", "fk_id_material"],
        value=material_id
    )

    if refs and not force:
        raise HTTPException(
            status_code=409,
            detail=f"No se puede eliminar porque hay {refs} existencias asociadas. "
                   f"Vuelve a intentar con ?force=1 si deseas borrarlas también."
        )

    # 3) Borra en una sola transacción
    if refs and ref_col:
        conn.execute(text(f"DELETE FROM existencias_material WHERE {ref_col}=:v"), {"v": material_id})

    conn.execute(text("DELETE FROM catalogo_materiales WHERE id_catalogo=:mid"), {"mid": material_id})
    conn.commit()

    return {"ok": True, "deleted_material_id": material_id, "deleted_existencias": refs}



@router.get("/medidas", summary="Catálogo Medidas por material")
def medidas_por_material(
    material_id: int = Query(..., description="ID del material"),
    conn: Connection = Depends(get_conn),
) -> List[Dict[str, float]]:
    mat = _one(
        conn,
        "SELECT Material FROM catalogo_materiales WHERE id_catalogo = :mid",
        {"mid": material_id},
    )
    if not mat:
        raise HTTPException(status_code=404, detail="Material no encontrado")

    tabla = "medidas_lonas" if _es_lona(mat["Material"]) else "medidas_adhesivos"

    sql = f"""
    SELECT DISTINCT ancho_cm AS ancho
    FROM {tabla}
    WHERE ancho_cm IS NOT NULL
    ORDER BY 1
    """
    filas = _rows(conn, sql)
    return [{"ancho": float(r["ancho"])} for r in filas]

@router.get("/medidas/sugerida", summary="Medida sugerida del rollo (estricta)")
def medida_sugerida(
    material_id: int = Query(...),
    ancho_orden_cm: float = Query(..., gt=0),
    estricto: bool = Query(True, description="Si es True, exige rollo > arte; si no hay, devuelve 400"),
    conn: Connection = Depends(get_conn),
) -> Dict[str, float]:
    """Devuelve la primera medida de rollo estrictamente MAYOR al ancho del arte."""
    medidas = medidas_por_material(material_id, conn)
    anchos = sorted(m["ancho"] for m in medidas)
    for w in anchos:
        if ancho_orden_cm < w:              # <--- REGLA ESTRICTA
            return {"ancho_rollo_cm": w}    # clave esperada por tu UI

    if estricto:
        raise HTTPException(
            status_code=400,
            detail=(
                "Medida no válida / material no válido. "
                "No existe un ancho de rollo mayor que el ancho del arte para este material. "
                "Si no está disponible, puedes agregarlo en la sección de Inventario."
            ),
        )
    # compat: si estricto=False, responde el máximo disponible
    return {"ancho_rollo_cm": max(anchos) if anchos else 0.0}

@router.get("/medidas/validar_arte", summary="Valida arte vs rollo (respuesta estructurada)")
def validar_arte_vs_rollo(
    material_id: int = Query(...),
    ancho_arte_cm: float = Query(..., gt=0),
    conn: Connection = Depends(get_conn),
) -> Dict[str, Any]:
    medidas = medidas_por_material(material_id, conn)
    anchos = sorted(m["ancho"] for m in medidas)
    for w in anchos:
        if ancho_arte_cm < w:
            return {"ok": True, "ancho_rollo_cm": w}
    return {
        "ok": False,
        "mensaje": (
            "Medida no válida / material no válido. "
            "No existe un ancho de rollo mayor que el ancho del arte para este material. "
            "Si no está disponible, puedes agregarlo en la sección de Inventario."
        ),
        "sugerencias": anchos,
    }


# ======================================
#   LAMINADOS (tipos / marcas / medidas)
#   * Medidas de laminado: usan las de adhesivo, salvo “SIN LAMINAR” (0.0)
#   * Marcas de laminado: excluir 3M y ORAJET
# ======================================


@router.post("/materiales/create")
def crear_material(payload: Dict[str, Any], conn: Connection = Depends(get_conn)):
    nombre = (payload.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, detail="Nombre requerido.")

    table = "catalogo_materiales"
    id_col = _first_existing(conn, table, ["id_catalogo", "id_material_final", "id_material", "id"])
    name_col = _first_existing(conn, table, ["Material", "material", "nombre"])
    if not (id_col and name_col):
        raise HTTPException(500, detail="No se pudieron resolver columnas de materiales.")

    row = conn.execute(
        text(f"SELECT {id_col} AS id FROM {table} WHERE UPPER({name_col}) = UPPER(:n) LIMIT 1"),
        {"n": nombre}
    ).mappings().first()
    if row:
        return {"id": int(row["id"]), "nombre": nombre, "existed": True}

    conn.execute(text(f"INSERT INTO {table} ({name_col}) VALUES (:n)"), {"n": nombre})
    new_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    conn.commit()
    return {"id": int(new_id), "nombre": nombre, "existed": False}


@router.post("/marcas/create", summary="Crear marca de material (idempotente)")
def crear_marca_material(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    nombre_raw = (payload.get("nombre") or "").strip()
    if not nombre_raw:
        raise HTTPException(status_code=400, detail="El nombre de la marca es obligatorio.")
    nombre = " ".join(nombre_raw.split()).upper()

    # Evita duplicados si hay UNIQUE en nombre_marca (ver punto 3)
    conn.execute(text("""
        INSERT INTO catalogo_marcas_material (nombre_marca)
        VALUES (:n)
        ON DUPLICATE KEY UPDATE id_marca = LAST_INSERT_ID(id_marca)
    """), {"n": nombre})
    new_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    if not new_id:
        new_id = conn.execute(
            text("SELECT id_marca FROM catalogo_marcas_material WHERE nombre_marca=:n"),
            {"n": nombre}
        ).scalar()
    conn.commit()
    return {"id": int(new_id), "nombre": nombre}



@router.post("/medidas/add")
def medidas_add(payload: Dict[str, Any], conn: Connection = Depends(get_conn)):
    categoria = (payload.get("categoria") or "").strip().upper()   # "ADHESIVO" | "LONA"
    ancho = float(payload.get("ancho_cm", 0) or 0)
    if ancho <= 0:
        raise HTTPException(400, detail="ancho_cm > 0 requerido.")

    table = "medidas_lonas" if categoria == "LONA" else "medidas_adhesivos"
    # Evitar duplicados
    conn.execute(text(f"""
        INSERT INTO {table} (ancho_cm)
        SELECT :ancho
        WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE ancho_cm = :ancho LIMIT 1)
    """), {"ancho": ancho})
    conn.commit()
    return {"ok": True, "categoria": categoria, "ancho_cm": ancho}

@router.post("/laminados/tipos/create")
def crear_tipo_laminado(payload: Dict[str, Any], conn: Connection = Depends(get_conn)):
    nombre = (payload.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, detail="Nombre requerido.")

    table = "catalogo_tipos_laminado"
    id_col = _first_existing(conn, table, ["id_tipo_laminado", "id_tipo", "id", "laminado_id"])
    name_col = _first_existing(conn, table, ["nombre_laminado", "laminado", "nombre", "nombre_tipo"])
    if not (id_col and name_col):
        raise HTTPException(500, detail="No se pudieron resolver columnas de tipos de laminado.")

    row = conn.execute(
        text(f"SELECT {id_col} AS id FROM {table} WHERE UPPER({name_col}) = UPPER(:n) LIMIT 1"),
        {"n": nombre}
    ).mappings().first()
    if row:
        return {"id": int(row["id"]), "nombre": nombre, "existed": True}

    conn.execute(text(f"INSERT INTO {table} ({name_col}) VALUES (:n)"), {"n": nombre})
    new_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    conn.commit()
    return {"id": int(new_id), "nombre": nombre, "existed": False}





@router.get("/laminados/tipos", summary="Tipos de Laminado")
def lam_tipos(conn: Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
    sql = """
    SELECT id_laminado AS id, nombre_laminado AS nombre
    FROM catalogo_tipos_laminado
    WHERE TRIM(COALESCE(nombre_laminado,'')) <> ''
    ORDER BY nombre
    """
    return _rows(conn, sql)

@router.post("/laminados/marcas/create", summary="Crear marca de laminado (idempotente)")
def crear_marca_laminado(payload: dict = Body(...), conn: Connection = Depends(get_conn)):
    nombre = " ".join((payload.get("nombre") or "").split()).upper()
    if not nombre:
        raise HTTPException(400, "El nombre de la marca es obligatorio.")

    conn.execute(text("""
        INSERT INTO catalogo_marcas_laminado (nombre_marca)
        VALUES (:n)
        ON DUPLICATE KEY UPDATE id_marca = LAST_INSERT_ID(id_marca)
    """), {"n": nombre})
    new_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    if not new_id:
        new_id = conn.execute(
            text("SELECT id_marca FROM catalogo_marcas_laminado WHERE nombre_marca=:n"),
            {"n": nombre}
        ).scalar()
    conn.commit()
    return {"id": int(new_id), "nombre": nombre}


@router.get("/laminados/marcas")
def lam_marcas(conn: Connection = Depends(get_conn)):
    table = "catalogo_marcas_laminado"
    id_col   = _first_existing(conn, table, ["id_marca", "id", "id_marca_laminado", "lam_marca_id"])
    name_col = _first_existing(conn, table, ["nombre_marca", "nombre", "marca"])
    if not id_col or not name_col:
        raise HTTPException(status_code=500, detail="No se pudieron resolver columnas de marcas de laminado.")

    rows = conn.execute(
        text(f"SELECT {id_col} AS id, {name_col} AS nombre FROM {table} ORDER BY {name_col}")
    ).mappings().all()

    return [{"id": int(r["id"]), "nombre": r["nombre"]} for r in rows]



@router.get("/laminados/medidas", summary="Medidas de Laminado")
def lam_medidas(
    tipo_id: Optional[int] = Query(None, description="Id del tipo de laminado (opcional)"),
    tipo: Optional[str] = Query(None, description="Nombre del tipo de laminado (opcional)"),
    conn: Connection = Depends(get_conn)
) -> List[Dict[str, float]]:
    if _is_sin_laminar(conn, tipo_id, tipo):
        return [{"ancho": 0.0}]  # única medida cuando no se lamina

    sql = """
    SELECT DISTINCT ancho_cm AS ancho
    FROM medidas_adhesivos
    WHERE ancho_cm IS NOT NULL
    ORDER BY 1
    """
    filas = _rows(conn, sql)
    return [{"ancho": float(r["ancho"])} for r in filas]

