# backend/routers/auth.py
from __future__ import annotations

import os
import smtplib
import random
import string
import secrets
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Optional, Iterable, List, Set

from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse

from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from passlib.hash import bcrypt
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.db import get_auth_conn as get_conn



# === BLOQUE 1: CONFIG + safe ident (REEMPLAZA tu bloque duplicado de USERS_TABLE) ===
import re

def _safe_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise RuntimeError(f"Identificador SQL inválido: {name}")
    return name

# =========================
#        CONFIG
# =========================
GOOGLE_CLIENT_IDS = [x.strip() for x in os.getenv("GOOGLE_CLIENT_IDS", "").replace(";", ",").split(",") if x.strip()]
GOOGLE_ALLOWED_HD = (os.getenv("GOOGLE_ALLOWED_HD") or "").strip()

JWT_SECRET = os.getenv("JWT_SECRET", "CAMBIA_ESTE_SECRETO_LARGO")
JWT_ALG    = os.getenv("JWT_ALG", "HS256")
JWT_HOURS  = int(os.getenv("JWT_HOURS", "24"))

USERS_TABLE = _safe_ident(os.getenv("USERS_TABLE", "usuarios"))
COL_ID      = "id_usuario"
COL_USER    = "nombre_usuario"
COL_HASH    = "password_hash"
COL_ROL     = "rol"
COL_VCODE   = "verif_code"
COL_VEXPI   = "verif_expira"
COL_VOK     = "email_verificado"
COL_ACTIVE  = "is_active"

EMAIL_TOKEN_EXP_MIN = 45

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@example.com")
ADMIN_EMAILS = [e.strip() for e in (os.getenv("ADMIN_EMAILS") or "").replace(";", ",").split(",") if e.strip()]
MAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS)

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

ROLES_VALIDOS = {"IMPRESION", "DISENO", "ADMIN", "PENDIENTE"}


router = APIRouter(prefix="/auth", tags=["Auth"])
bearer = HTTPBearer(auto_error=False)

# =========================
#        MODELOS
# =========================
class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    rol_solicitado: Optional[str] = None

class LoginBody(BaseModel):
    username: str
    password: str

class RequestCodeBody(BaseModel):
    email: EmailStr

class ConfirmBody(BaseModel):
    email: EmailStr
    code: str
    password: Optional[str] = None
    rol_solicitado: Optional[str] = None

class AprobarBody(BaseModel):
    rol: str

class GoogleTokenBody(BaseModel):
    id_token: str






# =========================
#        HELPERS
# =========================
def _verify_google_id_token(id_token: str) -> dict:
    if not GOOGLE_CLIENT_IDS:
        raise HTTPException(500, "Falta GOOGLE_CLIENT_IDS en configuración.")
    try:
        claims = google_id_token.verify_oauth2_token(
            id_token, google_requests.Request(), audience=None
        )
    except Exception:
        raise HTTPException(401, "ID token de Google inválido.")

    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    email_verified = bool(claims.get("email_verified", False))
    aud = claims.get("aud") or claims.get("azp")

    if not sub or not email or not aud:
        raise HTTPException(400, "Token Google incompleto.")
    if aud not in set(GOOGLE_CLIENT_IDS):
        raise HTTPException(401, "Client ID (aud) no permitido.")
    if GOOGLE_ALLOWED_HD:
        hd = (claims.get("hd") or "").lower()
        if hd != GOOGLE_ALLOWED_HD.lower():
            raise HTTPException(403, "Dominio de Google no autorizado.")
    if not email_verified:
        raise HTTPException(403, "Tu correo de Google no está verificado.")
    return {"sub": sub, "email": email}

def _norm_role(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().upper()
    return s if s in {"ADMIN", "DISENO", "IMPRESION"} else None
# === BLOQUE 2: _send_mail (AHORA retorna bool) ===
def _send_mail(
    to: Iterable[str],
    subject: str,
    html: str,
    text: str | None = None,
    reply_to: str | None = None,
) -> bool:
    recipients = [x.strip() for x in (to or []) if x and x.strip()]
    if not recipients:
        print("[SMTP] sin destinatarios; no se envía.")
        return False
    if not MAIL_ENABLED:
        print("[SMTP] MAIL_ENABLED=False; no se envía.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(recipients)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text or "")
    msg.add_alternative(html, subtype="html")

    try:
        port = int(SMTP_PORT or 587)
        if port == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, port, timeout=30) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, port, timeout=30) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        print(f"[SMTP] enviado OK -> {recipients}")
        return True
    except Exception as e:
        print(f"[SMTP] error enviando a {recipients}: {e}")
        return False


