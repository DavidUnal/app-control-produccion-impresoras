# run_desktop.py
import sys
import ctypes
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication, QWidget, QStyle
from PySide6.QtGui import QIcon


def _project_root() -> Path:
    """
    Retorna la carpeta base del proyecto:
    - En .exe: carpeta donde está el ejecutable
    - En script: carpeta donde está este archivo
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _ensure_import_path() -> None:
    """
    Garantiza que 'escritorio' sea importable incluso si corres:
      python C:\\ruta\\a\\run_desktop.py
    desde otro working directory.
    """
    root = str(_project_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def load_env() -> None:
    """
    Carga el archivo .env tanto en modo script como empaquetado (.exe).
    Busca el .env en la carpeta base (root).
    """
    env_path = _project_root() / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[BOOT] ENV_PATH = {env_path} exists = True")
    else:
        print(f"[WARN] .env no encontrado en: {env_path}")


def _app_icon() -> QIcon:
    """
    Construye un icono para la app sin depender de funciones del UI.
    - Si existe LOGO_PATH, lo usa.
    - Si no, usa un icono estándar de Qt.
    """
    try:
        # Import tardío para no romper el arranque si hay errores de import previos
        from escritorio.app import LOGO_PATH  # noqa: WPS433
        if LOGO_PATH and Path(LOGO_PATH).exists():
            return QIcon(str(LOGO_PATH))
    except Exception:
        pass

    # Fallback: icono estándar
    tmp = QWidget()
    return tmp.style().standardIcon(QStyle.SP_ComputerIcon)


def main() -> int:
    # 0) Path + env
    _ensure_import_path()
    load_env()

    # 1) Windows AppUserModelID (para icono en barra)
    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "GF.ControlProduccion"
            )
        except Exception:
            pass

    # 2) Qt App
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("Control de Producción - Gran Formato")
    qapp.setWindowIcon(_app_icon())

    # 3) Theme (si existe el módulo)
    try:
        from escritorio.ui_theme import apply_theme  # noqa: WPS433
        apply_theme(qapp, mode="light")
    except Exception:
        # Si no está ui_theme o falla, no bloqueamos el arranque
        pass

    # 4) Importar y mostrar ventana principal
    from escritorio.app import Main  # noqa: WPS433

    win = Main()
    win.resize(1300, 800)
    win.show()

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
