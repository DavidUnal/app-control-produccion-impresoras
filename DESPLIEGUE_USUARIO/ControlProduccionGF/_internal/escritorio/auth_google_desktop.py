# escritorio/auth_google_desktop.py
import os, json, base64, hashlib, secrets, socket, threading, urllib.parse, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import requests, webbrowser
from dotenv import load_dotenv
import sys
AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES    = ["openid", "email", "profile"]

def _load_env():
    """Carga .env (con override=True para evitar valores viejos cacheados)."""
    here = Path(__file__).resolve()
    for p in {
        Path.cwd() / ".env",
        here.parent / ".env",
        here.parent.parent / ".env",
        here.parent.parent.parent / ".env",
    }:
        if p.exists():
            load_dotenv(p, override=True)

def _code_verifier() -> str:
    # 32 bytes → ~43 chars URL-safe (válido para PKCE)
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()

def _code_challenge(verifier: str) -> str:
    h = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(h).rstrip(b"=").decode()

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    return port



def _load_google_client_id() -> str | None:
    # 1) desktop_config.json junto al exe o en la carpeta de instalación
    try:
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    except Exception:
        base_dir = Path.cwd()

    # intenta varias ubicaciones típicas
    for p in [
        Path(sys.executable).parent / "desktop_config.json",
        Path.cwd() / "desktop_config.json",
        base_dir / "desktop_config.json",
    ]:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                cid = (data.get("GOOGLE_CLIENT_ID") or "").strip()
                if cid:
                    return cid
            except Exception:
                pass

    # 2) fallback env
    ids = (os.getenv("GOOGLE_CLIENT_IDS") or "").replace(",", " ").split()
    return (os.getenv("GOOGLE_CLIENT_ID") or (ids[0] if ids else "")).strip() or None







def _loopback_handler(expected_state: str, slot: dict):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = dict(urllib.parse.parse_qsl(parsed.query or ""))
            if params.get("state") != expected_state:
                self.send_response(400); self.end_headers()
                self.wfile.write(b"Invalid state")
                return
            if "error" in params:
                slot["error"] = params.get("error_description") or params["error"]
            else:
                slot["code"]  = params.get("code")
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Listo. Puedes cerrar esta ventana.")
        def log_message(self, *a, **k): 
            # Silenciar logs del mini HTTP server
            pass
    return H

def signin_with_google(debug: bool=False) -> dict:
    """
    Flujo desktop con PKCE. Si GOOGLE_CLIENT_SECRET existe en .env,
    también lo envía en el intercambio /token (cubre clientes que lo exigen).
    """
    _load_env()

    CLIENT_ID = _load_google_client_id()
    CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET") or None

    if not CLIENT_ID:
        raise RuntimeError("GOOGLE_CLIENT_ID no está definido (.env).")

    verifier  = _code_verifier()
    challenge = _code_challenge(verifier)
    state     = secrets.token_urlsafe(16)
    port      = _free_port()
    # Incluimos '/' final para evitar rarezas en algunos agentes
    redirect  = f"http://127.0.0.1:{port}/oauth2cb"

    if debug:
        print("AUTH client_id=", CLIENT_ID)
        print("AUTH redirect_uri=", redirect)
        print("AUTH using_secret=", bool(CLIENT_SECRET))

    slot = {}
    H    = _loopback_handler(state, slot)
    http = HTTPServer(("127.0.0.1", port), H)
    th   = threading.Thread(target=http.serve_forever, daemon=True)
    th.start()

    # Paso 1: autorizar en el navegador
    q = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "state": state,
        "prompt": "select_account",
    }
    webbrowser.open(f"{AUTH_URL}?{urllib.parse.urlencode(q)}")

    # Esperar el callback
    for _ in range(600):  # 60s
        if "code" in slot or "error" in slot:
            break
        time.sleep(0.1)
    http.shutdown()

    if slot.get("error"):
        raise RuntimeError(f"Autorización cancelada/denegada: {slot['error']}")
    if "code" not in slot:
        raise RuntimeError("No se recibió 'code' (timeout).")

    # Paso 2: intercambio de code por tokens
    payload = {
        "client_id": CLIENT_ID,
        "code": slot["code"],
        "code_verifier": verifier,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }
    # Enviar secret si está disponible (cubre clientes que lo requieren)
    if CLIENT_SECRET:
        payload["client_secret"] = CLIENT_SECRET

    if debug:
        dbg = dict(payload)
        dbg["code"] = (dbg.get("code") or "")[:12] + "..."
        if "client_secret" in dbg:
            dbg["client_secret"] = "***"
        print("TOKEN payload=", dbg)

    r = requests.post(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=25,
    )

    if debug:
        # Cuidar tamaño para no inundar la consola
        print("TOKEN status=", r.status_code, "body=", r.text[:400])

    # Mejorar el mensaje de error si algo sale mal
    if r.status_code >= 400:
        tip = ""
        try:
            body = r.json()
            err_desc = (body.get("error_description") or "").lower()
            if "client_secret is missing" in err_desc and not CLIENT_SECRET:
                tip = (
                    "\n\nSugerencia: tu cliente exige 'client_secret'. "
                    "Agrega GOOGLE_CLIENT_SECRET al .env (del mismo cliente) "
                    "o crea un cliente 'Desktop app' y usa su ID."
                )
            elif "redirect_uri_mismatch" in err_desc:
                tip = (
                    "\n\nSugerencia: revisa que el redirect usado sea loopback "
                    "(http://127.0.0.1:<puerto>/oauth2cb)."
                )
        except Exception:
            body = r.text
        raise RuntimeError(f"Intercambio de token falló ({r.status_code}).\nRespuesta de Google:\n{body}{tip}")

    tok = r.json()

    # Paso 3: entregar id_token al backend para crear/ingresar usuario
    base = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    try:
        rr = requests.post(f"{base}/auth/google", json={"id_token": tok.get("id_token")}, timeout=12)
        rr.raise_for_status()
        return rr.json()
    except Exception:
        # Fallback: devolver los tokens crudos si el backend no respondió
        return tok
