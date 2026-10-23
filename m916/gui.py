# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt 6 (PySide6) interface: python3 -m m916.gui [--fake FLASH.bin]"""
import argparse
import os
import queue
import sys
import time

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from . import __version__, config as cfg
from .protocol import (FLASH_SIZE, STATUS_BATTERY, STATUS_DPI, Mouse, MouseAsleep, ProtocolError,
                       find_devices)

STATE_DISCONNECTED, STATE_ASLEEP, STATE_ONLINE = "disconnected", "asleep", "online"


class DeviceWorker(QObject):
    """Owns the hidraw device on its own thread: runs queued jobs and, when idle, watches
    notifications and whether the mouse is awake."""

    state_changed = Signal(str, str)        # state, device path
    info = Signal(object)                   # {"firmware": bytes, "battery": (lvl, chg, mv)}
    config_loaded = Signal(object)          # CONFIG_SIZE image
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
            self.config_loaded.emit(m.read_flash(0, cfg.CONFIG_SIZE))
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
            chunks = m.restore_chunks(current, image)
            for i, addr in enumerate(chunks):
                m.write_flash(addr, image[addr:addr + 10])
                self.progress.emit(FLASH_SIZE + FLASH_SIZE * (i + 1) // len(chunks), 2 * FLASH_SIZE)
            if chunks and m.read_flash(0, FLASH_SIZE) != image:
                raise ProtocolError("La verificación falló tras restaurar")
            self.progress.emit(1, 1)
            self.job_done.emit(name, len(chunks))
            self.config_loaded.emit(m.read_flash(0, cfg.CONFIG_SIZE))


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
    ("scroll-left", "Scroll horizontal: izquierda"),
    ("scroll-right", "Scroll horizontal: derecha"),
    ("report-rate", "Cambiar frecuencia de sondeo"),
]
FIRE = "fire"
OTHER = "other"


class ButtonRow:
    def __init__(self, grid, row, index, edited):
        self.raw = (0, 0, 0)
        self.combo = QComboBox()
        for key, text in BUTTON_LABELS:
            self.combo.addItem(text, key)
        self.combo.addItem("Disparo repetido", FIRE)
        self.p1, self.p2 = QSpinBox(), QSpinBox()
        for sb, tip in ((self.p1, "Parámetro 1 (¿intervalo?)"), (self.p2, "Parámetro 2 (¿repeticiones?)")):
            sb.setRange(0, 255)
            sb.setToolTip(tip + ": significado sin confirmar")
            sb.valueChanged.connect(lambda *_: edited())
        grid.addWidget(QLabel("Botón %d" % (index + 1)), row, 0)
        grid.addWidget(self.combo, row, 1)
        grid.addWidget(self.p1, row, 2)
        grid.addWidget(self.p2, row, 3)
        self.combo.currentIndexChanged.connect(self._update)
        self.combo.currentIndexChanged.connect(lambda *_: edited())

    def _update(self):
        fire = self.combo.currentData() == FIRE
        self.p1.setVisible(fire)
        self.p2.setVisible(fire)

    def load(self, value):
        self.raw = tuple(value)
        other = self.combo.findData(OTHER)
        if other >= 0:
            self.combo.removeItem(other)
        key = next((k for k, v in cfg.BUTTON_ACTIONS.items() if v == self.raw), None)
        if key:
            self.combo.setCurrentIndex(self.combo.findData(key))
        elif self.raw[0] == cfg.KEY_FIRE:
            self.p1.setValue(self.raw[1])
            self.p2.setValue(self.raw[2])
            self.combo.setCurrentIndex(self.combo.findData(FIRE))
        else:
            # Shortcut, macro or unknown: keep it as is, not editable yet
            self.combo.addItem("Sin cambios: %s" % cfg.describe_key(*self.raw), OTHER)
            self.combo.setCurrentIndex(self.combo.count() - 1)
        self._update()

    def value(self):
        key = self.combo.currentData()
        if key == FIRE:
            return (cfg.KEY_FIRE, self.p1.value(), self.p2.value())
        if key == OTHER:
            return self.raw
        return cfg.BUTTON_ACTIONS[key]


