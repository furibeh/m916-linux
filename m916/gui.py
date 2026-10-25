# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt 6 (PySide6) interface: python3 -m m916.gui [--fake FLASH.bin]"""
import argparse
import os
import queue
import sys
import time

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                               QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import __version__, config as cfg, keys
from .protocol import (FLASH_SIZE, STATUS_BATTERY, STATUS_DPI, Mouse, MouseAsleep, ProtocolError,
                       find_devices)

STATE_DISCONNECTED, STATE_ASLEEP, STATE_ONLINE = "disconnected", "asleep", "online"


class DeviceWorker(QObject):
    """Owns the hidraw device on its own thread: runs queued jobs and, when idle, watches
    notifications and whether the mouse is awake."""

    state_changed = Signal(str, str)        # state, device path
    info = Signal(object)                   # {"firmware": bytes, "battery": (lvl, chg, mv)}
    config_loaded = Signal(object)          # image from cfg.read_image()
    progress = Signal(int, int)
    job_done = Signal(str, object)
    job_failed = Signal(str, str)
    notified = Signal(int)

    def __init__(self, fake_image=None):
        super().__init__()
        self.jobs = queue.Queue()
        self._stop = False
        self._fake_image = fake_image
        self._fake = None
        self.mouse = None
        self.state = None

    def submit(self, *job):
        self.jobs.put(job)

    def stop(self):
        self._stop = True

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.state_changed.emit(state, self.mouse.path if self.mouse else "")
            if state == STATE_ONLINE:
                self.submit("load")

    def _open(self):
        if self._fake_image is not None:
            from .fake import FakeMouse
            self._fake = FakeMouse(self._fake_image)
            self.mouse = self._fake.mouse()
            return
        for path in find_devices():
            try:
                self.mouse = Mouse(path)
                return
            except PermissionError:
                self.job_failed.emit("open", "Sin permiso sobre %s: instala la regla udev "
                                             "(udev/70-redragon-m916.rules)." % path)

    def _close(self):
        if self.mouse:
            try:
                self.mouse.close()
            except OSError:
                pass
        self.mouse = None
        self._set_state(STATE_DISCONNECTED)

    def run(self):
        last_scan = last_online = 0.0
        while not self._stop:
            now = time.monotonic()
            if self.mouse is None:
                if now - last_scan > 2:
                    last_scan = now
                    self._open()
                if self.mouse is None:
                    self._set_state(STATE_DISCONNECTED)
                    self._fail_pending("Receptor no encontrado")
                    time.sleep(0.2)
                    continue
                last_online = 0.0
            try:
                job = self.jobs.get(timeout=0.2)
            except queue.Empty:
                job = None
            try:
                if job:
                    if self.state != STATE_ONLINE and job[0] != "load":
                        raise MouseAsleep("el ratón está dormido")
                    self._run_job(job)
                else:
                    for flags in self.mouse.poll_notifications():
                        self.notified.emit(flags)
                    if time.monotonic() - last_online > 3:
                        last_online = time.monotonic()
                        self._set_state(STATE_ONLINE if self.mouse.online() else STATE_ASLEEP)
            except MouseAsleep as e:
                self._set_state(STATE_ASLEEP)
                if job:
                    self.job_failed.emit(job[0], "El ratón no responde (¿dormido?): muévelo y "
                                                 "vuelve a intentarlo.\n\n%s" % e)
            except ProtocolError as e:
                self.job_failed.emit(job[0] if job else "", str(e))
            except OSError as e:  # receiver unplugged
                if job:
                    self.job_failed.emit(job[0], str(e))
                self._close()
        if self.mouse:
            self.mouse.close()
        if self._fake:
            self._fake.close()

    def _fail_pending(self, msg):
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            self.job_failed.emit(job[0], msg)

    def _run_job(self, job):
        name, args = job[0], job[1:]
        m = self.mouse
        if name == "load":
            if not m.online():
                raise MouseAsleep("sin respuesta")
            self.info.emit({"firmware": m.version(), "battery": m.battery()})
            self.config_loaded.emit(cfg.read_image(m))
        elif name == "battery":
            self.info.emit({"battery": m.battery()})
        elif name == "active":
            self.job_done.emit(name, m.read_flash(cfg.OFF_DPI_ACTIVE, 1)[0])
        elif name == "apply":
            old, new = args
            m.write_fields(cfg.changed_fields(old, new), new)
            self.job_done.emit(name, new)
        elif name == "dump":
            data = m.read_flash(0, FLASH_SIZE, self.progress.emit)
            with open(args[0], "wb") as f:
                f.write(data)
            self.job_done.emit(name, args[0])
        elif name == "restore":
            image = args[0]
            current = m.read_flash(0, FLASH_SIZE, lambda d, t: self.progress.emit(d, 2 * t))
            writes = cfg.restore_writes(current, image)
            for i, (addr, data) in enumerate(writes):
                m.write_flash(addr, data)
                self.progress.emit(FLASH_SIZE + FLASH_SIZE * (i + 1) // len(writes), 2 * FLASH_SIZE)
            if writes and cfg.restore_writes(m.read_flash(0, FLASH_SIZE), image):
                raise ProtocolError("La verificación falló tras restaurar")
            self.progress.emit(1, 1)
            self.job_done.emit(name, len(writes))
            self.config_loaded.emit(cfg.read_image(m))


class DpiSpinBox(QSpinBox):
    def __init__(self):
        super().__init__()
        self.setRange(cfg.DPI_MIN, cfg.DPI_MAX)
        self.setSingleStep(cfg.DPI_STEP)
        self.setSuffix(" DPI")
        self.setAccelerated(True)

    def valueFromText(self, text):
        value = super().valueFromText(text)
        return max(cfg.DPI_MIN, round(value / cfg.DPI_STEP) * cfg.DPI_STEP)


class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self, title):
        super().__init__()
        self.title = title
        self.setFixedWidth(64)
        self.color = (0, 0, 0)
        self.clicked.connect(self._pick)

    def set_color(self, rgb):
        self.color = tuple(rgb)
        self.setStyleSheet("QPushButton { background-color: #%02x%02x%02x; border: 1px solid "
                           "palette(dark); border-radius: 3px; min-height: 22px; }" % self.color)
        self.setToolTip("#%02x%02x%02x" % self.color)

    def _pick(self):
        c = QColorDialog.getColor(QColor(*self.color), self, self.title)
        if c.isValid():
            self.set_color((c.red(), c.green(), c.blue()))
            self.changed.emit()


