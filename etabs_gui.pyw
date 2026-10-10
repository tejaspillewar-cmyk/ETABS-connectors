"""
ETABS Live Connector -- control panel for the ETABS COM API.

Run with:
    pythonw etabs_gui.pyw       (no console window)
    python  etabs_gui.pyw       (console visible, useful for debugging)

Requirements:
    pip install comtypes psutil pandas openpyxl ezdxf

Why a single worker thread
--------------------------
Every ETABS call runs on one long-lived background thread that calls
CoInitialize once. A COM pointer belongs to the apartment that created it,
so creating SapModel on one thread and using it on another is what makes
these scripts fail intermittently. One thread also means ETABS is never
asked to do two things at once, and the window stays responsive.
"""

import io
import os
import queue
import subprocess
import sys
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from tkinter import font as tkfont

import app_paths

# ── Palette ───────────────────────────────────────────────────────────────────
# Two full palettes; the one that matches the OS's current light/dark setting
# is picked once at startup (see _system_prefers_dark below).
_DARK = dict(
    BG="#0d1117", SURFACE="#161b22", SURFACE2="#1c2128", BORDER="#30363d",
    BLUE="#2f81f7", BLUE_H="#388bfd", GREEN="#3fb950", RED="#f85149",
    AMBER="#d29922", PURPLE="#bc8cff", FG="#e6edf3", FG_DIM="#8b949e",
    FG_MUTED="#484f58", CONSOLE_BG="#010409",
)
_LIGHT = dict(
    BG="#f6f8fa", SURFACE="#ffffff", SURFACE2="#eef1f4", BORDER="#d0d7de",
    BLUE="#0969da", BLUE_H="#0550ae", GREEN="#1a7f37", RED="#cf222e",
    AMBER="#9a6700", PURPLE="#8250df", FG="#1f2328", FG_DIM="#57606a",
    FG_MUTED="#8c959f", CONSOLE_BG="#ffffff",
)


def _system_prefers_dark():
    """Read the Windows 'Apps use dark mode' setting; default to dark elsewhere."""
    if sys.platform != "win32":
        return True
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return True


IS_DARK = _system_prefers_dark()
_palette = _DARK if IS_DARK else _LIGHT
BG = _palette["BG"]
SURFACE = _palette["SURFACE"]
SURFACE2 = _palette["SURFACE2"]
BORDER = _palette["BORDER"]
BLUE = _palette["BLUE"]
BLUE_H = _palette["BLUE_H"]
GREEN = _palette["GREEN"]
RED = _palette["RED"]
AMBER = _palette["AMBER"]
PURPLE = _palette["PURPLE"]
FG = _palette["FG"]
FG_DIM = _palette["FG_DIM"]
FG_MUTED = _palette["FG_MUTED"]
CONSOLE_BG = _palette["CONSOLE_BG"]

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_FY = "500"
DEFAULT_FCK = "30"
DEFAULT_TEXT_HEIGHT = "150"
DEFAULT_WIND_KEYWORDS = "gx, gwx, wx, wy, gy, gwy"
DEFAULT_OFFSET_PIER = "300"
DEFAULT_OFFSET_PT = "200"
DEFAULT_OFFSET_AS = "200"
ALL_STORIES = "(All stories)"

WINDOW_SIZE = "1000x880"
WINDOW_MIN = (860, 640)
CONSOLE_LINES = 9
UI_POLL_MS = 40          # how often the Tk thread drains the worker's queue

# ── Shared namespace for the code cells ───────────────────────────────────────
NS = {}


from etabs_worker import etabs_helper as _etabs_helper

NS["_etabs_helper"] = _etabs_helper


# ═════════════════════════════════════════════════════════════════════════════
# Cell definitions
# ═════════════════════════════════════════════════════════════════════════════

LAUNCH_CODE = (
    "helper = _etabs_helper()\n"
    "import comtypes.gen.ETABSv1\n"
    "import app_paths as _app_paths, time as _time\n"
    "_before_pids = {p['pid'] for p in _app_paths.running_etabs()}\n"
    "print('Launching ETABS from:', etabs_path)\n"
    "myETABSObject = helper.CreateObject(etabs_path)\n"
    "myETABSObject.ApplicationStart()\n"
    "myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)\n"
    "SapModel = myETABSObject.SapModel\n"
    # No direct API for "what PID did I just start" -- diff the process list
    # before/after so other tools (e.g. the DXF Model Builder card) can
    # attach to this exact instance without the user attaching a second time.
    "_new_pids = []\n"
    "for _ in range(20):\n"
    "    _new_pids = [p['pid'] for p in _app_paths.running_etabs() if p['pid'] not in _before_pids]\n"
    "    if _new_pids:\n"
    "        break\n"
    "    _time.sleep(0.25)\n"
    "if len(_new_pids) == 1:\n"
    "    connected_pid = _new_pids[0]\n"
    "print('Connected. Open your .edb model in the ETABS window.')"
)

# The PID is always chosen before this runs -- see App._attach_pid_then_run,
# which handles the none/one/several cases on the Tk thread so it can ask.
ATTACH_CODE = (
    "pid = globals().pop('attach_pid', None)\n"
    "if not pid:\n"
    "    raise RuntimeError('No ETABS instance was selected.')\n"
    "print('Attaching to ETABS PID', pid)\n"
    "helper = _etabs_helper()\n"
    "import comtypes.gen.ETABSv1\n"
    "myETABSObject = helper.GetObjectProcess('CSI.ETABS.API.ETABSObject', pid)\n"
    "if myETABSObject is None:\n"
    "    raise RuntimeError(\n"
    "        'ETABS refused the connection (PID ' + str(pid) + ').\\n\\n'\n"
    "        'Most often the model is still loading -- wait for ETABS to finish opening it and try again.')\n"
    "myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)\n"
    "SapModel = myETABSObject.SapModel\n"
    # Separate from attach_pid (which stays popped so the next Attach click
    # always re-prompts): this is "the PID we're currently connected to",
    # for other tools -- e.g. the DXF Model Builder card -- to read.
    "connected_pid = pid\n"
    "print('Attached. Active file:', SapModel.GetModelFilename() or '(none)')"
)

FILE_CODE = (
    "filepath = SapModel.GetModelFilename()\n"
    "print(f'Active file: {filepath}' if filepath else 'No model open yet.')"
)

INVENTORY_CODE = (
    "print('=== MODEL INVENTORY ===')\n"
    "print(f'Nodes  (Points) : {SapModel.PointObj.Count()}')\n"
    "print(f'Frames (Members): {SapModel.FrameObj.Count()}')\n"
    "print(f'Areas  (Shells) : {SapModel.AreaObj.Count()}')\n"
    # GetNameList comes back as (count, names, ret) on some builds and
    # (ret, count, names) on others -- reuse the parser that already knows.
    "from fdr_tool import parse_namelist\n"
    "_ok, _count, _ = parse_namelist(SapModel.PierLabel.GetNameList(0, []))\n"
    "print(f'Pier labels     : {_count if _ok else \"could not read\"}')"
)

CELLS = [
    {
        "id": "cell_1",
        "label": "01",
        "title": "Connect",
        "subtitle": "Launch ETABS 23, or attach to one already open",
        "detail": ("Launch starts ETABS from its exact .exe path, sidestepping "
                   "the registry ProgID. Attach grabs an ETABS that is already "
                   "running, by process id."),
        "accent": BLUE,
        "actions": [("Launch", "launch"), ("Attach", "attach"),
                    ("Diagnose", "diagnose")],
        "is_connect": True,
    },
    {
        "id": "cell_2",
        "label": "02",
        "title": "Active File",
        "subtitle": "Read the open .edb path",
        "detail": "Asks ETABS which model is currently loaded in the GUI.",
        "accent": PURPLE,
        "actions": [("Run", "file")],
        "is_connect": False,
    },
    {
        "id": "cell_3",
        "label": "03",
        "title": "Model Inventory",
        "subtitle": "Nodes, frames, areas, piers",
        "detail": "Counts the objects in the loaded model, including how many "
                  "pier labels are defined.",
        "accent": GREEN,
        "actions": [("Run", "inventory")],
        "is_connect": False,
    },
    {
        "id": "cell_4",
        "label": "04",
        "title": "DXF Model Builder",
        "subtitle": "Import a floorplan DXF and draw the model",
        "detail": "Opens the DXF Model Builder window, already attached to "
                  "this same ETABS instance. Classify layers, preview the "
                  "plan, and draw walls/slabs/beams/columns into ETABS.",
        "accent": AMBER,
        "actions": [("Open", "open_dxf_builder")],
        "is_connect": False,
    },
]

CODE_BY_KEY = {
    "launch": LAUNCH_CODE,
    "attach": ATTACH_CODE,
    "file": FILE_CODE,
    "inventory": INVENTORY_CODE,
}


# ═════════════════════════════════════════════════════════════════════════════
# ETABS worker -- one thread, one COM apartment, one job at a time
# (moved to etabs_worker.py so dxf_model_builder.py can share it)
# ═════════════════════════════════════════════════════════════════════════════

from etabs_worker import EtabsWorker


