# backend/routers/auth.py
from __future__ import annotations

import os
import re
import random
import string
import secrets
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Optional, Iterable, List, Set

import requests
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse

from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.engine import Connection

from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

# opcional: si lo tienes instalado, se usa solo para verificar hashes bcrypt
try:
    from passlib.hash import bcrypt as passlib_bcrypt
except Exception:
    passlib_bcrypt = None

from backend.db import get_auth_conn as get_conn


# =========================
#    Safe SQL identifiers
# =========================
def _safe_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise RuntimeError(f"Identificador SQL inválido: {name}")
    return name


# =========================
#        CONFIG
# =========================
USERS_TABLE = _safe_ident(os.getenv("USERS_TABLE", "usuarios"))
SUB_TABLE   = _safe_ident(os.getenv("SUBUSERS_TABLE", "sub_usuarios"))

# columnas usuarios (schema auth_sso.usuarios)
COL_ID            = "id_usuario"
COL_USER          = "nombre_usuario"     # legacy: antes guardabas aquí el email
COL_HASH          = "password_hash"      # puede contener hash bcrypt o texto plano (por decisión tuya)
COL_ROL           = "rol"
COL_VOK           = "email_verificado"
COL_VCODE         = "verif_code"
COL_VEXPI         = "verif_expira"
COL_ACTIVE        = "is_active"
COL_GOOGLE_SUB    = "google_sub"
COL_EMAIL_PRINC   = "email_principal"    # cuenta principal

# google
def _split_ids(raw: str) -> List[str]:
    raw = (raw or "").strip()
    raw = raw.strip('"').strip("'")
    # soporta "a,b" o "a;b"
    parts = [p.strip().strip('"').strip("'") for p in raw.replace(";", ",").split(",")]
    return [p for p in parts if p]

GOOGLE_CLIENT_ID     = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
GOOGLE_CLIENT_IDS    = _split_ids(os.getenv("GOOGLE_CLIENT_IDS", "")) or ([GOOGLE_CLIENT_ID] if GOOGLE_CLIENT_ID else [])
GOOGLE_ALLOWED_HD    = (os.getenv("GOOGLE_ALLOWED_HD") or "").strip()

# jwt
JWT_SECRET = os.getenv("JWT_SECRET", "CAMBIA_ESTE_SECRETO_LARGO")
JWT_ALG    = os.getenv("JWT_ALG", "HS256")
JWT_HOURS  = int(os.getenv("JWT_HOURS", "24"))

# mail
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@example.com")
ADMIN_EMAILS = [e.strip() for e in (os.getenv("ADMIN_EMAILS") or "").replace(";", ",").split(",") if e.strip()]
MAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS)

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

EMAIL_TOKEN_EXP_MIN = 45

ROLES_VALIDOS = {"IMPRESION", "DISENO", "ADMIN", "PENDIENTE", "RECHAZADO"}


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

class GoogleDesktopIn(BaseModel):
    code: str
    redirect_uri: str

class LoginSubuserIn(BaseModel):
    email_principal: EmailStr
    # tu app a veces manda "usuario", a veces "subusuario"
    subusuario: Optional[str] = None
    usuario: Optional[str] = None
    password: str

    def username(self) -> str:
        u = (self.subusuario or self.usuario or "").strip()
        if not u:
            raise ValueError("Debe enviarse 'subusuario' o 'usuario'.")
        return u

class CrearSubusuarioBody(BaseModel):
    cuenta_id: int
    usuario: str
    nombre: Optional[str] = None
    password_plain: str
    rol: str = "PENDIENTE"

class ToggleActivoBody(BaseModel):
    activo: bool


# =========================
#        HELPERS
# =========================
def _rand_code(n=6) -> str:
    return "".join(random.choices(string.digits, k=n))