class DpiTab(QWidget):
    edited = Signal()

    def __init__(self):
        super().__init__()
        top = QHBoxLayout()
        self.count = QSpinBox()
        self.count.setRange(1, cfg.MAX_DPI_LEVELS)
        top.addWidget(QLabel("Niveles activos:"))
        top.addWidget(self.count)
        top.addSpacing(24)
        self.independent = QCheckBox("X e Y independientes")
        top.addWidget(self.independent)
        top.addStretch()

        grid = QGridLayout()
        headers = [QLabel("<b>%s</b>" % t) for t in ("En uso", "Nivel", "X", "Y", "Color")]
        for col, header in enumerate(headers):
            grid.addWidget(header, 0, col)
        self.x_header, self.y_header = headers[2], headers[3]
        self.active = QButtonGroup(self)
        self.rows = []
        for i in range(cfg.MAX_DPI_LEVELS):
            radio = QRadioButton()
            self.active.addButton(radio, i)
            label = QLabel("Nivel %d" % (i + 1))
            x, y = DpiSpinBox(), DpiSpinBox()
            color = ColorButton("Color del nivel %d" % (i + 1))
            for col, w in enumerate([radio, label, x, y, color]):
                grid.addWidget(w, i + 1, col)
            x.valueChanged.connect(lambda v, i=i: self._x_changed(i, v))
            y.valueChanged.connect(lambda *_: self.edited.emit())
            color.changed.connect(self.edited)
            self.rows.append((radio, label, x, y, color))
        grid.setColumnStretch(5, 1)

        note = QLabel("El LED del ratón muestra el color del nivel en uso. Con el PAW3395, X e Y "
                      "deben estar en el mismo tramo de 12 800 DPI.")
        note.setWordWrap(True)
        note.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(grid)
        layout.addWidget(note)
        layout.addStretch()

        self.count.valueChanged.connect(self._update_rows)
        self.count.valueChanged.connect(lambda *_: self.edited.emit())
        self.independent.toggled.connect(self._update_rows)
        self.active.idToggled.connect(lambda i, on: on and self.edited.emit())

    def _x_changed(self, i, value):
        if not self.independent.isChecked():
            self.rows[i][3].setValue(value)
        self.edited.emit()

    def _update_rows(self):
        n = self.count.value()
        if self.active.checkedId() >= n:
            self.rows[n - 1][0].setChecked(True)
        self.y_header.setVisible(self.independent.isChecked())
        self.x_header.setText("<b>%s</b>" % ("X" if self.independent.isChecked() else "DPI"))
        for i, (radio, label, x, y, color) in enumerate(self.rows):
            for w in (radio, label, x, y, color):
                w.setEnabled(i < n)
            y.setVisible(self.independent.isChecked())
            if not self.independent.isChecked():
                y.setValue(x.value())

    def load(self, conf):
        self.count.setValue(conf.dpi_count)
        self.independent.setChecked(any(lv.x != lv.y for lv in conf.levels))
        for (radio, _, x, y, color), lv in zip(self.rows, conf.levels):
            x.setValue(lv.x)
            y.setValue(lv.y)
            color.set_color(lv.color)
        self.rows[min(conf.dpi_active, cfg.MAX_DPI_LEVELS - 1)][0].setChecked(True)
        self._update_rows()

    def store(self, conf):
        conf.dpi_count = self.count.value()
        conf.dpi_active = max(0, self.active.checkedId())
        for (_, _, x, y, color), lv in zip(self.rows, conf.levels):
            lv.x = x.value()
            lv.y = y.value() if self.independent.isChecked() else x.value()
            lv.color = color.color

    def set_active(self, index):
        if 0 <= index < cfg.MAX_DPI_LEVELS:
            self.rows[index][0].setChecked(True)


BUTTON_LABELS = [
    ("disabled", "Desactivado"),
    ("left", "Clic izquierdo"),
    ("right", "Clic derecho"),
    ("middle", "Clic central"),
    ("back", "Atrás"),
    ("forward", "Adelante"),
    ("dpi-loop", "DPI: ciclo"),
    ("dpi-up", "DPI: subir"),
    ("dpi-down", "DPI: bajar"),
    ("scroll-up", "Scroll: arriba"),
    ("scroll-down", "Scroll: abajo"),
    ("scroll-left", "Scroll horizontal: izquierda"),
    ("scroll-right", "Scroll horizontal: derecha"),
    ("report-rate", "Cambiar frecuencia de sondeo"),
]
FIRE, DPI_LOCK, SHORTCUT, MEDIA, MACRO, OTHER = "fire", "dpi-lock", "shortcut", "media", "macro", "other"
SPECIAL_LABELS = [(FIRE, "Disparo repetido"), (DPI_LOCK, "Bloqueo de DPI"),
                  (SHORTCUT, "Atajo de teclado"), (MEDIA, "Tecla multimedia"), (MACRO, "Macro")]

MEDIA_LABELS = {
    "play-pause": "Reproducir/pausa", "next": "Siguiente pista", "previous": "Pista anterior",
    "stop": "Detener", "mute": "Silenciar", "volume-up": "Subir volumen",
    "volume-down": "Bajar volumen", "media-player": "Reproductor multimedia", "email": "Correo",
    "calculator": "Calculadora", "my-computer": "Mi equipo", "www-search": "Buscar",
    "www-home": "Navegador: inicio", "www-back": "Navegador: atrás",
    "www-forward": "Navegador: adelante", "www-stop": "Navegador: detener",
    "www-refresh": "Navegador: recargar", "www-favorites": "Favoritos",
}

