# backend/server.py
import os
import asyncio
from pathlib import Path

# ✅ Cargar .env ANTES de importar routers (CRÍTICO)
try:
    from dotenv import load_dotenv

    # Estructura esperada:
    #   <ROOT>/
    #     .env
    #     backend/
    #       server.py
    ROOT_DIR = Path(__file__).resolve().parents[1]
    ENV_PATH = ROOT_DIR / ".env"

    load_dotenv(ENV_PATH, override=True)

    print("[BOOT] CWD =", os.getcwd())
    print("[BOOT] ENV_PATH =", str(ENV_PATH), "exists =", ENV_PATH.exists())
except Exception as e:
    print(f"[BOOT] dotenv no cargado: {e!r}")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import text
from passlib.hash import bcrypt

from .db import auth_engine, bank_engine
from .db import get_auth_conn as get_conn
from .events import manager, set_loop

from .routers.catalogos import router as catalogos_router
from .routers.ordenes import router as ordenes_router
from .routers.inventario import router as inventario_router
from .routers.auth import router as auth_router
from .routers.auth import _decode as _decode_token
from .routers.admin_usuarios import router as admin_users_router

app = FastAPI(title="Control de Producción")

# Routers
app.include_router(auth_router)                 # /auth/...
app.include_router(catalogos_router)            # /catalogos/...
app.include_router(ordenes_router)              # /ordenes/...
app.include_router(inventario_router, prefix="/inventario")
app.include_router(admin_users_router)          # ✅ solo una vez

# --------- Constantes de tabla/columnas para primer admin ---------
USERS_TABLE = "usuarios"
COL_USER    = "nombre_usuario"
COL_HASH    = "password_hash"
COL_ROL     = "rol"
COL_VOK     = "email_verificado"  # 0/1


def ensure_first_admin():
    """
    Crea un administrador inicial si no existe ninguno y si están
    configuradas las variables de entorno:
      - FIRST_ADMIN_EMAIL
      - FIRST_ADMIN_PASSWORD
    """
    email = os.getenv("FIRST_ADMIN_EMAIL")
    pwd   = os.getenv("FIRST_ADMIN_PASSWORD")
    if not email or not pwd:
        return

    gen = get_conn()
    conn = next(gen)
    try:
        q_cnt = text(f"SELECT COUNT(*) AS c FROM {USERS_TABLE} WHERE UPPER({COL_ROL})='ADMIN'")
        cnt = (conn.execute(q_cnt).mappings().first() or {}).get("c", 0) or 0
        if cnt == 0:
            ph = bcrypt.hash(pwd)
            ins = text(f"""
                INSERT INTO {USERS_TABLE} ({COL_USER}, {COL_HASH}, {COL_ROL}, {COL_VOK})
                VALUES (:e, :p, 'ADMIN', 1)
            """)
            conn.execute(ins, {"e": email.strip().lower(), "p": ph})
            try:
                conn.commit()
            except Exception:
                pass
    finally:
        # cierra dependencia generadora
        try:
            next(gen)
        except StopIteration:
            pass


# --------- Healthcheck ---------
@app.get("/health")
def health():
    out = {"app": "ok"}

    # bank
    try:
        with bank_engine.connect() as c:
            c.execute(text("SELECT 1"))
        out["db_bank"] = "ok"
    except Exception as e:
        out["db_bank"] = f"error: {e.__class__.__name__}"

    # auth
    try:
        with auth_engine.connect() as c:
            c.execute(text("SELECT 1"))
        out["db_auth"] = "ok"
    except Exception as e:
        out["db_auth"] = f"error: {e.__class__.__name__}"

    return out


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ✅ Diagnóstico rápido (no expone secretos completos)
@app.get("/debug/env")
def debug_env():
    google_ids = (os.getenv("GOOGLE_CLIENT_IDS") or "")
    jwt_secret = os.getenv("JWT_SECRET") or ""
    return {
        "GOOGLE_CLIENT_IDS_present": bool(google_ids.strip()),
        "GOOGLE_CLIENT_IDS_len": len(google_ids.strip()),
        "JWT_SECRET_present": bool(jwt_secret),
        "JWT_SECRET_len": len(jwt_secret),
        "PUBLIC_BASE_URL": os.getenv("PUBLIC_BASE_URL"),
        "API_BASE_URL": os.getenv("API_BASE_URL"),
        "ENV_PATH_used": str((Path(__file__).resolve().parents[1] / ".env")),
    }


# --------- Startup ---------
@app.on_event("startup")
async def _startup():
    loop = asyncio.get_running_loop()
    set_loop(loop)

    # logs de arranque
    g_ids = os.getenv("GOOGLE_CLIENT_IDS") or ""
    jwt_secret = os.getenv("JWT_SECRET") or ""
    print("[BOOT] GOOGLE_CLIENT_IDS_present=", bool(g_ids.strip()), "len=", len(g_ids.strip()))
    print("[BOOT] JWT_SECRET_present=", bool(jwt_secret), "len=", len(jwt_secret))

    try:
        ensure_first_admin()
    except Exception as e:
        print(f"[startup] ensure_first_admin omitido: {e}")


# --------- WebSocket protegido ---------
# Toggle por env:
#   WS_REQUIRE_TOKEN=1 (default)  -> valida JWT
#   WS_REQUIRE_TOKEN=0            -> NO valida (solo para DEV/LAN controlada)
def _ws_require_token() -> bool:
    v = (os.getenv("WS_REQUIRE_TOKEN") or "1").strip().lower()
    return v not in {"0", "false", "no", "off"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(None)):
    if _ws_require_token():
        if not token:
            await ws.close(code=4401)  # Unauthorized
            return
        try:
            _decode_token(token)  # Lanza si inválido/expirado
        except Exception:
            await ws.close(code=4401)
            return

    await manager.connect(ws)
    try:
        while True:
            # Mantener viva la conexión; clientes pueden enviar pings
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)


# --------- Entrypoint de desarrollo ---------
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("APP_HOST") or "127.0.0.1"
    port = int(os.getenv("APP_PORT") or "8000")
    uvicorn.run("backend.server:app", host=host, port=port, reload=True)
