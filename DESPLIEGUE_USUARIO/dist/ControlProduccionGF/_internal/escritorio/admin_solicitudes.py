# desktop/admin_solicitudes.py
from __future__ import annotations

import requests
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
    QDialog, QFormLayout, QLineEdit, QCheckBox
)

TIMEOUT = 15


# =========================
# Helpers HTTP / Errores
# =========================
def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _safe_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return {}


def _safe_err_msg(exc: Exception) -> str:
    try:
        if isinstance(exc, requests.RequestException) and getattr(exc, "response", None) is not None:
            r = exc.response
            try:
                data = r.json()
                if isinstance(data, dict):
                    return str(data.get("detail") or data)
                return str(data)
            except Exception:
                return f"{r.status_code} {r.text}"
        return str(exc)
    except Exception:
        return str(exc)


def _show_err(parent: QWidget, exc: Exception):
    QMessageBox.critical(parent, "Error", _safe_err_msg(exc))


def _try_get(base_url: str, token: str, paths: list[str], timeout: int = TIMEOUT):
    """
    Intenta GET en varios endpoints. Ignora 404 para compatibilidad.
    Retorna el primer JSON válido.
    """
    base = (base_url or "").rstrip("/")
    last_err = None
    for p in paths:
        try:
            r = requests.get(f"{base}{p}", headers=_auth_headers(token), timeout=timeout)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            return _safe_json(r)
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return []


def _try_post(base_url: str, token: str, paths: list[str], payload: dict, timeout: int = TIMEOUT):
    """
    Intenta POST en varios endpoints. Ignora 404 para compatibilidad.
    Retorna el primer JSON válido (o {} si no hay body).
    """
    base = (base_url or "").rstrip("/")
    last_err = None
    for p in paths:
        try:
            r = requests.post(f"{base}{p}", json=payload, headers=_auth_headers(token), timeout=timeout)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = _safe_json(r)
            return data if data is not None else {}
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return None


def _try_delete(base_url: str, token: str, paths: list[str], timeout: int = TIMEOUT):
    base = (base_url or "").rstrip("/")
    last_err = None
    for p in paths:
        try:
            r = requests.delete(f"{base}{p}", headers=_auth_headers(token), timeout=timeout)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = _safe_json(r)
            return data if data is not None else {}
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return None


def _first(d: dict, keys: tuple[str, ...], default=None):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return default


def _as_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "si", "sí", "true", "yes")


# =========================
# Normalización (tolerante)
# =========================
def _row_subuser_id(r: dict):
    return _first(r, ("id_subuser", "id_subusuario", "subuser_id", "id"), "")


def _row_cuenta_id(r: dict):
    return _first(r, ("cuenta_id", "id_usuario", "uid", "account_id"), "")


def _row_email_principal(r: dict) -> str:
    return str(_first(r, ("email_principal", "correo_principal", "principal_email", "email", "nombre_usuario"), "")).strip()


def _row_usuario(r: dict) -> str:
    return str(_first(r, ("usuario", "subusuario", "label", "etiqueta", "username"), "")).strip()


def _row_rol(r: dict) -> str:
    return str(_first(r, ("rol", "rol_solicitado"), "PENDIENTE")).strip()


def _row_verificado(r: dict) -> int:
    return _as_int(_first(r, ("email_verificado", "verificado"), 0), 0)


def _row_activo(r: dict) -> int:
    v = _first(r, ("is_active", "activo", "active"), 0)
    return 1 if _as_bool(v) else 0


# =========================
# API Admin (backend) + fallback
# =========================
def admin_list_pendientes(base_url: str, token: str) -> list[dict]:
    """
    SOLO pendientes: idealmente viene de tu router admin_usuarios.py:
      GET /auth/admin/subusuarios/pendientes
    """
    data = _try_get(base_url, token, [
        "/auth/admin/subusuarios/pendientes",
        "/auth/admin/subusers/pendientes",
        "/auth/admin/pendientes",  # legacy si existía
    ]) or []
    return data if isinstance(data, list) else (data.get("items") or data.get("rows") or [])


def admin_list_cuentas(base_url: str, token: str) -> list[dict]:
    data = _try_get(base_url, token, [
        "/auth/admin/usuarios",  # auth.py
    ]) or []
    return data if isinstance(data, list) else (data.get("items") or data.get("rows") or [])