QT_KEY_NAMES = {
    Qt.Key.Key_Control: "ctrl", Qt.Key.Key_Shift: "shift", Qt.Key.Key_Alt: "alt",
    Qt.Key.Key_Meta: "super", Qt.Key.Key_AltGr: "ralt", Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "kpenter", Qt.Key.Key_Escape: "esc", Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Space: "space", Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Insert: "insert", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "pageup", Qt.Key.Key_PageDown: "pagedown", Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
    Qt.Key.Key_Print: "printscreen", Qt.Key.Key_Pause: "pause", Qt.Key.Key_Menu: "menu",
    Qt.Key.Key_CapsLock: "capslock", Qt.Key.Key_VolumeMute: "mute",
    Qt.Key.Key_VolumeUp: "volume-up", Qt.Key.Key_VolumeDown: "volume-down",
    Qt.Key.Key_MediaPlay: "play-pause", Qt.Key.Key_MediaTogglePlayPause: "play-pause",
    Qt.Key.Key_MediaNext: "next", Qt.Key.Key_MediaPrevious: "previous",
    Qt.Key.Key_MediaStop: "stop",
}
MOUSE_NAMES = {Qt.MouseButton.LeftButton: "mouse-left", Qt.MouseButton.RightButton: "mouse-right",
               Qt.MouseButton.MiddleButton: "mouse-middle", Qt.MouseButton.BackButton: "mouse-back",
               Qt.MouseButton.ForwardButton: "mouse-forward"}


def key_event_name(event):
    """Key name (see keys.py) of a QKeyEvent: the physical key when Qt reports the evdev scan
    code, since the mouse sends positions and the layout is applied by the computer."""
    scan = event.nativeScanCode()
    if scan > 8 and scan - 8 in keys.EVDEV:
        return keys.EVDEV[scan - 8]
    key = event.key()
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(key).lower()
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return "f%d" % (key - Qt.Key.Key_F1 + 1)
    return QT_KEY_NAMES.get(Qt.Key(key))


class ShortcutEdit(QLineEdit):
    """Key combination as text ("ctrl+shift+t"); while capturing, the keys pressed fill it."""
    capture_finished = Signal()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("p. ej. ctrl+c")
        self.capturing = False
        self._held = set()
        self.textChanged.connect(self._validate)

    def events(self):
        try:
            return cfg.parse_combo(self.text())
        except (KeyError, ValueError):
            return None

    def _validate(self):
        ok = self.events() is not None
        self.setStyleSheet("" if ok else "QLineEdit { color: palette(link-visited); }")
        self.setToolTip("" if ok else "Combinación no válida: usa nombres como ctrl, shift, alt, "
                                      "super, a–z, 0–9, f1–f24, enter, esc, tab, space… "
                                      "(hasta %d teclas)" % cfg.SHORTCUT_KEYS_MAX)

    def start_capture(self):
        self.capturing = True
        self._held.clear()
        self._names = []
        self.setText("")
        self.setPlaceholderText("Pulsa la combinación…")
        self.setFocus()
        self.grabKeyboard()

    def _stop_capture(self):
        self.capturing = False
        self.releaseKeyboard()
        self.setPlaceholderText("p. ej. ctrl+c")
        self.capture_finished.emit()

    def keyPressEvent(self, event):
        if not self.capturing:
            return super().keyPressEvent(event)
        name = key_event_name(event)
        if event.isAutoRepeat() or not name:
            return
        self._held.add(name)
        if name not in self._names and len(self._names) < cfg.SHORTCUT_KEYS_MAX:
            self._names.append(name)
            self.setText("+".join(self._names))

    def keyReleaseEvent(self, event):
        if not self.capturing:
            return super().keyReleaseEvent(event)
        if event.isAutoRepeat():
            return
        self._held.discard(key_event_name(event))
        if not self._held and self._names:
            self._stop_capture()

    def focusOutEvent(self, event):
        if self.capturing:
            self._stop_capture()
        super().focusOutEvent(event)


class RecordArea(QLabel):
    """Records key and mouse button presses/releases with the time between them."""
    recorded = Signal(object)  # (ms since the previous one, cfg.Event)

    def __init__(self):
        super().__init__("Pulsa teclas o botones del ratón aquí")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(48)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.last = None

    def _add(self, press, name):
        if not name:
            return
        now = time.monotonic()
        delay = 0 if self.last is None else min(0xFFFF, round((now - self.last) * 1000))
        self.last = now
        kind, code = keys.lookup(name)
        self.recorded.emit((delay, cfg.Event(press, kind, code)))

    def keyPressEvent(self, event):
        if not event.isAutoRepeat():
            self._add(True, key_event_name(event))

    def keyReleaseEvent(self, event):
        if not event.isAutoRepeat():
            self._add(False, key_event_name(event))

    def mousePressEvent(self, event):
        self.setFocus()
        self._add(True, MOUSE_NAMES.get(event.button()))

    def mouseReleaseEvent(self, event):
        self._add(False, MOUSE_NAMES.get(event.button()))


