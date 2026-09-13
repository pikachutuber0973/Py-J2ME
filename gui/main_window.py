
import io
import os
import sys
import zipfile

from PyQt5.QtCore import Qt, QProcess
from PyQt5.QtGui import QPixmap, QIcon, QImage
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QLabel, QFileDialog, QComboBox, QSpinBox, QCheckBox, QTextEdit,
    QListWidget, QListWidgetItem, QTabWidget, QSplitter, QTreeWidget, QTreeWidgetItem,
    QGroupBox, QMessageBox, QStatusBar, QLineEdit,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jvm.machine import ClassLoader  # noqa: E402
from emulator.runner import RESOLUTION_PRESETS  # noqa: E402

IMAGE_EXTS = (".png", ".gif", ".jpg", ".jpeg", ".bmp")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("J2ME Emulator - Control Panel")
        self.resize(980, 640)

        self.jar_path = None
        self.classloader = None
        self.detected_midlet = None
        self.process = None

        self._build_ui()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Open a .jar to begin")

    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- top bar: open + run controls ----
        bar = QHBoxLayout()
        open_btn = QPushButton("Open JAR…")
        open_btn.clicked.connect(self.open_jar)
        self.jar_label = QLabel("No JAR loaded")
        self.run_btn = QPushButton("▶ Run")
        self.run_btn.clicked.connect(self.run_emulator)
        self.run_btn.setEnabled(False)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.stop_emulator)
        self.stop_btn.setEnabled(False)
        bar.addWidget(open_btn)
        bar.addWidget(self.jar_label, 1)
        bar.addWidget(self.run_btn)
        bar.addWidget(self.stop_btn)
        root.addLayout(bar)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        tabs.addTab(self._build_settings_tab(), "Run Settings")
        tabs.addTab(self._build_sprites_tab(), "Sprite Viewer")
        tabs.addTab(self._build_classes_tab(), "Classes")
        tabs.addTab(self._build_controls_tab(), "Controls")

        # ---- console ----
        console_box = QGroupBox("Console")
        cl = QVBoxLayout(console_box)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet("background:#111; color:#ddd; font-family: monospace;")
        cl.addWidget(self.console)
        root.addWidget(console_box, 1)

    def _build_settings_tab(self):
        w = QWidget()
        form = QFormLayout(w)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Custom", None)
        for key, (pw, ph) in RESOLUTION_PRESETS.items():
            self.preset_combo.addItem(f"{key}  ({pw}×{ph})", key)
        self.preset_combo.setCurrentText("qvga-240x320  (240×320)")
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        form.addRow("Resolution preset:", self.preset_combo)

        wh_row = QHBoxLayout()
        self.width_spin = QSpinBox(); self.width_spin.setRange(32, 1024); self.width_spin.setValue(240)
        self.height_spin = QSpinBox(); self.height_spin.setRange(32, 1024); self.height_spin.setValue(320)
        wh_row.addWidget(QLabel("W:")); wh_row.addWidget(self.width_spin)
        wh_row.addWidget(QLabel("H:")); wh_row.addWidget(self.height_spin)
        wh_widget = QWidget(); wh_widget.setLayout(wh_row)
        form.addRow("Custom resolution:", wh_widget)

        self.scale_spin = QSpinBox(); self.scale_spin.setRange(1, 6); self.scale_spin.setValue(2)
        form.addRow("Window zoom:", self.scale_spin)

        self.fps_spin = QSpinBox(); self.fps_spin.setRange(5, 60); self.fps_spin.setValue(30)
        form.addRow("Target FPS:", self.fps_spin)

        self.force_repaint_check = QCheckBox("Force continuous repaint (compatibility fallback)")
        form.addRow("", self.force_repaint_check)

        self.locale_edit = QLineEdit()
        self.locale_edit.setPlaceholderText("e.g. en-US, de, fr (leave blank for default)")
        form.addRow("Locale override:", self.locale_edit)

        self.midlet_combo = QComboBox()
        form.addRow("MIDlet class:", self.midlet_combo)

        return w

    def _build_sprites_tab(self):
        w = QWidget()
        layout = QHBoxLayout(w)
        self.sprite_list = QListWidget()
        self.sprite_list.setIconSize(self.sprite_list.iconSize() * 2)
        self.sprite_list.currentItemChanged.connect(self._sprite_selected)
        layout.addWidget(self.sprite_list, 1)

        preview_box = QGroupBox("Preview")
        pv = QVBoxLayout(preview_box)
        self.sprite_preview = QLabel("Select an image")
        self.sprite_preview.setAlignment(Qt.AlignCenter)
        self.sprite_preview.setMinimumSize(240, 240)
        self.sprite_info = QLabel("")
        pv.addWidget(self.sprite_preview, 1)
        pv.addWidget(self.sprite_info)
        layout.addWidget(preview_box, 1)
        return w

    def _build_classes_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.class_tree = QTreeWidget()
        self.class_tree.setHeaderLabels(["Class / Method", "Details"])
        layout.addWidget(self.class_tree)
        return w

    def _build_controls_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        table = QTreeWidget()
        table.setHeaderLabels(["Keyboard", "J2ME control"])
        rows = [
            ("Q", "Left soft key"),
            ("W", "Right soft key"),
            ("Space", "Fire / center key"),
            ("Arrow keys", "D-pad (Up / Down / Left / Right)"),
            ("0-9", "Numeric keypad"),
            ("*", "Star key"),
            ("/", "Pound / # key"),
        ]
        for k, v in rows:
            QTreeWidgetItem(table, [k, v])
        table.expandAll()
        for i in range(2):
            table.resizeColumnToContents(i)
        layout.addWidget(QLabel("This control scheme is fixed for every loaded MIDlet:"))
        layout.addWidget(table)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------------
    def _preset_changed(self):
        key = self.preset_combo.currentData()
        if key:
            w, h = RESOLUTION_PRESETS[key]
            self.width_spin.setValue(w)
            self.height_spin.setValue(h)

    # ------------------------------------------------------------------
    def open_jar(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open J2ME JAR", "", "JAR files (*.jar)")
        if not path:
            return
        try:
            self.classloader = ClassLoader(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Failed to load JAR", str(exc))
            return
        self.jar_path = path
        self.jar_label.setText(path)
        self.run_btn.setEnabled(True)
        self._populate_classes()
        self._populate_sprites()
        self.statusBar().showMessage(f"Loaded {os.path.basename(path)}")

    def _find_midlet_classes(self):
        found = []
        for name in self.classloader.list_class_names():
            cur = name
            seen = set()
            while cur and cur not in seen:
                seen.add(cur)
                if cur == "javax/microedition/midlet/MIDlet":
                    found.append(name)
                    break
                if self.classloader.has_class(cur):
                    cur = self.classloader.get_class(cur).super_name
                else:
                    break
        return found

    def _populate_classes(self):
        self.class_tree.clear()
        self.midlet_combo.clear()
        midlets = self._find_midlet_classes()
        for name in self.classloader.list_class_names():
            jc = self.classloader.get_class(name)
            label = name.replace("/", ".")
            if name in midlets:
                label += "   [MIDlet]"
            top = QTreeWidgetItem(self.class_tree, [label, f"extends {jc.super_name.replace('/', '.') if jc.super_name else '?'}"])
            for meth in jc.methods:
                QTreeWidgetItem(top, [f"{meth.name}{meth.descriptor}", "abstract/native" if meth.code is None else f"{len(meth.code.code)} bytes"])
        self.class_tree.expandToDepth(0)
        for i in range(2):
            self.class_tree.resizeColumnToContents(i)

        for name in midlets:
            self.midlet_combo.addItem(name.replace("/", "."), name)
        self.detected_midlet = midlets[0] if midlets else None
        if not midlets:
            self.midlet_combo.addItem("(none detected — this JAR may not be a MIDlet)", None)

    def _populate_sprites(self):
        self.sprite_list.clear()
        try:
            names = self.classloader.list_resource_names()
        except Exception:
            names = []
        for name in names:
            if name.lower().endswith(IMAGE_EXTS):
                data = self.classloader.read_resource(name)
                if not data:
                    continue
                img = QImage.fromData(data)
                if img.isNull():
                    continue
                item = QListWidgetItem(QIcon(QPixmap.fromImage(img)), os.path.basename(name))
                item.setData(Qt.UserRole, (name, data, img.width(), img.height()))
                self.sprite_list.addItem(item)

    def _sprite_selected(self, current, _previous):
        if current is None:
            return
        name, data, w, h = current.data(Qt.UserRole)
        img = QImage.fromData(data)
        pix = QPixmap.fromImage(img)
        if pix.width() < 128 and pix.height() < 128:
            pix = pix.scaled(pix.width() * 4, pix.height() * 4,
                              Qt.KeepAspectRatio, Qt.FastTransformation)
        self.sprite_preview.setPixmap(pix)
        self.sprite_info.setText(f"{name}  —  {w}×{h}px")

    # ------------------------------------------------------------------
    def run_emulator(self):
        if not self.jar_path:
            return
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Already running", "Stop the current session before starting another.")
            return

        width = self.width_spin.value()
        height = self.height_spin.value()
        args = [
            "-m", "emulator.runner", self.jar_path,
            "--width", str(width), "--height", str(height),
            "--scale", str(self.scale_spin.value()),
            "--fps", str(self.fps_spin.value()),
        ]
        midlet = self.midlet_combo.currentData()
        if midlet:
            args += ["--midlet-class", midlet]
        if self.force_repaint_check.isChecked():
            args.append("--force-repaint")
        locale = self.locale_edit.text().strip()
        if locale:
            args += ["--locale", locale]

        self.console.clear()
        self.console.append(f"$ {sys.executable} {' '.join(args)}\n")

        self.process = QProcess(self)
        self.process.setWorkingDirectory(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.start(sys.executable, args)

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.statusBar().showMessage("Emulator running…")

    def stop_emulator(self):
        if self.process is not None:
            self.process.kill()

    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self.console.append(data.rstrip("\n"))

    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode(errors="replace")
        self.console.append(f'<span style="color:#f66;">{data.rstrip(chr(10))}</span>')

    def _process_finished(self, code, _status):
        self.console.append(f"\n[process exited with code {code}]")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage("Emulator stopped")


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
