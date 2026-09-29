"""
DXF -> ETABS Model Builder.

Standalone PySide6 app (kept separate from etabs_gui.pyw / study_sheet.pyw
because it embeds ezdxf's Qt-based CADViewer, which can't share a window
with Tkinter). Reads a DXF floorplan, lets the user confirm which layers
hold which element type, extracts walls/slabs/beams/columns with their
dimensions, shows an interactive reference plan plus a classified summary,
and -- once attached to a live ETABS instance -- draws the confirmed
elements into the model as real objects with generated sections.

Launched with --pid <N> (normally by the "DXF Model Builder" card in
etabs_gui.pyw, which is already attached to that PID) to auto-attach to the
same running ETABS instance on startup, instead of connecting separately.
"""
import os
import sys
import traceback

from ezdxf.addons.xqt import QtWidgets as qw, QtCore as qc, QtGui as qg
from ezdxf.addons.drawing.qtviewer import CADViewer
from ezdxf.addons.drawing.config import Configuration

from dxf_import.dxf_helpers import load_dxf, get_layer_names, detect_dxf_scale_to_mm
from dxf_import.layer_classifier import auto_classify_layers
from dxf_import.layer_confirm_dialog import LayerConfirmDialog
from dxf_import.wall_slab_extractor import extract_slabs, extract_walls
from dxf_import.beam_extractor import extract_beams
from dxf_import.column_extractor import extract_columns
from dxf_import.connectivity import snap_connections
from dxf_import.deduplication import deduplicate_parsed
from dxf_import.etabs_model_writer import draw_model
from dxf_import.clean_dxf_exporter import export_clean_dxf
from dxf_import.clean_dxf_importer import parse_clean_dxf

import etabs_worker

DEFAULT_FCK = "30"  # matches etabs_gui.pyw's DEFAULT_FCK, N/mm^2


# ──────────────────────────────────────────────────────────────────────────
# Dark stylesheet -- applied at the QApplication level (see main()) so it
# reaches every top-level widget this app creates, including QDialog,
# QMessageBox and QInputDialog, not just the main window's own children.
# The base QWidget rule sets BOTH background-color and text color so nothing
# falls back to the OS light/dark theme (which is what caused white text on
# a white background when Windows was in light mode).
# ──────────────────────────────────────────────────────────────────────────
DARK_STYLESHEET = """
    QWidget {
        background-color: #1e272c; color: #eceff1;
        font-family: "Segoe UI", Arial, sans-serif; font-size: 13px;
    }
    QMainWindow { background-color: #1e272c; }
    QSplitter::handle { background-color: #2c3b42; width: 2px; }
    #TitleLabel { font-size: 20px; font-weight: bold; color: #2FA572; margin-top: 8px; }
    #SubtitleLabel { font-size: 12px; color: #90a4ae; margin-bottom: 8px; }
    #SectionHeader { font-weight: bold; color: #90a4ae; margin-top: 4px; }
    QGroupBox {
        border: 1px solid #37474f; border-radius: 6px; margin-top: 12px;
        font-weight: bold; color: #90a4ae; background-color: #263238;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
    QLabel { background-color: transparent; }
    QPushButton {
        background-color: #37474f; border: 1px solid #4f5b66; border-radius: 4px;
        padding: 6px 12px; font-weight: bold; color: #ffffff;
    }
    QPushButton:hover { background-color: #455a64; border-color: #78909c; }
    QPushButton:disabled { background-color: #263238; color: #56636a; border-color: #2c3b42; }
    #RunButton {
        background-color: #2FA572; border: 1px solid #1f7a52; color: white;
        font-size: 14px; padding: 9px; border-radius: 6px;
    }
    #RunButton:hover { background-color: #38b883; }
    #RunButton:disabled { background-color: #37474f; color: #78909c; border-color: #37474f; }
    QLineEdit {
        background-color: #1c2529; color: #eceff1; border: 1px solid #37474f;
        border-radius: 4px; padding: 5px;
    }
    #FilePathEdit { color: #cfd8dc; }
    #LogConsole {
        background-color: #10171a; border: 1px solid #263238; border-radius: 6px;
        color: #81c784; font-family: Consolas, "Courier New", monospace; font-size: 12px;
    }
    QTableWidget {
        background-color: #1c2529; color: #eceff1; gridline-color: #37474f;
        selection-background-color: #2FA572; selection-color: #ffffff;
    }
    QHeaderView::section { background-color: #263238; color: #90a4ae; padding: 4px; border: none; }
    QTabWidget::pane { border: 1px solid #37474f; background-color: #1c2529; }
    QTabBar::tab { background-color: #263238; color: #90a4ae; padding: 8px 16px; font-weight: bold; }
    QTabBar::tab:selected { background-color: #1c2529; color: #ffffff; }

    /* Dialogs / list widgets / scroll areas / combo boxes: covered by the
       base QWidget rule above for bg+color, but pinned explicitly here too
       since some platforms substitute a native (light) palette for these
       otherwise. */
    QDialog { background-color: #1e272c; color: #eceff1; }
    QMessageBox { background-color: #1e272c; color: #eceff1; }
    QScrollArea { background-color: #1e272c; border: none; }
    QScrollArea > QWidget > QWidget { background-color: #1e272c; }
    QListWidget {
        background-color: #1c2529; color: #eceff1; border: 1px solid #37474f; border-radius: 4px;
    }
    QListWidget::item:selected { background-color: #2FA572; color: #ffffff; }
    QComboBox {
        background-color: #1c2529; color: #eceff1; border: 1px solid #37474f;
        border-radius: 4px; padding: 4px;
    }
    QComboBox QAbstractItemView {
        background-color: #1c2529; color: #eceff1; selection-background-color: #2FA572;
    }
"""