class MacroDialog(QDialog):
    REPEAT_COUNT = "count"

    def __init__(self, parent, macro, repeat):
        super().__init__(parent)
        self.setWindowTitle("Editar macro")
        self.resize(520, 520)
        self.name = QLineEdit(macro.name if macro else "")
        self.name.setMaxLength(cfg.MACRO_NAME_MAX)
        self.mode = QComboBox()
        self.mode.addItem("Repetir un número de veces", self.REPEAT_COUNT)
        self.mode.addItem("Mientras se mantenga pulsado", cfg.REPEAT_WHILE_HELD)
        self.mode.addItem("Hasta volver a pulsar el botón", cfg.REPEAT_TOGGLE)
        self.mode.addItem("Hasta pulsar cualquier botón", cfg.REPEAT_UNTIL_ANY)
        self.times = QSpinBox()
        self.times.setRange(1, cfg.REPEAT_COUNT_MAX)
        self.times.setSuffix(" veces")
        if repeat in cfg.REPEAT_MODES.values():
            self.mode.setCurrentIndex(self.mode.findData(repeat))
        else:
            self.times.setValue(repeat)
        self.mode.currentIndexChanged.connect(
            lambda: self.times.setVisible(self.mode.currentData() == self.REPEAT_COUNT))
        self.times.setVisible(self.mode.currentData() == self.REPEAT_COUNT)
        repeat_row = QHBoxLayout()
        repeat_row.addWidget(self.mode, 1)
        repeat_row.addWidget(self.times)
        form = QFormLayout()
        form.addRow("Nombre:", self.name)
        form.addRow("Ejecución:", repeat_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Acción", "Tecla o botón", "Espera (ms)"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for step in (macro.steps if macro else []):
            self._add_row(step)

        add = QPushButton(QIcon.fromTheme("list-add"), "Añadir")
        remove = QPushButton(QIcon.fromTheme("list-remove"), "Quitar")
        up = QPushButton(QIcon.fromTheme("go-up"), "Subir")
        down = QPushButton(QIcon.fromTheme("go-down"), "Bajar")
        self.record = QPushButton(QIcon.fromTheme("media-record"), "Grabar")
        self.record.setCheckable(True)
        clear = QPushButton(QIcon.fromTheme("edit-clear"), "Vaciar")
        tools = QHBoxLayout()
        for w in (add, remove, up, down, clear):
            tools.addWidget(w)
        tools.addStretch()
        tools.addWidget(self.record)
        self.area = RecordArea()
        self.area.setVisible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.table)
        layout.addLayout(tools)
        layout.addWidget(self.area)
        hint = QLabel("Nombres de tecla: ctrl, shift, alt, super, a–z, 0–9, f1–f24, enter, esc, "
                      "space, mouse-left, mouse-right… Máximo %d pasos." % cfg.MACRO_STEPS_MAX)
        hint.setWordWrap(True)
        hint.setEnabled(False)
        layout.addWidget(hint)
        layout.addWidget(buttons)

        add.clicked.connect(lambda: self._add_row(cfg.Event(True, keys.KEY, keys.KEYS["a"], 10),
                                                  self.table.currentRow() + 1 or None))
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        clear.clicked.connect(lambda: self.table.setRowCount(0))
        self.record.toggled.connect(self._toggle_record)
        self.area.recorded.connect(self._recorded)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

    def _add_row(self, step, row=None):
        row = self.table.rowCount() if row is None else row
        self.table.insertRow(row)
        action = QComboBox()
        action.addItems(["Pulsar", "Soltar"])
        action.setCurrentIndex(0 if step.press else 1)
        self.table.setCellWidget(row, 0, action)
        self.table.setItem(row, 1, QTableWidgetItem(keys.name(step.kind, step.code)))
        delay = QSpinBox()
        delay.setRange(0, 0xFFFF)
        delay.setValue(step.delay)
        delay.setToolTip("Milisegundos de espera antes del paso siguiente")
        self.table.setCellWidget(row, 2, delay)

    def _row_step(self, row):
        press = self.table.cellWidget(row, 0).currentIndex() == 0
        kind, code = keys.lookup(self.table.item(row, 1).text())
        return cfg.Event(press, kind, code, self.table.cellWidget(row, 2).value())

    def _move(self, delta):
        row = self.table.currentRow()
        if row < 0 or not 0 <= row + delta < self.table.rowCount():
            return
        try:
            step = self._row_step(row)
        except KeyError:
            return
        self.table.removeRow(row)
        self._add_row(step, row + delta)
        self.table.selectRow(row + delta)

    def _toggle_record(self, on):
        self.area.setVisible(on)
        self.area.last = None
        self.record.setText("Detener" if on else "Grabar")
        if on:
            self.area.setFocus()

    def _recorded(self, item):
        delay, step = item
        last = self.table.rowCount() - 1
        if last >= 0 and delay:
            self.table.cellWidget(last, 2).setValue(delay)
        if self.table.rowCount() < cfg.MACRO_STEPS_MAX:
            self._add_row(step)
            self.table.scrollToBottom()

    def steps(self):
        return [self._row_step(r) for r in range(self.table.rowCount())]

    def repeat(self):
        mode = self.mode.currentData()
        return self.times.value() if mode == self.REPEAT_COUNT else mode

    def _accept(self):
        try:
            macro = cfg.Macro(self.name.text().strip(), self.steps())
        except KeyError as e:
            QMessageBox.warning(self, "Macro no válida", str(e))
            return
        errors = macro.errors()
        if errors:
            QMessageBox.warning(self, "Macro no válida", "\n".join(errors))
            return
        self.macro = macro
        self.accept()