def _norm_role(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().upper()
    return s if s in {"ADMIN", "DISENO", "IMPRESION"} else None

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

def _send_mail(
    to: Iterable[str],
    subject: str,
    html: str,
    text_plain: str | None = None,
    reply_to: str | None = None,
) -> bool:
    recipients = [x.strip() for x in (to or []) if x and x.strip()]
    if not recipients:
        return False
    if not MAIL_ENABLED:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(recipients)
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.set_content(text_plain or "")
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
        return True
    except Exception:
        return False

def _admin_recipients(conn: Connection) -> List[str]:
    env_set: Set[str] = {e.strip().lower() for e in ADMIN_EMAILS if e.strip()}
    rows = conn.execute(text(f"""
        SELECT {COL_USER} AS email
        FROM {USERS_TABLE}
        WHERE UPPER({COL_ROL})='ADMIN' AND COALESCE({COL_VOK},0)=1
    """)).mappings().all()
    db_emails = [(r["email"] or "").strip().lower() for r in rows if (r["email"] or "").strip()]
    return sorted(env_set | set(db_emails))

def _looks_like_bcrypt(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith("$2a$") or s.startswith("$2b$") or s.startswith("$2y$")

def _verify_password(stored: str, incoming: str) -> bool:
    stored = stored or ""
    incoming = incoming or ""
    if _looks_like_bcrypt(stored) and passlib_bcrypt is not None:
        try:
            return passlib_bcrypt.verify(incoming, stored)
        except Exception:
            return False
    # fallback: texto plano
    return stored == incoming

def _get_cuenta_by_email(conn: Connection, email: str):
    """
    Busca la cuenta por:
      - email_principal = email
      - o nombre_usuario = email (legacy)
    """
    q = text(f"""
        SELECT
            {COL_ID}          AS id,
            {COL_USER}        AS nombre_usuario,
            {COL_EMAIL_PRINC} AS email_principal,
            {COL_ROL}         AS rol,
            COALESCE({COL_VOK},0)    AS email_verificado,
            COALESCE({COL_ACTIVE},1) AS is_active,
            {COL_GOOGLE_SUB}  AS google_sub,
            {COL_HASH}        AS password_hash
        FROM {USERS_TABLE}
        WHERE LOWER(COALESCE({COL_EMAIL_PRINC}, '')) = LOWER(:e)
           OR LOWER(COALESCE({COL_USER}, ''))       = LOWER(:e)
        LIMIT 1
    """)
    return conn.execute(q, {"e": email}).mappings().first()

def _verify_google_claims_from_id_token(id_token_str: str) -> dict:
    if not GOOGLE_CLIENT_IDS:
        raise HTTPException(500, "Falta GOOGLE_CLIENT_IDS en configuración.")
    try:
        claims = google_id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), audience=None
        )
    except Exception:
        raise HTTPException(401, "ID token de Google inválido.")

    email = (claims.get("email") or "").strip().lower()
    sub = claims.get("sub")
    aud = claims.get("aud") or claims.get("azp")
    email_verified = bool(claims.get("email_verified", False))

    if not email or not sub or not aud:
        raise HTTPException(400, "Token Google incompleto.")
    if aud not in set(GOOGLE_CLIENT_IDS):
        raise HTTPException(401, "Client ID (aud) no permitido.")
    if GOOGLE_ALLOWED_HD:
        hd = (claims.get("hd") or "").strip().lower()
        if hd != GOOGLE_ALLOWED_HD.lower():
            raise HTTPException(403, "Dominio de Google no autorizado.")
    if not email_verified:
        raise HTTPException(403, "Tu correo de Google no está verificado.")
    return {"email": email, "sub": sub}