# ──────────────────────────────────────────────────────────────────────────
# Origin offset for large DXF coordinates (survey / UTM)
# ──────────────────────────────────────────────────────────────────────────
def _compute_origin_offset(parsed: dict) -> tuple[float, float]:
    """If the geometry centroid is far from the origin (> 100 m = 100 000 mm),
    return an offset to translate everything near (0, 0) for numerical
    stability in ETABS.  Otherwise return (0, 0)."""
    coords = []
    for slab in parsed.get("slabs", []):
        coords.extend(slab.vertices)
    for wall in parsed.get("walls", []):
        coords.extend([wall.p1, wall.p2])
    for beam in parsed.get("beams", []):
        coords.extend([beam.p1, beam.p2])
    for col in parsed.get("columns", []):
        coords.append(col.centroid)
    if not coords:
        return (0.0, 0.0)
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    if abs(cx) > 100_000 or abs(cy) > 100_000:
        return (round(cx, 1), round(cy, 1))
    return (0.0, 0.0)


# ──────────────────────────────────────────────────────────────────────────
# Background parse worker
# ──────────────────────────────────────────────────────────────────────────
class ParseWorker(qc.QThread):
    log_signal = qc.Signal(str)
    finished_signal = qc.Signal(bool, object)  # (success, result_dict | error_str)

    def __init__(self, dxf_path: str, buckets: dict, auto_connect: bool = True):
        super().__init__()
        self.dxf_path = dxf_path
        self.buckets = buckets
        self.auto_connect = auto_connect

    def run(self):
        try:
            self.log_signal.emit("Loading DXF geometry...")
            doc, msp = load_dxf(self.dxf_path)

            # Detect DXF drawing units and compute a scale factor to mm.
            scale, scale_source = detect_dxf_scale_to_mm(doc, msp)
            self.log_signal.emit(f"DXF units: {scale_source} (scale factor: {scale}×)")

            self.log_signal.emit("Extracting slabs...")
            slabs = extract_slabs(msp, self.buckets.get("slabs", []), scale=scale)

            self.log_signal.emit("Extracting walls...")
            walls = extract_walls(msp, self.buckets.get("str_walls", []), self.buckets.get("ns_walls", []),
                                  scale=scale)

            self.log_signal.emit("Extracting beams...")
            beams = extract_beams(msp, self.buckets.get("beams", []), scale=scale)

            self.log_signal.emit("Extracting columns...")
            columns = extract_columns(msp, self.buckets.get("columns", []), scale=scale)

            self.log_signal.emit(
                f"Extracted: {len(slabs)} slabs, {len(walls)} walls, {len(beams)} beams, {len(columns)} columns."
            )
            result = {"slabs": slabs, "walls": walls, "beams": beams, "columns": columns}

            if self.auto_connect:
                self.log_signal.emit("Connecting geometry (snapping shared nodes)...")
                result = snap_connections(result, log=self.log_signal.emit)

            # Remove duplicates (e.g. same element drawn as both LWPOLYLINE + HATCH).
            self.log_signal.emit("Checking for duplicates...")
            result = deduplicate_parsed(result, log=self.log_signal.emit)

            # Compute origin offset for large DXF coordinates (survey/UTM).
            origin_offset = _compute_origin_offset(result)
            if origin_offset != (0.0, 0.0):
                self.log_signal.emit(
                    f"Large coordinate offset detected: ({origin_offset[0]:.0f}, {origin_offset[1]:.0f})mm. "
                    f"Geometry will be translated near the ETABS origin when drawing."
                )
            result["origin_offset"] = origin_offset
            result["scale"] = scale
            result["scale_source"] = scale_source

            self.finished_signal.emit(True, result)
        except Exception:
            self.finished_signal.emit(False, traceback.format_exc())