class ChooseInstance(tk.Toplevel):
    """Modal picker for which running ETABS to attach to.

    Instances are listed by the model they have open, because a bare PID
    means nothing to the person choosing.
    """

    def __init__(self, parent, options):
        super().__init__(parent)
        self.result = None
        # Not self._options -- that name is a tkinter.Misc internal.
        self._choices = options

        self.title("Which ETABS?")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)

        tk.Label(self, text="Several ETABS instances are running.\n"
                            "Choose the one to connect to:",
                 font=parent.f_field, bg=BG, fg=FG, justify="left").pack(
            anchor="w", padx=16, pady=(14, 8))

        self._list = tk.Listbox(
            self, height=min(8, len(options)), width=58, font=parent.f_field,
            bg=SURFACE2, fg=FG, selectbackground=BLUE, selectforeground=FG,
            highlightthickness=0, bd=0, activestyle="none")
        for pid, label, _exe in options:
            name = os.path.basename(label) if label else "(no model open)"
            self._list.insert("end", f"   {name}    -  PID {pid}")
        self._list.selection_set(0)
        self._list.pack(fill="x", padx=16)

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=16, pady=14)
        for text, accent, cmd in (("Cancel", FG_MUTED, self._cancel),
                                  ("Connect", BLUE, self._ok)):
            tk.Button(row, text=text, font=parent.f_btn, bg=accent, fg=BG,
                      activebackground=accent, activeforeground=BG, bd=0,
                      relief="flat", padx=14, pady=5, cursor="hand2",
                      command=cmd).pack(side="right", padx=(8, 0))

        self._list.bind("<Double-Button-1>", lambda e: self._ok())
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        self.grab_set()
        self._list.focus_set()
        self.wait_window(self)

    def _ok(self):
        sel = self._list.curselection()
        if sel:
            pid, _label, exe = self._choices[sel[0]]
            self.result = (pid, exe)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ChooseMode(tk.Toplevel):
    """Renumber everything, or only repair the labels that clash."""

    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("Relabel piers")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)

        tk.Label(self, text="How should the piers be relabelled?",
                 font=parent.f_title, bg=BG, fg=FG).pack(
            anchor="w", padx=18, pady=(16, 10))

        for title, detail, mode in (
                ("Renumber all piers",
                 "Every stack is numbered P1..Pn by plan position. Gives a "
                 "clean, predictable scheme, but most labels change.", "all"),
                ("Fix clashes only",
                 "Keeps a label where it already names exactly one pier, and "
                 "renames only the ones that clash. Smallest change.", "fix")):
            box = tk.Frame(self, bg=SURFACE2, padx=12, pady=10)
            box.pack(fill="x", padx=18, pady=(0, 8))
            tk.Label(box, text=title, font=parent.f_btn, bg=SURFACE2,
                     fg=FG, anchor="w").pack(fill="x")
            tk.Label(box, text=detail, font=parent.f_detail, bg=SURFACE2,
                     fg=FG_DIM, anchor="w", justify="left",
                     wraplength=360).pack(fill="x", pady=(2, 6))
            tk.Button(box, text="Choose", font=parent.f_btn, bg=BLUE, fg=BG,
                      activebackground=BLUE_H, activeforeground=BG, bd=0,
                      relief="flat", padx=12, pady=3, cursor="hand2",
                      command=lambda m=mode: self._pick(m)).pack(anchor="e")

        tk.Button(self, text="Cancel", font=parent.f_btn, bg=FG_MUTED, fg=BG,
                  activebackground=FG_MUTED, activeforeground=BG, bd=0,
                  relief="flat", padx=14, pady=4, cursor="hand2",
                  command=self._cancel).pack(anchor="e", padx=18, pady=(2, 14))

        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.wait_window(self)

    def _pick(self, mode):
        self.result = mode
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ReportMappingDialog(tk.Toplevel):
    """Which load cases are EQ, SPEC, gust wind and static wind, plus stories and modifier tables."""

    IGNORE = "(ignore)"
    AUTO = "(auto)"

    def __init__(self, parent, cases, stories, mapping):
        super().__init__(parent)
        import report_extractor as rx
        self._rx = rx
        self.result = None
        self.title("Report case mapping")
        self.configure(bg=BG)
        self.transient(parent)
        self.geometry("760x740")
        names = [c[0] for c in cases]
        self.var_modal = tk.StringVar(value=mapping.modal_case)
        self.var_dead = tk.StringVar(value=mapping.dead_case)
        self.var_live = tk.StringVar(value=mapping.live_case)
        self.var_ground = tk.StringVar(value=mapping.ground_story or self.AUTO)
        self.var_typical = tk.StringVar(value=mapping.typical_story or self.AUTO)
        self.var_J = tk.BooleanVar(value=mapping.scale_frame_J)
        self._base = mapping
        titles = [rx.ROLE_TITLES[k] for k in rx.ROLE_KEYS]
        self._title_to_key = {rx.ROLE_TITLES[k]: k for k in rx.ROLE_KEYS}
        role_of = {}
        for k in rx.ROLE_KEYS:
            for c in getattr(mapping, k):
                role_of[c] = rx.ROLE_TITLES[k]
        self.var_role = {n: tk.StringVar(value=role_of.get(n, self.IGNORE)) for n in names}

        tk.Label(self, text="Report case mapping", font=parent.f_title, bg=BG,
                 fg=FG).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(self, text="Tell the report which load case is which. The guess below comes from "
                            "the case types and names; correct anything that is wrong. Saved next to the model.",
                 font=parent.f_detail, bg=BG, fg=FG_MUTED, wraplength=720,
                 justify="left").pack(anchor="w", padx=18, pady=(0, 8))

        single = tk.Frame(self, bg=BG)
        single.pack(fill="x", padx=18)
        for i, (label, var) in enumerate((("Modal case", self.var_modal),
                                          ("Dead load case", self.var_dead),
                                          ("Live load case", self.var_live))):
            tk.Label(single, text=label, font=parent.f_field, bg=BG, fg=FG_DIM).grid(
                row=0, column=2 * i, sticky="w", padx=(0 if i == 0 else 14, 6))
            ttk.Combobox(single, textvariable=var, values=names, width=14,
                         state="readonly").grid(row=0, column=2 * i + 1)

        box = tk.Frame(self, bg=BG)
        box.pack(fill="both", expand=True, padx=18, pady=10)
        canvas = tk.Canvas(box, bg=SURFACE, highlightthickness=0, height=260)
        sb = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=SURFACE)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for r, (n, kind, flagged) in enumerate(cases):
            tk.Label(inner, text=n, font=parent.f_field, bg=SURFACE, fg=FG, width=16,
                     anchor="w").grid(row=r, column=0, padx=(8, 4), pady=2, sticky="w")
            tk.Label(inner, text=kind + ("" if flagged else " (not run)"), font=parent.f_detail,
                     bg=SURFACE, fg=FG_MUTED, width=22, anchor="w").grid(row=r, column=1, pady=2)
            ttk.Combobox(inner, textvariable=self.var_role[n], width=30, state="readonly",
                         values=[self.IGNORE] + titles).grid(row=r, column=2, padx=(4, 8), pady=2)

        st = tk.Frame(self, bg=BG)
        st.pack(fill="x", padx=18)
        for i, (label, var) in enumerate((("Ground story", self.var_ground),
                                          ("Typical story", self.var_typical))):
            tk.Label(st, text=label, font=parent.f_field, bg=BG, fg=FG_DIM).grid(
                row=0, column=2 * i, sticky="w", padx=(0 if i == 0 else 14, 6))
            ttk.Combobox(st, textvariable=var, values=[self.AUTO] + list(stories), width=14,
                         state="readonly").grid(row=0, column=2 * i + 1)

        tk.Label(self, text="Stiffness modifiers applied for the SLS and ULS runs",
                 font=parent.f_field, bg=BG, fg=FG).pack(anchor="w", padx=18, pady=(12, 2))
        mods = tk.Frame(self, bg=BG)
        mods.pack(fill="x", padx=18)
        self.var_mod = {}
        for c, h in enumerate(("", "SLS (service)", "ULS (design)")):
            tk.Label(mods, text=h, font=parent.f_detail, bg=BG, fg=FG_MUTED).grid(row=0, column=c)
        for r, key in enumerate(("beams", "columns", "walls", "floors"), start=1):
            tk.Label(mods, text=key.capitalize(), font=parent.f_field, bg=BG, fg=FG_DIM).grid(
                row=r, column=0, sticky="w")
            for c in (0, 1):
                v = tk.StringVar(value=str(mapping.modifiers[key][c]))
                self.var_mod[(key, c)] = v
                tk.Entry(mods, textvariable=v, width=8, font=parent.f_field, bg=SURFACE2, fg=FG,
                         relief="flat", insertbackground=FG).grid(row=r, column=c + 1, padx=8, pady=2)
        tk.Checkbutton(self, text="Also scale the torsion constant J of frames "
                                  "(leave off if your models keep J = 1)", variable=self.var_J,
                       font=parent.f_detail, bg=BG, fg=FG_DIM, selectcolor=SURFACE2,
                       activebackground=BG, activeforeground=FG).pack(anchor="w", padx=18, pady=(6, 0))

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=18, pady=12)
        parent._flat_button(buttons, "Cancel", FG_MUTED, self._cancel, small=True).pack(side="right")
        parent._flat_button(buttons, "Save and continue", BLUE, self._accept,
                            small=True).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        self.grab_set()
        self.wait_window(self)

    def _accept(self):
        rx = self._rx
        m = rx.ReportMapping.from_dict(self._base.to_dict())
        for k in rx.ROLE_KEYS:
            setattr(m, k, [])
        for n, var in self.var_role.items():
            key = self._title_to_key.get(var.get())
            if key:
                getattr(m, key).append(n)
        m.modal_case, m.dead_case, m.live_case = (self.var_modal.get(), self.var_dead.get(),
                                                  self.var_live.get())
        m.ground_story = "" if self.var_ground.get() == self.AUTO else self.var_ground.get()
        m.typical_story = "" if self.var_typical.get() == self.AUTO else self.var_typical.get()
        m.scale_frame_J = bool(self.var_J.get())
        try:
            for (key, c), v in self.var_mod.items():
                val = float(v.get())
                if not 0.0 < val <= 1.0:
                    raise ValueError
                m.modifiers[key][c] = val
        except ValueError:
            messagebox.showerror("Modifiers", "Modifier values must be numbers between 0 and 1.", parent=self)
            return
        for r in ("eq_x", "eq_y"):
            if not getattr(m, r):
                messagebox.showerror("Mapping", f"Choose at least one case for {rx.ROLE_TITLES[r]}.", parent=self)
                return
        if not (m.modal_case and m.dead_case and m.live_case):
            messagebox.showerror("Mapping", "Choose the modal, dead load and live load cases.", parent=self)
            return
        self.result = m
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class Redirect(io.StringIO):
    """Send writes to a callback instead of the real stdout."""

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def write(self, text):
        if text:
            self._fn(text)
        return len(text) if text else 0

    def flush(self):
        pass