def admin_list_subusuarios_for_cuenta(base_url: str, token: str, cuenta_id: int) -> list[dict]:
    """
    OJO: evitamos llamar /auth/admin/subusuarios sin cuenta_id (eso genera 422 en auth.py).
    Usamos endpoints que sí aceptan cuenta_id:
      - /auth/admin/cuentas/{cid}/subusuarios   (auth.py)
      - /auth/admin/subusuarios?cuenta_id={cid} (auth.py)
    """
    cid = int(cuenta_id)
    data = _try_get(base_url, token, [
        f"/auth/admin/cuentas/{cid}/subusuarios",
        f"/auth/admin/subusuarios?cuenta_id={cid}",
    ]) or []
    return data if isinstance(data, list) else (data.get("items") or data.get("rows") or [])


def admin_aprobar(base_url: str, token: str, sid: int, rol: str):
    payload = {"rol": rol}
    return _try_post(base_url, token, [
        f"/auth/admin/subusuarios/{sid}/aprobar",
        f"/auth/admin/subusuarios/{sid}/set-rol",
    ], payload)


def admin_set_rol(base_url: str, token: str, sid: int, rol: str):
    payload = {"rol": rol}
    return _try_post(base_url, token, [
        f"/auth/admin/subusuarios/{sid}/set-rol",
    ], payload)


def admin_set_activo(base_url: str, token: str, sid: int, is_active: bool):
    # mandamos ambos campos por compatibilidad entre routers
    payload = {
        "is_active": bool(is_active),  # admin_usuarios.py
        "activo": bool(is_active),     # auth.py
        "active": bool(is_active),
    }
    return _try_post(base_url, token, [
        f"/auth/admin/subusuarios/{sid}/toggle-activo",
    ], payload)


def admin_delete_subuser(base_url: str, token: str, sid: int):
    return _try_delete(base_url, token, [
        f"/auth/admin/subusuarios/{sid}",
    ])


def admin_create_subuser(base_url: str, token: str, payload: dict):
    # payload: {cuenta_id, usuario, password_plain, rol, is_active?}
    return _try_post(base_url, token, [
        "/auth/admin/subusuarios",
    ], payload)


# =========================
# Dialog: Crear Subusuario
# =========================
class DlgCrearSubusuario(QDialog):
    def __init__(self, email_principal: str, cuenta_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crear subusuario")
        self._data = None

        form = QFormLayout(self)

        self.lbl_email = QLabel(email_principal)
        self.lbl_email.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.ed_usuario = QLineEdit()
        self.ed_usuario.setPlaceholderText("ej: operador_impresion")

        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.Password)
        self.ed_pass.setPlaceholderText("Contraseña (texto plano)")

        self.cmb_rol = QComboBox()
        self.cmb_rol.addItem("PENDIENTE", "PENDIENTE")
        self.cmb_rol.addItem("IMPRESION", "IMPRESION")
        self.cmb_rol.addItem("DISENO", "DISENO")
        self.cmb_rol.addItem("ADMIN", "ADMIN")

        self.chk_activo = QCheckBox("Activo (recomendado solo si NO es PENDIENTE)")
        self.chk_activo.setChecked(False)

        btns = QHBoxLayout()
        self.btn_ok = QPushButton("Crear")
        self.btn_cancel = QPushButton("Cancelar")
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)

        form.addRow("Email principal", self.lbl_email)
        form.addRow("Usuario (subusuario)", self.ed_usuario)
        form.addRow("Contraseña", self.ed_pass)
        form.addRow("Rol inicial", self.cmb_rol)
        form.addRow("", self.chk_activo)
        form.addRow(btns)

        self._cuenta_id = int(cuenta_id)
        self.btn_ok.clicked.connect(self._on_ok)
        self.btn_cancel.clicked.connect(self.reject)

    def _on_ok(self):
        usuario = (self.ed_usuario.text() or "").strip()
        pwd = (self.ed_pass.text() or "")
        rol = (self.cmb_rol.currentData() or "PENDIENTE")
        activo = bool(self.chk_activo.isChecked())

        if not usuario:
            QMessageBox.warning(self, "Crear subusuario", "El campo 'Usuario' es obligatorio.")
            return

        if rol == "PENDIENTE" and activo:
            if QMessageBox.question(
                self, "Confirmar",
                "Vas a crear un subusuario PENDIENTE pero ACTIVO.\n¿Seguro?",
                QMessageBox.Yes | QMessageBox.No
            ) != QMessageBox.Yes:
                return

        self._data = {
            "cuenta_id": self._cuenta_id,
            "usuario": usuario,
            "password_plain": pwd,
            "rol": rol,
            "is_active": activo,
        }
        self.accept()

    @property
    def data(self) -> dict | None:
        return self._data


