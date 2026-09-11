"""
ETABS Live Connector -- control panel for the ETABS COM API.

Run with:
    pythonw etabs_gui.pyw       (no console window)
    python  etabs_gui.py        (console visible, useful for debugging)

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
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from tkinter import font as tkfont

# ── Palette ───────────────────────────────────────────────────────────────────
BG = "#0d1117"
SURFACE = "#161b22"
SURFACE2 = "#1c2128"
BORDER = "#30363d"
BLUE = "#2f81f7"
BLUE_H = "#388bfd"
GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"
PURPLE = "#bc8cff"
FG = "#e6edf3"
FG_DIM = "#8b949e"
FG_MUTED = "#484f58"

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_ETABS_PATH = r"C:\Program Files\Computers and Structures\ETABS 23\ETABS.exe"
DEFAULT_FY = "500"
DEFAULT_FCK = "30"
DEFAULT_TEXT_HEIGHT = "150"
DEFAULT_WIND_KEYWORDS = "gx, gwx, wx, wy, gy, gwy"
ALL_STORIES = "(All stories)"

WINDOW_SIZE = "1000x880"
WINDOW_MIN = (860, 640)
CONSOLE_LINES = 9
UI_POLL_MS = 40          # how often the Tk thread drains the worker's queue

# ── Shared namespace for the code cells ───────────────────────────────────────
NS = {}


# ═════════════════════════════════════════════════════════════════════════════
# Cell definitions
# ═════════════════════════════════════════════════════════════════════════════

LAUNCH_CODE = (
    "import comtypes.client\n"
    "helper = comtypes.client.CreateObject('ETABSv1.Helper')\n"
    "helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)\n"
    "print('Launching ETABS from:', etabs_path)\n"
    "myETABSObject = helper.CreateObject(etabs_path)\n"
    "myETABSObject.ApplicationStart()\n"
    "myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)\n"
    "SapModel = myETABSObject.SapModel\n"
    "print('Connected. Open your .edb model in the ETABS window.')"
)

ATTACH_CODE = (
    "import comtypes.client, psutil\n"
    "procs = [p.info for p in psutil.process_iter(['pid', 'name'])\n"
    "         if p.info['name'] and 'ETABS' in p.info['name'].upper()]\n"
    "if not procs:\n"
    "    raise RuntimeError('No running ETABS process found. Open ETABS first.')\n"
    "if len(procs) > 1:\n"
    "    listed = ', '.join(f\"PID {p['pid']} ({p['name']})\" for p in procs)\n"
    "    raise RuntimeError('Several ETABS instances are running: ' + listed)\n"
    "pid = procs[0]['pid']\n"
    "print('Attaching to ETABS PID', pid)\n"
    "helper = comtypes.client.CreateObject('ETABSv1.Helper')\n"
    "helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)\n"
    "myETABSObject = helper.GetObjectProcess('CSI.ETABS.API.ETABSObject', pid)\n"
    "if myETABSObject is None:\n"
    "    raise RuntimeError('Attach failed. Is the model fully loaded?')\n"
    "myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)\n"
    "SapModel = myETABSObject.SapModel\n"
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
    "ret = SapModel.PierLabel.GetNameList(0, [])\n"
    "print(f'Pier labels     : {ret[1] if ret and ret[0] == 0 else 0}')"
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
        "actions": [("Launch", "launch"), ("Attach", "attach")],
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
]

CODE_BY_KEY = {
    "launch": LAUNCH_CODE,
    "attach": ATTACH_CODE,
    "file": FILE_CODE,
    "inventory": INVENTORY_CODE,
}


# ═════════════════════════════════════════════════════════════════════════════
# ETABS worker -- one thread, one COM apartment, one job at a time
# ═════════════════════════════════════════════════════════════════════════════

class EtabsWorker:
    """Serialises every ETABS call onto a single CoInitialize'd thread."""

    def __init__(self):
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="etabs-worker")
        self._thread.start()

    def submit(self, fn, on_done=None):
        """Queue fn() for the worker thread.

        on_done(ok, result_or_exception, elapsed_seconds) is called back on
        the worker thread; marshal it to Tk with .after().
        """
        self._jobs.put((fn, on_done))

    @property
    def busy(self) -> bool:
        return not self._jobs.empty()

    def _loop(self):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass  # non-Windows, or comtypes missing -- jobs will report it

        while True:
            fn, on_done = self._jobs.get()
            started = time.perf_counter()
            try:
                result = fn()
                ok, payload = True, result
            except Exception as exc:
                ok, payload = False, exc
            elapsed = time.perf_counter() - started
            if on_done is not None:
                try:
                    on_done(ok, payload, elapsed)
                except Exception:
                    traceback.print_exc()
            self._jobs.task_done()


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

        self._fonts()
        self._style()
        self._build()
        self._pump()

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
        # Bottom-up: console lowest, then the action bar, then the scroll area
        # takes whatever is left. Run and the two Save buttons therefore stay
        # on screen no matter how far the panel above is scrolled.
        self._console_area()
        self._action_bar()
        self._main_area()

    # ── Thread-safe hand-off to the Tk thread ────────────────────────────────
    #
    # Tk widgets, and .after() itself, may only be touched from the thread
    # running mainloop. The worker therefore queues work here and the Tk
    # thread drains the queue on a timer.

    def _post(self, fn, *args):
        self._ui_queue.put((fn, args))

    def _pump(self):
        try:
            while True:
                fn, args = self._ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.after(UI_POLL_MS, self._pump)

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

    def _main_area(self):
        wrap = tk.Frame(self, bg=BG)
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
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * int(e.delta / 120), "units"))

        self._cards_section()
        self._fdr_section()

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
                             "Connect first, then use the FDR panel below")
        grid = tk.Frame(self._scroll, bg=BG)
        grid.pack(fill="x", padx=24)
        for col in range(len(CELLS)):
            grid.columnconfigure(col, weight=1)
        for col, cell in enumerate(CELLS):
            self._card(grid, col, cell)

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

        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x")
        for i, (text, key) in enumerate(cell["actions"]):
            btn = self._flat_button(
                row, text, accent,
                lambda k=key, c=cell: self._run_cell(c, k))
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

    def _fdr_section(self):
        self._section_header(
            self._scroll, "FDR  --  Flexural Design Review",
            "Required Pt%, 0.4 fck and 0.2 fck checks, Excel and DXF output")

        border = tk.Frame(self._scroll, bg=BORDER, padx=1, pady=1)
        border.pack(fill="x", padx=24, pady=(0, 24))
        panel = tk.Frame(border, bg=SURFACE)
        panel.pack(fill="both", expand=True)
        tk.Frame(panel, bg=AMBER, height=3).pack(fill="x")

        body = tk.Frame(panel, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        # -- Row 1: material and drawing inputs ---------------------------
        inputs = tk.Frame(body, bg=SURFACE)
        inputs.pack(fill="x")

        self.var_fy = tk.StringVar(value=DEFAULT_FY)
        self.var_fck = tk.StringVar(value=DEFAULT_FCK)
        self.var_text_h = tk.StringVar(value=DEFAULT_TEXT_HEIGHT)
        self.var_wind = tk.StringVar(value=DEFAULT_WIND_KEYWORDS)
        self.var_story = tk.StringVar(value=ALL_STORIES)

        self._field(inputs, "fy (MPa)", self.var_fy, width=7).pack(side="left")
        self._field(inputs, "fck (MPa)", self.var_fck,
                    width=7).pack(side="left", padx=(14, 0))
        self._field(inputs, "DXF text height (mm)", self.var_text_h,
                    width=8).pack(side="left", padx=(14, 0))
        self._field(inputs, "Wind keywords (comma separated)", self.var_wind,
                    width=34).pack(side="left", padx=(14, 0), fill="x",
                                   expand=True)

        # -- Row 2: story + refresh ---------------------------------------
        row2 = tk.Frame(body, bg=SURFACE)
        row2.pack(fill="x", pady=(12, 0))

        story_box = tk.Frame(row2, bg=SURFACE)
        story_box.pack(side="left")
        tk.Label(story_box, text="Story", font=self.f_badge, fg=FG_DIM,
                 bg=SURFACE, anchor="w").pack(fill="x")
        self.cmb_story = ttk.Combobox(
            story_box, textvariable=self.var_story, values=[ALL_STORIES],
            state="readonly", width=28, style="Dark.TCombobox",
            font=self.f_field)
        self.cmb_story.pack()

        btn_refresh = self._flat_button(
            row2, "Read stories + combos", BLUE, self._load_model_lists,
            small=True)
        btn_refresh.pack(side="left", padx=(14, 0), pady=(14, 0))
        self._buttons["refresh"] = btn_refresh

        tk.Label(row2,
                 text="A DXF covers one story. Leave on All stories for a "
                      "whole-building Excel report.",
                 font=self.f_detail, fg=FG_MUTED, bg=SURFACE,
                 wraplength=340, justify="left").pack(side="left", padx=(14, 0),
                                                      pady=(14, 0))

        # -- Row 3: combination picker ------------------------------------
        combo_wrap = tk.Frame(body, bg=SURFACE)
        combo_wrap.pack(fill="x", pady=(14, 0))

        head = tk.Frame(combo_wrap, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="Load combinations", font=self.f_badge, fg=FG_DIM,
                 bg=SURFACE).pack(side="left")
        self.lbl_combo_count = tk.Label(head, text="none loaded",
                                        font=self.f_detail, fg=FG_MUTED,
                                        bg=SURFACE)
        self.lbl_combo_count.pack(side="left", padx=8)
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

        list_frame = tk.Frame(combo_wrap, bg=BORDER, padx=1, pady=1)
        list_frame.pack(fill="x", pady=(4, 0))
        self.lst_combos = tk.Listbox(
            list_frame, selectmode="extended", height=5, bg=SURFACE2, fg=FG,
            selectbackground=BLUE, selectforeground="white", relief="flat",
            highlightthickness=0, font=self.f_field,
            exportselection=False)
        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self.lst_combos.yview,
                           style="Dark.Vertical.TScrollbar")
        self.lst_combos.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lst_combos.pack(side="left", fill="both", expand=True)

        tk.Label(combo_wrap,
                 text="Nothing selected means every combination is used.",
                 font=self.f_detail, fg=FG_MUTED,
                 bg=SURFACE).pack(anchor="w", pady=(4, 0))

    # ── Action bar (always visible, sits just above the console) ─────────────

    def _action_bar(self):
        bar = tk.Frame(self, bg=SURFACE2)
        bar.pack(fill="x", side="bottom")
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

    # ── Console ──────────────────────────────────────────────────────────────

    def _console_area(self):
        frame = tk.Frame(self, bg=SURFACE2)
        frame.pack(fill="x", side="bottom")

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
            frame, font=self.f_con, bg="#010409", fg=FG, insertbackground=FG,
            relief="flat", bd=0, highlightthickness=0, state="disabled",
            height=CONSOLE_LINES, wrap="none")
        self._con.pack(fill="x")
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

    # ── Cell execution ───────────────────────────────────────────────────────

    def _run_cell(self, cell, key):
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

        NS["etabs_path"] = DEFAULT_ETABS_PATH

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
        self.lbl_summary.configure(text="Running...", fg=AMBER)

        self._log("")
        self._log("=" * 60, "info")
        self._log("  FDR ANALYSIS", "info")
        self._log("=" * 60, "info")

        def job():
            from fdr_tool import FDRTool
            tool = FDRTool(model, config,
                           log=lambda m: self._post(self._write, str(m) + "\n"))
            tool.extract_pier_forces(combos)
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

    def _export_done(self, button_key, what, ok, payload):
        self._enable(self._buttons[button_key])
        if ok:
            self._log(f"{what} saved: {payload}", "ok")
        else:
            self._write("".join(traceback.format_exception(
                type(payload), payload, payload.__traceback__)), "err")
            messagebox.showerror(f"{what} failed", str(payload), parent=self)


if __name__ == "__main__":
    # Make sure `import fdr_tool` works no matter where the app is started from.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    App().mainloop()
