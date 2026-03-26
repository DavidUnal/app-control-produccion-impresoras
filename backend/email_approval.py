# backend/email_approval.py
import os, time, secrets, smtplib, jwt
from email.mime.text import MIMEText
from urllib.parse import urlencode

JWT_SECRET = os.getenv("JWT_SECRET", "dev")
JWT_ALG    = os.getenv("JWT_ALG", "HS256")
BASE_URL   = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")

def _admin_emails():
    raw = os.getenv("ADMIN_EMAILS") or ""
    # admite ; o , como separador
    return [e.strip() for e in raw.replace(";", ",").split(",") if e.strip()]

def _sign_approval(email: str, role: str, ttl_seconds: int = 24*3600) -> str:
    now = int(time.time())
    payload = {
        "op": "approve_user",
        "email": email.lower(),
        "role": role,
        "iat": now,
        "exp": now + ttl_seconds,
        "nonce": secrets.token_urlsafe(10),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def _send_mail(to_addr: str, subject: str, body: str):
    host = os.getenv("SMTP_HOST"); port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER"); pwd = os.getenv("SMTP_PASS")
    from_addr = os.getenv("SMTP_FROM") or user
    if not (host and user and pwd and to_addr):
        return
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pwd)
        s.sendmail(from_addr, [to_addr], msg.as_string())

def notify_admins_new_user(email: str):
    """
    Envía a cada admin (ENV ADMIN_EMAILS) un email con enlaces
    firmados para aprobar IMPRESION / DISENO / ADMIN (caducidad 24h).
    Enlace: GET /admin/usuarios/approve-link?token=...
    """
    roles = [("IMPRESION", "Impresión"), ("DISENO", "Diseño"), ("ADMIN", "Administración")]
    lines = [f"Se verificó con Google el correo: {email}\n",
             "Asigna un rol con uno de estos enlaces (válidos 24h):\n"]
    for code, label in roles:
        tok = _sign_approval(email, code)
        url = f"{BASE_URL}/admin/usuarios/approve-link?{urlencode({'token': tok})}"
        lines.append(f"- {label}: {url}")
    lines.append("\nTambién puedes aprobar vía API protegida.")
    body = "\n".join(lines)

    for to in _admin_emails():
        _send_mail(to, "Aprobación de acceso – nuevo usuario", body)