class ButtonsTab(QWidget):
    edited = Signal()

    def __init__(self):
        super().__init__()
        inner = QWidget()
        grid = QGridLayout(inner)
        self.rows = [ButtonRow(grid, k, k, self.edited.emit) for k in range(cfg.KEY_COUNT)]
        grid.setColumnStretch(4, 1)
        grid.setRowStretch(cfg.KEY_COUNT, 1)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        note = QLabel("Los números son posiciones en la memoria del ratón; los botones 1–5 son "
                      "izquierdo, derecho, central, atrás y adelante. Los atajos de teclado y "
                      "las macros aún no se pueden editar y se conservan tal cual.")
        note.setWordWrap(True)
        note.setEnabled(False)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(note)

    def load(self, conf):
        for row, value in zip(self.rows, conf.buttons):
            row.load(value)

    def store(self, conf):
        conf.buttons = [row.value() for row in self.rows]


OPTION_LABELS = {
    "motion-sync": "Motion Sync",
    "linear-correction": "Corrección lineal (angle snapping)",
    "ripple-control": "Ripple control",
    "move-off-led": "Apagar LED en movimiento",
    "sleep-custom": "Tiempo de reposo personalizado",
    "power-saving": "Ahorro de energía del sensor",
    "lod": "Distancia de despegue (LOD)",
    "led-off-time": "Tiempo de apagado del LED",
    "sleep-time": "Tiempo de reposo del sensor",
}


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
        self.checks = {}
        for name in sorted(cfg.BOOL_OPTIONS, key=list(OPTION_LABELS).index):
            box = QCheckBox(OPTION_LABELS[name])
            box.toggled.connect(lambda *_: self.edited.emit())
            form.addRow(box)
            self.checks[name] = box

        raw = QGroupBox("Valores internos (significado sin confirmar)")
        raw_form = QFormLayout(raw)
        self.raws = {}
        for name in ("lod", "led-off-time", "sleep-time"):
            sb = QSpinBox()
            sb.setRange(0, 255)
            sb.valueChanged.connect(lambda *_: self.edited.emit())
            raw_form.addRow(OPTION_LABELS[name] + ":", sb)
            self.raws[name] = sb

        layout = QVBoxLayout(self)
        layout.addWidget(main)
        layout.addWidget(raw)
        layout.addStretch()
        self.rate.currentIndexChanged.connect(lambda *_: self.edited.emit())
        self.debounce.valueChanged.connect(lambda *_: self.edited.emit())

    def load(self, conf):
        self.rate.clear()
        codes = [cfg.RATES[hz] for hz in (125, 250, 500, 1000)]
        if conf.report_rate not in codes:
            codes.append(conf.report_rate)
        for code in codes:
            hz = cfg.RATE_HZ.get(code)
            self.rate.addItem("%d Hz" % hz if hz else "código 0x%02x" % code, code)
        self.rate.setCurrentIndex(self.rate.findData(conf.report_rate))
        self.debounce.setValue(conf.options["debounce"])
        for name, box in self.checks.items():
            box.setChecked(bool(conf.options[name]))
        for name, sb in self.raws.items():
            sb.setValue(conf.options[name])

    def store(self, conf):
        conf.report_rate = self.rate.currentData()
        conf.options["debounce"] = self.debounce.value()
        for name, box in self.checks.items():
            # Keep the stored non-zero value if the box was not touched
            if bool(conf.options[name]) != box.isChecked():
                conf.options[name] = int(box.isChecked())
        for name, sb in self.raws.items():
            conf.options[name] = sb.value()


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
        self.image = None      # CONFIG_SIZE bytes as stored in the mouse
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
        self.backup = BackupTab()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.dpi, QIcon.fromTheme("input-mouse"), "DPI")
        self.tabs.addTab(self.buttons, QIcon.fromTheme("input-keyboard"), "Botones")
        self.tabs.addTab(self.performance, QIcon.fromTheme("speedometer"), "Rendimiento")
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

        for tab in (self.dpi, self.buttons, self.performance):
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
        for tab in (self.dpi, self.buttons, self.performance):
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
        for tab in (self.dpi, self.buttons, self.performance):
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
        errors = conf.validate()
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