def current_user(
    conn: Connection = Depends(get_conn),
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    if not creds:
        raise HTTPException(401, "Falta Authorization")

    data = _decode(creds.credentials)

    # Si es token de subusuario:
    if (data.get("typ") == "subuser") and data.get("sid"):
        cuenta_id = int(data.get("sub", 0) or 0)
        sid = int(data.get("sid", 0) or 0)

        cuenta = conn.execute(text(f"""
            SELECT {COL_ID} AS id,
                   COALESCE({COL_ACTIVE},1) AS is_active,
                   COALESCE({COL_VOK},0)    AS email_verificado,
                   COALESCE({COL_EMAIL_PRINC}, {COL_USER}) AS email
            FROM {USERS_TABLE}
            WHERE {COL_ID}=:i
            LIMIT 1
        """), {"i": cuenta_id}).mappings().first()

        if not cuenta:
            raise HTTPException(401, "Cuenta ya no existe")
        if int(cuenta.get("is_active", 1)) == 0:
            raise HTTPException(403, "Cuenta desactivada")
        if int(cuenta.get("email_verificado", 0)) == 0:
            raise HTTPException(403, "Cuenta no verificada")

        sub = conn.execute(text(f"""
            SELECT id_subusuario, usuario, rol, COALESCE(is_active,1) AS is_active
            FROM {SUB_TABLE}
            WHERE id_subusuario=:sid
              AND cuenta_id=:cid
            LIMIT 1
        """), {"sid": sid, "cid": cuenta_id}).mappings().first()

        if not sub:
            raise HTTPException(401, "Subusuario ya no existe")
        # robusto: activo si != 0 (tu DB tiene 4)
        if int(sub.get("is_active", 1)) == 0:
            raise HTTPException(403, "Subusuario desactivado")

        return {
            "id": cuenta_id,
            "email": cuenta.get("email"),
            "rol": (sub.get("rol") or ""),
            "usuario": (sub.get("usuario") or ""),
            "sub_usuario_id": int(sub.get("id_subusuario")),
            "token_typ": "subuser",
        }

    # Token “legacy” (usuarios)
    uid = int(data.get("sub", 0) or 0)
    row = conn.execute(text(f"""
        SELECT {COL_ID} AS id,
               {COL_USER} AS email,
               {COL_ROL}  AS rol,
               COALESCE({COL_ACTIVE},1) AS is_active
        FROM {USERS_TABLE}
        WHERE {COL_ID}=:i
        LIMIT 1
    """), {"i": uid}).mappings().first()

    if not row:
        raise HTTPException(401, "Usuario ya no existe")
    if int(row.get("is_active", 1)) == 0:
        raise HTTPException(403, "Usuario desactivado")

    return {"id": int(row["id"]), "email": row["email"], "rol": row["rol"], "token_typ": "user"}

def require_role(*roles_ok: str):
    roles_ok_u = {r.upper() for r in roles_ok}
    def _dep(user=Depends(current_user)):
        if (user.get("rol","").upper() not in roles_ok_u):
            raise HTTPException(403, "Permiso denegado")
        return user
    return _dep


# =========================
#   EMAIL ACTION TOKENS
# =========================
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
    """), {"j": jti, "t": typ, "uid": target_uid, "r": role, "a": action, "to": issued_to, "x": exp})
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


# =========================
#   REGISTRO: REQUEST CODE
# =========================
@router.post("/register/request-code")
def request_code(body: RequestCodeBody, background: BackgroundTasks, conn: Connection = Depends(get_conn)):
    email = body.email.strip().lower()
    code  = _rand_code(6)

    # intenta actualizar por email_principal o por nombre_usuario (legacy)
    upd = text(f"""
        UPDATE {USERS_TABLE}
           SET {COL_VCODE}=:c,
               {COL_VEXPI}=UTC_TIMESTAMP() + INTERVAL 15 MINUTE
         WHERE LOWER(COALESCE({COL_EMAIL_PRINC}, '')) = LOWER(:e)
            OR LOWER(COALESCE({COL_USER}, ''))       = LOWER(:e)
    """)
    res = conn.execute(upd, {"e": email, "c": code})

    if res.rowcount == 0:
        ins = text(f"""
            INSERT INTO {USERS_TABLE}
                ({COL_USER}, {COL_EMAIL_PRINC}, {COL_HASH}, {COL_ROL}, {COL_VCODE}, {COL_VEXPI}, {COL_VOK}, {COL_ACTIVE})
            VALUES (:e, :e, '', 'PENDIENTE', :c, UTC_TIMESTAMP() + INTERVAL 15 MINUTE, 0, 1)
        """)
        conn.execute(ins, {"e": email, "c": code})
    conn.commit()

    if MAIL_ENABLED:
        recipients = _admin_recipients(conn)
        if recipients:
            html = f"<p>Solicitud de acceso para <b>{email}</b>.</p><p>Código: <b>{code}</b> (15 min).</p>"
            plain = f"Solicitud: {email} | Código: {code} (15 min)"
            background.add_task(_send_mail, recipients, "Aprobación de registro - Código", html, plain)

        background.add_task(
            _send_mail,
            [email],
            "Solicitud recibida",
            "<p>Tu solicitud fue enviada a los administradores para aprobación.</p>",
            "Tu solicitud fue enviada a los administradores para aprobación."
        )

    resp = {"ok": True}
    if not MAIL_ENABLED:
        resp["dev_code"] = code
    return resp


# =========================
#   CONFIRMAR CÓDIGO EMAIL
# =========================
@router.post("/register/confirm")
def confirm_code(body: ConfirmBody, background: BackgroundTasks, conn: Connection = Depends(get_conn)):
    email = body.email.strip().lower()
    code  = (body.code or "").strip()

    if not (code.isdigit() and len(code) == 6):
        raise HTTPException(400, "Formato de código inválido (6 dígitos).")

    q = text(f"""
        SELECT {COL_ID} AS id, {COL_VCODE} AS vcode, {COL_VEXPI} AS vexp, {COL_ROL} AS rol
        FROM {USERS_TABLE}
        WHERE LOWER(COALESCE({COL_EMAIL_PRINC}, '')) = LOWER(:e)
           OR LOWER(COALESCE({COL_USER}, ''))       = LOWER(:e)
        LIMIT 1
    """)
    row = conn.execute(q, {"e": email}).mappings().first()
    if not row or not row["vcode"]:
        raise HTTPException(400, "Solicita un código primero.")
    if row["vcode"] != code:
        raise HTTPException(400, "Código incorrecto.")

    now_utc = conn.execute(text("SELECT UTC_TIMESTAMP()")).scalar_one()
    if row["vexp"] and now_utc > row["vexp"]:
        raise HTTPException(400, "El código ha expirado.")

    # si mandas password, la guardamos en password_hash como TEXTO PLANO (tu decisión)
    new_pwd = None
    if body.password is not None:
        if len(body.password) < 4:
            raise HTTPException(400, "La contraseña debe tener al menos 4 caracteres.")
        new_pwd = body.password

    conn.execute(text(f"""
        UPDATE {USERS_TABLE}
        SET {COL_VOK}=1,
            {COL_HASH}=COALESCE(:p, {COL_HASH}),
            {COL_VCODE}=NULL,
            {COL_VEXPI}=NULL
        WHERE {COL_ID}=:id
    """), {"p": new_pwd, "id": int(row["id"])})
    conn.commit()

    # notificar admins con links
    rol_req = _norm_role(body.rol_solicitado)
    recipients = _admin_recipients(conn) if MAIL_ENABLED else []
    uid = int(row["id"])

    def _links_for(adm_email: str):
        if rol_req:
            return {
                "approve": _make_link(conn, uid, rol_req, "APPROVE", adm_email),
                "reject":  _make_link(conn, uid, None,    "REJECT",  adm_email),
            }
        return {
            "approve_impresion": _make_link(conn, uid, "IMPRESION", "APPROVE", adm_email),
            "approve_diseno":    _make_link(conn, uid, "DISENO",    "APPROVE", adm_email),
            "approve_admin":     _make_link(conn, uid, "ADMIN",     "APPROVE", adm_email),
            "reject":            _make_link(conn, uid, None,        "REJECT",  adm_email),
        }

    if recipients and MAIL_ENABLED:
        for adm in recipients:
            links = _links_for(adm)
            if rol_req:
                html = f"""
                    <p><b>{email}</b> verificó correo y solicita rol <b>{rol_req}</b>.</p>
                    <ul>
                      <li><a href="{links['approve']}">Aprobar como {rol_req}</a></li>
                      <li><a href="{links['reject']}">Rechazar</a></li>
                    </ul>
                """
                plain = f"{email} solicita {rol_req}\nAprobar: {links['approve']}\nRechazar: {links['reject']}"
            else:
                html = f"""
                    <p><b>{email}</b> verificó correo.</p>
                    <ul>
                      <li><a href="{links['approve_impresion']}">Aprobar IMPRESIÓN</a></li>
                      <li><a href="{links['approve_diseno']}">Aprobar DISEÑO</a></li>
                      <li><a href="{links['approve_admin']}">Aprobar ADMIN</a></li>
                      <li><a href="{links['reject']}">Rechazar</a></li>
                    </ul>
                """
                plain = (
                    f"{email} verificado\n"
                    f"IMPRESION: {links['approve_impresion']}\n"
                    f"DISENO: {links['approve_diseno']}\n"
                    f"ADMIN: {links['approve_admin']}\n"
                    f"RECHAZAR: {links['reject']}"
                )
            background.add_task(_send_mail, [adm], "Nueva solicitud de acceso", html, plain)

        return {"ok": True, "pendiente_aprobacion": True}

    return {"ok": True, "pendiente_aprobacion": True, "debug_links": _links_for("debug@local")}


# =========================
#   ADMIN: EMAIL ACTION
# =========================
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
    uid = int(data.get("target_uid", 0) or 0)
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
            conn.execute(text(f"UPDATE {USERS_TABLE} SET {COL_ROL}=:r WHERE {COL_ID}=:u"),
                         {"r": role, "u": uid})
            msg = f"Usuario #{uid} aprobado como {role}."
        elif action == "REJECT":
            conn.execute(text(f"UPDATE {USERS_TABLE} SET {COL_ROL}='RECHAZADO' WHERE {COL_ID}=:u"),
                         {"u": uid})
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
#      GOOGLE SIGN-IN
# =========================
@router.post("/google")
def google_web(body: GoogleTokenBody, conn: Connection = Depends(get_conn)):
    # Por si algún cliente te manda id_token directo
    claims = _verify_google_claims_from_id_token(body.id_token)
    email = claims["email"]
    sub = claims["sub"]

    cuenta = _get_cuenta_by_email(conn, email)
    if not cuenta:
        raise HTTPException(403, "Email principal no está registrado.")
    if int(cuenta.get("email_verificado", 0)) == 0:
        raise HTTPException(403, "Email principal no está verificado.")
    if int(cuenta.get("is_active", 1)) == 0:
        raise HTTPException(403, "Cuenta desactivada.")

    # asegurar google_sub/email_principal
    conn.execute(text(f"""
        UPDATE {USERS_TABLE}
           SET {COL_GOOGLE_SUB}=COALESCE({COL_GOOGLE_SUB}, :sub),
               {COL_EMAIL_PRINC}=COALESCE({COL_EMAIL_PRINC}, :email),
               {COL_VOK}=1
         WHERE {COL_ID}=:id
    """), {"sub": sub, "email": email, "id": int(cuenta["id"])})
    conn.commit()

    subs = conn.execute(text(f"""
        SELECT id_subusuario, usuario, nombre, rol, COALESCE(is_active,1) AS is_active
        FROM {SUB_TABLE}
        WHERE cuenta_id=:cid AND COALESCE(is_active,1) <> 0
        ORDER BY usuario
    """), {"cid": int(cuenta["id"])}).mappings().all()

    return {
        "email_principal": email,
        "cuenta_id": int(cuenta["id"]),
        "subusuarios": [dict(s) for s in subs],
    }

@router.post("/google/desktop")
def google_desktop(payload: GoogleDesktopIn, conn: Connection = Depends(get_conn)):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "GOOGLE_CLIENT_ID no definido")
    if not GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "GOOGLE_CLIENT_SECRET no definido")

    tok = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": payload.code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": payload.redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if tok.status_code != 200:
        raise HTTPException(400, f"Google token exchange failed: {tok.text}")

    data = tok.json()
    id_token_str = data.get("id_token")
    if not id_token_str:
        raise HTTPException(400, "Google no retornó id_token")

    claims = _verify_google_claims_from_id_token(id_token_str)
    email = claims["email"]
    sub = claims["sub"]

    # Regla acordada: el email principal debe existir y estar verificado
    cuenta = _get_cuenta_by_email(conn, email)
    if not cuenta:
        raise HTTPException(403, "Email principal no está registrado.")
    if int(cuenta.get("email_verificado", 0)) == 0:
        raise HTTPException(403, "Email principal no está verificado.")
    if int(cuenta.get("is_active", 1)) == 0:
        raise HTTPException(403, "Cuenta desactivada.")

    conn.execute(text(f"""
        UPDATE {USERS_TABLE}
           SET {COL_GOOGLE_SUB}=COALESCE({COL_GOOGLE_SUB}, :sub),
               {COL_EMAIL_PRINC}=COALESCE({COL_EMAIL_PRINC}, :email),
               {COL_VOK}=1
         WHERE {COL_ID}=:id
    """), {"sub": sub, "email": email, "id": int(cuenta["id"])})
    conn.commit()

    subs = conn.execute(text(f"""
        SELECT id_subusuario, usuario, nombre, rol, COALESCE(is_active,1) AS is_active
        FROM {SUB_TABLE}
        WHERE cuenta_id=:cid AND COALESCE(is_active,1) <> 0
        ORDER BY usuario
    """), {"cid": int(cuenta["id"])}).mappings().all()

    return {
        "email_principal": email,
        "cuenta_id": int(cuenta["id"]),
        "subusuarios": [dict(s) for s in subs],
    }


# =========================
#      LOGIN LEGACY
# =========================
@router.post("/login")
def login(body: LoginBody, conn: Connection = Depends(get_conn)):
    u = body.username.strip().lower()

    row = conn.execute(text(f"""
        SELECT {COL_ID} AS id, {COL_USER} AS email, {COL_HASH} AS p, {COL_ROL} AS rol,
               COALESCE({COL_ACTIVE},1) AS is_active
        FROM {USERS_TABLE}
        WHERE LOWER({COL_USER})=LOWER(:u)
        LIMIT 1
    """), {"u": u}).mappings().first()

    if not row:
        raise HTTPException(401, "Usuario o contraseña incorrectos.")

    if int(row.get("is_active", 1)) == 0:
        raise HTTPException(403, "Usuario desactivado.")

    if (row.get("rol") or "").upper() == "PENDIENTE":
        raise HTTPException(403, "Tu cuenta está pendiente de aprobación.")

    stored = row.get("p") or ""
    if not _verify_password(stored, body.password):
        raise HTTPException(401, "Usuario o contraseña incorrectos.")

    token = _make_token({
        "sub": str(int(row["id"])),
        "usr": row.get("email"),
        "rol": row.get("rol"),
        "typ": "user",
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "rol": row.get("rol"),
        "usuario": row.get("email"),
        "nombre_usuario": row.get("email"),
    }

@router.get("/me")
def me(user=Depends(current_user)):
    return user


# =========================
#   LOGIN SUBUSUARIO
# =========================
@router.post("/login-subuser")
def login_subuser(payload: LoginSubuserIn, conn: Connection = Depends(get_conn)):
    email = payload.email_principal.lower().strip()
    uname = payload.username()

    cuenta = _get_cuenta_by_email(conn, email)
    if not cuenta or int(cuenta.get("email_verificado", 0)) == 0:
        raise HTTPException(403, "Cuenta no existe o no está verificada por Google")
    if int(cuenta.get("is_active", 1)) == 0:
        raise HTTPException(403, "Cuenta desactivada")

    sub = conn.execute(text(f"""
        SELECT
            id_subusuario,
            usuario,
            nombre,
            COALESCE(password_plain, password_plan) AS password_plain,
            rol,
            COALESCE(is_active,1) AS is_active
        FROM {SUB_TABLE}
        WHERE cuenta_id=:cid
          AND LOWER(usuario)=LOWER(:u)
        LIMIT 1
    """), {"cid": int(cuenta["id"]), "u": uname}).mappings().first()

    if not sub:
        raise HTTPException(403, "Subusuario no existe o está inactivo.")
    if int(sub.get("is_active", 1)) == 0:
        raise HTTPException(403, "Subusuario no existe o está inactivo.")

    if (sub.get("password_plain") or "") != (payload.password or ""):
        raise HTTPException(403, "Credenciales inválidas")

    token = _make_token({
        # mantenemos sub = cuenta_id para compatibilidad con el resto del backend
        "sub": str(int(cuenta["id"])),
        "typ": "subuser",
        "sid": int(sub["id_subusuario"]),
        "usr": email,
        "usuario": sub.get("usuario"),
        "rol": sub.get("rol"),
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "rol": sub.get("rol"),
        "usuario": sub.get("usuario"),
        "email_principal": email,
        "cuenta_id": int(cuenta["id"]),
        "sub_usuario_id": int(sub["id_subusuario"]),
    }


# =========================
#   ADMIN: USUARIOS
# =========================
@router.get("/admin/usuarios", dependencies=[Depends(require_role("ADMIN"))])
def listar_usuarios(conn: Connection = Depends(get_conn)):
    rows = conn.execute(text(f"""
        SELECT
            {COL_ID} AS id,
            COALESCE({COL_EMAIL_PRINC}, {COL_USER}) AS email,
            {COL_ROL} AS rol,
            COALESCE({COL_VOK},0) AS verificado,
            COALESCE({COL_ACTIVE},1) AS activo
        FROM {USERS_TABLE}
        ORDER BY email
    """)).mappings().all()
    return [dict(r) for r in rows]

@router.get("/admin/pendientes", dependencies=[Depends(require_role("ADMIN"))])
def listar_pendientes(conn: Connection = Depends(get_conn)):
    rows = conn.execute(text(f"""
        SELECT
            {COL_ID} AS id,
            COALESCE({COL_EMAIL_PRINC}, {COL_USER}) AS email,
            {COL_ROL} AS rol,
            COALESCE({COL_VOK},0) AS verificado
        FROM {USERS_TABLE}
        WHERE UPPER({COL_ROL})='PENDIENTE'
        ORDER BY verificado DESC, email ASC
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
    if rol not in ROLES_VALIDOS:
        raise HTTPException(400, "Rol inválido")
    conn.execute(text(f"UPDATE {USERS_TABLE} SET {COL_ROL}=:r WHERE {COL_ID}=:u"),
                 {"r": rol, "u": uid})
    conn.commit()
    return {"ok": True}

@router.post("/admin/usuarios/{uid}/rechazar", dependencies=[Depends(require_role("ADMIN"))])
def rechazar(uid: int, conn: Connection = Depends(get_conn)):
    conn.execute(text(f"UPDATE {USERS_TABLE} SET {COL_ROL}='RECHAZADO' WHERE {COL_ID}=:u"),
                 {"u": uid})
    conn.commit()
    return {"ok": True}

@router.post("/admin/usuarios/{uid}/toggle-activo", dependencies=[Depends(require_role("ADMIN"))])
def toggle_activo(uid: int, body: ToggleActivoBody, conn: Connection = Depends(get_conn)):
    conn.execute(text(f"UPDATE {USERS_TABLE} SET {COL_ACTIVE}=:a WHERE {COL_ID}=:u"),
                 {"a": 1 if body.activo else 0, "u": uid})
    conn.commit()
    return {"ok": True, "activo": bool(body.activo)}


# =========================
#   ADMIN: SUBUSUARIOS
# =========================
@router.get("/admin/subusuarios", dependencies=[Depends(require_role("ADMIN"))])
def listar_subusuarios(
    cuenta_id: int = Query(..., description="id_usuario (cuenta principal)"),
    conn: Connection = Depends(get_conn),
):
    rows = conn.execute(text(f"""
        SELECT id_subusuario, cuenta_id, usuario, nombre, rol, COALESCE(is_active,1) AS is_active,
               creado_en, actualizado_en
        FROM {SUB_TABLE}
        WHERE cuenta_id=:cid
        ORDER BY usuario
    """), {"cid": int(cuenta_id)}).mappings().all()
    return [dict(r) for r in rows]

@router.get("/admin/cuentas/{cuenta_id}/subusuarios", dependencies=[Depends(require_role("ADMIN"))])
def listar_subusuarios_path(cuenta_id: int, conn: Connection = Depends(get_conn)):
    return listar_subusuarios(cuenta_id=cuenta_id, conn=conn)

@router.post("/admin/subusuarios", dependencies=[Depends(require_role("ADMIN"))])
def crear_subusuario(body: CrearSubusuarioBody, conn: Connection = Depends(get_conn)):
    rol = (body.rol or "PENDIENTE").upper().strip()
    if rol not in ROLES_VALIDOS:
        raise HTTPException(400, "Rol inválido")

    conn.execute(text(f"""
        INSERT INTO {SUB_TABLE} (cuenta_id, usuario, nombre, password_plain, rol, is_active)
        VALUES (:cid, :u, :n, :p, :r, 1)
    """), {
        "cid": int(body.cuenta_id),
        "u": (body.usuario or "").strip(),
        "n": (body.nombre or "").strip(),
        "p": (body.password_plain or ""),
        "r": rol,
    })
    conn.commit()
    return {"ok": True}

@router.post("/admin/subusuarios/{sid}/set-rol", dependencies=[Depends(require_role("ADMIN"))])
def set_rol_subusuario(sid: int, body: AprobarBody, conn: Connection = Depends(get_conn)):
    rol = (body.rol or "").upper().strip()
    if rol not in ROLES_VALIDOS:
        raise HTTPException(400, "Rol inválido")
    conn.execute(text(f"UPDATE {SUB_TABLE} SET rol=:r WHERE id_subusuario=:sid"), {"r": rol, "sid": int(sid)})
    conn.commit()
    return {"ok": True}

@router.post("/admin/subusuarios/{sid}/toggle-activo", dependencies=[Depends(require_role("ADMIN"))])
def toggle_activo_subusuario(sid: int, body: ToggleActivoBody, conn: Connection = Depends(get_conn)):
    conn.execute(
        text(f"UPDATE {SUB_TABLE} SET is_active=:a WHERE id_subusuario=:sid"),
        {"a": 1 if body.activo else 0, "sid": int(sid)}
    )
    conn.commit()
    return {"ok": True, "activo": bool(body.activo)}

@router.get("/admin/subusuarios/pendientes", dependencies=[Depends(require_role("ADMIN"))])
def listar_subusuarios_pendientes(conn: Connection = Depends(get_conn)):
    rows = conn.execute(text(f"""
        SELECT
            s.id_subusuario AS id,
            COALESCE(u.{COL_EMAIL_PRINC}, u.{COL_USER}) AS email_principal,
            s.usuario AS subusuario,
            s.rol AS rol,
            COALESCE(u.{COL_VOK},0) AS verificado,
            COALESCE(s.is_active,1) AS activo
        FROM {SUB_TABLE} s
        JOIN {USERS_TABLE} u ON u.{COL_ID} = s.cuenta_id
        WHERE UPPER(s.rol)='PENDIENTE'
        ORDER BY activo DESC, email_principal ASC, subusuario ASC
    """)).mappings().all()
    return [dict(r) for r in rows]

# =========================
#   DEBUG
# =========================
@router.get("/debug/admin-recipients")
def debug_admin_recipients(conn: Connection = Depends(get_conn)):
    return {
        "MAIL_ENABLED": MAIL_ENABLED,
        "SMTP_HOST": SMTP_HOST,
        "SMTP_USER": SMTP_USER,
        "ADMIN_EMAILS_env": ADMIN_EMAILS,
        "recipients_effective": _admin_recipients(conn),
        "GOOGLE_CLIENT_IDS_len": len(GOOGLE_CLIENT_IDS),
    }

@router.post("/debug/test-smtp")
def debug_test_smtp():
    ok = _send_mail(
        ADMIN_EMAILS,
        "Prueba SMTP",
        "<p>Prueba de envío SMTP desde el backend.</p>",
        "Prueba de envío SMTP desde el backend."
    )
    return {"sent": bool(ok), "to": ADMIN_EMAILS}


print(f"[BOOT][auth] MAIL_ENABLED={MAIL_ENABLED} SMTP_HOST={SMTP_HOST} FROM={SMTP_FROM} ADMIN_EMAILS={ADMIN_EMAILS}")
print(f"[BOOT][auth] GOOGLE_CLIENT_IDS_present={bool(GOOGLE_CLIENT_IDS)} len={len(GOOGLE_CLIENT_IDS)}")