# ═════════════════════════════════════════════════════════════════════════════
# Main window
# ═════════════════════════════════════════════════════════════════════════════

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("ETABS Live Connector")
        self.geometry(WINDOW_SIZE)
        self.minsize(*WINDOW_MIN)
        self.configure(bg=BG)

        self._buttons = {}          # key -> tk.Button
        self._badges = {}           # cell id -> tk.Label
        self._conn_state = "idle"

        self._worker = EtabsWorker()
        self._ui_queue = queue.Queue()
        self._tool = None           # fdr_tool.FDRTool once analysis has run
        self._fdr_running = False
        self._closing = False
        self._pump_id = None

        # Resolved before _build(), which renders it onto card 01. A running
        # ETABS wins over the saved value, so the path self-corrects whenever
        # the user opens a different install.
        self._etabs_path = app_paths.resolve_etabs_path()
        if self._etabs_path:
            app_paths.update_setting("etabs_path", self._etabs_path)

        self._fonts()
        self._style()
        self._build()
        self._pump()
        self._dark_titlebar()

        if NS.get("attach_pid"):
            # Launched by the ETABS plugin for a specific instance -- attach
            # to it automatically instead of waiting for a button click.
            self.after(150, lambda: self._run_cell(CELLS[0], "attach"))

    def _dark_titlebar(self):
        """Match the native title bar to the app's theme (Windows 10 20H1+)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1 if IS_DARK else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, old builds
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value),
                        ctypes.sizeof(value)) == 0:
                    break
        except Exception:
            pass

    # ── Fonts and ttk style ──────────────────────────────────────────────────

    def _fonts(self):
        self.f_app = tkfont.Font(family="Segoe UI", size=12, weight="bold")
        self.f_ver = tkfont.Font(family="Segoe UI", size=8)
        self.f_status = tkfont.Font(family="Segoe UI", size=9)
        self.f_num = tkfont.Font(family="Segoe UI", size=22, weight="bold")
        self.f_title = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.f_sub = tkfont.Font(family="Segoe UI", size=9)
        self.f_detail = tkfont.Font(family="Segoe UI", size=8, slant="italic")
        self.f_btn = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.f_badge = tkfont.Font(family="Segoe UI", size=7, weight="bold")
        self.f_field = tkfont.Font(family="Segoe UI", size=9)
        self.f_section = tkfont.Font(family="Segoe UI", size=13, weight="bold")
        self.f_con = tkfont.Font(family="Consolas", size=8)
        self.f_conhdr = tkfont.Font(family="Segoe UI", size=9, weight="bold")

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("default")
        s.configure("Dark.Vertical.TScrollbar", troughcolor=SURFACE,
                    background=BORDER, arrowcolor=FG_DIM, bordercolor=BG,
                    gripcount=0)
        s.map("Dark.Vertical.TScrollbar", background=[("active", FG_MUTED)])
        s.configure("Dark.TCombobox", fieldbackground=SURFACE2,
                    background=SURFACE2, foreground=FG, arrowcolor=FG_DIM,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    padding=4)
        s.map("Dark.TCombobox",
              fieldbackground=[("readonly", SURFACE2), ("disabled", SURFACE2)],
              foreground=[("readonly", FG), ("disabled", FG_MUTED)],
              selectbackground=[("readonly", SURFACE2)],
              selectforeground=[("readonly", FG)],
              arrowcolor=[("active", FG)])
        self.option_add("*TCombobox*Listbox.background", SURFACE2)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", BLUE)

    # ── Layout ───────────────────────────────────────────────────────────────

    def _build(self):
        self._topbar()
        tk.Frame(self, bg=BLUE, height=2).pack(fill="x")

        # A vertical sash lets the user trade space between the scrollable
        # input panel (top) and the action bar + console (bottom) -- drag it
        # to give more room to whichever side they need.
        paned = tk.PanedWindow(self, orient="vertical", bg=BORDER, bd=0,
                               sashwidth=6, sashrelief="flat",
                               opaqueresize=True)
        paned.pack(fill="both", expand=True)

        top_pane = tk.Frame(paned, bg=BG)
        bottom_pane = tk.Frame(paned, bg=SURFACE2)
        paned.add(top_pane, minsize=240, stretch="always")
        paned.add(bottom_pane, minsize=140, stretch="always")

        # The action bar sits above the console inside the bottom pane, so
        # Run/Save stay attached to the console instead of the scroll area.
        self._action_bar(bottom_pane)
        self._console_area(bottom_pane)
        self._main_area(top_pane)

        self.after(60, lambda: paned.sash_place(
            0, 0, max(1, int(self.winfo_height() * 0.66))))

    # ── Thread-safe hand-off to the Tk thread ────────────────────────────────
    #
    # Tk widgets, and .after() itself, may only be touched from the thread
    # running mainloop. The worker therefore queues work here and the Tk
    # thread drains the queue on a timer.

    def _post(self, fn, *args):
        self._ui_queue.put((fn, args))

    def _pump(self):
        if self._closing:
            return
        try:
            while True:
                fn, args = self._ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    text = traceback.format_exc()
                    _write_crash_log(text)
                    self._write(text, "err")
        except queue.Empty:
            pass
        self._pump_id = self.after(UI_POLL_MS, self._pump)

    def destroy(self):
        # Without this the queued _pump fires once more against a half-torn-down
        # window, and Tk reports it as an error -- which now means a dialog in
        # the user's face as they close the app.
        self._closing = True
        if self._pump_id is not None:
            try:
                self.after_cancel(self._pump_id)
            except Exception:
                pass
            self._pump_id = None
        super().destroy()

    def _topbar(self):
        bar = tk.Frame(self, bg=SURFACE2, height=54)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=SURFACE2)
        left.pack(side="left", padx=20, fill="y")
        tk.Label(left, text="", width=2, bg=BLUE).pack(side="left", padx=(0, 10),
                                                       pady=17, ipady=2)
        tk.Label(left, text="ETABS Live Connector", font=self.f_app, fg=FG,
                 bg=SURFACE2).pack(side="left", pady=17)
        tk.Label(left, text="v2.0", font=self.f_ver, fg=FG_MUTED,
                 bg=SURFACE2).pack(side="left", padx=6, pady=20)

        right = tk.Frame(bar, bg=SURFACE2)
        right.pack(side="right", padx=20, fill="y")
        self._status_dot = tk.Label(right, text="", width=2, bg=FG_DIM)
        self._status_dot.pack(side="right", pady=18, padx=(4, 0), ipady=2)
        self._status_txt = tk.Label(right, text="Not connected",
                                    font=self.f_status, fg=FG_DIM, bg=SURFACE2)
        self._status_txt.pack(side="right", pady=18)

    def _main_area(self, parent):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True)

        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview,
                            style="Dark.Vertical.TScrollbar")
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._scroll = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=self._scroll, anchor="nw")
        self._scroll.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(window, width=e.width))

        self._cards_section()
        self._bind_scroll(canvas)

    def _bind_scroll(self, canvas):
        """Global wheel binding that scrolls the canvas from anywhere over it.

        bind_all captures every MouseWheel event application-wide; the handler
        checks whether the event widget lives inside the canvas before acting,
        so the console and combo-box list keep their own scroll behaviour.
        """
        self._scroll_canvas = canvas

        def _is_descendant(widget, ancestor):
            """Walk up the widget tree to check parentage."""
            try:
                w = widget
                while w is not None:
                    if w is ancestor:
                        return True
                    w = w.master
            except Exception:
                pass
            return False

        def on_wheel(e):
            w = e.widget
            # Widgets that scroll themselves: leave them alone.
            if isinstance(w, (tk.Listbox, tk.Text)):
                return
            # Only scroll when the pointer is over the canvas / its children.
            if not (_is_descendant(w, canvas) or w is canvas):
                return
            canvas.yview_scroll(-1 * int(e.delta / 120), "units")

        canvas.bind_all("<MouseWheel>", on_wheel, add="+")

    # ── Cards ────────────────────────────────────────────────────────────────

    def _section_header(self, parent, title, subtitle):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=24, pady=(14, 6))
        tk.Label(row, text=title, font=self.f_section, fg=FG,
                 bg=BG).pack(side="left")
        tk.Label(row, text=subtitle, font=self.f_detail, fg=FG_DIM,
                 bg=BG).pack(side="left", padx=12, pady=4)

    def _cards_section(self):
        self._section_header(self._scroll, "Connection",
                             "Connect, check the model, then set up FDR")
        grid = tk.Frame(self._scroll, bg=BG)
        grid.pack(fill="x", padx=24, pady=(0, 24))
        for col in range(3):
            grid.columnconfigure(col, weight=1)

        self._card(grid, 0, CELLS[0])
        self._stacked_pair(grid, 1, CELLS[1], CELLS[2])
        self._fdr_card(grid, 2)

    def _pier_mini_card(self, parent, row):
        """Compact pier-relabel card for the stacked column."""
        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        border.grid(row=row, column=0, sticky="nsew", pady=(8, 0))
        card = tk.Frame(border, bg=SURFACE)
        card.pack(fill="both", expand=True)
        tk.Frame(card, bg=AMBER, height=3).pack(fill="x")

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        top = tk.Frame(body, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text="Pier Labels", font=self.f_title, fg=FG,
                 bg=SURFACE).pack(side="left")

        tk.Label(body,
                 text="Rebuild pier labels from wall geometry",
                 font=self.f_sub, fg=AMBER, bg=SURFACE, anchor="w",
                 justify="left", wraplength=260).pack(fill="x", pady=(4, 8))

        row_btns = tk.Frame(body, bg=SURFACE)
        row_btns.pack(fill="x")
        for i, (text, key, accent, cmd) in enumerate((
                ("Preview", "pier_preview", AMBER, self._relabel_preview),
                ("Apply", "pier_apply", RED, self._relabel_apply))):
            btn = self._flat_button(row_btns, text, accent, cmd, small=True)
            btn.pack(side="left", expand=True, fill="x",
                     padx=(0 if i == 0 else 6, 0))
            self._buttons[key] = btn

    def _card(self, parent, col, cell):
        accent = cell["accent"]

        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        border.grid(row=0, column=col, padx=(0 if col == 0 else 8, 0),
                    sticky="nsew")
        card = tk.Frame(border, bg=SURFACE)
        card.pack(fill="both", expand=True)
        tk.Frame(card, bg=accent, height=3).pack(fill="x")

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        top = tk.Frame(body, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text=cell["label"], font=self.f_num, fg=accent,
                 bg=SURFACE).pack(side="left")

        tk.Label(body, text=cell["title"], font=self.f_title, fg=FG, bg=SURFACE,
                 anchor="w").pack(fill="x", pady=(6, 0))
        tk.Label(body, text=cell["subtitle"], font=self.f_sub, fg=accent,
                 bg=SURFACE, anchor="w", wraplength=250,
                 justify="left").pack(fill="x")
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=9)
        tk.Label(body, text=cell["detail"], font=self.f_detail, fg=FG_DIM,
                 bg=SURFACE, anchor="w", justify="left",
                 wraplength=250).pack(fill="x")
        tk.Frame(body, bg=SURFACE, height=8).pack()

        badge = tk.Label(body, text="IDLE", font=self.f_badge, fg=BG,
                         bg=FG_MUTED, padx=6, pady=2)
        badge.pack(anchor="w", pady=(0, 8))
        self._badges[cell["id"]] = badge

        if cell.get("is_connect"):
            self._etabs_path_row(body)

        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x")
        for i, (text, key) in enumerate(cell["actions"]):
            btn = self._flat_button(
                row, text, accent,
                lambda k=key, c=cell: self._run_cell(c, k))
            btn.pack(side="left", expand=True, fill="x",
                     padx=(0 if i == 0 else 6, 0))
            self._buttons[key] = btn

    def _etabs_path_row(self, parent):
        """Which ETABS Launch will start, with a way to correct it.

        Only Launch reads this -- Attach finds ETABS by process id -- so it
        stays a quiet one-liner rather than a settings screen.
        """
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=(0, 8))

        tk.Label(row, text="ETABS:", font=self.f_detail, fg=FG_MUTED,
                 bg=SURFACE).pack(side="left")
        tk.Button(row, text="Change…", font=self.f_detail, fg=FG_DIM,
                  bg=SURFACE, activebackground=SURFACE, activeforeground=FG,
                  bd=0, relief="flat", padx=4, pady=0, cursor="hand2",
                  command=self._pick_etabs).pack(side="right")
        self.lbl_etabs = tk.Label(row, text="", font=self.f_detail, fg=FG_DIM,
                                  bg=SURFACE, anchor="w")
        self.lbl_etabs.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self._refresh_etabs_label()

    def _stacked_pair(self, parent, col, cell_top, cell_bottom):
        """Compact cards stacked in one grid column: 02, 03, pier relabel, design review report."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.grid(row=0, column=col, padx=(8, 0), sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        self._mini_card(wrap, 0, cell_top)
        self._mini_card(wrap, 1, cell_bottom)
        self._pier_mini_card(wrap, 2)
        self._report_mini_card(wrap, 3)

    def _report_mini_card(self, parent, row):
        """Compact card: build the design review report from the analysed model."""
        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        border.grid(row=row, column=0, sticky="nsew", pady=(8, 0))
        card = tk.Frame(border, bg=SURFACE)
        card.pack(fill="both", expand=True)
        tk.Frame(card, bg=BLUE, height=3).pack(fill="x")

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        tk.Label(body, text="Design Review Report", font=self.f_title, fg=FG,
                 bg=SURFACE, anchor="w").pack(fill="x")
        tk.Label(body,
                 text="Reads the model (SLS and ULS), then opens the report "
                      "in the report app to check and edit",
                 font=self.f_sub, fg=BLUE, bg=SURFACE, anchor="w",
                 justify="left", wraplength=260).pack(fill="x", pady=(4, 8))
        row_btns = tk.Frame(body, bg=SURFACE)
        row_btns.pack(fill="x")
        for i, (text, key, accent, cmd) in enumerate((
                ("Cases...", "report_map", PURPLE, self._report_edit_mapping),
                ("Generate", "report_full", BLUE,
                 lambda: self._report_start("full")),
                ("Quick read", "report_quick", GREEN,
                 lambda: self._report_start("quick")))):
            btn = self._flat_button(row_btns, text, accent, cmd, small=True)
            btn.pack(side="left", expand=True, fill="x",
                     padx=(0 if i == 0 else 6, 0))
            self._buttons[key] = btn

    def _mini_card(self, parent, row, cell):
        """A compact variant of _card for the stacked column."""
        accent = cell["accent"]

        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        border.grid(row=row, column=0, sticky="nsew",
                    pady=(0 if row == 0 else 8, 0))
        card = tk.Frame(border, bg=SURFACE)
        card.pack(fill="both", expand=True)
        tk.Frame(card, bg=accent, height=3).pack(fill="x")

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        top = tk.Frame(body, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text=cell["label"], font=self.f_title, fg=accent,
                 bg=SURFACE).pack(side="left")
        tk.Label(top, text=cell["title"], font=self.f_title, fg=FG,
                 bg=SURFACE).pack(side="left", padx=(8, 0))

        tk.Label(body, text=cell["subtitle"], font=self.f_sub, fg=accent,
                 bg=SURFACE, anchor="w", wraplength=260,
                 justify="left").pack(fill="x", pady=(4, 8))

        badge = tk.Label(body, text="IDLE", font=self.f_badge, fg=BG,
                         bg=FG_MUTED, padx=6, pady=2)
        badge.pack(anchor="w", pady=(0, 8))
        self._badges[cell["id"]] = badge

        row_btns = tk.Frame(body, bg=SURFACE)
        row_btns.pack(fill="x")
        for i, (text, key) in enumerate(cell["actions"]):
            btn = self._flat_button(
                row_btns, text, accent,
                lambda k=key, c=cell: self._run_cell(c, k), small=True)
            btn.pack(side="left", expand=True, fill="x",
                     padx=(0 if i == 0 else 6, 0))
            self._buttons[key] = btn

    def _flat_button(self, parent, text, accent, command, small=False):
        btn = tk.Button(
            parent, text=text, font=self.f_btn, fg="white", bg=accent,
            activebackground=BLUE_H if accent == BLUE else accent,
            activeforeground="white", relief="flat",
            padx=10, pady=4 if small else 8, cursor="hand2",
            disabledforeground=FG_MUTED, command=command,
        )
        self._hover(btn, accent)
        return btn

    def _hover(self, widget, accent):
        darker = self._darken(accent)

        def enter(_):
            if str(widget["state"]) != "disabled":
                widget.configure(bg=darker)

        def leave(_):
            if str(widget["state"]) != "disabled":
                widget.configure(bg=accent)

        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)
        widget._accent = accent

    @staticmethod
    def _darken(hex_color):
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return f"#{max(0, r - 25):02x}{max(0, g - 25):02x}{max(0, b - 25):02x}"

    # ── FDR panel ────────────────────────────────────────────────────────────

    def _fdr_card(self, parent, col):
        """The FDR setup, styled as a card in the same row as 01/02+03.

        Kept narrow (one grid column) on purpose, rather than the old
        full-width panel, to leave room to add more cards alongside it later.
        """
        accent = AMBER

        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        border.grid(row=0, column=col, padx=(8, 0), sticky="nsew")
        card = tk.Frame(border, bg=SURFACE)
        card.pack(fill="both", expand=True)
        tk.Frame(card, bg=accent, height=3).pack(fill="x")

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        tk.Label(body, text="FDR", font=self.f_title, fg=FG, bg=SURFACE,
                 anchor="w").pack(fill="x")
        tk.Label(body, text="Flexural Design Review", font=self.f_sub,
                 fg=accent, bg=SURFACE, anchor="w").pack(fill="x")
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=9)
        tk.Label(body,
                 text="Required Pt%, 0.4 fck and 0.2 fck checks, Excel and "
                      "DXF output.",
                 font=self.f_detail, fg=FG_DIM, bg=SURFACE, anchor="w",
                 justify="left", wraplength=260).pack(fill="x")
        tk.Frame(body, bg=SURFACE, height=8).pack()

        # -- Material and drawing inputs ------------------------------------
        self.var_fy = tk.StringVar(value=DEFAULT_FY)
        self.var_fck = tk.StringVar(value=DEFAULT_FCK)
        self.var_text_h = tk.StringVar(value=DEFAULT_TEXT_HEIGHT)
        self.var_wind = tk.StringVar(value=DEFAULT_WIND_KEYWORDS)
        self.var_story = tk.StringVar(value=ALL_STORIES)

        fy_fck = tk.Frame(body, bg=SURFACE)
        fy_fck.pack(fill="x", pady=(0, 8))
        self._field(fy_fck, "fy (MPa)", self.var_fy).pack(
            side="left", fill="x", expand=True)
        self._field(fy_fck, "fck (MPa)", self.var_fck).pack(
            side="left", fill="x", expand=True, padx=(8, 0))

        self._field(body, "DXF text height (mm)", self.var_text_h).pack(
            fill="x", pady=(0, 8))
        self._field(body, "Wind keywords (comma separated)",
                    self.var_wind).pack(fill="x", pady=(0, 10))

        # -- Text stack offsets ----------------------------------------------
        self.var_offset_pier = tk.StringVar(value=DEFAULT_OFFSET_PIER)
        self.var_offset_pt = tk.StringVar(value=DEFAULT_OFFSET_PT)
        self.var_offset_as = tk.StringVar(value=DEFAULT_OFFSET_AS)

        tk.Label(body, text="Text stack offsets (mm)", font=self.f_badge,
                 fg=FG_DIM, bg=SURFACE, anchor="w").pack(fill="x", pady=(0, 4))
        offsets_row = tk.Frame(body, bg=SURFACE)
        offsets_row.pack(fill="x", pady=(0, 6))
        self._field(offsets_row, "Pier gap", self.var_offset_pier, width=6).pack(
            side="left", fill="x", expand=True)
        self._field(offsets_row, "Label→Pt%", self.var_offset_pt, width=6).pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self._field(offsets_row, "Pt%→As", self.var_offset_as, width=6).pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        tk.Label(body,
                 text="Stack (bottom to top): Pier label, Required Pt%, "
                      "As Required.",
                 font=self.f_detail, fg=FG_MUTED, bg=SURFACE, anchor="w",
                 wraplength=260, justify="left").pack(fill="x", pady=(0, 10))

        # -- Text content options ---------------------------------------------
        self.var_show_percent = tk.BooleanVar(value=True)
        self.var_plot_min_values = tk.BooleanVar(value=True)

        chk_row = tk.Frame(body, bg=SURFACE)
        chk_row.pack(fill="x", pady=(0, 10))
        self._checkbox(chk_row, "Show \"%\" symbol",
                       self.var_show_percent).pack(side="left")
        self._checkbox(chk_row, "Plot min-governed",
                       self.var_plot_min_values).pack(side="left", padx=(12, 0))

        # -- Story + refresh ---------------------------------------------------
        tk.Label(body, text="Story", font=self.f_badge, fg=FG_DIM,
                 bg=SURFACE, anchor="w").pack(fill="x")
        self.cmb_story = ttk.Combobox(
            body, textvariable=self.var_story, values=[ALL_STORIES],
            state="readonly", style="Dark.TCombobox", font=self.f_field)
        self.cmb_story.pack(fill="x", pady=(2, 8))

        btn_refresh = self._flat_button(
            body, "Read stories + combos", BLUE, self._load_model_lists,
            small=True)
        btn_refresh.pack(fill="x", pady=(0, 6))
        self._buttons["refresh"] = btn_refresh

        tk.Label(body,
                 text="A DXF covers one story. Leave on All stories for a "
                      "whole-building Excel report.",
                 font=self.f_detail, fg=FG_MUTED, bg=SURFACE, anchor="w",
                 wraplength=260, justify="left").pack(fill="x", pady=(0, 10))

        # -- Combination picker -------------------------------------------------
        head = tk.Frame(body, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="Load combinations", font=self.f_badge, fg=FG_DIM,
                 bg=SURFACE).pack(side="left")
        tk.Button(head, text="None", font=self.f_badge, fg=FG_DIM, bg=SURFACE,
                  activebackground=BORDER, activeforeground=FG, relief="flat",
                  padx=6, cursor="hand2",
                  command=lambda: self.lst_combos.selection_clear(0, "end")
                  ).pack(side="right")
        tk.Button(head, text="All", font=self.f_badge, fg=FG_DIM, bg=SURFACE,
                  activebackground=BORDER, activeforeground=FG, relief="flat",
                  padx=6, cursor="hand2",
                  command=lambda: self.lst_combos.selection_set(0, "end")
                  ).pack(side="right", padx=4)

        self.lbl_combo_count = tk.Label(body, text="none loaded",
                                        font=self.f_detail, fg=FG_MUTED,
                                        bg=SURFACE, anchor="w")
        self.lbl_combo_count.pack(fill="x", pady=(2, 4))

        list_frame = tk.Frame(body, bg=BORDER, padx=1, pady=1)
        list_frame.pack(fill="x")
        self.lst_combos = tk.Listbox(
            list_frame, selectmode="extended", height=6, bg=SURFACE2, fg=FG,
            selectbackground=BLUE, selectforeground="white", relief="flat",
            highlightthickness=0, font=self.f_field,
            exportselection=False)
        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self.lst_combos.yview,
                           style="Dark.Vertical.TScrollbar")
        self.lst_combos.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lst_combos.pack(side="left", fill="both", expand=True)

        tk.Label(body,
                 text="Nothing selected means every combination is used.",
                 font=self.f_detail, fg=FG_MUTED, bg=SURFACE, anchor="w",
                 wraplength=260, justify="left").pack(fill="x", pady=(4, 0))

    # ── Action bar (always visible, sits just above the console) ─────────────

    def _action_bar(self, parent):
        bar = tk.Frame(parent, bg=SURFACE2)
        bar.pack(fill="x", side="top")
        tk.Frame(bar, bg=BORDER, height=1).pack(fill="x")

        inner = tk.Frame(bar, bg=SURFACE2)
        inner.pack(fill="x", padx=24, pady=12)

        btn_dxf = self._flat_button(inner, "Save DXF...", PURPLE,
                                    self._save_dxf)
        btn_dxf.pack(side="right")
        self._buttons["fdr_dxf"] = btn_dxf

        btn_xl = self._flat_button(inner, "Save Excel...", GREEN,
                                   self._save_excel)
        btn_xl.pack(side="right", padx=(0, 10))
        self._buttons["fdr_excel"] = btn_xl

        btn_run = self._flat_button(inner, "Run FDR analysis", AMBER,
                                    self._run_fdr)
        btn_run.pack(side="right", padx=(0, 10))
        self._buttons["fdr_run"] = btn_run

        self.lbl_summary = tk.Label(
            inner, text="No analysis run yet.", font=self.f_field, fg=FG_DIM,
            bg=SURFACE2, anchor="w", justify="left")
        self.lbl_summary.pack(side="left", fill="x", expand=True)

        self._set_export_enabled(False)

    def _field(self, parent, label, variable, width=10):
        box = tk.Frame(parent, bg=SURFACE)
        tk.Label(box, text=label, font=self.f_badge, fg=FG_DIM, bg=SURFACE,
                 anchor="w").pack(fill="x")
        tk.Entry(box, textvariable=variable, width=width, bg=SURFACE2, fg=FG,
                 insertbackground=FG, relief="flat", font=self.f_field,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=BLUE).pack(fill="x", ipady=3)
        return box

    def _checkbox(self, parent, label, variable):
        return tk.Checkbutton(
            parent, text=label, variable=variable, font=self.f_field,
            fg=FG, bg=SURFACE, activebackground=SURFACE, activeforeground=FG,
            selectcolor=SURFACE2, highlightthickness=0, bd=0, cursor="hand2",
            anchor="w")

    # ── Console ──────────────────────────────────────────────────────────────

    def _console_area(self, parent):
        frame = tk.Frame(parent, bg=SURFACE2)
        frame.pack(fill="both", expand=True, side="top")

        head = tk.Frame(frame, bg=SURFACE2)
        head.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(head, text="Output", font=self.f_conhdr, fg=FG_DIM,
                 bg=SURFACE2).pack(side="left")
        tk.Button(head, text="Clear", font=self.f_badge, fg=FG_DIM, bg=SURFACE2,
                  activebackground=BORDER, activeforeground=FG, relief="flat",
                  padx=6, pady=2, cursor="hand2",
                  command=self._clear).pack(side="right")
        tk.Button(head, text="Save log...", font=self.f_badge, fg=FG_DIM,
                  bg=SURFACE2, activebackground=BORDER, activeforeground=FG,
                  relief="flat", padx=6, pady=2, cursor="hand2",
                  command=self._save_log).pack(side="right", padx=6)

        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", pady=(6, 0))

        self._con = scrolledtext.ScrolledText(
            frame, font=self.f_con, bg=CONSOLE_BG, fg=FG, insertbackground=FG,
            relief="flat", bd=0, highlightthickness=0, state="disabled",
            height=CONSOLE_LINES, wrap="none")
        self._con.pack(fill="both", expand=True)
        self._con.tag_config("err", foreground=RED)
        self._con.tag_config("ok", foreground=GREEN)
        self._con.tag_config("info", foreground=BLUE)
        self._con.tag_config("warn", foreground=AMBER)

    def _write(self, text, tag=None):
        self._con.configure(state="normal")
        self._con.insert("end", text, tag or "")
        self._con.see("end")
        self._con.configure(state="disabled")

    def _log(self, text, tag=None):
        self._write(text if text.endswith("\n") else text + "\n", tag)

    def report_callback_exception(self, exc_type, exc, tb):
        """Tk calls this by name when a widget callback raises.

        Tk's default implementation prints to stderr, which is None under
        pythonw.exe -- so without this override an error inside any button
        handler or .after() job disappears completely.
        """
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if self._closing:
            _write_crash_log(text)   # teardown noise: log it, don't nag
            return
        try:
            self._write(text, "err")
        except Exception:
            pass
        path = _write_crash_log(text)
        messagebox.showerror(
            "Unexpected error",
            f"{exc_type.__name__}: {exc}"
            + (f"\n\nFull report:\n{path}" if path else ""),
            parent=self)

    def _clear(self):
        self._con.configure(state="normal")
        self._con.delete("1.0", "end")
        self._con.configure(state="disabled")

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save output log", defaultextension=".txt",
            initialfile="etabs_session_log.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self._con.get("1.0", "end"))
        self._log(f"Log saved: {path}", "ok")

    # ── Connect: deciding what to connect to ─────────────────────────────────

    def _choose_attach_target(self, cell):
        """Settle which ETABS to attach to, then re-enter _run_cell.

        On the Tk thread, because the several-instances case has to ask.
        """
        procs = app_paths.running_etabs()

        if not procs:
            messagebox.showwarning(
                "No ETABS running",
                "No ETABS process is running on this machine.\n\n"
                "Open ETABS and load your model, then press Attach again — "
                "or press Launch to start ETABS from here.", parent=self)
            self._log("Attach: no running ETABS found.", "warn")
            return

        readable = [p for p in procs if not p["access_denied"]]
        if not readable:
            self._elevation_blocked(procs)
            return

        if len(readable) == 1:
            self._attach_to(cell, readable[0]["pid"], readable[0]["exe"])
            return

        self._probe_instances(cell, readable)

    def _elevation_blocked(self, procs):
        """We can see ETABS but not read it -- the classic privilege mismatch.

        psutil can read an elevated process's name but not its exe path, so
        AccessDenied here is a reliable signal that ETABS is running elevated
        and we are not. COM cannot cross that boundary, and nothing about the
        raw failure says so.
        """
        pids = ", ".join(str(p["pid"]) for p in procs)
        messagebox.showerror(
            "ETABS is running as administrator",
            f"ETABS (PID {pids}) is running with administrator privileges, "
            "but this app is not. Windows blocks connections between "
            "programs at different privilege levels.\n\n"
            "Either one of these fixes it:\n"
            "  •  Close ETABS and reopen it normally (without 'Run as "
            "administrator'), or\n"
            "  •  Start this app as administrator too.", parent=self)
        self._log(f"Attach blocked: ETABS (PID {pids}) is elevated and this "
                  f"app is not.", "err")

    def _probe_instances(self, cell, procs):
        """Ask each running ETABS which model it has open, then let the user
        pick. The COM calls have to happen on the worker thread."""
        self._log(f"{len(procs)} ETABS instances running — reading their "
                  f"models...", "info")

        def job():
            import comtypes.gen.ETABSv1
            helper = NS["_etabs_helper"]()
            found = []
            for proc in procs:
                label = ""
                try:
                    obj = helper.GetObjectProcess(
                        "CSI.ETABS.API.ETABSObject", proc["pid"])
                    if obj is not None:
                        obj = obj.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
                        label = obj.SapModel.GetModelFilename() or ""
                except Exception:
                    label = ""      # listed by PID alone; not fatal
                found.append((proc["pid"], label, proc["exe"]))
            return found

        def done(ok, payload, elapsed):
            self._post(self._pick_instance, cell, ok, payload, procs)

        self._worker.submit(job, done)

    def _pick_instance(self, cell, ok, payload, procs):
        if ok:
            options = payload
        else:
            self._log(f"Could not read model names ({payload}); listing by "
                      f"PID.", "warn")
            options = [(p["pid"], "", p["exe"]) for p in procs]

        choice = ChooseInstance(self, options).result
        if choice is None:
            self._log("Attach cancelled.", "warn")
            return
        self._attach_to(cell, choice[0], choice[1])

    def _attach_to(self, cell, pid, exe=None):
        NS["attach_pid"] = pid
        # A running instance is the most trustworthy source there is for where
        # ETABS lives, so attaching once teaches Launch the path for free.
        if exe:
            self._remember_etabs_path(exe)
        self._run_cell(cell, "attach")

    # ── Connect: where ETABS.exe lives ───────────────────────────────────────

    def _confirm_etabs_path(self):
        """True when Launch has a usable ETABS.exe, asking the user if not."""
        if self._etabs_path and os.path.isfile(self._etabs_path):
            return True

        if self._etabs_path:
            message = ("The saved ETABS location no longer exists:\n\n"
                       f"{self._etabs_path}\n\n"
                       "Would you like to locate ETABS.exe yourself?")
        else:
            message = ("ETABS could not be found automatically on this "
                       "machine.\n\nWould you like to locate ETABS.exe "
                       "yourself?")
        if not messagebox.askyesno("Where is ETABS?", message, parent=self):
            self._log("Launch cancelled — no ETABS location set.", "warn")
            return False
        return self._pick_etabs()

    def _pick_etabs(self):
        """File picker for ETABS.exe. True when a path was chosen."""
        start = os.path.dirname(self._etabs_path) if self._etabs_path else ""
        path = filedialog.askopenfilename(
            parent=self, title="Locate ETABS.exe",
            initialdir=start or None,
            filetypes=[("ETABS program", "ETABS.exe"),
                       ("Programs", "*.exe"),
                       ("All files", "*.*")])
        if not path:
            return False
        self._remember_etabs_path(path)
        return True

    def _remember_etabs_path(self, path):
        if path and path != self._etabs_path:
            self._etabs_path = path
            app_paths.update_setting("etabs_path", path)
            self._log(f"ETABS location saved: {path}", "ok")
        self._refresh_etabs_label()

    def _refresh_etabs_label(self):
        label = getattr(self, "lbl_etabs", None)
        if label is None:
            return
        if not self._etabs_path:
            label.configure(text="ETABS not found — click Change…", fg=RED)
        elif not os.path.isfile(self._etabs_path):
            label.configure(text=f"Missing: {self._etabs_path}", fg=RED)
        else:
            folder = os.path.basename(os.path.dirname(self._etabs_path))
            label.configure(text=folder or self._etabs_path, fg=FG_DIM)

    # ── Pier relabelling ─────────────────────────────────────────────────────

    def _relabel_preview(self):
        model = self._sap_model()
        if model is None:
            return
        self._disable(self._buttons["pier_preview"])
        self._log("")
        self._log("=" * 60, "info")
        self._log("  PIER LABELS -- PREVIEW", "info")
        self._log("=" * 60, "info")

        def job():
            import pier_relabel
            return pier_relabel.relabel(
                model, mode="all", write=False,
                log=lambda m: self._post(self._log, str(m)))

        def done(ok, payload, elapsed):
            self._post(self._relabel_preview_done, ok, payload, elapsed)

        self._worker.submit(job, done)

    def _relabel_preview_done(self, ok, payload, elapsed):
        self._enable(self._buttons["pier_preview"])
        if not ok:
            self._log(f"  Preview failed: {payload}", "err")
            messagebox.showerror("Preview failed", str(payload), parent=self)
            return
        self._log(f"  Preview finished in {elapsed:.1f}s", "ok")
        if payload.get("locked"):
            self._log("  Apply is blocked until the model is unlocked.", "warn")

    def _relabel_apply(self):
        model = self._sap_model()
        if model is None:
            return

        import pier_relabel
        if pier_relabel.is_locked(model):
            messagebox.showerror(
                "Model is locked",
                "This model has analysis results, so ETABS will not let pier "
                "labels change.\n\n"
                "Unlocking discards the results and the model must be "
                "re-analysed, which can take hours on a large model.\n\n"
                "Relabel first, then run the analysis, then the FDR.",
                parent=self)
            self._log("Apply refused: the model is locked.", "err")
            return

        mode = ChooseMode(self).result
        if mode is None:
            self._log("Relabelling cancelled.", "warn")
            return

        if not messagebox.askyesno(
                "Rewrite pier labels?",
                ("Every pier will be renumbered from its position."
                 if mode == "all" else
                 "Only piers whose labels clash will be renamed.")
                + "\n\nThis changes the open model. It is NOT saved, so "
                  "closing ETABS without saving still discards it.\n\n"
                  "Continue?", parent=self):
            self._log("Relabelling cancelled.", "warn")
            return

        self._disable(self._buttons["pier_apply"])
        self._log("")
        self._log("=" * 60, "info")
        self._log(f"  PIER LABELS -- APPLYING ({mode})", "info")
        self._log("=" * 60, "info")

        def job():
            import pier_relabel
            return pier_relabel.relabel(
                model, mode=mode, write=True,
                log=lambda m: self._post(self._log, str(m)))

        def done(ok, payload, elapsed):
            self._post(self._relabel_apply_done, ok, payload, elapsed)

        self._worker.submit(job, done)

    def _relabel_apply_done(self, ok, payload, elapsed):
        self._enable(self._buttons["pier_apply"])
        if not ok:
            self._log(f"  Relabelling failed: {payload}", "err")
            messagebox.showerror("Relabelling failed", str(payload), parent=self)
            return
        written, failed = payload.get("written", (0, []))
        if failed:
            self._log(f"  {len(failed)} assignments failed", "err")
            messagebox.showwarning(
                "Partly applied",
                f"{written} labels were set but {len(failed)} failed.\n\n"
                "The model is now in a mixed state. Reopen it without saving "
                "to get back to where you started.", parent=self)
            return
        self._log(f"  Relabelled {written} wall areas in {elapsed:.1f}s", "ok")
        messagebox.showinfo(
            "Pier labels rewritten",
            f"{written} wall areas relabelled.\n\n"
            "The model has NOT been saved. Check it in ETABS, then save there "
            "if you are happy with it.", parent=self)

    # ── Connect: self-diagnosis ──────────────────────────────────────────────

    def _diagnose(self):
        """Everything needed to explain a failed connection, in one place.

        Needs no ETABS connection, so it still works when nothing else does.
        Ask a colleague for this before asking anything else.
        """
        import importlib.util
        import platform

        self._log("")
        self._log("=" * 60, "info")
        self._log("  CONNECTION DIAGNOSTICS", "info")
        self._log("=" * 60, "info")

        bits = 64 if sys.maxsize > 2 ** 32 else 32
        elevated = app_paths.is_elevated()
        self._log(f"  Windows      : {platform.platform()}")
        self._log(f"  Python       : {platform.python_version()} ({bits}-bit)")
        self._log(f"  Interpreter  : {sys.executable}")
        self._log(f"  Running as   : {'administrator' if elevated else 'normal user'}")

        self._log("")
        for module in ("comtypes", "psutil", "pandas", "openpyxl", "ezdxf"):
            found = importlib.util.find_spec(module) is not None
            self._log(f"  {module:<12} : {'installed' if found else 'MISSING'}",
                      None if found else "err")
        generated = importlib.util.find_spec("comtypes.gen.ETABSv1") is not None
        self._log(f"  {'ETABS API':<12} : "
                  + ("type library ready" if generated
                     else "not generated yet (happens on first connect)"),
                  None if generated else "warn")

        self._log("")
        if self._etabs_path and os.path.isfile(self._etabs_path):
            self._log(f"  ETABS.exe    : {self._etabs_path}", "ok")
        elif self._etabs_path:
            self._log(f"  ETABS.exe    : MISSING - {self._etabs_path}", "err")
        else:
            self._log("  ETABS.exe    : not found on this machine", "err")
        for exe in app_paths.detect_etabs():
            if str(exe) != self._etabs_path:
                self._log(f"  also found   : {exe}")

        self._log("")
        procs = app_paths.running_etabs()
        if not procs:
            self._log("  Running ETABS: none", "warn")
        for proc in procs:
            if proc["access_denied"]:
                self._log(f"  PID {proc['pid']:<8} : running as administrator "
                          f"- this app cannot reach it", "err")
            else:
                self._log(f"  PID {proc['pid']:<8} : {proc['exe']}", "ok")
        if procs and elevated is False and any(p["access_denied"] for p in procs):
            self._log("")
            self._log("  -> Reopen ETABS without 'Run as administrator', or "
                      "start this app as administrator.", "warn")

        self._log("")
        self._log(f"  Settings     : {app_paths.settings_path()}")
        self._log(f"  Logs         : {app_paths.logs_dir()}")
        self._log("=" * 60, "info")

    def _open_dxf_builder(self):
        """Launch the standalone DXF Model Builder, attached to this ETABS.

        A separate process, not a Tk panel: it embeds a Qt CADViewer, and
        Qt/Tk can't share one window. See dxf_model_builder.py. It attaches
        itself using the same --pid convention main() already understands
        for this app (see the bottom of this file).
        """
        pid = NS.get("connected_pid")
        if not pid:
            self._log("")
            self._log("Connect to ETABS first (card 01 -- Launch or Attach), "
                      "then open the DXF Model Builder.", "warn")
            return

        here = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(here, "dxf_model_builder.py")
        if not os.path.isfile(script):
            self._log(f"dxf_model_builder.py not found next to this app: {script}", "err")
            return

        python = sys.executable
        if python.lower().endswith("python.exe"):
            pythonw = python[:-len("python.exe")] + "pythonw.exe"
            if os.path.isfile(pythonw):
                python = pythonw

        try:
            subprocess.Popen([python, script, "--pid", str(pid)], cwd=here)
            self._log(f"Opened DXF Model Builder, attached to ETABS PID {pid}.", "ok")
        except OSError as exc:
            self._log(f"Could not launch DXF Model Builder: {exc}", "err")

    # ── Cell execution ───────────────────────────────────────────────────────

    def _run_cell(self, cell, key):
        # Both connect paths need a decision made on the Tk thread first --
        # which ETABS to attach to, or where ETABS.exe lives -- because either
        # may have to ask. Those helpers call back here once settled.
        if key == "diagnose":
            self._diagnose()
            return
        if key == "open_dxf_builder":
            self._open_dxf_builder()
            return
        if key == "attach" and not NS.get("attach_pid"):
            self._choose_attach_target(cell)
            return
        if key == "launch" and not self._confirm_etabs_path():
            return

        code = CODE_BY_KEY[key]
        badge = self._badges[cell["id"]]
        buttons = [self._buttons[k] for _, k in cell["actions"]]

        for btn in buttons:
            self._disable(btn)
        badge.configure(text="RUNNING", bg=AMBER, fg=BG)

        if cell.get("is_connect"):
            self._conn_state = "connecting"
            self._status_txt.configure(text="Connecting...", fg=AMBER)
            self._status_dot.configure(bg=AMBER)

        self._log("")
        self._log("-" * 60, "info")
        self._log(f"  {cell['title']} -- {key}", "info")
        self._log("-" * 60, "info")

        NS["etabs_path"] = self._etabs_path

        def job():
            redirect = Redirect(lambda t: self._post(self._write, t))
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = redirect
            try:
                exec(compile(code, f"<{cell['id']}:{key}>", "exec"), NS)
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        def done(ok, payload, elapsed):
            self._post(self._cell_done, cell, buttons, ok, payload, elapsed)

        self._worker.submit(job, done)

    def _cell_done(self, cell, buttons, ok, payload, elapsed):
        badge = self._badges[cell["id"]]
        for btn in buttons:
            self._enable(btn)

        if ok:
            badge.configure(text="DONE", bg=GREEN, fg=BG)
            self._log(f"  Completed in {elapsed:.2f}s", "ok")
        else:
            badge.configure(text="ERROR", bg=RED, fg=FG)
            self._write("".join(traceback.format_exception(
                type(payload), payload, payload.__traceback__)), "err")
            self._log(f"  Failed after {elapsed:.2f}s", "err")

        if cell.get("is_connect"):
            if ok:
                self._conn_state = "ok"
                self._status_txt.configure(text="ETABS connected", fg=GREEN)
                self._status_dot.configure(bg=GREEN)
                self._load_model_lists()
            else:
                self._conn_state = "error"
                self._status_txt.configure(text="Connection failed", fg=RED)
                self._status_dot.configure(bg=RED)

    def _disable(self, btn):
        btn.configure(state="disabled", bg=SURFACE2)

    def _enable(self, btn):
        btn.configure(state="normal", bg=getattr(btn, "_accent", BLUE))

    def _set_export_enabled(self, enabled):
        for key in ("fdr_excel", "fdr_dxf"):
            btn = self._buttons.get(key)
            if btn is None:
                continue
            if enabled:
                self._enable(btn)
            else:
                self._disable(btn)

    # ── FDR: read stories and combos ─────────────────────────────────────────

    def _sap_model(self):
        model = NS.get("SapModel")
        if model is None:
            messagebox.showwarning(
                "Not connected",
                "Connect to ETABS first using card 01 (Launch or Attach).",
                parent=self)
            return None
        return model

    def _load_model_lists(self):
        model = NS.get("SapModel")
        if model is None:
            return

        btn = self._buttons["refresh"]
        self._disable(btn)

        def job():
            from fdr_tool import FDRTool
            probe = FDRTool(model, log=lambda *_: None)
            return probe.get_stories(), probe.get_available_combos()

        def done(ok, payload, elapsed):
            self._post(self._model_lists_done, ok, payload)

        self._worker.submit(job, done)

    def _model_lists_done(self, ok, payload):
        self._enable(self._buttons["refresh"])
        if not ok:
            self._log(f"Could not read stories/combinations: {payload}", "warn")
            return

        stories, combos = payload
        current = self.var_story.get()
        values = [ALL_STORIES] + list(stories)
        self.cmb_story.configure(values=values)
        self.var_story.set(current if current in values else ALL_STORIES)

        self.lst_combos.delete(0, "end")
        for name in combos:
            self.lst_combos.insert("end", name)
        self.lbl_combo_count.configure(
            text=f"{len(combos)} in model, {len(stories)} storey(s)")
        self._log(f"Model read: {len(stories)} storey(s), "
                  f"{len(combos)} load combination(s).", "ok")

    # ── FDR: run ─────────────────────────────────────────────────────────────

    def _read_config(self):
        """Build an FDRConfig from the panel, or None if the input is bad."""
        from fdr_tool import FDRConfig

        def number(var, name, cast, minimum):
            raw = var.get().strip()
            try:
                value = cast(raw)
            except ValueError:
                messagebox.showerror(
                    "Check the input",
                    f"{name} must be a number (got '{raw}').", parent=self)
                return None
            if value < minimum:
                messagebox.showerror(
                    "Check the input",
                    f"{name} must be at least {minimum}.", parent=self)
                return None
            return value

        fy = number(self.var_fy, "fy", int, 1)
        if fy is None:
            return None
        fck = number(self.var_fck, "fck", int, 1)
        if fck is None:
            return None
        text_h = number(self.var_text_h, "DXF text height", float, 1.0)
        if text_h is None:
            return None
        offset_pier = number(self.var_offset_pier, "Pier-to-text gap", float, 0.0)
        if offset_pier is None:
            return None
        offset_pt = number(self.var_offset_pt, "Label-to-Pt% spacing", float, 0.0)
        if offset_pt is None:
            return None
        offset_as = number(self.var_offset_as, "Pt%-to-As spacing", float, 0.0)
        if offset_as is None:
            return None

        keywords = [k.strip().lower() for k in self.var_wind.get().split(",")
                    if k.strip()]
        if not keywords:
            messagebox.showwarning(
                "No wind keywords",
                "Without wind keywords every combination counts as non-wind, "
                "so the 0.4 fck and 0.2 fck checks will include wind cases.",
                parent=self)

        story = self.var_story.get()
        return FDRConfig(
            fy=fy, fck=fck, text_height=text_h, wind_keywords=keywords,
            story_filter=None if story == ALL_STORIES else story,
            cad_offset_1=offset_pier, cad_offset_2=offset_pt, cad_offset_3=offset_as,
            show_percent_symbol=self.var_show_percent.get(),
            plot_minimum_governed_values=self.var_plot_min_values.get(),
        )

    def _selected_combos(self):
        picked = [self.lst_combos.get(i) for i in self.lst_combos.curselection()]
        return picked or None

    def _run_fdr(self):
        if self._fdr_running:
            return
        model = self._sap_model()
        if model is None:
            return
        config = self._read_config()
        if config is None:
            return

        combos = self._selected_combos()
        self._fdr_running = True
        self._disable(self._buttons["fdr_run"])
        self._set_export_enabled(False)
        self.lbl_summary.configure(text="Checking model...", fg=AMBER)

        self._log("")
        self._log("=" * 60, "info")
        self._log("  FDR ANALYSIS", "info")
        self._log("=" * 60, "info")

        def check_job():
            from fdr_tool import FDRTool
            tool = FDRTool(model, config,
                           log=lambda m: self._post(self._write, str(m) + "\n"))
            return tool, tool.needs_analysis()

        def check_done(ok, payload, elapsed):
            self._post(self._fdr_after_check, ok, payload, combos)

        self._worker.submit(check_job, check_done)

    def _fdr_after_check(self, ok, payload, combos):
        if not ok:
            self._fdr_done(False, payload, 0.0)
            return

        tool, needs_analysis = payload
        if not needs_analysis:
            self._start_fdr_pipeline(tool, combos, run_analysis_first=False)
            return

        proceed = messagebox.askyesno(
            "Analysis required",
            "This model has no analysis results available.\n\n"
            "Run analysis now? The model will be saved first, and this can "
            "take a while for large models.",
            parent=self)
        if not proceed:
            self._fdr_running = False
            self._enable(self._buttons["fdr_run"])
            self.lbl_summary.configure(
                text="Cancelled - run analysis in ETABS first.", fg=FG_DIM)
            self._log("FDR cancelled: analysis required but declined.", "warn")
            return

        self.lbl_summary.configure(text="Running analysis...", fg=AMBER)
        self._start_fdr_pipeline(tool, combos, run_analysis_first=True)

    def _start_fdr_pipeline(self, tool, combos, run_analysis_first):
        self.lbl_summary.configure(text="Running...", fg=AMBER)

        def job():
            if run_analysis_first:
                tool.run_analysis()
            tool.extract_pier_forces(combos)
            tool.extract_pier_coordinates()
            tool.run_calculations()
            tool.print_pt_results()
            tool.print_04fck_results()
            tool.print_02fck_results()
            return tool

        def done(ok, payload, elapsed):
            self._post(self._fdr_done, ok, payload, elapsed)

        self._worker.submit(job, done)

    def _fdr_done(self, ok, payload, elapsed):
        self._fdr_running = False
        self._enable(self._buttons["fdr_run"])

        if not ok:
            self._tool = None
            self._set_export_enabled(False)
            self._write("".join(traceback.format_exception(
                type(payload), payload, payload.__traceback__)), "err")
            self.lbl_summary.configure(text=f"Failed: {payload}", fg=RED)
            self._log(f"  FDR failed after {elapsed:.2f}s", "err")
            return

        self._tool = payload
        summary = self._tool.get_summary()
        self._set_export_enabled(True)

        for warning in self._tool.warnings:
            self._log(f"  [WARN] {warning}", "warn")

        self.lbl_summary.configure(
            fg=FG,
            text=(
                f"{summary['total_piers']} pier(s) over "
                f"{summary['stories']} storey(s)   |   "
                f"max Pt% {summary['max_pt']:.2f}   |   "
                f"tension {summary['tension_count']} / "
                f"compression {summary['compression_count']} / "
                f"minimum {summary['minimum_count']}\n"
                f"0.4 fck inadequate: {summary['inadequate_04']}   |   "
                f"ductile detailing required: {summary['ductile_reqd']}   |   "
                f"no net compression: {summary['no_compression']}"
            ))
        self._log(f"  FDR completed in {elapsed:.2f}s", "ok")

    # ── FDR: exports ─────────────────────────────────────────────────────────

    def _initial_dir(self):
        """Start the save dialog next to the ETABS model when we know it."""
        try:
            model = NS.get("SapModel")
            if model is not None:
                path = model.GetModelFilename()
                if path:
                    return os.path.dirname(path)
        except Exception:
            pass
        return os.path.expanduser("~")

    def _save_excel(self):
        if self._tool is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Save FDR Excel report",
            initialdir=self._initial_dir(),
            initialfile=self._tool.default_excel_name(),
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")])
        if not path:
            self._log("Excel export cancelled.", "warn")
            return

        self._disable(self._buttons["fdr_excel"])
        tool = self._tool

        def job():
            return tool.export_excel(path)

        def done(ok, payload, elapsed):
            self._post(self._export_done, "fdr_excel", "Excel report",
                       ok, payload)

        self._worker.submit(job, done)

    def _save_dxf(self):
        if self._tool is None:
            return

        stories = self._tool.stories_in_results()
        if not stories:
            messagebox.showwarning("Nothing to draw",
                                   "The results contain no piers.", parent=self)
            return

        if len(stories) == 1:
            story = stories[0]
        else:
            story = self._ask_story(stories)
            if story is None:
                self._log("DXF export cancelled.", "warn")
                return

        path = filedialog.asksaveasfilename(
            parent=self, title=f"Save DXF for {story}",
            initialdir=self._initial_dir(),
            initialfile=self._tool.default_dxf_name(story),
            defaultextension=".dxf",
            filetypes=[("DXF drawing", "*.dxf"), ("All files", "*.*")])
        if not path:
            self._log("DXF export cancelled.", "warn")
            return

        self._disable(self._buttons["fdr_dxf"])
        tool = self._tool

        def job():
            return tool.export_dxf(path, story)

        def done(ok, payload, elapsed):
            self._post(self._export_done, "fdr_dxf", "DXF drawing",
                       ok, payload)

        self._worker.submit(job, done)

    def _ask_story(self, stories):
        """Modal single-story picker used when the results span several."""
        dialog = tk.Toplevel(self)
        dialog.title("Choose a story")
        dialog.configure(bg=SURFACE)
        dialog.transient(self)
        dialog.resizable(False, False)

        tk.Label(dialog, text="A DXF covers one story. Which one?",
                 font=self.f_field, fg=FG, bg=SURFACE).pack(padx=16,
                                                            pady=(16, 8))
        listbox = tk.Listbox(dialog, height=min(12, len(stories)), width=32,
                             bg=SURFACE2, fg=FG, selectbackground=BLUE,
                             selectforeground="white", relief="flat",
                             highlightthickness=1, highlightbackground=BORDER,
                             font=self.f_field, exportselection=False)
        for name in stories:
            listbox.insert("end", name)
        listbox.selection_set(0)
        listbox.pack(padx=16, fill="both", expand=True)

        chosen = {"value": None}

        def accept():
            selection = listbox.curselection()
            if selection:
                chosen["value"] = stories[selection[0]]
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=SURFACE)
        buttons.pack(fill="x", padx=16, pady=12)
        self._flat_button(buttons, "Cancel", FG_MUTED, dialog.destroy,
                          small=True).pack(side="right")
        self._flat_button(buttons, "Use this story", BLUE, accept,
                          small=True).pack(side="right", padx=(0, 8))
        listbox.bind("<Double-Button-1>", lambda _e: accept())

        dialog.grab_set()
        dialog.wait_window()
        return chosen["value"]

    # ── Design review report ─────────────────────────────────────────────────

    _REPORT_BUTTONS = ("report_map", "report_full", "report_quick")

    def _report_buttons(self, enabled):
        for key in self._REPORT_BUTTONS:
            (self._enable if enabled else self._disable)(self._buttons[key])

    def _report_banner(self, title):
        self._log("")
        self._log("-" * 60, "info")
        self._log(f"  {title}", "info")
        self._log("-" * 60, "info")

    def _report_edit_mapping(self):
        self._report_start("mapping")

    def _report_start(self, mode):
        """mode: 'mapping' (just edit the case mapping), 'full' or 'quick'."""
        model = self._sap_model()
        if model is None:
            return
        self._report_buttons(False)
        self._report_banner("Design Review Report -- reading the case list")

        def job():
            import report_extractor as rx
            import report_modifiers
            path = report_modifiers.model_file(model)
            saved, was_saved = rx.load_mapping(path) if path else (None, False)
            mapping = saved or rx.guess_mapping(model)
            return {"path": path, "mapping": mapping, "from_file": was_saved,
                    "cases": rx.describe_cases(model),
                    "stories": list(model.Story.GetNameList(0, [])[1])}

        def done(ok, payload, elapsed):
            self._post(self._report_mapping_step, mode, ok, payload)

        self._worker.submit(job, done)

    def _report_mapping_step(self, mode, ok, info):
        if not ok:
            self._report_failed("Could not read the case list", info)
            return
        import report_extractor as rx
        if mode == "mapping" or not info["from_file"]:
            self._log("  Confirm which load cases are EQ, SPEC, gust wind and static wind."
                      if not info["from_file"] else "  Editing the saved case mapping.", "info")
            dlg = ReportMappingDialog(self, info["cases"], info["stories"], info["mapping"])
            if dlg.result is None:
                self._log("  Cancelled.", "warn")
                self._report_buttons(True)
                return
            info["mapping"] = dlg.result
            if info["path"]:
                rx.save_mapping(info["path"], dlg.result)
                self._log(f"  Mapping saved: {rx.mapping_path(info['path'])}", "ok")
        if mode == "mapping":
            self._report_buttons(True)
            return
        mapping = info["mapping"]
        model = NS.get("SapModel")
        self._report_banner(f"Design Review Report -- checking the model ({mode})")

        def job():
            import report_pipeline
            return report_pipeline.prepare(
                model, mapping, mode, log=lambda t: self._post(self._log, "  " + t))

        def done(ok, payload, elapsed):
            self._post(self._report_plan_step, mode, mapping, ok, payload)

        self._worker.submit(job, done)

    def _report_plan_step(self, mode, mapping, ok, prepared):
        if not ok:
            self._report_failed("The check failed", prepared)
            return
        for w in prepared.warnings:
            self._log(f"  warning: {w}", "warn")
        if prepared.errors:
            for e in prepared.errors:
                self._log(f"  problem: {e}", "err")
            messagebox.showerror("Cannot generate the report",
                                 "\n\n".join(prepared.errors), parent=self)
            self._report_buttons(True)
            return
        text = "\n\n".join(prepared.plan)
        self._log("  Plan:", "info")
        for line in prepared.plan:
            self._log("   - " + line)
        if mode == "full" and not messagebox.askokcancel(
                "Generate the design review report",
                text + "\n\nProceed?", parent=self):
            self._log("  Cancelled before anything was changed.", "warn")
            self._report_buttons(True)
            return
        self._report_banner("Design Review Report -- generating")
        model = NS.get("SapModel")

        def job():
            import report_pipeline
            log = lambda t: self._post(self._log, "  " + t)
            report = report_pipeline.execute(model, prepared, mapping, log=log)
            path = report_pipeline.save_report(
                report, report_pipeline.output_path(prepared.model_path))
            return path, report

        def done(ok, payload, elapsed):
            self._post(self._report_done, ok, payload, elapsed)

        self._worker.submit(job, done)

    def _report_failed(self, what, exc):
        self._write("".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__)), "err")
        self._log(f"  {what}: {exc}", "err")
        messagebox.showerror(what, str(exc), parent=self)
        self._report_buttons(True)

    def _report_done(self, ok, payload, elapsed):
        self._report_buttons(True)
        if not ok:
            self._report_failed("Report generation failed", payload)
            return
        path, data = payload
        ex = data["extraction"]
        b = data["building"]
        self._log(f"  Report data saved: {path}  ({elapsed / 60:.1f} min)", "ok")
        self._log(f"  {data['metadata']['projectName']}: "
                  f"{b['heightAboveGround_m']:.1f} m above ground, typical story "
                  f"{ex['typicalStory']}, torsion points on {ex['torsionStory']}", "ok")
        for c in ex["checks"]:
            tag = {"pass": "ok", "warn": "warn", "fail": "err"}.get(c["status"])
            self._log(f"  [{c['status']}] {c['id']}: {c['detail']}", tag)
        for note in ex["notes"]:
            self._log(f"  note: {note}")
        self._log("  suggested statuses (engineer decides): " +
                  ", ".join(f"{k} = {v}" for k, v in ex["suggestions"].items()), "warn")
        self._log("  still to type in the report: " + "; ".join(ex["needsInput"]), "warn")
        self._open_report(path)

    def _open_report(self, path):
        import report_server
        dist = report_server.find_app_dist()
        if dist is None:
            self._log("  The report app is not built (etabs-report/dist missing). "
                      "Run 'npm run build' in the etabs-report folder, then use "
                      "Import JSON in the app with the file above.", "warn")
            messagebox.showwarning(
                "Report app not found",
                "The report data was saved, but the report app is not built.\n\n"
                "Run 'npm run build' in the etabs-report folder, or set the "
                "ETABS_REPORT_APP environment variable to its dist folder.\n\n" + path,
                parent=self)
            return
        if getattr(self, "_report_server", None) is None:
            self._report_server = report_server.ReportServer(dist)
        url = self._report_server.open_report(path)
        self._log(f"  Opened the report in your browser: {url}", "ok")
        self._log("  Check the values there, fix anything, then Export Excel. "
                  "Keep this window open while you work in the report.")

    def _export_done(self, button_key, what, ok, payload):
        self._enable(self._buttons[button_key])
        if ok:
            self._log(f"{what} saved: {payload}", "ok")
        else:
            self._write("".join(traceback.format_exception(
                type(payload), payload, payload.__traceback__)), "err")
            messagebox.showerror(f"{what} failed", str(payload), parent=self)


