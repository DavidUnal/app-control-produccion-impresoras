# backend/routers/admin_usuarios.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import get_auth_conn                     # <— tu helper real
from .auth import require_role                     # reutilizamos el guard de admin

router = APIRouter(prefix="/auth/admin", tags=["Admin"])

class AprobarBody(BaseModel):
    rol: str

@router.get("/pendientes", dependencies=[Depends(require_role("ADMIN"))])
def listar_pendientes(conn: Connection = Depends(get_auth_conn)):
    rows = conn.execute(text("""
        SELECT id_usuario, nombre_usuario, rol, COALESCE(email_verificado,0) AS email_verificado
        FROM usuarios
        WHERE UPPER(rol)='PENDIENTE'
        ORDER BY email_verificado DESC, nombre_usuario ASC
    """)).mappings().all()
    return [dict(r) for r in rows]

@router.post("/usuarios/{uid}/aprobar", dependencies=[Depends(require_role("ADMIN"))])
def aprobar(uid: int, body: AprobarBody, conn: Connection = Depends(get_auth_conn)):
    rol = (body.rol or "").upper()
    if rol not in {"IMPRESION", "DISENO", "ADMIN"}:
        raise HTTPException(400, "Rol inválido")
    conn.execute(text("UPDATE usuarios SET rol=:r WHERE id_usuario=:u"),
                 {"r": rol, "u": uid})
    conn.commit()
    return {"ok": True}

@router.post("/usuarios/{uid}/set-rol", dependencies=[Depends(require_role("ADMIN"))])
def set_rol(uid: int, body: AprobarBody, conn: Connection = Depends(get_auth_conn)):
    rol = (body.rol or "").upper()
    if rol not in {"IMPRESION", "DISENO", "ADMIN", "PENDIENTE"}:
        raise HTTPException(400, "Rol inválido")
    conn.execute(text("UPDATE usuarios SET rol=:r WHERE id_usuario=:u"),
                 {"r": rol, "u": uid})
    conn.commit()
    return {"ok": True}