class ButtonRow:
    def __init__(self, grid, row, index, edited):
        self.index = index
        self.edited = edited
        self.raw = (0, 0, 0)
        self.macro = None
        self.repeat = 1
        self.current = None
        self._loading = False
        self.combo = QComboBox()
        for key, text in BUTTON_LABELS + SPECIAL_LABELS:
            self.combo.addItem(text, key)
        self.combo.insertSeparator(len(BUTTON_LABELS))

        self.pages = QStackedWidget()
        self.pages.addWidget(QWidget())
        self.page = {}

        self.interval, self.shots = QSpinBox(), QSpinBox()
        self.interval.setRange(cfg.FIRE_INTERVAL_MIN, 255)
        self.interval.setSuffix(" ms")
        self.interval.setToolTip("Tiempo entre disparos")
        self.shots.setRange(0, cfg.FIRE_TIMES_MAX)
        self.shots.setSuffix(" disparos")
        self.shots.setSpecialValueText("Mientras se pulse")
        self.shots.setToolTip("Disparos por pulsación; 0 repite mientras se mantenga pulsado")
        self._add_page(FIRE, QLabel("Cada"), self.interval, self.shots)

        self.lock_dpi = DpiSpinBox()
        self.lock_dpi.setMaximum(256 * cfg.DPI_STEP)
        self.lock_dpi.setToolTip("DPI mientras se mantiene pulsado el botón")
        self._add_page(DPI_LOCK, self.lock_dpi)

        self.shortcut = ShortcutEdit()
        capture = QPushButton(QIcon.fromTheme("input-keyboard"), "Capturar")
        capture.setToolTip("Pulsa este botón y después la combinación de teclas")
        capture.clicked.connect(self.shortcut.start_capture)
        self._add_page(SHORTCUT, self.shortcut, capture)

        self.media = QComboBox()
        for name, text in MEDIA_LABELS.items():
            self.media.addItem(text, name)
        self._add_page(MEDIA, self.media)

        self.macro_label = QLabel()
        edit_macro = QPushButton(QIcon.fromTheme("document-edit"), "Editar…")
        edit_macro.clicked.connect(self.edit_macro)
        self._add_page(MACRO, self.macro_label, edit_macro)

        self.other_label = QLabel()
        self.other_label.setEnabled(False)
        self._add_page(OTHER, self.other_label)

        grid.addWidget(QLabel("Botón %d" % (index + 1)), row, 0)
        grid.addWidget(self.combo, row, 1)
        grid.addWidget(self.pages, row, 2)
        for w in (self.interval, self.shots, self.lock_dpi):
            w.valueChanged.connect(lambda *_: edited())
        self.shortcut.textChanged.connect(lambda *_: edited())
        self.media.currentIndexChanged.connect(lambda *_: edited())
        self.combo.currentIndexChanged.connect(self._selected)

    def _add_page(self, key, *widgets):
        page = QWidget()
        box = QHBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        for w in widgets:
            box.addWidget(w)
        box.addStretch()
        self.page[key] = self.pages.addWidget(page)

    def _selected(self):
        key = self.combo.currentData()
        previous, self.current = self.current, key
        self.pages.setCurrentIndex(self.page.get(key, 0))
        if not self._loading:
            if key == SHORTCUT and not self.shortcut.text():
                self.shortcut.start_capture()
            if key == MACRO and self.macro is None and not self.edit_macro():
                # Cancelled before defining anything: go back to the previous function
                self.combo.setCurrentIndex(self.combo.findData(previous))
                return
        self.edited()

    def edit_macro(self):
        dialog = MacroDialog(self.combo.window(), self.macro, self.repeat)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.macro, self.repeat = dialog.macro, dialog.repeat()
        self._show_macro()
        self.edited()
        return True

    def _show_macro(self):
        if self.macro:
            self.macro_label.setText("<b>%s</b> · %d pasos · %s" % (
                self.macro.name, len(self.macro.steps), REPEAT_TEXT.get(
                    self.repeat, "%d veces" % self.repeat)))
        else:
            self.macro_label.setText("Sin definir")

    def load(self, value, conf):
        self._loading = True
        self.raw = tuple(value)
        self.macro, self.repeat = None, 1
        other = self.combo.findData(OTHER)
        if other >= 0:
            self.combo.removeItem(other)
        t, p1, p2 = self.raw
        key = next((k for k, v in cfg.BUTTON_ACTIONS.items() if v == self.raw), None)
        combo = cfg.format_combo(conf.shortcuts[self.index]) \
            if t == cfg.KEY_SHORTCUT and self.index in conf.shortcuts else None
        if key:
            pass
        elif t == cfg.KEY_FIRE:
            key = FIRE
            self.interval.setValue(p1)
            self.shots.setValue(p2)
        elif t == cfg.KEY_DPI_LOCK:
            key = DPI_LOCK
            self.lock_dpi.setValue((p1 + 1) * cfg.DPI_STEP)
        elif combo in keys.MEDIA_KEYS:
            key = MEDIA
            self.media.setCurrentIndex(self.media.findData(combo))
        elif combo:
            key = SHORTCUT
            self.shortcut.setText(combo)
        elif t == cfg.KEY_MACRO and self.index in conf.macros and p1 == self.index:
            key = MACRO
            self.macro, self.repeat = conf.macros[self.index], p2
        else:
            # Unknown or unreadable: keep it as is
            key = OTHER
            self.combo.addItem("Sin cambios", OTHER)
            self.other_label.setText(conf.describe_button(self.index))
        self._show_macro()
        self.combo.setCurrentIndex(self.combo.findData(key))
        self._selected()
        self._loading = False

    def error(self):
        key = self.combo.currentData()
        if key == SHORTCUT and self.shortcut.events() is None:
            return "botón %d: el atajo de teclado no es válido" % (self.index + 1)
        if key == MACRO and self.macro is None:
            return "botón %d: falta definir la macro" % (self.index + 1)
        return None

    def store(self, conf):
        key = self.combo.currentData()
        k = self.index
        if self.error() or key == OTHER:
            conf.buttons[k] = self.raw
        elif key == FIRE:
            conf.buttons[k] = (cfg.KEY_FIRE, self.interval.value(), self.shots.value())
        elif key == DPI_LOCK:
            conf.buttons[k] = (cfg.KEY_DPI_LOCK, self.lock_dpi.value() // cfg.DPI_STEP - 1, 0)
        elif key == SHORTCUT:
            conf.set_shortcut(k, self.shortcut.events())
        elif key == MEDIA:
            conf.set_shortcut(k, cfg.parse_combo(self.media.currentData()))
        elif key == MACRO:
            conf.set_macro(k, self.macro, self.repeat)
        else:
            conf.buttons[k] = cfg.BUTTON_ACTIONS[key]


REPEAT_TEXT = {cfg.REPEAT_WHILE_HELD: "mientras se pulse", cfg.REPEAT_TOGGLE: "hasta volver a pulsar",
               cfg.REPEAT_UNTIL_ANY: "hasta pulsar otro botón", 1: "una vez"}


class ButtonsTab(QWidget):
    edited = Signal()

    def __init__(self):
        super().__init__()
        inner = QWidget()
        grid = QGridLayout(inner)
        self.rows = [ButtonRow(grid, k, k, self.edited.emit) for k in range(cfg.KEY_COUNT)]
        grid.setColumnStretch(2, 1)
        grid.setRowStretch(cfg.KEY_COUNT, 1)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        note = QLabel("Los números son posiciones en la memoria del ratón; los botones 1–5 son "
                      "izquierdo, derecho, central, atrás y adelante. Los atajos se guardan como "
                      "teclas físicas: el ratón envía la posición y tu distribución de teclado "
                      "decide el carácter.")
        note.setWordWrap(True)
        note.setEnabled(False)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(note)

    def load(self, conf):
        for row, value in zip(self.rows, conf.buttons):
            row.load(value, conf)

    def store(self, conf):
        for row in self.rows:
            row.store(conf)

    def errors(self):
        return [e for e in (row.error() for row in self.rows) if e]


class ChoiceBox(QComboBox):
    """Combo box over raw option values; a stored value it does not list is kept and shown."""

    def __init__(self, choices):
        super().__init__()
        self.choices = list(choices)

    def load(self, value, raw_text="valor %d"):
        self.clear()
        for v, text in self.choices:
            self.addItem(text, v)
        if self.findData(value) < 0:
            self.addItem(raw_text % value, value)
        self.setCurrentIndex(self.findData(value))


def time_choices(values):
    return [(v, cfg.format_time(v)) for v in values]


class PerformanceTab(QWidget):
    edited = Signal()

    def __init__(self):
        super().__init__()
        main = QGroupBox("Sensor")
        form = QFormLayout(main)
        self.rate = QComboBox()
        form.addRow("Frecuencia de sondeo:", self.rate)
        self.debounce = QSpinBox()
        self.debounce.setRange(0, 255)
        self.debounce.setSuffix(" ms")
        form.addRow("Debounce de los botones:", self.debounce)
        self.lod = ChoiceBox(cfg.LOD_CHOICES.items())
        self.lod.setToolTip("Altura a la que el sensor deja de leer al levantar el ratón")
        form.addRow("Distancia de despegue (LOD):", self.lod)
        self.mode = ChoiceBox([(0, "Bajo consumo (LP)"), (1, "Alto rendimiento (HP)")])
        form.addRow("Modo del sensor:", self.mode)
        self.full = QCheckBox("Rendimiento máximo durante:")
        self.full.setToolTip("El sensor se mantiene a máximo rendimiento este tiempo antes "
                             "de pasar a reposo")
        self.full_time = ChoiceBox(time_choices(cfg.FULL_PERFORMANCE_TIMES))
        form.addRow(self.full, self.full_time)
        self.checks = {}
        for name, text in (("motion-sync", "Motion Sync"),
                           ("linear-correction", "Corrección lineal (angle snapping)"),
                           ("ripple-control", "Ripple control")):
            box = QCheckBox(text)
            box.toggled.connect(lambda *_: self.edited.emit())
            form.addRow(box)
            self.checks[name] = box

        layout = QVBoxLayout(self)
        layout.addWidget(main)
        layout.addStretch()
        for combo in (self.rate, self.lod, self.mode, self.full_time):
            combo.currentIndexChanged.connect(lambda *_: self.edited.emit())
        self.debounce.valueChanged.connect(lambda *_: self.edited.emit())
        self.full.toggled.connect(self.full_time.setEnabled)
        self.full.toggled.connect(lambda *_: self.edited.emit())

    def load(self, conf):
        self.rate.clear()
        codes = [cfg.RATES[hz] for hz in (125, 250, 500, 1000)]
        if conf.report_rate not in codes:
            codes.append(conf.report_rate)
        for code in codes:
            hz = cfg.RATE_HZ.get(code)
            self.rate.addItem("%d Hz" % hz if hz else "código 0x%02x" % code, code)
        self.rate.setCurrentIndex(self.rate.findData(conf.report_rate))
        opts = conf.options
        self.debounce.setValue(opts["debounce"])
        self.lod.load(opts["lod"])
        self.mode.load(opts["sensor-mode"])
        self.full_time.load(opts["full-performance-time"], "%d × 10 s")
        self.full.setChecked(bool(opts["full-performance"]))
        self.full_time.setEnabled(self.full.isChecked())
        for name, box in self.checks.items():
            box.setChecked(bool(opts[name]))

    def store(self, conf):
        conf.report_rate = self.rate.currentData()
        opts = conf.options
        opts["debounce"] = self.debounce.value()
        opts["lod"] = self.lod.currentData()
        opts["sensor-mode"] = self.mode.currentData()
        opts["full-performance-time"] = self.full_time.currentData()
        store_bool(opts, "full-performance", self.full.isChecked())
        for name, box in self.checks.items():
            store_bool(opts, name, box.isChecked())


def store_bool(opts, name, checked):
    # Keep the stored non-zero value if the box was not touched
    if bool(opts[name]) != checked:
        opts[name] = int(checked)


DPI_LED_OFF = 0


class LightsTab(QWidget):
    edited = Signal()

    def __init__(self):
        super().__init__()
        dpi = QGroupBox("LED de DPI")
        form = QFormLayout(dpi)
        self.effect = ChoiceBox([(DPI_LED_OFF, "Apagado"), (1, "Fijo"), (2, "Parpadeo")])
        form.addRow("Efecto:", self.effect)
        self.brightness = ChoiceBox((b, "%d" % (i + 1))
                                    for i, b in enumerate(cfg.DPI_LED_BRIGHTNESS))
        form.addRow("Brillo (1–10):", self.brightness)
        self.speed = ChoiceBox((v, "%d" % v) for v in cfg.DPI_LED_SPEEDS)
        form.addRow("Velocidad (1–5):", self.speed)
        note = QLabel("El LED muestra el color de cada nivel de DPI (pestaña DPI). El brillo se "
                      "aplica al efecto fijo y la velocidad al parpadeo.")
        note.setWordWrap(True)
        note.setEnabled(False)
        form.addRow(note)

        power = QGroupBox("Ahorro de energía")
        pform = QFormLayout(power)
        self.off_time = ChoiceBox(time_choices(cfg.LED_OFF_TIMES))
        pform.addRow("Apagar las luces en reposo tras:", self.off_time)
        self.move_off = QCheckBox("Apagar las luces mientras se mueve el ratón")
        pform.addRow(self.move_off)

        layout = QVBoxLayout(self)
        layout.addWidget(dpi)
        layout.addWidget(power)
        layout.addStretch()
        for combo in (self.effect, self.brightness, self.speed, self.off_time):
            combo.currentIndexChanged.connect(lambda *_: self.edited.emit())
        self.effect.currentIndexChanged.connect(self._update)
        self.move_off.toggled.connect(lambda *_: self.edited.emit())

    def _update(self):
        effect = self.effect.currentData()
        self.brightness.setEnabled(effect == 1)
        self.speed.setEnabled(effect == 2)

    def load(self, conf):
        opts = conf.options
        # Off is "disabled", whatever mode is stored (_3In1.customComboBox_DPIEffect)
        self.effect.load(opts["dpi-led-mode"] if opts["dpi-led"] else DPI_LED_OFF, "modo %d")
        self.brightness.load(opts["dpi-led-brightness"])
        self.speed.load(opts["dpi-led-speed"])
        self.off_time.load(opts["led-off-time"], "%d × 10 s")
        self.move_off.setChecked(bool(opts["move-off-led"]))
        self._update()

    def store(self, conf):
        opts = conf.options
        effect = self.effect.currentData()
        store_bool(opts, "dpi-led", effect != DPI_LED_OFF)
        if effect != DPI_LED_OFF:
            opts["dpi-led-mode"] = effect
        opts["dpi-led-brightness"] = self.brightness.currentData()
        opts["dpi-led-speed"] = self.speed.currentData()
        opts["led-off-time"] = self.off_time.currentData()
        store_bool(opts, "move-off-led", self.move_off.isChecked())


class BackupTab(QWidget):
    dump_requested = Signal(str)
    restore_requested = Signal(str)

    def __init__(self):
        super().__init__()
        text = QLabel("Guarda una copia completa de la memoria del ratón (6912 bytes) antes de "
                      "hacer cambios. Al restaurar solo se reescriben las partes que difieren, "
                      "y después se verifica toda la memoria.")
        text.setWordWrap(True)
        self.dump_btn = QPushButton(QIcon.fromTheme("document-save"), "Guardar copia…")
        self.restore_btn = QPushButton(QIcon.fromTheme("document-revert"), "Restaurar copia…")
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.dump_btn)
        buttons.addWidget(self.restore_btn)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(buttons)
        layout.addWidget(self.bar)
        layout.addStretch()
        self.dump_btn.clicked.connect(self._dump)
        self.restore_btn.clicked.connect(self._restore)

    def _dump(self):
        name = "m916-%s.bin" % time.strftime("%Y%m%d-%H%M")
        path, _ = QFileDialog.getSaveFileName(self, "Guardar copia de la memoria",
                                              os.path.join(os.path.expanduser("~"), name),
                                              "Volcado de flash (*.bin)")
        if path:
            self.dump_requested.emit(path)

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "Restaurar copia de la memoria",
                                              os.path.expanduser("~"), "Volcado de flash (*.bin)")
        if path:
            self.restore_requested.emit(path)

    def set_busy(self, busy):
        self.dump_btn.setEnabled(not busy)
        self.restore_btn.setEnabled(not busy)
        self.bar.setVisible(busy)
        if busy:
            self.bar.setValue(0)

    def set_progress(self, done, total):
        self.bar.setMaximum(total)
        self.bar.setValue(done)


