# backend/server.py
from __future__ import annotations

from pathlib import Path
from backend.routers import inventario 
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# =========================
#   BOOT / .env loader
# =========================
CWD = Path.cwd()
ENV_PATH = CWD / ".env"

print(f"[BOOT] CWD = {CWD}")
print(f"[BOOT] ENV_PATH = {ENV_PATH} exists = {ENV_PATH.exists()}")

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv and ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)

# =========================
#   FASTAPI APP
# =========================
app = FastAPI(title="Control de Producción - Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
#   ROUTERS
# =========================
from backend.routers.auth import router as auth_router
app.include_router(auth_router)
app.include_router(inventario.router, prefix="/inventario")
# --- Catalogos ---
# --- Catalogos ---
from backend.routers.catalogos import router as catalogos_router
app.include_router(catalogos_router)
print("[BOOT] catalogos_router OK")

# --- Ordenes ---
from backend.routers.ordenes import router as ordenes_router
app.include_router(ordenes_router)
print("[BOOT] ordenes_router OK")

# --- Ordenes ---
try:
    from backend.routers.ordenes import router as ordenes_router
    app.include_router(ordenes_router)
    print("[BOOT] ordenes_router OK")
except Exception as e:
    print("[BOOT] ordenes_router NO cargó:", e)

# --- WebSocket (prueba ambas ubicaciones) ---
ws_loaded = False
try:
    from backend.routers.ws import router as ws_router  # backend/routers/ws.py
    app.include_router(ws_router)
    ws_loaded = True
    print("[BOOT] ws_router OK (backend.routers.ws)")
except Exception as e1:
    try:
        from backend.routers_ws import router as ws_router  # backend/routers_ws.py
        app.include_router(ws_router)
        ws_loaded = True
        print("[BOOT] ws_router OK (backend.routers_ws)")
    except Exception as e2:
        print("[BOOT] ws_router NO cargó:", e1, " | ", e2)

# =========================
#   HEALTHCHECK
# =========================
@app.get("/health")
def health():
    return {"ok": True, "ws_loaded": ws_loaded}
