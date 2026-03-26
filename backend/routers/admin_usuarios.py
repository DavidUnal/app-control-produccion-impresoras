from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import get_auth_conn
from .auth import require_role

router = APIRouter(prefix="/auth/admin", tags=["Admin"])


class AprobarBody(BaseModel):
    rol: str


class ToggleActivoBody(BaseModel):
    is_active: bool


class CrearSubusuarioBody(BaseModel):
    cuenta_id: int
    usuario: str
    password_plain: str
    rol: str = "PENDIENTE"
    is_active: bool | None = None


# -------------------- LISTAR SUBUSUARIOS PENDIENTES -----------------------
def _listar_pendientes(conn: Connection):
    """
    Devuelve claves listas para la UI Desktop:
      id, email_principal, subusuario, rol, verificado, activo, cuenta_id
    """
    rows = conn.execute(text("""
        SELECT
            su.id_subusuario AS id,
            su.cuenta_id     AS cuenta_id,
            COALESCE(u.email_principal, u.nombre_usuario) AS email_principal,
            su.usuario       AS subusuario,
            su.rol           AS rol,
            COALESCE(u.email_verificado, 0) AS verificado,
            COALESCE(su.is_active, 1) AS activo
        FROM sub_usuarios su
        LEFT JOIN usuarios u ON u.id_usuario = su.cuenta_id
        WHERE UPPER(su.rol) = 'PENDIENTE'
        ORDER BY activo DESC, email_principal ASC, subusuario ASC
    """)).mappings().all()
    return [dict(r) for r in rows]


# ✅ SOLO este endpoint (evita choque con auth.py que ya tiene /auth/admin/pendientes)
@router.get("/subusuarios/pendientes", dependencies=[Depends(require_role("ADMIN"))])
def listar_subusuarios_pendientes(conn: Connection = Depends(get_auth_conn)):
    return _listar_pendientes(conn)


# -------------------- APROBAR SUBUSUARIO -----------------------
@router.post("/subusuarios/{uid}/aprobar", dependencies=[Depends(require_role("ADMIN"))])
def aprobar_subusuario(uid: int, body: AprobarBody, conn: Connection = Depends(get_auth_conn)):
    rol = (body.rol or "").upper().strip()
    if rol not in {"IMPRESION", "DISENO", "ADMIN"}:
        raise HTTPException(400, "Rol inválido")

    # ✅ al aprobar: asigna rol y activa
    conn.execute(
        text("UPDATE sub_usuarios SET rol=:r, is_active=1 WHERE id_subusuario=:u"),
        {"r": rol, "u": uid},
    )
    conn.commit()
    return {"ok": True}


# -------------------- CAMBIAR ROL DE SUBUSUARIO -----------------------
@router.post("/subusuarios/{uid}/set-rol", dependencies=[Depends(require_role("ADMIN"))])
def set_rol_subusuario(uid: int, body: AprobarBody, conn: Connection = Depends(get_auth_conn)):
    rol = (body.rol or "").upper().strip()
    if rol not in {"IMPRESION", "DISENO", "ADMIN", "PENDIENTE"}:
        raise HTTPException(400, "Rol inválido")

    conn.execute(
        text("UPDATE sub_usuarios SET rol=:r WHERE id_subusuario=:u"),
        {"r": rol, "u": uid},
    )
    conn.commit()
    return {"ok": True}


# -------------------- LISTAR TODOS LOS SUBUSUARIOS -----------------------
@router.get("/subusuarios", dependencies=[Depends(require_role("ADMIN"))])
def listar_subusuarios(conn: Connection = Depends(get_auth_conn)):
    rows = conn.execute(text("""
        SELECT
            su.id_subusuario AS id,
            su.cuenta_id     AS cuenta_id,
            COALESCE(u.email_principal, u.nombre_usuario) AS email_principal,
            su.usuario       AS subusuario,
            su.rol           AS rol,
            COALESCE(u.email_verificado, 0) AS verificado,
            COALESCE(su.is_active, 1) AS activo
        FROM sub_usuarios su
        LEFT JOIN usuarios u ON u.id_usuario = su.cuenta_id
        ORDER BY email_principal ASC, subusuario ASC
    """)).mappings().all()
    return [dict(r) for r in rows]


# -------------------- CREAR SUBUSUARIO (JSON) -----------------------
@router.post("/subusuarios", dependencies=[Depends(require_role("ADMIN"))])
def crear_subusuario(body: CrearSubusuarioBody, conn: Connection = Depends(get_auth_conn)):
    cuenta_id = int(body.cuenta_id)
    usuario = (body.usuario or "").strip()
    password_plain = body.password_plain or ""
    rol = (body.rol or "PENDIENTE").upper().strip()

    if not usuario:
        raise HTTPException(400, "usuario es requerido")
    if rol not in {"IMPRESION", "DISENO", "ADMIN", "PENDIENTE"}:
        raise HTTPException(400, "Rol inválido")

    existing = conn.execute(text("""
        SELECT id_subusuario
        FROM sub_usuarios
        WHERE cuenta_id = :cuenta_id AND usuario = :usuario
    """), {"cuenta_id": cuenta_id, "usuario": usuario}).mappings().first()

    if existing:
        raise HTTPException(409, "El subusuario ya existe.")

    # ✅ regla simple: si rol es PENDIENTE -> inactivo por defecto
    is_active = body.is_active
    if is_active is None:
        is_active = False if rol == "PENDIENTE" else True

    conn.execute(text("""
        INSERT INTO sub_usuarios (cuenta_id, usuario, password_plain, rol, is_active)
        VALUES (:cuenta_id, :usuario, :password_plain, :rol, :is_active)
    """), {
        "cuenta_id": cuenta_id,
        "usuario": usuario,
        "password_plain": password_plain,
        "rol": rol,
        "is_active": int(bool(is_active)),
    })
    conn.commit()
    return {"ok": True, "msg": "Subusuario creado con éxito."}


# -------------------- ACTIVAR / DESACTIVAR SUBUSUARIO -----------------------
@router.post("/subusuarios/{uid}/toggle-activo", dependencies=[Depends(require_role("ADMIN"))])
def toggle_activo_subusuario(uid: int, body: ToggleActivoBody, conn: Connection = Depends(get_auth_conn)):
    conn.execute(text("""
        UPDATE sub_usuarios SET is_active = :is_active WHERE id_subusuario = :uid
    """), {"is_active": int(bool(body.is_active)), "uid": uid})
    conn.commit()
    return {"ok": True, "is_active": bool(body.is_active)}


# -------------------- ELIMINAR SUBUSUARIO -----------------------
@router.delete("/subusuarios/{uid}", dependencies=[Depends(require_role("ADMIN"))])
def eliminar_subusuario(uid: int, conn: Connection = Depends(get_auth_conn)):
    conn.execute(text("DELETE FROM sub_usuarios WHERE id_subusuario = :uid"), {"uid": uid})
    conn.commit()
    return {"ok": True, "msg": "Subusuario eliminado con éxito."}
