# backend/config.py
from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # carpeta raíz del proyecto
load_dotenv(BASE_DIR / ".env")

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() == "true"


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "tu_cuenta@gmail.com"
SMTP_PASS = "tu_password_o_app_password"
SMTP_FROM = "Soporte <tu_cuenta@gmail.com>"

# a quién se notifica cuando hay una nueva solicitud
ADMIN_EMAILS = ["admin@tuempresa.com", "otro_admin@tuempresa.com"]





DB_DRIVER = os.getenv("DB_DRIVER", "mysql+pymysql")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "bank")

DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))

def build_db_url() -> str:
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "FAXhsx98")
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    db = os.getenv("DB_NAME", "bank")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}" 

DB_URL = build_db_url()
