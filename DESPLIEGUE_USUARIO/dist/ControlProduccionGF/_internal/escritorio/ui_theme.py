# escritorio/ui_theme.py
from pathlib import Path
from PySide6.QtWidgets import QApplication
import re
def _resolve_qss(name_light="theme_light.qss", name_dark="theme_dark.qss"):
    """Permite cargar tanto theme_*.qss como escritoriotheme_*.qss."""
    here = Path(__file__).parent
    # nombres "bonitos"
    p_light = here / name_light
    p_dark  = here / name_dark
    # nombres con prefijo "escritoriotheme_*.qss"
    p_light_alt = here / "escritoriotheme_light.qss"
    p_dark_alt  = here / "escritoriotheme_dark.qss"
    return (p_light if p_light.exists() else p_light_alt,
            p_dark  if p_dark.exists()  else p_dark_alt)

def apply_theme(qapp: "QApplication", mode: str = "light") -> None:
    light, dark = _resolve_qss()
    mode = (mode or "light").lower()
    qss_path = dark if mode == "dark" else light
    if not qss_path or not qss_path.exists():
        # fallback mínimo si faltan archivos
        qapp.setStyleSheet("QWidget{background:#ffffff;color:#111827;}")
        return
    qapp.setStyleSheet(qss_path.read_text(encoding="utf-8"))

def toggle_theme(qapp: "QApplication") -> str:
    """Alterna entre light/dark por contenido heurístico."""
    css = qapp.styleSheet() or ""
    is_dark = "#0b1220" in css.lower() or "E5E7EB".lower() in css.lower()
    new_mode = "light" if is_dark else "dark"
    apply_theme(qapp, new_mode)
    return new_mode

def _sanitize_qss(qss: str) -> str:
    # borra líneas con transform: ...; o filter: ...;
    return re.sub(r'^\s*(transform|filter)\s*:[^;]+;\s*$', '', qss, flags=re.MULTILINE|re.IGNORECASE)

def apply_theme(app, mode="light"):
    qss = """ ... tu QSS ... """
    app.setStyleSheet(_sanitize_qss(qss))