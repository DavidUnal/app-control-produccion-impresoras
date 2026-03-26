# backend/__init__.py
from pathlib import Path
from dotenv import load_dotenv

def _load_env():
    root = Path(__file__).resolve().parents[1]  # carpeta que contiene backend/
    load_dotenv(dotenv_path=root / ".env", override=False)
    load_dotenv(override=False)

_load_env()