class MainWindow(QMainWindow):
    def __init__(self, worker):
        super().__init__()
        self.worker = worker
        self.image = None      # cfg.read_image() of what is stored in the mouse
        self.state = STATE_DISCONNECTED
        self._loading = False
        self._busy = False
        self.setWindowTitle("Redragon M916-PRO-1K")
        self.setWindowIcon(QIcon.fromTheme("input-mouse"))

        self.status = QLabel()
        self.battery = QProgressBar()
        self.battery.setRange(0, 100)
        self.battery.setFixedWidth(160)
        self.battery.setFormat("Batería %p %")
        self.firmware = QLabel()
        self.firmware.setEnabled(False)
        header = QHBoxLayout()
        header.addWidget(self.status, 1)
        header.addWidget(self.firmware)
        header.addWidget(self.battery)

        self.dpi = DpiTab()
        self.buttons = ButtonsTab()
        self.performance = PerformanceTab()
        self.lights = LightsTab()
        self.config_tabs = (self.dpi, self.buttons, self.performance, self.lights)
        self.backup = BackupTab()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.dpi, QIcon.fromTheme("input-mouse"), "DPI")
        self.tabs.addTab(self.buttons, QIcon.fromTheme("input-keyboard"), "Botones")
        self.tabs.addTab(self.performance, QIcon.fromTheme("speedometer"), "Rendimiento")
        self.tabs.addTab(self.lights, QIcon.fromTheme("lighttable"), "Luces")
        self.tabs.addTab(self.backup, QIcon.fromTheme("document-save"), "Copia de seguridad")

        self.reload_btn = QPushButton(QIcon.fromTheme("view-refresh"), "Recargar")
        self.revert_btn = QPushButton(QIcon.fromTheme("edit-undo"), "Descartar cambios")
        self.apply_btn = QPushButton(QIcon.fromTheme("dialog-ok-apply"), "Aplicar")
        self.apply_btn.setDefault(True)
        footer = QHBoxLayout()
        footer.addWidget(self.reload_btn)
        footer.addStretch()
        footer.addWidget(self.revert_btn)
        footer.addWidget(self.apply_btn)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(header)
        layout.addWidget(self.tabs)
        layout.addLayout(footer)
        self.setCentralWidget(central)
        self.resize(640, 560)

        for tab in self.config_tabs:
            tab.edited.connect(self._edited)
        self.reload_btn.clicked.connect(lambda: self._submit("load"))
        self.revert_btn.clicked.connect(self._revert)
        self.apply_btn.clicked.connect(self._apply)
        self.backup.dump_requested.connect(lambda p: self._submit("dump", p))
        self.backup.restore_requested.connect(self._restore)

        worker.state_changed.connect(self._state_changed)
        worker.info.connect(self._info)
        worker.config_loaded.connect(self._config_loaded)
        worker.progress.connect(self.backup.set_progress)
        worker.job_done.connect(self._job_done)
        worker.job_failed.connect(self._job_failed)
        worker.notified.connect(self._notified)
        self._state_changed(STATE_DISCONNECTED, "")

    # --- helpers -------------------------------------------------------------------------
    def _submit(self, *job):
        self._busy = True
        if job[0] in ("dump", "restore"):
            self.backup.set_busy(True)
        self._update_actions()
        self.worker.submit(*job)

    def _current_config(self):
        conf = cfg.Config.decode(self.image)
        for tab in self.config_tabs:
            tab.store(conf)
        return conf

    def _dirty(self):
        if self.image is None:
            return False
        return self._current_config().encode(self.image) != self.image

    def _update_actions(self):
        ready = self.state == STATE_ONLINE and self.image is not None and not self._busy
        dirty = self._dirty()
        self.apply_btn.setEnabled(ready and dirty)
        self.revert_btn.setEnabled(dirty and not self._busy)
        self.reload_btn.setEnabled(self.state == STATE_ONLINE and not self._busy)
        self.tabs.setEnabled(self.image is not None)
        for btn in (self.backup.dump_btn, self.backup.restore_btn):
            btn.setEnabled(self.state == STATE_ONLINE and not self._busy)
        self.setWindowTitle("Redragon M916-PRO-1K" + (" *" if dirty else ""))

    def _load_widgets(self, image):
        self._loading = True
        conf = cfg.Config.decode(image)
        for tab in self.config_tabs:
            tab.load(conf)
        self._loading = False
        bad = cfg.bad_checks(image)
        if bad:
            self.statusBar().showMessage("Aviso: bytes de comprobación incorrectos en %s"
                                         % ", ".join(hex(o) for o in bad))

    # --- user actions --------------------------------------------------------------------
    def _edited(self):
        if not self._loading:
            self._update_actions()

    def _revert(self):
        if self.image is not None:
            self._load_widgets(self.image)
            self._update_actions()

    def _apply(self):
        conf = self._current_config()
        bands = [i + 1 for i, lv in enumerate(conf.levels) if cfg.dpi_band(lv.x) != cfg.dpi_band(lv.y)]
        if bands:
            QMessageBox.warning(self, "DPI no válido",
                                "En los niveles %s, X e Y deben estar ambos por debajo o ambos "
                                "por encima de 12 800 DPI (y de 25 600)." % ", ".join(map(str, bands)))
            return
        errors = self.buttons.errors() + conf.validate()
        if errors:
            QMessageBox.warning(self, "Configuración no válida", "\n".join(errors))
            return
        self._submit("apply", self.image, conf.encode(self.image))

    def _restore(self, path):
        try:
            with open(path, "rb") as f:
                image = f.read()
        except OSError as e:
            QMessageBox.critical(self, "Restaurar", str(e))
            return
        if len(image) != FLASH_SIZE:
            QMessageBox.critical(self, "Restaurar", "%s tiene %d bytes; se esperaban %d."
                                 % (path, len(image), FLASH_SIZE))
            return
        answer = QMessageBox.question(self, "Restaurar copia",
                                      "Se reescribirá la memoria del ratón con el contenido de\n%s"
                                      "\n\nLos cambios sin aplicar se perderán. ¿Continuar?" % path)
        if answer == QMessageBox.StandardButton.Yes:
            self._submit("restore", image)

    # --- worker signals ------------------------------------------------------------------
    def _state_changed(self, state, path):
        self.state = state
        dev = os.path.basename(path) if path else ""
        text = {
            STATE_ONLINE: "<b>Conectado</b> · %s" % dev,
            STATE_ASLEEP: "<b>Ratón dormido</b> · muévelo o haz clic para despertarlo",
            STATE_DISCONNECTED: "<b>Receptor no encontrado</b> · conéctalo para empezar",
        }[state]
        self.status.setText(text)
        self.battery.setVisible(state == STATE_ONLINE)
        if state == STATE_DISCONNECTED:
            self.image = None
            self._busy = False
            self.backup.set_busy(False)
        self._update_actions()

    def _info(self, info):
        if "firmware" in info:
            self.firmware.setText("Firmware %s · v%s" % (info["firmware"].hex(" "), __version__))
        if "battery" in info:
            level, charging, mv = info["battery"]
            self.battery.setValue(level)
            self.battery.setFormat("Batería %d %%%s" % (level, " ⚡" if charging else ""))
            self.battery.setToolTip("%d mV" % mv)

    def _config_loaded(self, image):
        self._busy = False
        self.image = image
        self._load_widgets(image)
        self._update_actions()

    def _job_done(self, name, result):
        if name == "apply":
            self.image = result
            self.statusBar().showMessage("Cambios aplicados y verificados", 5000)
        elif name == "active" and self.image is not None:
            # The mouse changed its DPI level: update the stored copy and the widget
            img = bytearray(self.image)
            img[cfg.OFF_DPI_ACTIVE:cfg.OFF_DPI_ACTIVE + 2] = cfg.with_check([result])
            dirty = self._dirty()
            self.image = bytes(img)
            if not dirty:
                self.dpi.set_active(result)
        elif name == "dump":
            self.statusBar().showMessage("Copia guardada en %s" % result, 8000)
        elif name == "restore":
            self.statusBar().showMessage("Copia restaurada (%d bloques reescritos) y verificada"
                                         % result, 8000)
        if name in ("apply", "dump", "restore"):
            self._busy = False
            self.backup.set_busy(False)
        self._update_actions()

    def _job_failed(self, name, message):
        self._busy = False
        self.backup.set_busy(False)
        self._update_actions()
        if name == "load" and self.state != STATE_ONLINE:
            return  # the status line already says the mouse is asleep
        QMessageBox.warning(self, "Error", message)

    def _notified(self, flags):
        if flags & STATUS_BATTERY:
            self.worker.submit("battery")
        if flags & STATUS_DPI:
            self.worker.submit("active")
        if flags & ~(STATUS_BATTERY | STATUS_DPI) and not self._dirty() and not self._busy:
            self._submit("load")

    def closeEvent(self, event):
        if self._dirty():
            answer = QMessageBox.question(
                self, "Cambios sin aplicar", "Hay cambios sin aplicar. ¿Salir de todos modos?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="m916-gui")
    ap.add_argument("--fake", metavar="FLASH.bin", help="use a simulated mouse with this flash dump")
    ap.add_argument("--screenshot", metavar="DIR", help=argparse.SUPPRESS)
    args, qt_args = ap.parse_known_args(argv)

    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("m916-linux")
    app.setDesktopFileName("io.github.furibeh.m916-linux")

    fake = None
    if args.fake:
        with open(args.fake, "rb") as f:
            fake = f.read()
    worker = DeviceWorker(fake)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    window = MainWindow(worker)
    window.show()
    thread.start()

    if args.screenshot:
        from PySide6.QtCore import QTimer

        def shoot():
            os.makedirs(args.screenshot, exist_ok=True)
            for i in range(window.tabs.count()):
                window.tabs.setCurrentIndex(i)
                app.processEvents()
                window.grab().save(os.path.join(args.screenshot, "tab%d.png" % i))
            app.quit()
        QTimer.singleShot(2500, shoot)

    code = app.exec()
    worker.stop()
    thread.quit()
    thread.wait(3000)
    return code


if __name__ == "__main__":
    sys.exit(main())
