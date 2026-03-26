# desktop/admin_solicitudes.py
import requests
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)

def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}

def _show_err(parent, exc: Exception):
    try:
        if isinstance(exc, requests.RequestException) and exc.response is not None:
            data = exc.response.json()
            msg = data.get("detail") or data
        else:
            msg = str(exc)
    except Exception:
        msg = str(exc)
    QMessageBox.critical(parent, "Error", str(msg))

class AdminSolicitudesFrame(QWidget):
    """
    Lista TODOS los usuarios (email, rol, verificado, activo).
    Acciones:
      - Aprobar/Cambiar rol (usa el rol del combo superior)
      - Quitar rol (PENDIENTE)
      - Habilitar/Deshabilitar
    """
    COLS = ["ID", "Email", "Rol", "Verificado", "Activo"]

    def __init__(self, parent, base_url: str, token: str):
        super().__init__(parent)
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._build()
        self._load()

    # ---------- UI ----------
    def _build(self):
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Rol:"))
        self.cmb_rol = QComboBox()
        self.cmb_rol.addItems(["Impresión", "Diseño", "Admin"])
        top.addWidget(self.cmb_rol)

        self.btn_aprobar = QPushButton("Aprobar / Cambiar rol")
        self.btn_quitar  = QPushButton("Quitar rol (PENDIENTE)")
        self.btn_toggle  = QPushButton("Deshabilitar")  # cambia a “Habilitar” según selección
        self.btn_refresh = QPushButton("Refrescar")

        self.btn_aprobar.clicked.connect(self._do_aprobar)
        self.btn_quitar.clicked.connect(self._do_quitar)
        self.btn_toggle.clicked.connect(self._do_toggle)
        self.btn_refresh.clicked.connect(self._load)

        top.addStretch(1)
        top.addWidget(self.btn_aprobar)
        top.addWidget(self.btn_quitar)
        top.addWidget(self.btn_toggle)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        self.tbl = QTableWidget(0, len(self.COLS))
        self.tbl.setHorizontalHeaderLabels(self.COLS)
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SingleSelection)
        self.tbl.itemSelectionChanged.connect(self._update_toggle_label)

        # anchos
        widths = [60, 300, 120, 100, 100]
        for i, w in enumerate(widths):
            self.tbl.setColumnWidth(i, w)

        lay.addWidget(self.tbl, 1)

    # ---------- helpers ----------
    def _sel_row(self):
        r = self.tbl.currentRow()
        if r < 0:
            return None
        def _val(c):
            it = self.tbl.item(r, c)
            return (it.text() if it else "").strip()
        return {
            "id": int(_val(0) or 0),
            "email": _val(1),
            "rol": _val(2).upper(),
            "verificado": _val(3).lower() in ("1", "si", "sí", "true", "yes"),
            "activo": _val(4).lower() in ("1", "si", "sí", "true", "yes"),
        }

    def _set_row(self, i: int, user: dict):
        def put(col, val):
            self.tbl.setItem(i, col, QTableWidgetItem("" if val is None else str(val)))
        put(0, user.get("id"))
        put(1, user.get("email"))
        put(2, user.get("rol"))
        put(3, "Sí" if int(user.get("verificado", 0)) == 1 else "No")
        put(4, "Sí" if int(user.get("activo", 1)) == 1 else "No")

    def _role_code(self) -> str:
        txt = (self.cmb_rol.currentText() or "").lower()
        if "admin" in txt:     return "ADMIN"
        if "ise" in txt or "dise" in txt: return "DISENO"
        return "IMPRESION"

    def _update_toggle_label(self):
        sel = self._sel_row()
        if not sel:
            self.btn_toggle.setText("Deshabilitar")
            return
        self.btn_toggle.setText("Habilitar" if not sel["activo"] else "Deshabilitar")

    # ---------- red ----------
    def _load(self):
        self.tbl.setRowCount(0)
        try:
            r = requests.get(f"{self.base_url}/auth/admin/usuarios",
                             headers=_h(self.token), timeout=15)
            r.raise_for_status()
            rows = r.json() or []
        except Exception as e:
            _show_err(self, e); return

        for user in rows:
            i = self.tbl.rowCount()
            self.tbl.insertRow(i)
            self._set_row(i, user)
        self._update_toggle_label()

    def _do_aprobar(self):
        sel = self._sel_row()
        if not sel:
            QMessageBox.information(self, "Administración", "Selecciona un usuario."); return
        nuevo = self._role_code()
        try:
            r = requests.post(
                f"{self.base_url}/auth/admin/usuarios/{sel['id']}/set-rol",
                json={"rol": nuevo}, headers=_h(self.token), timeout=15
            )
            r.raise_for_status()
            QMessageBox.information(self, "Administración", f"Rol actualizado a {nuevo}.")
            self._load()
        except Exception as e:
            _show_err(self, e)

    def _do_quitar(self):
        sel = self._sel_row()
        if not sel:
            QMessageBox.information(self, "Administración", "Selecciona un usuario."); return
        try:
            r = requests.post(
                f"{self.base_url}/auth/admin/usuarios/{sel['id']}/set-rol",
                json={"rol": "PENDIENTE"}, headers=_h(self.token), timeout=15
            )
            r.raise_for_status()
            QMessageBox.information(self, "Administración", "Rol puesto en PENDIENTE.")
            self._load()
        except Exception as e:
            _show_err(self, e)

    def _do_toggle(self):
        sel = self._sel_row()
        if not sel:
            QMessageBox.information(self, "Administración", "Selecciona un usuario."); return
        nuevo = not sel["activo"]
        try:
            r = requests.post(
                f"{self.base_url}/auth/admin/usuarios/{sel['id']}/toggle-activo",
                json={"activo": bool(nuevo)}, headers=_h(self.token), timeout=15
            )
            r.raise_for_status()
            QMessageBox.information(
                self, "Administración",
                "Usuario habilitado." if nuevo else "Usuario deshabilitado."
            )
            self._load()
        except Exception as e:
            _show_err(self, e)
