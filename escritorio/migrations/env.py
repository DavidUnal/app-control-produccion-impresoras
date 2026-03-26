from __future__ import annotations
from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# --- Habilitar import del paquete 'backend' ---
BASE_DIR = Path(__file__).resolve().parents[1]  # carpeta raíz del proyecto
sys.path.append(str(BASE_DIR))

# --- Cargar .env ---
from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

# --- Tu config / modelos ---
from backend.config import build_db_url
from backend.models import Base  # Base.metadata es el objetivo de autogenerate

# --- Config de Alembic ---
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL de conexión desde .env
config.set_main_option("sqlalchemy.url", build_db_url())

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Ejecuta migraciones en modo offline."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Ejecuta migraciones en modo online (con conexión real)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
