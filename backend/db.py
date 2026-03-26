# backend/db.py
import os
from typing import Iterator
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, Connection

def _make_url(user: str, pwd: str, host: str, port: str, name: str) -> str:
    pwd = pwd or ""
    return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{name}?charset=utf8mb4"

# -------- Conexión principal (bank)
DB_URL = os.getenv("DB_URL")
if not DB_URL:
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_NAME = os.getenv("DB_NAME", "bank")
    DB_URL = _make_url(DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME)

# -------- Conexión de autenticación (auth_sso)
AUTH_DB_URL = os.getenv("AUTH_DB_URL")
if not AUTH_DB_URL:
    AUTH_DB_USER = os.getenv("AUTH_DB_USER", os.getenv("DB_USER", "root"))
    AUTH_DB_PASSWORD = os.getenv("AUTH_DB_PASSWORD", os.getenv("DB_PASSWORD", ""))
    AUTH_DB_HOST = os.getenv("AUTH_DB_HOST", os.getenv("DB_HOST", "127.0.0.1"))
    AUTH_DB_PORT = os.getenv("AUTH_DB_PORT", os.getenv("DB_PORT", "3306"))
    AUTH_DB_NAME = os.getenv("AUTH_DB_NAME", "auth_sso")
    AUTH_DB_URL = _make_url(AUTH_DB_USER, AUTH_DB_PASSWORD, AUTH_DB_HOST, AUTH_DB_PORT, AUTH_DB_NAME)

bank_engine: Engine = create_engine(DB_URL,  pool_pre_ping=True, pool_recycle=280, future=True)
auth_engine: Engine = create_engine(AUTH_DB_URL, pool_pre_ping=True, pool_recycle=280, future=True)

# -------- Dependencias FastAPI (generadores)
def get_conn() -> Iterator[Connection]:
    """Conexión al esquema de negocio (bank)."""
    with bank_engine.connect() as conn:
        yield conn

def get_auth_conn() -> Iterator[Connection]:
    """Conexión al esquema de autenticación (auth_sso)."""
    with auth_engine.connect() as conn:
        yield conn