# =========================
# TAB 1: Pendientes
# (1 fila = 1 subusuario PENDIENTE)
# =========================
class AdminSolicitudesPendientesPage(QWidget):
    def __init__(self, base_url: str, token: str, parent=None):
        super().__init__(parent)
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self._build()
        self._load()

    def _build(self):
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Asignar rol:"))

        self.cmb_rol = QComboBox()
        self.cmb_rol.addItem("Impresión", "IMPRESION")
        self.cmb_rol.addItem("Diseño", "DISENO")
        self.cmb_rol.addItem("Administrador", "ADMIN")
        top.addWidget(self.cmb_rol)

        self.lbl_estado = QLabel("")
        self.lbl_estado.setStyleSheet("color:#666; padding-left:10px;")
        top.addWidget(self.lbl_estado, 1)

        self.btn_aprobar = QPushButton("Aprobar")
        self.btn_toggle = QPushButton("Deshabilitar")
        self.btn_eliminar = QPushButton("Eliminar")
        self.btn_ref = QPushButton("Refrescar")

        top.addWidget(self.btn_aprobar)
        top.addWidget(self.btn_toggle)
        top.addWidget(self.btn_eliminar)
        top.addWidget(self.btn_ref)
        lay.addLayout(top)

        self.tbl = QTableWidget(0, 6)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.verticalHeader().setDefaultSectionSize(36)
        self.tbl.setHorizontalHeaderLabels(["ID", "Email principal", "Subusuario", "Rol", "Verificado", "Activo"])

        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)

        self.tbl.setColumnWidth(0, 70)
        self.tbl.setColumnWidth(1, 320)
        self.tbl.setColumnWidth(2, 200)
        self.tbl.setColumnWidth(3, 140)
        self.tbl.setColumnWidth(4, 110)
        self.tbl.setColumnWidth(5, 90)

        self.tbl.itemSelectionChanged.connect(self._update_toggle_label)
        lay.addWidget(self.tbl, 1)

        self.btn_ref.clicked.connect(self._load)
        self.btn_aprobar.clicked.connect(self._aprobar)
        self.btn_toggle.clicked.connect(self._toggle_activo)
        self.btn_eliminar.clicked.connect(self._eliminar)

    def _sel_row(self) -> dict | None:
        r = self.tbl.currentRow()
        if r < 0:
            return None

        def cell(c):
            it = self.tbl.item(r, c)
            return (it.text().strip() if it else "")

        return {
            "sid": cell(0),
            "email_principal": cell(1),
            "subusuario": cell(2),
            "rol": cell(3),
            "verif_txt": cell(4),
            "activo_txt": cell(5),
        }

    def _update_toggle_label(self):
        sel = self._sel_row()
        if not sel or not sel["sid"]:
            self.btn_toggle.setText("Deshabilitar")
            return
        activo = _as_bool(sel["activo_txt"])
        self.btn_toggle.setText("Habilitar" if not activo else "Deshabilitar")

    def _put_row(self, i: int, r: dict):
        sid = _row_subuser_id(r)
        emailp = _row_email_principal(r)
        subu = _row_usuario(r)
        rol = _row_rol(r)
        verif = _row_verificado(r)
        activo = _row_activo(r)

        def put(c, v):
            it = QTableWidgetItem("" if v is None else str(v))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(i, c, it)

        put(0, sid)
        put(1, emailp)
        put(2, subu)
        put(3, rol)
        put(4, "Sí" if int(verif) == 1 else "No")
        put(5, "Sí" if int(activo) == 1 else "No")

    def _load(self):
        self.tbl.setRowCount(0)
        try:
            rows = admin_list_pendientes(self.base_url, self.token) or []
        except Exception as e:
            _show_err(self, e)
            return

        self.lbl_estado.setText(f"Mostrando: pendientes ({len(rows)})")

        for r in rows:
            i = self.tbl.rowCount()
            self.tbl.insertRow(i)
            self._put_row(i, r)

        if self.tbl.rowCount() > 0:
            self.tbl.setCurrentCell(0, 1)
        self._update_toggle_label()

    def _aprobar(self):
        sel = self._sel_row()
        if not sel or not sel["sid"]:
            QMessageBox.information(self, "Pendientes", "Selecciona un subusuario primero.")
            return

        sid = _as_int(sel["sid"], 0)
        if sid <= 0:
            QMessageBox.information(self, "Pendientes", "ID inválido.")
            return

        rol = str(self.cmb_rol.currentData())
        try:
            admin_aprobar(self.base_url, self.token, sid, rol)
            # al aprobar, lo activamos
            try:
                admin_set_activo(self.base_url, self.token, sid, True)
            except Exception:
                pass
            QMessageBox.information(self, "OK", f"Subusuario #{sid} → {rol}")
            self._load()
        except Exception as e:
            _show_err(self, e)

    def _toggle_activo(self):
        sel = self._sel_row()
        if not sel or not sel["sid"]:
            QMessageBox.information(self, "Pendientes", "Selecciona un subusuario primero.")
            return

        sid = _as_int(sel["sid"], 0)
        if sid <= 0:
            QMessageBox.information(self, "Pendientes", "ID inválido.")
            return

        activo = _as_bool(sel["activo_txt"])
        nuevo = not activo
        try:
            admin_set_activo(self.base_url, self.token, sid, nuevo)
            QMessageBox.information(self, "OK", "Usuario habilitado." if nuevo else "Usuario deshabilitado.")
            self._load()
        except Exception as e:
            _show_err(self, e)

    def _eliminar(self):
        sel = self._sel_row()
        if not sel or not sel["sid"]:
            QMessageBox.information(self, "Pendientes", "Selecciona un subusuario primero.")
            return

        sid = _as_int(sel["sid"], 0)
        if sid <= 0:
            QMessageBox.information(self, "Pendientes", "ID inválido.")
            return

        if QMessageBox.question(
            self, "Confirmar",
            f"¿Eliminar subusuario #{sid} ({sel['subusuario']})?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        try:
            admin_delete_subuser(self.base_url, self.token, sid)
            QMessageBox.information(self, "OK", "Subusuario eliminado.")
            self._load()
        except Exception as e:
            _show_err(self, e)


# ✅ Compatibilidad: tu app.py importa AdminSolicitudesPage como “Pendientes”
class AdminSolicitudesPage(AdminSolicitudesPendientesPage):
    pass


# =========================
# TAB 2: Usuarios
# (1 fila = 1 cuenta principal, columna subusuario = dropdown)
# =========================
class AdminUsuariosPage(QWidget):
    """
    Muestra CUENTAS PRINCIPALES y permite seleccionar subusuarios por cuenta desde un combo.
    Acciones aplican al subusuario seleccionado.
    """
    def __init__(self, base_url: str, token: str, parent=None):
        super().__init__(parent)
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self._build()
        self._load()

    def _build(self):
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Rol:"))

        self.cmb_rol = QComboBox()
        self.cmb_rol.addItem("Impresión", "IMPRESION")
        self.cmb_rol.addItem("Diseño", "DISENO")
        self.cmb_rol.addItem("Administrador", "ADMIN")
        top.addWidget(self.cmb_rol)

        self.lbl_estado = QLabel("")
        self.lbl_estado.setStyleSheet("color:#666; padding-left:10px;")
        top.addWidget(self.lbl_estado, 1)

        self.btn_crear = QPushButton("Crear subusuario")
        self.btn_aprobar = QPushButton("Aprobar / Cambiar rol")
        self.btn_pend = QPushButton("Quitar rol (PENDIENTE)")
        self.btn_toggle = QPushButton("Deshabilitar")
        self.btn_eliminar = QPushButton("Eliminar")
        self.btn_ref = QPushButton("Refrescar")

        top.addWidget(self.btn_crear)
        top.addWidget(self.btn_aprobar)
        top.addWidget(self.btn_pend)
        top.addWidget(self.btn_toggle)
        top.addWidget(self.btn_eliminar)
        top.addWidget(self.btn_ref)
        lay.addLayout(top)

        self.tbl = QTableWidget(0, 6)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.verticalHeader().setDefaultSectionSize(36)
        self.tbl.setHorizontalHeaderLabels(["ID", "Email principal", "Subusuario", "Rol", "Verificado", "Activo"])

        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)

        self.tbl.setColumnWidth(0, 70)
        self.tbl.setColumnWidth(1, 320)
        self.tbl.setColumnWidth(2, 220)
        self.tbl.setColumnWidth(3, 140)
        self.tbl.setColumnWidth(4, 110)
        self.tbl.setColumnWidth(5, 90)

        self.tbl.itemSelectionChanged.connect(self._update_toggle_label)
        lay.addWidget(self.tbl, 1)

        self.btn_ref.clicked.connect(self._load)
        self.btn_crear.clicked.connect(self._crear_subusuario)
        self.btn_aprobar.clicked.connect(self._aprobar)
        self.btn_pend.clicked.connect(self._poner_pendiente)
        self.btn_toggle.clicked.connect(self._toggle_activo)
        self.btn_eliminar.clicked.connect(self._eliminar)

    def _row_of_combo(self, cb: QComboBox) -> int:
        for r in range(self.tbl.rowCount()):
            w = self.tbl.cellWidget(r, 2)
            if w is cb:
                return r
        return -1

    def _current_selection(self) -> tuple[int | None, str, dict | None]:
        """
        retorna: (cuenta_id, email_principal, subuser_dict|None)
        """
        r = self.tbl.currentRow()
        if r < 0:
            return None, "", None

        email_item = self.tbl.item(r, 1)
        cuenta_id = None
        emailp = ""
        if email_item:
            emailp = email_item.text().strip()
            try:
                cuenta_id = int(email_item.data(Qt.UserRole))
            except Exception:
                cuenta_id = None

        cb = self.tbl.cellWidget(r, 2)
        sub = None
        if isinstance(cb, QComboBox):
            sub = cb.currentData()  # userData por defecto = Qt.UserRole
            if not isinstance(sub, dict):
                sub = None

        return cuenta_id, emailp, sub

    def _update_toggle_label(self):
        _, _, sub = self._current_selection()
        if not sub:
            self.btn_toggle.setText("Deshabilitar")
            return
        activo = (_row_activo(sub) == 1)
        self.btn_toggle.setText("Habilitar" if not activo else "Deshabilitar")

    def _on_combo_changed(self, cb: QComboBox):
        r = self._row_of_combo(cb)
        if r < 0:
            return

        sub = cb.currentData()
        if not isinstance(sub, dict):
            sub = None

        def set_txt(c: int, txt: str):
            it = self.tbl.item(r, c)
            if it is None:
                it = QTableWidgetItem("")
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.tbl.setItem(r, c, it)
            it.setText(txt)

        if not sub:
            set_txt(0, "")
            set_txt(3, "")
            set_txt(5, "")
        else:
            set_txt(0, str(_row_subuser_id(sub)))
            set_txt(3, _row_rol(sub))
            set_txt(5, "Sí" if _row_activo(sub) == 1 else "No")

        if self.tbl.currentRow() == r:
            self._update_toggle_label()

    def _load(self):
        self.tbl.setRowCount(0)
        try:
            cuentas = admin_list_cuentas(self.base_url, self.token) or []
        except Exception as e:
            _show_err(self, e)
            return

        self.lbl_estado.setText(f"Mostrando: cuentas ({len(cuentas)})")

        for cuenta in cuentas:
            emailp = str(_first(cuenta, ("email", "email_principal", "nombre_usuario"), "")).strip()
            cuenta_id = _as_int(_first(cuenta, ("id", "id_usuario"), 0), 0)
            verif = _as_int(_first(cuenta, ("verificado", "email_verificado"), 0), 0)

            # ✅ subusuarios por cuenta (evita 422)
            try:
                subs = admin_list_subusuarios_for_cuenta(self.base_url, self.token, int(cuenta_id)) or []
            except Exception:
                subs = []

            subs = sorted(subs, key=lambda x: (_row_usuario(x) or "").lower())

            r = self.tbl.rowCount()
            self.tbl.insertRow(r)

            it_id = QTableWidgetItem("")
            it_id.setFlags(it_id.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(r, 0, it_id)

            it_email = QTableWidgetItem(emailp)
            it_email.setData(Qt.UserRole, int(cuenta_id))
            it_email.setFlags(it_email.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(r, 1, it_email)

            cb = QComboBox()
            cb.setEditable(False)
            cb.setMinimumWidth(180)

            if not subs:
                cb.addItem("(sin subusuarios)", None)
            else:
                for s in subs:
                    label = _row_usuario(s) or "(sin nombre)"
                    cb.addItem(label, s)

            cb.currentIndexChanged.connect(partial(self._on_combo_changed, cb))
            self.tbl.setCellWidget(r, 2, cb)

            it_rol = QTableWidgetItem("")
            it_rol.setFlags(it_rol.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(r, 3, it_rol)

            it_ver = QTableWidgetItem("Sí" if int(verif) == 1 else "No")
            it_ver.setFlags(it_ver.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(r, 4, it_ver)

            it_act = QTableWidgetItem("")
            it_act.setFlags(it_act.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(r, 5, it_act)

            self._on_combo_changed(cb)

        if self.tbl.rowCount() > 0:
            self.tbl.setCurrentCell(0, 1)
        self._update_toggle_label()

    def _crear_subusuario(self):
        cuenta_id, emailp, _ = self._current_selection()
        if not cuenta_id or not emailp:
            QMessageBox.information(self, "Usuarios", "Selecciona una cuenta principal primero.")
            return

        dlg = DlgCrearSubusuario(emailp, int(cuenta_id), self)
        if dlg.exec() != QDialog.Accepted or not dlg.data:
            return

        try:
            admin_create_subuser(self.base_url, self.token, dlg.data)
            QMessageBox.information(self, "OK", "Subusuario creado.")
            self._load()
        except Exception as e:
            _show_err(self, e)

    def _aprobar(self):
        _, _, sub = self._current_selection()
        if not sub:
            QMessageBox.information(self, "Usuarios", "Selecciona un subusuario en el desplegable.")
            return

        sid = _as_int(_row_subuser_id(sub), 0)
        if sid <= 0:
            QMessageBox.information(self, "Usuarios", "Subusuario inválido.")
            return

        rol = str(self.cmb_rol.currentData())
        try:
            admin_aprobar(self.base_url, self.token, sid, rol)
            try:
                admin_set_activo(self.base_url, self.token, sid, True)
            except Exception:
                pass
            QMessageBox.information(self, "OK", f"Subusuario #{sid} → {rol}")
            self._load()
        except Exception as e:
            _show_err(self, e)

    def _poner_pendiente(self):
        _, _, sub = self._current_selection()
        if not sub:
            QMessageBox.information(self, "Usuarios", "Selecciona un subusuario en el desplegable.")
            return

        sid = _as_int(_row_subuser_id(sub), 0)
        if sid <= 0:
            QMessageBox.information(self, "Usuarios", "Subusuario inválido.")
            return

        try:
            admin_set_rol(self.base_url, self.token, sid, "PENDIENTE")
            try:
                admin_set_activo(self.base_url, self.token, sid, False)
            except Exception:
                pass
            QMessageBox.information(self, "OK", f"Subusuario #{sid} → PENDIENTE")
            self._load()
        except Exception as e:
            _show_err(self, e)

    def _toggle_activo(self):
        _, _, sub = self._current_selection()
        if not sub:
            QMessageBox.information(self, "Usuarios", "Selecciona un subusuario en el desplegable.")
            return

        sid = _as_int(_row_subuser_id(sub), 0)
        if sid <= 0:
            QMessageBox.information(self, "Usuarios", "Subusuario inválido.")
            return

        activo = (_row_activo(sub) == 1)
        nuevo = not activo
        try:
            admin_set_activo(self.base_url, self.token, sid, nuevo)
            QMessageBox.information(self, "OK", "Usuario habilitado." if nuevo else "Usuario deshabilitado.")
            self._load()
        except Exception as e:
            _show_err(self, e)

    def _eliminar(self):
        _, _, sub = self._current_selection()
        if not sub:
            QMessageBox.information(self, "Usuarios", "Selecciona un subusuario en el desplegable.")
            return

        sid = _as_int(_row_subuser_id(sub), 0)
        subu = _row_usuario(sub)
        if sid <= 0:
            QMessageBox.information(self, "Usuarios", "Subusuario inválido.")
            return

        if QMessageBox.question(
            self, "Confirmar",
            f"¿Eliminar subusuario #{sid} ({subu})?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        try:
            admin_delete_subuser(self.base_url, self.token, sid)
            QMessageBox.information(self, "OK", "Subusuario eliminado.")
            self._load()
        except Exception as e:
            _show_err(self, e)


# =========================
# Wrapper para tab "Usuarios"
# (mantiene compatibilidad con tu app.py / PagAdmin)
# =========================
class AdminSolicitudesFrame(QWidget):
    def __init__(self, parent, base_url: str, token: str):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(AdminUsuariosPage(base_url, token, self))