def _preflight():
    """Names of required packages that are not importable."""
    import importlib.util
    required = ("comtypes", "psutil", "pandas", "openpyxl")
    return [m for m in required if importlib.util.find_spec(m) is None]


def _show_error(title, text):
    """Put text in front of the user without assuming Tk is usable.

    Under pythonw.exe there is no console, so a failure that escapes this
    function is a failure nobody ever sees.
    """
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, text, parent=None)
        root.destroy()
        return
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text[:1500], title, 0x10)
    except Exception:
        pass


def _write_crash_log(text):
    """Append a timestamped report; returns the path, or "" if we could not."""
    try:
        import app_paths
        if not app_paths.ensure_dir(app_paths.logs_dir()):
            return ""
        path = app_paths.logs_dir() / f"crash_{time.strftime('%Y%m%d_%H%M%S')}.log"
        header = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                  f"python     : {sys.version}\n"
                  f"executable : {sys.executable}\n"
                  f"argv       : {sys.argv}\n"
                  f"cwd        : {os.getcwd()}\n\n")
        path.write_text(header + text, encoding="utf-8")
        return str(path)
    except Exception:
        return ""


def _fatal(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log_path = _write_crash_log(text)
    tail = f"\n\nFull report:\n{log_path}" if log_path else ""
    _show_error("ETABS Live Connector could not start",
                f"{exc_type.__name__}: {exc}{tail}")


def main():
    # Make sure `import fdr_tool` works no matter where the app is started from.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    missing = _preflight()
    if missing:
        _show_error(
            "Missing Python packages",
            "This app needs the following package(s), which are not installed:\n\n"
            f"    {', '.join(missing)}\n\n"
            "Install them by running:\n\n"
            f'    "{sys.executable}" -m pip install -r requirements.txt')
        return 1

    # --pid <N> lets a launcher attach this GUI to one specific ETABS instance
    # instead of guessing. PyLauncherPlugin doesn't pass it today, but the
    # flag is kept for anyone launching the GUI from a script that knows the
    # target PID.
    if "--pid" in sys.argv:
        try:
            NS["attach_pid"] = int(sys.argv[sys.argv.index("--pid") + 1])
        except (ValueError, IndexError):
            pass

    if sys.platform == "win32":
        # Without this, Windows bitmap-scales the whole window on displays
        # set above 100% scaling, which blurs the UI and inflates its size.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    App().mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        _fatal(*sys.exc_info())
        sys.exit(1)