# ──────────────────────────────────────────────────────────────────────────
# Main window
# ──────────────────────────────────────────────────────────────────────────
class ModelBuilderApp(qw.QMainWindow):
    attach_finished = qc.Signal(bool, object)  # (ok, (SapModel, filename) | exception)
    draw_finished = qc.Signal(bool, object)    # (ok, DrawResult | exception)

    def __init__(self, attach_pid: int = None):
        super().__init__()
        self.dxf_path = None
        self.all_layers: list[str] = []
        self.buckets: dict[str, list[str]] = {}
        self.worker = None
        self.last_parse_result = None
        self.sap_model = None

        self.etabs_worker = etabs_worker.EtabsWorker()
        self.attach_finished.connect(self._on_attach_done)
        self.draw_finished.connect(self._on_draw_done)

        self.setWindowTitle("DXF -> ETABS Model Builder")
        self.resize(1400, 850)
        self._setup_ui()
        self._apply_styles()

        if attach_pid:
            self._start_attach(attach_pid)
        else:
            self._set_conn_status("Not connected to ETABS.", "#90a4ae")

    # ---- UI construction --------------------------------------------------
    def _setup_ui(self):
        splitter = qw.QSplitter(qc.Qt.Horizontal, self)
        self.setCentralWidget(splitter)

        left = qw.QWidget()
        left_layout = qw.QVBoxLayout(left)
        left_layout.setContentsMargins(20, 20, 20, 20)
        left_layout.setSpacing(12)

        title = qw.QLabel("DXF -> ETABS Model Builder")
        title.setObjectName("TitleLabel")
        left_layout.addWidget(title)

        subtitle = qw.QLabel("Read, classify, preview, and draw into ETABS.")
        subtitle.setObjectName("SubtitleLabel")
        left_layout.addWidget(subtitle)

        self.conn_status_label = qw.QLabel("Not connected to ETABS.")
        self.conn_status_label.setWordWrap(True)
        left_layout.addWidget(self.conn_status_label)

        file_group = qw.QGroupBox("DXF File")
        file_layout = qw.QHBoxLayout(file_group)
        self.select_btn = qw.QPushButton("Browse DXF")
        self.select_btn.clicked.connect(self.select_file)
        file_layout.addWidget(self.select_btn)
        self.file_path_edit = qw.QLineEdit("No file selected")
        self.file_path_edit.setReadOnly(True)
        self.file_path_edit.setObjectName("FilePathEdit")
        file_layout.addWidget(self.file_path_edit)
        left_layout.addWidget(file_group)

        self.layers_btn = qw.QPushButton("Detect / Confirm Layers")
        self.layers_btn.setEnabled(False)
        self.layers_btn.clicked.connect(self.confirm_layers)
        left_layout.addWidget(self.layers_btn)

        self.auto_connect_check = qw.QCheckBox("Auto-connect geometry (snap shared nodes)")
        self.auto_connect_check.setChecked(True)
        self.auto_connect_check.setToolTip(
            "Snaps wall/beam corners and T-junctions together, and expands slab edges out to the "
            "centerline of the beams/walls that bound them, so connected elements actually share a "
            "node in ETABS. Turn off to preview the raw, unsnapped extraction instead."
        )
        left_layout.addWidget(self.auto_connect_check)

        self.parse_btn = qw.QPushButton("Parse && Preview")
        self.parse_btn.setObjectName("RunButton")
        self.parse_btn.setEnabled(False)
        self.parse_btn.clicked.connect(self.run_parse)
        left_layout.addWidget(self.parse_btn)

        draw_group = qw.QGroupBox("Generate & Draw")
        draw_layout = qw.QVBoxLayout(draw_group)
        fck_row = qw.QHBoxLayout()
        fck_row.addWidget(qw.QLabel("Concrete grade fck (N/mm²):"))
        self.fck_edit = qw.QLineEdit(DEFAULT_FCK)
        self.fck_edit.setFixedWidth(70)
        fck_row.addWidget(self.fck_edit)
        fck_row.addStretch()
        draw_layout.addLayout(fck_row)

        btn_row = qw.QHBoxLayout()
        self.export_btn = qw.QPushButton("Export Centerline DXF...")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_dxf)
        btn_row.addWidget(self.export_btn)
        
        self.import_draw_btn = qw.QPushButton("Import Clean DXF && Draw...")
        self.import_draw_btn.setObjectName("RunButton")
        self.import_draw_btn.clicked.connect(self.import_and_draw)
        btn_row.addWidget(self.import_draw_btn)
        draw_layout.addLayout(btn_row)

        self.draw_btn = qw.QPushButton("Draw Extracted Directly Into ETABS")
        self.draw_btn.setEnabled(False)
        self.draw_btn.clicked.connect(self.run_draw)
        draw_layout.addWidget(self.draw_btn)
        left_layout.addWidget(draw_group)

        log_label = qw.QLabel("Process Log:")
        log_label.setObjectName("SectionHeader")
        left_layout.addWidget(log_label)
        self.log_box = qw.QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName("LogConsole")
        self.log_box.append("Browse a DXF file to begin.")
        left_layout.addWidget(self.log_box)

        splitter.addWidget(left)

        right_tabs = qw.QTabWidget()
        config = Configuration(lineweight_scaling=0)
        self.viewer = CADViewer.from_config(config)
        self.viewer.menuBar().hide()
        right_tabs.addTab(self.viewer, "Reference Plan")

        self.summary_table = qw.QTableWidget(0, 6)
        self.summary_table.setHorizontalHeaderLabels(["Type", "Name", "Layer", "Dimensions", "Source", "Warnings"])
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.setEditTriggers(qw.QAbstractItemView.NoEditTriggers)
        right_tabs.addTab(self.summary_table, "Classified Elements")

        self.results_table = qw.QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["Type", "Name", "Result"])
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setEditTriggers(qw.QAbstractItemView.NoEditTriggers)
        right_tabs.addTab(self.results_table, "ETABS Draw Results")

        splitter.addWidget(right_tabs)
        splitter.setSizes([380, 1020])
        self.right_tabs = right_tabs

    def _apply_styles(self):
        self.setStyleSheet(DARK_STYLESHEET)

    # ---- logic --------------------------------------------------------
    def log(self, message: str):
        self.log_box.append(message)
        self.log_box.ensureCursorVisible()

    def _set_conn_status(self, text: str, color: str):
        self.conn_status_label.setText(text)
        self.conn_status_label.setStyleSheet(f"color: {color};")

    # ---- ETABS connection -----------------------------------------------
    def _start_attach(self, pid: int):
        self._set_conn_status(f"Attaching to ETABS (PID {pid})...", "#ffb74d")
        self.log(f"\nAttaching to ETABS PID {pid}...")

        def job():
            sap_model = etabs_worker.attach_by_pid(pid)
            filename = sap_model.GetModelFilename() or "(untitled)"
            return sap_model, filename

        def on_done(ok, payload, elapsed):
            self.attach_finished.emit(ok, payload)

        self.etabs_worker.submit(job, on_done)

    def _on_attach_done(self, ok: bool, payload):
        if not ok:
            self._set_conn_status("ETABS connection failed.", "#ef5350")
            self.log(f"Attach failed: {payload}")
            return
        self.sap_model, filename = payload
        self._set_conn_status(f"Attached to ETABS. Active file: {filename}", "#66bb6a")
        self.log(f"Attached. Active file: {filename}")
        self._update_draw_button_state()

    def _update_draw_button_state(self):
        can_draw = self.sap_model is not None and self.last_parse_result is not None
        self.draw_btn.setEnabled(can_draw)
        if hasattr(self, 'export_btn'):
            self.export_btn.setEnabled(self.last_parse_result is not None)

    # ---- Draw into ETABS --------------------------------------------------
    def run_draw(self):
        if self.sap_model is None or self.last_parse_result is None:
            return
        try:
            fck = float(self.fck_edit.text())
            if fck <= 0:
                raise ValueError
        except ValueError:
            qw.QMessageBox.warning(self, "Invalid fck", "Enter a positive concrete grade (N/mm²).")
            return

        self.draw_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.log("\n" + "=" * 50)
        self.log(f"Drawing into ETABS (fck={fck:g} N/mm2)...")

        parsed = self.last_parse_result
        sap_model = self.sap_model
        origin_offset = parsed.get("origin_offset", (0.0, 0.0))

        def job():
            return draw_model(sap_model, parsed, fck, origin_offset=origin_offset, log=lambda m: None)

        def on_done(ok, payload, elapsed):
            self.draw_finished.emit(ok, payload)

        self.etabs_worker.submit(job, on_done)

    def _on_draw_done(self, ok: bool, payload):
        self.draw_btn.setEnabled(True)
        self.parse_btn.setEnabled(True)

        if not ok:
            self.log(f"\nDRAW FAILED:\n{payload}")
            qw.QMessageBox.critical(self, "Draw Error", f"Drawing into ETABS failed:\n{payload}")
            return

        result = payload
        drawn_total = sum(result.drawn.values())
        skipped_total = sum(len(v) for v in result.skipped.values())
        self.log(f"Drawn: {result.drawn}. Skipped: { {k: len(v) for k, v in result.skipped.items()} }.")
        self._populate_draw_results(result)
        self.right_tabs.setCurrentIndex(2)
        qw.QMessageBox.information(
            self, "Draw Complete",
            f"Drew {drawn_total} object(s) into ETABS.\n"
            f"Skipped {skipped_total} (see the ETABS Draw Results tab for reasons).")

    def _populate_draw_results(self, result):
        rows = []
        for kind in ("slabs", "walls", "beams", "columns"):
            for name, reasons in result.skipped[kind]:
                rows.append((kind[:-1].capitalize(), name, "SKIPPED: " + "; ".join(reasons)))
            if result.drawn[kind]:
                rows.append((kind[:-1].capitalize(), f"({result.drawn[kind]} objects)", "Drawn"))

        self.results_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = qw.QTableWidgetItem(str(value))
                if row[2].startswith("SKIPPED"):
                    item.setForeground(qg.QColor("#ffb74d"))
                elif row[2] == "Drawn":
                    item.setForeground(qg.QColor("#66bb6a"))
                self.results_table.setItem(r, c, item)
        self.results_table.resizeColumnsToContents()

    def select_file(self):
        filename, _ = qw.QFileDialog.getOpenFileName(self, "Select DXF Drawing", "", "DXF Files (*.dxf);;All Files (*)")
        if not filename:
            return
        self.dxf_path = filename
        self.file_path_edit.setText(os.path.basename(filename))
        self.log(f"\nSelected: {filename}")

        try:
            doc, _ = load_dxf(filename)
            self.all_layers = get_layer_names(doc)
            self.buckets = auto_classify_layers(self.all_layers)
            self.layers_btn.setEnabled(True)
            self.parse_btn.setEnabled(False)
            self.summary_table.setRowCount(0)
            self.log(f"Found {len(self.all_layers)} layers. Click 'Detect / Confirm Layers' to classify them.")
        except Exception as exc:
            self.log(f"Failed to read DXF layers: {exc}")
            return

        try:
            self.viewer.load_file(filename)
            self.log("Interactive reference plan loaded (pan/zoom, toggle layers).")
        except Exception as exc:
            self.log(f"Warning: interactive viewer failed to load this file: {exc}")

    def confirm_layers(self):
        if not self.dxf_path:
            return
        dialog = LayerConfirmDialog(self.buckets, self.all_layers, self)
        if dialog.exec() == qw.QDialog.Accepted:
            self.buckets = dialog.get_selections()
            total = sum(len(v) for v in self.buckets.values())
            self.log(f"Layer classification confirmed ({total} layer assignments).")
            self.parse_btn.setEnabled(True)
        else:
            self.log("Layer confirmation cancelled.")

    def run_parse(self):
        if not self.dxf_path:
            return
        self.select_btn.setEnabled(False)
        self.layers_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.log("\n" + "=" * 50)
        self.worker = ParseWorker(self.dxf_path, self.buckets, auto_connect=self.auto_connect_check.isChecked())
        self.worker.log_signal.connect(self.log)
        self.worker.finished_signal.connect(self.on_parse_finished)
        self.worker.start()

    def on_parse_finished(self, success: bool, result):
        self.select_btn.setEnabled(True)
        self.layers_btn.setEnabled(True)
        self.parse_btn.setEnabled(True)

        if not success:
            self.log(f"\nFAILED:\n{result}")
            qw.QMessageBox.critical(self, "Parse Error", f"Extraction failed:\n{result}")
            return

        self.last_parse_result = result
        self._update_draw_button_state()
        self._populate_summary(result)
        self.right_tabs.setCurrentIndex(1)

    def export_dxf(self):
        if not self.last_parse_result:
            return
        path, _ = qw.QFileDialog.getSaveFileName(self, "Export Clean Centerline DXF", "", "DXF Files (*.dxf)")
        if not path:
            return
        try:
            export_clean_dxf(self.last_parse_result, path, log=self.log)
        except Exception as e:
            self.log(f"Export failed: {e}")

    def import_and_draw(self):
        if not self.sap_model:
            qw.QMessageBox.warning(self, "Not Connected", "Please connect to ETABS first (e.g. by launching via the main tool).")
            return
        path, _ = qw.QFileDialog.getOpenFileName(self, "Select Clean Centerline DXF", "", "DXF Files (*.dxf)")
        if not path:
            return
        try:
            self.log("Importing clean DXF...")
            parsed = parse_clean_dxf(path, scale=1.0, log=self.log)
            offset = _compute_origin_offset(parsed)
            parsed["origin_offset"] = offset
            
            self.last_parse_result = parsed
            self._update_draw_button_state()
            self._populate_summary(parsed)
            self.run_draw()
        except Exception as e:
            self.log(f"Import failed: {e}")

    def _populate_summary(self, result: dict):
        rows = []
        for slab in result["slabs"]:
            rows.append(("Slab", slab.name, slab.layer, f"{slab.thickness_mm} mm thk",
                         slab.thickness_source, "; ".join(slab.warnings)))
        for wall in result["walls"]:
            kind = "Wall (Str)" if wall.is_structural else "Wall (NS)"
            rows.append((kind, wall.name, wall.layer, f"{wall.thickness_mm} mm thk, {wall.length_m} m long",
                         wall.thickness_source, "; ".join(wall.warnings)))
        for beam in result["beams"]:
            rows.append(("Beam", beam.name, beam.layer, f"{beam.width_mm:.0f} x {beam.depth_mm:.0f} mm",
                         beam.dimension_source, "; ".join(beam.warnings)))
        for col in result["columns"]:
            angle_info = f" @ {col.angle_deg:.0f}\u00b0" if col.angle_deg != 0 else ""
            rows.append(("Column", col.name, col.layer,
                         f"{col.width_mm:.0f} x {col.depth_mm:.0f} mm{angle_info}",
                         col.dimension_source, "; ".join(col.warnings)))

        self.summary_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = qw.QTableWidgetItem(str(value))
                if row[5]:
                    item.setForeground(qg.QColor("#ffb74d"))
                self.summary_table.setItem(r, c, item)
        self.summary_table.resizeColumnsToContents()


def main():
    # --pid <N> lets a launcher (the "DXF Model Builder" card in
    # etabs_gui.pyw) attach this window to the same ETABS instance it's
    # already connected to, instead of connecting separately. Mirrors
    # etabs_gui.pyw's own --pid convention exactly.
    attach_pid = None
    if "--pid" in sys.argv:
        try:
            attach_pid = int(sys.argv[sys.argv.index("--pid") + 1])
        except (ValueError, IndexError):
            pass

    app = qw.QApplication(sys.argv)
    # Applied at the QApplication level (not just the main window) so it
    # reaches every top-level widget -- QMessageBox, QInputDialog, the
    # LayerConfirmDialog -- regardless of Windows' own light/dark setting.
    app.setStyleSheet(DARK_STYLESHEET)
    window = ModelBuilderApp(attach_pid=attach_pid)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
