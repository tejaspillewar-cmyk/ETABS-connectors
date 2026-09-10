"""
ETABS Live Connector
A professional control panel for interacting with ETABS via its COM API.

Usage:
    python etabs_gui.py

Requirements:
    pip install comtypes
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, font as tkfont
import threading
import sys
import io
import traceback
import time

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = "#0d1117"
SURFACE   = "#161b22"
SURFACE2  = "#1c2128"
BORDER    = "#30363d"
BLUE      = "#2f81f7"
BLUE_H    = "#388bfd"
GREEN     = "#3fb950"
RED       = "#f85149"
AMBER     = "#d29922"
PURPLE    = "#bc8cff"
FG        = "#e6edf3"
FG_DIM    = "#8b949e"
FG_MUTED  = "#484f58"

# ── Shared kernel namespace ───────────────────────────────────────────────────
NS = {}

# ── Cell definitions ──────────────────────────────────────────────────────────
CELLS = [
    {
        "id": "cell_1",
        "icon": "rocket",
        "label": "01",
        "title": "Launch ETABS",
        "subtitle": "Connect to ETABS 23 via COM API",
        "detail": "Starts a new ETABS instance and establishes the COM connection. Must be run once before any other cell.",
        "code": (
            "import comtypes.client\n"
            "etabs_path = r'C:\\Program Files\\Computers and Structures\\ETABS 23\\ETABS.exe'\n"
            "helper = comtypes.client.CreateObject('ETABSv1.Helper')\n"
            "helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)\n"
            "print('Launching ETABS...')\n"
            "myETABSObject = helper.CreateObject(etabs_path)\n"
            "myETABSObject.ApplicationStart()\n"
            "myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)\n"
            "SapModel = myETABSObject.SapModel\n"
            "print('Connection established!')"
        ),
        "accent": BLUE,
    },
    {
        "id": "cell_2",
        "icon": "file",
        "label": "02",
        "title": "Active File",
        "subtitle": "Read the currently open .edb file path",
        "detail": "Queries ETABS for the file path of the model currently open in the GUI.",
        "code": (
            "filepath = SapModel.GetModelFilename()\n"
            "if filepath:\n"
            "    print(f'Active file: {filepath}')\n"
            "else:\n"
            "    print('No file open yet.')"
        ),
        "accent": PURPLE,
    },
    {
        "id": "cell_3",
        "icon": "grid",
        "label": "03",
        "title": "Model Inventory",
        "subtitle": "Count nodes and frame elements",
        "detail": "Reads the total number of joint nodes and frame members from the loaded ETABS model.",
        "code": (
            "num_points = SapModel.PointObj.Count()\n"
            "num_frames = SapModel.FrameObj.Count()\n"
            "print('=== MODEL INVENTORY ===')\n"
            "print(f'Nodes  (Points) : {num_points}')\n"
            "print(f'Frames (Members): {num_frames}')"
        ),
        "accent": GREEN,
    },
]


# ── Stream redirect ───────────────────────────────────────────────────────────
class Redirect(io.StringIO):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def write(self, t):
        if t:
            self._fn(t)

    def flush(self):
        pass


# ── Main window ───────────────────────────────────────────────────────────────
class App(tk.Tk):
    STATUS_IDLE       = ("Not connected", FG_DIM)
    STATUS_CONNECTING = ("Connecting...", AMBER)
    STATUS_OK         = ("ETABS connected", GREEN)
    STATUS_ERR        = ("Connection failed", RED)

    def __init__(self):
        super().__init__()
        self.title("ETABS Live Connector")
        self.geometry("820x700")
        self.minsize(700, 560)
        self.configure(bg=BG)
        self.resizable(True, True)

        self._btns   = []
        self._badges = []
        self._dots   = []
        self._pulse_job = None
        self._conn_state = "idle"   # idle | connecting | ok | error

        self._fonts()
        self._style()
        self._build()
        self._animate_dots(0)

    # ── Fonts ─────────────────────────────────────────────────────────────────
    def _fonts(self):
        self.f_app    = tkfont.Font(family="Segoe UI",     size=12, weight="bold")
        self.f_ver    = tkfont.Font(family="Segoe UI",     size=8)
        self.f_status = tkfont.Font(family="Segoe UI",     size=9)
        self.f_label  = tkfont.Font(family="Segoe UI",     size=22, weight="bold")
        self.f_title  = tkfont.Font(family="Segoe UI",     size=11, weight="bold")
        self.f_sub    = tkfont.Font(family="Segoe UI",     size=9)
        self.f_detail = tkfont.Font(family="Segoe UI",     size=8,  slant="italic")
        self.f_btn    = tkfont.Font(family="Segoe UI",     size=9,  weight="bold")
        self.f_badge  = tkfont.Font(family="Segoe UI",     size=7,  weight="bold")
        self.f_con    = tkfont.Font(family="Cascadia Code",size=8)
        self.f_conhdr = tkfont.Font(family="Segoe UI",     size=9,  weight="bold")

    # ── ttk style ─────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("default")
        s.configure("Dark.Vertical.TScrollbar",
                    troughcolor=SURFACE, background=BORDER,
                    arrowcolor=FG_DIM, bordercolor=BG, gripcount=0)
        s.map("Dark.Vertical.TScrollbar", background=[("active", FG_MUTED)])

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build(self):
        self._topbar()
        tk.Frame(self, bg=BLUE, height=2).pack(fill="x")
        self._main_area()
        self._console_area()

    # ── Top bar ───────────────────────────────────────────────────────────────
    def _topbar(self):
        bar = tk.Frame(self, bg=SURFACE2, height=54)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=SURFACE2)
        left.pack(side="left", padx=20, pady=0, fill="y")

        # Logo dot
        tk.Label(left, text="", width=2, bg=BLUE, fg=BLUE).pack(side="left", padx=(0, 10), pady=17, ipady=2)
        tk.Label(left, text="ETABS Live Connector", font=self.f_app, fg=FG, bg=SURFACE2).pack(side="left", pady=17)
        tk.Label(left, text="v1.0", font=self.f_ver, fg=FG_MUTED, bg=SURFACE2).pack(side="left", padx=6, pady=20)

        # Right status
        right = tk.Frame(bar, bg=SURFACE2)
        right.pack(side="right", padx=20, fill="y")

        self._status_dot = tk.Label(right, text="", width=2, font=self.f_ver,
                                     fg=FG_DIM, bg=FG_DIM)
        self._status_dot.pack(side="right", pady=18, padx=(4, 0), ipady=2)
        self._status_txt = tk.Label(right, text="Not connected", font=self.f_status,
                                     fg=FG_DIM, bg=SURFACE2)
        self._status_txt.pack(side="right", pady=18)

    # ── Main scrollable area ──────────────────────────────────────────────────
    def _main_area(self):
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True)

        cvs = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        sb  = ttk.Scrollbar(wrap, orient="vertical", command=cvs.yview,
                             style="Dark.Vertical.TScrollbar")
        cvs.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cvs.pack(side="left", fill="both", expand=True)

        self._sf = tk.Frame(cvs, bg=BG)
        self._sw = cvs.create_window((0, 0), window=self._sf, anchor="nw")

        self._sf.bind("<Configure>",
                      lambda e: cvs.configure(scrollregion=cvs.bbox("all")))
        cvs.bind("<Configure>",
                 lambda e: cvs.itemconfig(self._sw, width=e.width))
        cvs.bind_all("<MouseWheel>",
                     lambda e: cvs.yview_scroll(-1 * int(e.delta / 120), "units"))

        # Header row
        hdr = tk.Frame(self._sf, bg=BG)
        hdr.pack(fill="x", padx=24, pady=(20, 8))
        tk.Label(hdr, text="Control Panel", font=tkfont.Font(family="Segoe UI", size=15, weight="bold"),
                 fg=FG, bg=BG).pack(side="left")
        tk.Label(hdr, text="Run cells in sequence to interact with ETABS",
                 font=self.f_detail, fg=FG_DIM, bg=BG).pack(side="left", padx=12, pady=4)

        # Cards grid
        grid = tk.Frame(self._sf, bg=BG)
        grid.pack(fill="x", padx=24, pady=4)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=1)

        for col, cell in enumerate(CELLS):
            self._card(grid, col, cell)

    # ── Single card ───────────────────────────────────────────────────────────
    def _card(self, parent, col, cell):
        accent = cell["accent"]

        # Outer border frame
        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        border.grid(row=0, column=col, padx=(0 if col == 0 else 8, 0), sticky="nsew", pady=0)

        card = tk.Frame(border, bg=SURFACE)
        card.pack(fill="both", expand=True)

        # Top accent strip
        tk.Frame(card, bg=accent, height=3).pack(fill="x")

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=18, pady=16)

        # Big number label
        num_row = tk.Frame(body, bg=SURFACE)
        num_row.pack(fill="x")
        tk.Label(num_row, text=cell["label"], font=self.f_label,
                 fg=accent, bg=SURFACE).pack(side="left")

        # Status dot for this cell (tiny circle)
        dot = tk.Label(num_row, text="", width=2, bg=FG_MUTED, fg=FG_MUTED)
        dot.pack(side="right", padx=2, pady=12, ipady=2)
        self._dots.append(dot)

        # Title
        tk.Label(body, text=cell["title"], font=self.f_title,
                 fg=FG, bg=SURFACE, anchor="w").pack(fill="x", pady=(6, 0))

        # Subtitle
        tk.Label(body, text=cell["subtitle"], font=self.f_sub,
                 fg=BLUE if accent == BLUE else (PURPLE if accent == PURPLE else GREEN),
                 bg=SURFACE, anchor="w").pack(fill="x")

        # Divider
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=12)

        # Detail text
        tk.Label(body, text=cell["detail"], font=self.f_detail,
                 fg=FG_DIM, bg=SURFACE, anchor="w", justify="left",
                 wraplength=180).pack(fill="x")

        # Spacer
        tk.Frame(body, bg=SURFACE, height=12).pack()

        # Status badge
        badge = tk.Label(body, text="IDLE", font=self.f_badge,
                         fg=BG, bg=FG_MUTED, padx=6, pady=2)
        badge.pack(anchor="w", pady=(0, 10))
        self._badges.append(badge)

        # Run button
        idx = len(self._btns)
        btn = tk.Button(
            body,
            text="Run",
            font=self.f_btn,
            fg="white",
            bg=accent,
            activebackground=BLUE_H if accent == BLUE else accent,
            activeforeground="white",
            relief="flat",
            padx=0, pady=8,
            cursor="hand2",
            command=lambda i=idx: self._run(i),
        )
        btn.pack(fill="x", pady=(0, 2))
        self._btns.append(btn)

        self._hover(btn, accent)

    # ── Hover effect ──────────────────────────────────────────────────────────
    def _hover(self, widget, accent):
        darker = self._darken(accent)
        widget.bind("<Enter>", lambda e: widget.configure(bg=darker))
        widget.bind("<Leave>", lambda e: widget.configure(bg=accent))

    @staticmethod
    def _darken(hex_color):
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        r = max(0, r - 25)
        g = max(0, g - 25)
        b = max(0, b - 25)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── Console area ──────────────────────────────────────────────────────────
    def _console_area(self):
        frame = tk.Frame(self, bg=SURFACE2, padx=0, pady=0)
        frame.pack(fill="x", side="bottom")

        # Console header
        hdr = tk.Frame(frame, bg=SURFACE2)
        hdr.pack(fill="x", padx=16, pady=(10, 0))

        tk.Label(hdr, text="Output", font=self.f_conhdr, fg=FG_DIM, bg=SURFACE2).pack(side="left")

        clr = tk.Button(hdr, text="Clear", font=self.f_badge, fg=FG_DIM, bg=SURFACE2,
                        activebackground=BORDER, activeforeground=FG,
                        relief="flat", padx=6, pady=2, cursor="hand2",
                        command=self._clear)
        clr.pack(side="right")

        # Thin separator
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", pady=(6, 0))

        self._con = scrolledtext.ScrolledText(
            frame,
            font=self.f_con,
            bg="#010409",
            fg=FG,
            insertbackground=FG,
            relief="flat",
            bd=0,
            highlightthickness=0,
            state="disabled",
            height=10,
        )
        self._con.pack(fill="x", padx=0, pady=0)
        self._con.tag_config("err",  foreground=RED)
        self._con.tag_config("ok",   foreground=GREEN)
        self._con.tag_config("info", foreground=BLUE)
        self._con.tag_config("dim",  foreground=FG_DIM)

    # ── Animated status dots (topbar) ─────────────────────────────────────────
    def _animate_dots(self, tick):
        # Subtle pulse when connecting
        if self._conn_state == "connecting":
            col = AMBER if tick % 2 == 0 else FG_MUTED
            self._status_dot.configure(bg=col, fg=col)
        self._pulse_job = self.after(600, self._animate_dots, tick + 1)

    # ── Run a cell ────────────────────────────────────────────────────────────
    def _run(self, idx):
        cell = CELLS[idx]
        btn  = self._btns[idx]
        badge = self._badges[idx]
        dot   = self._dots[idx]
        accent = cell["accent"]

        # Mark running
        btn.configure(state="disabled", text="Running...", bg=AMBER)
        badge.configure(text="RUNNING", bg=AMBER, fg=BG)
        dot.configure(bg=AMBER, fg=AMBER)

        if idx == 0:
            self._conn_state = "connecting"
            self._status_txt.configure(text="Connecting...", fg=AMBER)
            self._status_dot.configure(bg=AMBER, fg=AMBER)

        self._log(f"\n{'─' * 48}\n  {cell['title']}\n{'─' * 48}\n", tag="info")

        def _worker():
            t0 = time.perf_counter()
            redir = Redirect(lambda t: self.after(0, self._write, t))
            old_o, old_e = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = redir
            ok = False
            try:
                exec(compile(cell["code"], f"<{cell['id']}>", "exec"), NS)
                ok = True
            except Exception:
                tb = traceback.format_exc()
                self.after(0, self._write, tb, "err")
            finally:
                sys.stdout, sys.stderr = old_o, old_e
            elapsed = time.perf_counter() - t0
            self.after(0, self._done, idx, ok, elapsed)

        threading.Thread(target=_worker, daemon=True).start()

    def _done(self, idx, ok, elapsed):
        cell   = CELLS[idx]
        btn    = self._btns[idx]
        badge  = self._badges[idx]
        dot    = self._dots[idx]
        accent = cell["accent"]

        btn.configure(state="normal", text="Run", bg=accent)
        self._hover(btn, accent)

        if ok:
            badge.configure(text="DONE", bg=GREEN, fg=BG)
            dot.configure(bg=GREEN, fg=GREEN)
            self._log(f"  Completed in {elapsed:.2f}s\n", tag="ok")
        else:
            badge.configure(text="ERROR", bg=RED, fg=FG)
            dot.configure(bg=RED, fg=RED)
            self._log(f"  Failed after {elapsed:.2f}s\n", tag="err")

        if idx == 0:
            if ok:
                self._conn_state = "ok"
                self._status_txt.configure(text="ETABS connected", fg=GREEN)
                self._status_dot.configure(bg=GREEN, fg=GREEN)
            else:
                self._conn_state = "error"
                self._status_txt.configure(text="Connection failed", fg=RED)
                self._status_dot.configure(bg=RED, fg=RED)

    # ── Console helpers ───────────────────────────────────────────────────────
    def _write(self, text, tag=None):
        self._con.configure(state="normal")
        self._con.insert("end", text, tag or "")
        self._con.see("end")
        self._con.configure(state="disabled")

    def _log(self, text, tag=None):
        self._write(text, tag)

    def _clear(self):
        self._con.configure(state="normal")
        self._con.delete("1.0", "end")
        self._con.configure(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