def _rand_code(n=6) -> str:
    return "".join(random.choices(string.digits, k=n))

def _get_user(conn: Connection, user: str):
    q = text(f"""
        SELECT {COL_ID}, {COL_USER}, {COL_HASH}, {COL_ROL},
               COALESCE({COL_VOK}, 0) AS {COL_VOK},
               {COL_VCODE}, {COL_VEXPI},
               COALESCE({COL_ACTIVE}, 1) AS {COL_ACTIVE}
        FROM {USERS_TABLE}
        WHERE LOWER({COL_USER}) = LOWER(:u)
        LIMIT 1
    """)
    return conn.execute(q, {"u": user}).mappings().first()

def _make_token(payload: dict) -> str:
    exp = datetime.utcnow() + timedelta(hours=JWT_HOURS)
    return jwt.encode({**payload, "exp": exp}, JWT_SECRET, algorithm=JWT_ALG)

def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except ExpiredSignatureError:
        raise HTTPException(401, "Token expirado")
    except JWTError:
        raise HTTPException(401, "Token inválido")

# === BLOQUE 3: current_user (AGREGA validación de is_active) ===
def current_user(
    conn: Connection = Depends(get_conn),
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    if not creds:
        raise HTTPException(401, "Falta Authorization")

    data = _decode(creds.credentials)

    row = conn.execute(text(f"""
        SELECT {COL_ID}, {COL_USER}, {COL_ROL}, COALESCE({COL_ACTIVE},1) AS {COL_ACTIVE}
        FROM {USERS_TABLE}
        WHERE {COL_ID}=:i
        LIMIT 1
    """), {"i": int(data["sub"])}).mappings().first()

    if not row:
        raise HTTPException(401, "Usuario ya no existe")

    if int(row.get(COL_ACTIVE, 1)) != 1:
        raise HTTPException(403, "Usuario desactivado.")

    return {"id": int(row[COL_ID]), "email": row[COL_USER], "rol": row[COL_ROL]}


def require_role(*roles_ok: str):
    roles_ok_u = {r.upper() for r in roles_ok}
    def _dep(user=Depends(current_user)):
        if (user.get("rol","").upper() not in roles_ok_u):
            raise HTTPException(403, "Permiso denegado")
        return user
    return _dep

# ---- tokens/links de aprobación por e-mail ----
def _new_email_action_token(payload: dict) -> tuple[str, str]:
    jti = secrets.token_hex(16)
    data = {**payload, "jti": jti, "exp": datetime.utcnow() + timedelta(minutes=EMAIL_TOKEN_EXP_MIN)}
    tok = jwt.encode(data, JWT_SECRET, algorithm=JWT_ALG)
    return jti, tok

def _store_email_token(conn: Connection, jti: str, typ: str, target_uid: int,
                       role: Optional[str], action: str, issued_to: str, exp: datetime):
    conn.execute(text("""
      INSERT INTO email_action_tokens (jti, typ, target_uid, role, action, issued_to, expires_at)
      VALUES (:j,:t,:uid,:r,:a,:to,:x)
    """), {"j": jti, "t": typ, "uid": target_uid, "r": role, "a": action,
           "to": issued_to, "x": exp})
    conn.commit()

def _make_link(conn: Connection, uid: int, role: Optional[str], action: str, adm_email: str) -> str:
    jti, tok = _new_email_action_token({
        "typ": "APPROVE_USER",
        "target_uid": int(uid),
        "role": (role or None),
        "action": action,
        "issued_to": adm_email
    })
    _store_email_token(conn, jti, "APPROVE_USER", uid, role, action, adm_email,
                       datetime.utcnow() + timedelta(minutes=EMAIL_TOKEN_EXP_MIN))
    return f"{PUBLIC_BASE_URL}/auth/admin/email-action?token={tok}"

def _admin_recipients(conn: Connection) -> List[str]:
    env_set: Set[str] = {e.strip().lower() for e in ADMIN_EMAILS if e.strip()}
    rows = conn.execute(text(f"""
        SELECT {COL_USER} AS email
        FROM {USERS_TABLE}
        WHERE UPPER({COL_ROL})='ADMIN' AND COALESCE({COL_VOK},0)=1
    """)).mappings().all()
    db_emails = [(r["email"] or "").strip().lower() for r in rows if (r["email"] or "").strip()]
    return sorted(env_set | set(db_emails))

# =========================
#   REGISTRO: CÓDIGO EMAIL
# =========================
# === BLOQUE 4: request_code (admins desde DB+ENV usando _admin_recipients) ===
@router.post("/register/request-code")
def request_code(body: RequestCodeBody, background: BackgroundTasks, conn: Connection = Depends(get_conn)):
    email = body.email.strip().lower()
    code  = _rand_code(6)

    upd = text(f"""
        UPDATE {USERS_TABLE}
           SET {COL_VCODE}=:c,
               {COL_VEXPI}=UTC_TIMESTAMP() + INTERVAL 15 MINUTE
         WHERE LOWER({COL_USER}) = LOWER(:e)
    """)
    res = conn.execute(upd, {"e": email, "c": code})

    if res.rowcount == 0:
        ins = text(f"""
            INSERT INTO {USERS_TABLE}
                ({COL_USER}, {COL_HASH}, {COL_ROL}, {COL_VCODE}, {COL_VEXPI}, {COL_VOK})
            VALUES (:e, '', 'PENDIENTE', :c, UTC_TIMESTAMP() + INTERVAL 15 MINUTE, 0)
        """)
        conn.execute(ins, {"e": email, "c": code})
    conn.commit()

    # Admins: envío en background (si hay SMTP)
    if MAIL_ENABLED:
        recipients = _admin_recipients(conn)
        if recipients:
            html = f"<p>Solicitud de acceso para <b>{email}</b>.</p><p>Código de verificación: <b>{code}</b> (15 min).</p>"
            plain = f"Solicitud: {email} | Código: {code} (15 min)"
            background.add_task(_send_mail, recipients, "Aprobación de registro - Código", html, plain)

        # Usuario: acuse sin código
        background.add_task(
            _send_mail,
            [email],
            "Solicitud recibida",
            "<p>Tu solicitud fue enviada a los administradores para aprobación.</p>",
            "Tu solicitud fue enviada a los administradores."
        )

    resp = {"ok": True}
    if not MAIL_ENABLED:
        resp["dev_code"] = code
    return resp


# =========================
#      GOOGLE SIGN-IN
# =========================
@router.post("/admin/usuarios/{uid}/rechazar", dependencies=[Depends(require_role("ADMIN"))])
def rechazar(uid: int, conn: Connection = Depends(get_conn)):
    conn.execute(text(f"UPDATE {USERS_TABLE} SET {COL_ROL}='RECHAZADO' WHERE {COL_ID}=:u"),
                 {"u": uid})
    conn.commit()
    return {"ok": True}



# === BLOQUE 5: google_auth (enviar correos por BackgroundTasks) ===
@router.post("/google")
def google_auth(
    body: GoogleTokenBody,
    background: BackgroundTasks,
    conn: Connection = Depends(get_conn),
):
    claims = _verify_google_id_token(body.id_token)
    sub   = claims["sub"]
    email = claims["email"]

    row = conn.execute(text(f"""
        SELECT {COL_ID}, {COL_USER}, {COL_HASH}, {COL_ROL},
               COALESCE({COL_VOK},0) AS {COL_VOK}, google_sub
        FROM {USERS_TABLE}
        WHERE google_sub=:sub OR LOWER({COL_USER})=LOWER(:email)
        LIMIT 1
    """), {"sub": sub, "email": email}).mappings().first()

    if not row:
        conn.execute(text(f"""
            INSERT INTO {USERS_TABLE}
                ({COL_USER}, {COL_HASH}, {COL_ROL}, {COL_VOK}, google_sub)
            VALUES (:e, '', 'PENDIENTE', 1, :s)
        """), {"e": email, "s": sub})
        conn.commit()

        row = conn.execute(text(f"""
            SELECT {COL_ID}, {COL_USER}, {COL_HASH}, {COL_ROL},
                   COALESCE({COL_VOK},0) AS {COL_VOK}, google_sub
            FROM {USERS_TABLE}
            WHERE google_sub=:sub
            LIMIT 1
        """), {"sub": sub}).mappings().first()
    else:
        updates = []
        params = {"id": row[COL_ID]}
        if not row.get("google_sub"):
            updates.append("google_sub=:s")
            params["s"] = sub
        if int(row.get(COL_VOK, 0)) != 1:
            updates.append(f"{COL_VOK}=1")
        if updates:
            conn.execute(text(f"UPDATE {USERS_TABLE} SET {', '.join(updates)} WHERE {COL_ID}=:id"), params)
            conn.commit()
            row = conn.execute(text(f"""
                SELECT {COL_ID}, {COL_USER}, {COL_HASH}, {COL_ROL},
                       COALESCE({COL_VOK},0) AS {COL_VOK}, google_sub
                FROM {USERS_TABLE}
                WHERE {COL_ID}=:id
                LIMIT 1
            """), {"id": params["id"]}).mappings().first()

    rol = (row[COL_ROL] or "").upper()
    if rol == "PENDIENTE":
        recipients = _admin_recipients(conn) if MAIL_ENABLED else []
        uid = int(row[COL_ID])

        if recipients and MAIL_ENABLED:
            for adm in recipients:
                approve_imp = _make_link(conn, uid, "IMPRESION", "APPROVE", adm)
                approve_dis = _make_link(conn, uid, "DISENO",   "APPROVE", adm)
                approve_adm = _make_link(conn, uid, "ADMIN",    "APPROVE", adm)
                reject_link = _make_link(conn, uid, None,       "REJECT",  adm)

                html = f"""
                    <p><b>{email}</b> solicita acceso (Google Sign-In).</p>
                    <p>Responde a este correo para contactar al solicitante.</p>
                    <ul>
                    <li><a href="{approve_imp}">Aprobar como IMPRESIÓN</a></li>
                    <li><a href="{approve_dis}">Aprobar como DISEÑO</a></li>
                    <li><a href="{approve_adm}">Aprobar como ADMIN</a></li>
                    <li><a href="{reject_link}">Rechazar solicitud</a></li>
                    </ul>
                """
                plain = (
                    f"{email} solicita acceso (Google Sign-In).\n"
                    f"IMPRESION: {approve_imp}\n"
                    f"DISENO:    {approve_dis}\n"
                    f"ADMIN:     {approve_adm}\n"
                    f"Rechazar:  {reject_link}\n"
                    f"(Responder a este correo contactará al solicitante)"
                )

                background.add_task(_send_mail, [adm], "Nueva solicitud de acceso", html, plain, email)

            return {"ok": True, "pendiente_aprobacion": True}

        approve_imp = _make_link(conn, uid, "IMPRESION", "APPROVE", "debug@local")
        approve_dis = _make_link(conn, uid, "DISENO",   "APPROVE", "debug@local")
        approve_adm = _make_link(conn, uid, "ADMIN",    "APPROVE", "debug@local")
        reject_link = _make_link(conn, uid, None,       "REJECT",  "debug@local")
        return {
            "ok": True,
            "pendiente_aprobacion": True,
            "debug_links": {
                "approve_impresion": approve_imp,
                "approve_diseno":   approve_dis,
                "approve_admin":    approve_adm,
                "reject":           reject_link
            }
        }

    payload = {"sub": str(row[COL_ID]), "usr": row[COL_USER], "rol": row[COL_ROL]}
    token = _make_token(payload)
    return {
        "ok": True,
        "access_token": token,
        "token_type": "bearer",
        "rol": row[COL_ROL],
        "usuario": row[COL_USER],
        "nombre_usuario": row[COL_USER],
    }





# =========================
#  CONFIRMAR CÓDIGO E-MAIL
# =========================
@router.post("/register/confirm")
def confirm_code(body: ConfirmBody, background: BackgroundTasks, conn: Connection = Depends(get_conn)):
    email = body.email.strip().lower()
    code  = (body.code or "").strip()

    if not (code.isdigit() and len(code) == 6):
        raise HTTPException(400, "Formato de código inválido. Deben ser 6 dígitos.")

    q = text(f"""
        SELECT {COL_ID}, {COL_VCODE}, {COL_VEXPI}, {COL_ROL}
        FROM {USERS_TABLE}
        WHERE LOWER({COL_USER})=:e
        LIMIT 1
    """)
    row = conn.execute(q, {"e": email}).mappings().first()
    if not row or not row[COL_VCODE]:
        raise HTTPException(400, "Solicita un código primero.")

    if row[COL_VCODE] != code:
        raise HTTPException(400, "Código incorrecto.")

    now_utc = conn.execute(text("SELECT UTC_TIMESTAMP()")).scalar_one()
    if row[COL_VEXPI] and now_utc > row[COL_VEXPI]:
        raise HTTPException(400, "El código ha expirado.")

    pwd_hash = None
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres.")
        pwd_hash = bcrypt.hash(body.password)

    conn.execute(text(f"""
        UPDATE {USERS_TABLE}
        SET {COL_VOK}=1,
            {COL_HASH}=COALESCE(:p, {COL_HASH}),
            {COL_VCODE}=NULL, {COL_VEXPI}=NULL
        WHERE {COL_ID}=:id
    """), {"p": pwd_hash, "id": row[COL_ID]})
    conn.commit()

    # Notificar admins con enlaces de aprobación
    rol_req = _norm_role(body.rol_solicitado)
    recipients = _admin_recipients(conn) if MAIL_ENABLED else []
    uid = int(row[COL_ID])

    def _links_for(adm_email: str):
        if rol_req:
            approve = _make_link(conn, uid, rol_req, "APPROVE", adm_email)
            reject  = _make_link(conn, uid, None,    "REJECT",  adm_email)
            return {"approve": approve, "reject": reject}
        return {
            "approve_impresion": _make_link(conn, uid, "IMPRESION", "APPROVE", adm_email),
            "approve_diseno":   _make_link(conn, uid, "DISENO",    "APPROVE", adm_email),
            "approve_admin":    _make_link(conn, uid, "ADMIN",     "APPROVE", adm_email),
            "reject":           _make_link(conn, uid, None,        "REJECT",  adm_email),
        }

    if recipients and MAIL_ENABLED:
        for adm in recipients:
            links = _links_for(adm)
            if rol_req:
                html = f"""
                    <p>Usuario <b>{email}</b> verificó su correo y solicita el rol <b>{rol_req}</b>.</p>
                    <ul>
                      <li><a href="{links['approve']}">Aprobar como {rol_req}</a></li>
                      <li><a href="{links['reject']}">Rechazar solicitud</a></li>
                    </ul>
                """
                plain = (
                    f"Usuario {email} verificó su correo y solicita rol {rol_req}.\n"
                    f"- Aprobar: {links['approve']}\n"
                    f"- Rechazar: {links['reject']}\n"
                )
            else:
                html = f"""
                    <p>Usuario <b>{email}</b> verificó su correo.</p>
                    <ul>
                      <li><a href="{links['approve_impresion']}">Aprobar como IMPRESIÓN</a></li>
                      <li><a href="{links['approve_diseno']}">Aprobar como DISEÑO</a></li>
                      <li><a href="{links['approve_admin']}">Aprobar como ADMIN</a></li>
                      <li><a href="{links['reject']}">Rechazar solicitud</a></li>
                    </ul>
                """
                plain = (
                    f"Usuario {email} verificó su correo.\n"
                    f"- IMPRESION: {links['approve_impresion']}\n"
                    f"- DISENO:    {links['approve_diseno']}\n"
                    f"- ADMIN:     {links['approve_admin']}\n"
                    f"- Rechazar:  {links['reject']}\n"
                )
            background.add_task(_send_mail, [adm], "Nueva solicitud de acceso", html, plain)

        return {"ok": True, "pendiente_aprobacion": True}

    # DEV: sin SMTP
    return {"ok": True, "pendiente_aprobacion": True, "debug_links": _links_for("debug@local")}

# =========================
#  ACCIÓN POR E-MAIL (one-click)
# =========================

class ToggleActivoBody(BaseModel):
    activo: bool



@router.post("/admin/usuarios/{uid}/toggle-activo", dependencies=[Depends(require_role("ADMIN"))])
def toggle_activo(uid: int, body: ToggleActivoBody, conn: Connection = Depends(get_conn)):
    conn.execute(
        text(f"UPDATE {USERS_TABLE} SET {COL_ACTIVE}=:a WHERE {COL_ID}=:u"),
        {"a": 1 if body.activo else 0, "u": uid}
    )
    conn.commit()
    return {"ok": True, "activo": bool(body.activo)}







# === BLOQUE 6: UNIFICAR /admin/usuarios (ELIMINA el duplicado y usa ESTE ÚNICO) ===
# Este esquema es el más útil para tu PagAdmin (incluye activo).
@router.get("/admin/usuarios", dependencies=[Depends(require_role("ADMIN"))])
def listar_usuarios(conn: Connection = Depends(get_conn)):
    rows = conn.execute(text(f"""
        SELECT
            {COL_ID}          AS id,
            {COL_USER}        AS email,
            {COL_ROL}         AS rol,
            COALESCE({COL_VOK},0)     AS verificado,
            COALESCE({COL_ACTIVE},1)  AS activo
        FROM {USERS_TABLE}
        ORDER BY {COL_USER}
    """)).mappings().all()
    return [dict(r) for r in rows]





# === BLOQUE 7: admin_email_action (usar UTC_TIMESTAMP en used_at) ===
@router.get("/admin/email-action", response_class=HTMLResponse)
def admin_email_action(request: Request, token: str, conn: Connection = Depends(get_conn)):
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except ExpiredSignatureError:
        return HTMLResponse("<h3>Enlace expirado</h3>", status_code=400)
    except JWTError:
        return HTMLResponse("<h3>Token inválido</h3>", status_code=400)

    if (data.get("typ") != "APPROVE_USER") or ("jti" not in data):
        return HTMLResponse("<h3>Token inválido</h3>", status_code=400)

    jti = data["jti"]
    uid = int(data.get("target_uid", 0))
    role = (data.get("role") or "").upper() if data.get("role") else None
    action = (data.get("action") or "").upper()

    row = conn.execute(text("SELECT * FROM email_action_tokens WHERE jti=:j"), {"j": jti}).mappings().first()
    if not row:
        return HTMLResponse("<h3>Token no registrado</h3>", status_code=400)
    if row["used_at"] is not None:
        return HTMLResponse("<h3>Este enlace ya fue utilizado</h3>", status_code=400)
    if datetime.utcnow() > row["expires_at"]:
        return HTMLResponse("<h3>Enlace expirado</h3>", status_code=400)

    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")
    try:
        if action == "APPROVE":
            if role not in {"IMPRESION", "DISENO", "ADMIN"}:
                raise ValueError("Rol inválido")
            conn.execute(
                text(f"UPDATE {USERS_TABLE} SET {COL_ROL}=:r WHERE {COL_ID}=:u"),
                {"r": role, "u": uid}
            )
            msg = f"Usuario #{uid} aprobado como {role}."
        elif action == "REJECT":
            conn.execute(
                text(f"UPDATE {USERS_TABLE} SET {COL_ROL}='RECHAZADO' WHERE {COL_ID}=:u"),
                {"u": uid}
            )
            msg = "Usuario rechazado."
        else:
            raise ValueError("Acción inválida")

        conn.execute(text("""
            UPDATE email_action_tokens
               SET used_at=UTC_TIMESTAMP(), used_ip=:ip, used_ua=:ua, result='DONE'
             WHERE jti=:j
        """), {"ip": ip, "ua": ua, "j": jti})
        conn.commit()
        return HTMLResponse(f"<h3>{msg}</h3><p>La acción fue registrada.</p>")
    except Exception as e:
        conn.execute(text("UPDATE email_action_tokens SET result='ERROR' WHERE jti=:j"), {"j": jti})
        conn.commit()
        return HTMLResponse(f"<h3>Error</h3><pre>{str(e)}</pre>", status_code=400)


# =========================
#     ADMINISTRACIÓN
# =========================
@router.get("/admin/pendientes", dependencies=[Depends(require_role("ADMIN"))])
def listar_pendientes(conn: Connection = Depends(get_conn)):
    rows = conn.execute(text(f"""
        SELECT {COL_ID}, {COL_USER}, {COL_ROL}, COALESCE({COL_VOK},0) AS {COL_VOK}
        FROM {USERS_TABLE}
        WHERE UPPER({COL_ROL})='PENDIENTE'
        ORDER BY {COL_VOK} DESC, {COL_USER} ASC
    """)).mappings().all()
    return [dict(r) for r in rows]

@router.post("/admin/usuarios/{uid}/aprobar", dependencies=[Depends(require_role("ADMIN"))])
def aprobar(uid: int, body: AprobarBody, conn: Connection = Depends(get_conn)):
    rol = (body.rol or "").upper()
    if rol not in {"IMPRESION", "DISENO", "ADMIN"}:
        raise HTTPException(400, "Rol inválido")
    conn.execute(text(f"UPDATE {USERS_TABLE} SET {COL_ROL}=:r WHERE {COL_ID}=:u"),
                 {"r": rol, "u": uid})
    conn.commit()
    return {"ok": True}

@router.post("/admin/usuarios/{uid}/set-rol", dependencies=[Depends(require_role("ADMIN"))])
def set_rol(uid: int, body: AprobarBody, conn: Connection = Depends(get_conn)):
    rol = (body.rol or "").upper()
    if rol not in {"IMPRESION", "DISENO", "ADMIN", "PENDIENTE"}:
        raise HTTPException(400, "Rol inválido")
    conn.execute(text(f"UPDATE {USERS_TABLE} SET {COL_ROL}=:r WHERE {COL_ID}=:u"),
                 {"r": rol, "u": uid})
    conn.commit()
    return {"ok": True}


@router.get("/debug/admin-recipients")
def debug_admin_recipients(conn: Connection = Depends(get_conn)):
    return {
        "MAIL_ENABLED": MAIL_ENABLED,
        "SMTP_HOST": SMTP_HOST,
        "SMTP_USER": SMTP_USER,
        "ADMIN_EMAILS_env": ADMIN_EMAILS,
        "recipients_effective": _admin_recipients(conn),
    }

# === BLOQUE 8: debug_test_smtp (ahora sent=True/False real) ===
@router.post("/debug/test-smtp")
def debug_test_smtp():
    ok = _send_mail(
        ADMIN_EMAILS,
        "Prueba SMTP",
        "<p>Prueba de envío SMTP desde el backend.</p>",
        "Prueba de envío SMTP desde el backend."
    )
    return {"sent": bool(ok), "to": ADMIN_EMAILS}




# =========================
#          LOGIN
# =========================
@router.post("/login")
def login(body: LoginBody, conn: Connection = Depends(get_conn)):
    row = _get_user(conn, body.username.strip().lower())
    if not row or not row[COL_HASH] or not bcrypt.verify(body.password, row[COL_HASH]):
        raise HTTPException(401, "Usuario o contraseña incorrectos.")
    if (row[COL_ROL] or "").upper() == "PENDIENTE":
        raise HTTPException(403, "Tu cuenta está pendiente de aprobación.")
    if int(row.get(COL_ACTIVE, 1)) != 1:
        raise HTTPException(403, "Usuario desactivado.")
    token = _make_token({
        "sub": str(row[COL_ID]),
        "usr": row[COL_USER],
        "rol": row[COL_ROL],
    })
    return {
        "access_token": token,
        "token_type": "bearer",
        "rol": row[COL_ROL],
        "usuario": row[COL_USER],
        "nombre_usuario": row[COL_USER],
    }

@router.get("/me")
def me(user = Depends(current_user)):
    return user

# =========================
#   REGISTRO SIMPLE (fallback)
# =========================
@router.post("/register")
def register(body: RegisterBody, conn: Connection = Depends(get_conn)):
    if _get_user(conn, body.email.strip().lower()):
        raise HTTPException(409, "Ese correo ya está registrado.")
    if len(body.password) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres.")
    phash = bcrypt.hash(body.password)
    conn.execute(text(f"""
        INSERT INTO {USERS_TABLE} ({COL_USER}, {COL_HASH}, {COL_ROL}, {COL_VOK})
        VALUES (:u, :p, 'PENDIENTE', 0)
    """), {"u": body.email.strip().lower(), "p": phash})
    conn.commit()
    return {"ok": True, "msg": "Cuenta creada. Pendiente de aprobación por Administración."}

@router.get("/_debug/send-mail")
def _debug_send_mail(to: str = None, background: BackgroundTasks = None):
    dests = [to] if to else ADMIN_EMAILS
    if background:
        background.add_task(_send_mail, dests, "Prueba SMTP", "<p>Prueba SMTP</p>", "Prueba SMTP")
    else:
        _send_mail(dests, "Prueba SMTP", "<p>Prueba SMTP</p>", "Prueba SMTP")
    return {"ok": True, "to": dests}




print(f"[BOOT] MAIL_ENABLED={MAIL_ENABLED} SMTP_HOST={SMTP_HOST} FROM={SMTP_FROM} ADMIN_EMAILS={ADMIN_EMAILS}")

