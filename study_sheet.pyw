import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont
import platform
import math
import os

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ---------------------------------------------------------------------------
# Excel formulas for every computed cell, keyed by the same cell_id used in
# the GUI (self.cells). This is the single source of truth shared between the
# live GUI preview (recalculate()) and the exported workbook (export_to_excel()),
# and matches the reference "ETABS STUDY ... .xlsx" template cell-for-cell.
# ---------------------------------------------------------------------------
FORMULAS = {
    "D7": "=D8+D10",
    "D11": "=D6/D5",
    "D12": "=D8/D5",
    "D18": "=D17/D15",
    "H10": "=0.0672*POWER(D8,0.75)",
    "G16": "=1/H10",
    "G20": "=G13*G14*G16/(2*G15)",
    "G22": "=MAX(G20:G21)*H9",
    "D41": "=D40/D16",
    "D44": "=D40+D42+D43",
    "D45": "=D44/D16",
    "H36": "=(C35-C36)/C36",
    "H37": "=(C36-C37)/C37",
    "V52": "=MAX(V48/W48,V50/W50)",
    "X52": "=MAX(X48/Y48,X49/Y49)",
    "AF48": '=IF(OR(G35="Torsion",G36="Torsion"),"NO","OK")',
    "AF49": '=IF(ABS(H36)>=0.1,"OK","NO")',
    "AF50": '=IF(AND(H41<=I41,H42<=I42,H43<=I43,H44<=I44),"OK","NO")',
    "AF52": '=IF(MAX(V52,X52)>1.2,"EXCEEDS","OK")',
}
for _r in range(49, 54):
    FORMULAS[f"H{_r}"] = f"=G{_r}/$G$54"
    FORMULAS[f"I{_r}"] = f"=G{_r}/25"
    FORMULAS[f"J{_r}"] = f"=I{_r}/(D16*10.75)"
FORMULAS["G54"] = "=SUM(G49:G53)"
FORMULAS["H54"] = "=G54/$G$54"
FORMULAS["I54"] = "=SUM(I49:I52)"
FORMULAS["J54"] = "=I54/(D16*10.75)"
for _r in range(48, 52):
    FORMULAS[f"U{_r}"] = (
        f"=MAX(SQRT(M{_r}^2+N{_r}^2),SQRT(O{_r}^2+P{_r}^2),"
        f"SQRT(Q{_r}^2+R{_r}^2),SQRT(S{_r}^2+T{_r}^2))"
    )
FORMULAS["V48"] = "=MAX(U48:U49)"
FORMULAS["W48"] = "=(U48+U49)/2"
FORMULAS["V49"] = "=MAX(U48:U49)"
FORMULAS["W49"] = "=(U48+U49)/2"
FORMULAS["X48"] = "=MAX(U48,U50)"
FORMULAS["Y48"] = "=(U48+U50)/2"
FORMULAS["X50"] = "=MAX(U48,U50)"
FORMULAS["Y50"] = "=(U48+U50)/2"
FORMULAS["V50"] = "=MAX(U50:U51)"
FORMULAS["W50"] = "=(U50+U51)/2"
FORMULAS["V51"] = "=MAX(U50:U51)"
FORMULAS["W51"] = "=(U50+U51)/2"
FORMULAS["X49"] = "=MAX(U49,U51)"
FORMULAS["Y49"] = "=(U49+U51)/2"
FORMULAS["X51"] = "=MAX(U49,U51)"
FORMULAS["Y51"] = "=(U49+U51)/2"

# Cells that keep an orange "input-like" fill in the reference even though
# they hold a formula (a quirk of the original template, replicated for
# visual fidelity).
ORANGE_FORMULA_CELLS = {"H10", "G16", "D41"}

MODE_ROWS = [35, 36, 37]
TORSION_ROWS = [48, 49, 50, 51]
TORSION_IN_COLS = ["M", "N", "O", "P", "Q", "R", "S", "T"]
SMI_ROWS = [("Column", 49), ("Beam", 50), ("Wall", 51), ("Floor", 52), ("Raft", 53)]


class StudySheetApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Study Sheet GUI")
        self.geometry("1400x850")
        self.configure(background="#ffffff")

        self.current_font_size = 8

        self.std_font = tkfont.Font(family="Arial", size=self.current_font_size)
        self.bold_font = tkfont.Font(family="Arial", size=self.current_font_size, weight="bold")
        self.small_font = tkfont.Font(family="Arial", size=max(6, self.current_font_size - 2))

        self.canvas = tk.Canvas(self, borderwidth=0, background="#ffffff", highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        self.vsb.pack(side="right", fill="y")
        self.hsb.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.main_frame = tk.Frame(self.canvas, background="#ffffff")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.main_frame, anchor="nw")

        self.main_frame.bind("<Configure>", self.on_frame_configure)
        self.canvas.bind("<Configure>", self.on_canvas_configure)

        if platform.system() == "Windows" or platform.system() == "Darwin":
            self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
            self.canvas.bind_all("<Shift-MouseWheel>", self._on_shift_mousewheel)
            self.canvas.bind_all("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        else:
            self.canvas.bind_all("<Button-4>", self._on_mousewheel)
            self.canvas.bind_all("<Button-5>", self._on_mousewheel)
            self.canvas.bind_all("<Control-Button-4>", self._on_ctrl_mousewheel)
            self.canvas.bind_all("<Control-Button-5>", self._on_ctrl_mousewheel)

        self.cells = {}
        self.wind_rows = []
        self.floor_plan_path = None
        self.floor_plan_photo = None

        self.build_ui()
        self.recalculate(None)

    # ---------------- scrolling / zoom plumbing (unchanged behaviour) ----------------
    def _on_mousewheel(self, event):
        if event.state & 0x0004:
            return
        if event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5 or event.delta < 0:
            self.canvas.yview_scroll(1, "units")

    def _on_shift_mousewheel(self, event):
        if event.num == 4 or event.delta > 0:
            self.canvas.xview_scroll(-1, "units")
        elif event.num == 5 or event.delta < 0:
            self.canvas.xview_scroll(1, "units")

    def _on_ctrl_mousewheel(self, event):
        if event.num == 4 or event.delta > 0:
            self.current_font_size += 1
        elif event.num == 5 or event.delta < 0:
            self.current_font_size = max(5, self.current_font_size - 1)

        self.std_font.configure(size=self.current_font_size)
        self.bold_font.configure(size=self.current_font_size)
        self.small_font.configure(size=max(5, self.current_font_size - 1))
        self.after(50, lambda: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

    def on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def on_canvas_configure(self, event):
        min_width = self.main_frame.winfo_reqwidth()
        if event.width > min_width:
            self.canvas.itemconfig(self.canvas_window, width=event.width)
        else:
            self.canvas.itemconfig(self.canvas_window, width=min_width)

    # ---------------- cell helpers ----------------
    def _widget_text(self, w):
        if isinstance(w, (tk.Entry, ttk.Combobox)):
            return w.get().strip()
        return w.cget("text").strip()

    def _get_text(self, cell_id):
        if cell_id not in self.cells:
            return ""
        return self._widget_text(self.cells[cell_id])

    def get_val(self, cell_id):
        if cell_id not in self.cells:
            return None
        val = self._widget_text(self.cells[cell_id]).replace("%", "")
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            return None

    def set_val(self, cell_id, value, fmt="{:.4f}"):
        if cell_id not in self.cells:
            return
        widget = self.cells[cell_id]
        if value is None or (isinstance(value, float) and math.isnan(value)):
            str_val = ""
        elif isinstance(value, (int, float)):
            str_val = fmt.format(value)
        else:
            str_val = str(value)

        if isinstance(widget, ttk.Combobox):
            if widget.get() != str_val:
                widget.set(str_val)
        elif isinstance(widget, tk.Entry):
            state = widget.cget("state")
            widget.config(state="normal")
            if widget.get() != str_val:
                widget.delete(0, tk.END)
                widget.insert(0, str_val)
            widget.config(state=state)
        else:
            widget.config(text=str_val)

    def create_cell(self, parent, text="", bg="#ffffff", is_input=False, width=12, cell_id=None):
        if is_input:
            widget = tk.Entry(parent, width=width, bg=bg, justify="center", font=self.std_font, relief="solid", borderwidth=1)
            widget.insert(0, text)
            widget.bind("<KeyRelease>", self.recalculate)
        else:
            widget = tk.Label(parent, text=text, width=width, bg=bg, font=self.std_font, relief="solid", borderwidth=1, anchor="c")
        if cell_id:
            self.cells[cell_id] = widget
        return widget

    def create_combo(self, parent, values, bg="#ffffff", width=10, cell_id=None):
        widget = ttk.Combobox(parent, values=values, width=width, font=self.std_font, state="normal")
        widget.bind("<<ComboboxSelected>>", self.recalculate)
        widget.bind("<KeyRelease>", self.recalculate)
        if cell_id:
            self.cells[cell_id] = widget
        return widget

    def create_label(self, parent, text, bg="#ffffff", font=None, anchor="w", width=None, borderwidth=1, cell_id=None):
        if font is None:
            font = self.std_font
        l = tk.Label(parent, text=text, bg=bg, font=font, anchor=anchor, relief="solid", borderwidth=borderwidth, width=width)
        if cell_id:
            self.cells[cell_id] = l
        return l

    def _add(self, *args):
        if any(a is None for a in args):
            return None
        return sum(args)

    def _div(self, a, b):
        if a is None or b is None or b == 0:
            return None
        return a / b

    def _mul(self, *args):
        if any(a is None for a in args):
            return None
        res = 1
        for a in args:
            res *= a
        return res

    # ---------------- recalculation (mirrors FORMULAS above) ----------------
    def recalculate(self, event):
        self.set_val("D7", self._add(self.get_val("D8"), self.get_val("D10")), "{:.2f}")
        self.set_val("D11", self._div(self.get_val("D6"), self.get_val("D5")), "{:.2f}")
        self.set_val("D12", self._div(self.get_val("D8"), self.get_val("D5")), "{:.2f}")

        d15, d17 = self.get_val("D15"), self.get_val("D17")
        if d15 and d17 is not None:
            self.set_val("D18", (d17 / d15) * 100, "{:.2f}%")
        else:
            self.set_val("D18", None)

        d8 = self.get_val("D8")
        h10 = 0.0672 * (d8 ** 0.75) if d8 else None
        self.set_val("H10", h10, "{:.2f}")
        self.set_val("G16", self._div(1, h10), "{:.4f}")

        g13, g14, g15, g16 = self.get_val("G13"), self.get_val("G14"), self.get_val("G15"), self.get_val("G16")
        g20 = self._div(self._mul(g13, g14, g16), 2 * g15) if g15 else None
        self.set_val("G20", g20, "{:.4f}")

        g21, h9 = self.get_val("G21"), self.get_val("H9")
        if g20 is not None and g21 is not None and h9 is not None:
            self.set_val("G22", max(g20, g21) * h9, "{:.0f}")
        else:
            self.set_val("G22", None)

        # Gravity
        d16 = self.get_val("D16")
        self.set_val("D41", self._div(self.get_val("D40"), d16), "{:.2f}")
        d44 = self._add(self.get_val("D40"), self.get_val("D42"), self.get_val("D43"))
        self.set_val("D44", d44, "{:.0f}")
        self.set_val("D45", self._div(d44, d16), "{:.2f}")

        # Modal period-difference callouts + dominant-direction highlight
        c35, c36, c37 = self.get_val("C35"), self.get_val("C36"), self.get_val("C37")
        self.set_val("H36", self._div(self._add(c35, -c36) if c36 is not None else None, c36), "{:.1%}")
        self.set_val("H37", self._div(self._add(c36, -c37) if c37 is not None else None, c37), "{:.1%}")
        for r in MODE_ROWS:
            vals = {col: self.get_val(f"{col}{r}") for col in ("D", "E", "F")}
            present = {k: v for k, v in vals.items() if v is not None}
            dominant = max(present, key=lambda k: abs(present[k])) if present else None
            for col in ("D", "E", "F"):
                cid = f"{col}{r}"
                if cid in self.cells and isinstance(self.cells[cid], tk.Entry):
                    self.cells[cid].config(bg="#FBD4B4" if col == dominant else "#ffffff")

        # Structure Mass Index
        g_vals = [self.get_val(f"G{r}") for _, r in SMI_ROWS]
        g54 = self._add(*g_vals) if all(v is not None for v in g_vals) else None
        self.set_val("G54", g54, "{:.0f}")
        for _, r in SMI_ROWS:
            g = self.get_val(f"G{r}")
            self.set_val(f"H{r}", (g / g54) * 100 if (g is not None and g54) else None, "{:.0f}%")
            i_val = g / 25 if g is not None else None
            self.set_val(f"I{r}", i_val, "{:.0f}")
            self.set_val(f"J{r}", self._div(i_val, (d16 * 10.75) if d16 else None), "{:.4f}")
        self.set_val("H54", 100 if g54 is not None else None, "{:.0f}%")
        i_vals = [self.get_val(f"I{r}") for _, r in SMI_ROWS if r != 53]
        i54 = self._add(*i_vals) if all(v is not None for v in i_vals) else None
        self.set_val("I54", i54, "{:.0f}")
        self.set_val("J54", self._div(i54, (d16 * 10.75) if d16 else None), "{:.4f}")

        # Torsional irregularity — every downstream value is kept in full
        # Python-float precision (u/vw/xy dicts below) and only rounded at the
        # moment it's written to a widget; re-reading the rounded display text
        # for further math (as an earlier version of this did) silently drifts
        # the final ratio away from the reference's live-formula result.
        u = {}
        for r in TORSION_ROWS:
            vals = [self.get_val(f"{c}{r}") for c in TORSION_IN_COLS]
            if all(v is not None for v in vals):
                m, n, o, p, q, rv, s, t = vals
                u[r] = max(math.sqrt(m * m + n * n), math.sqrt(o * o + p * p), math.sqrt(q * q + rv * rv), math.sqrt(s * s + t * t))
            else:
                u[r] = None
            self.set_val(f"U{r}", u[r], "{:.2f}")

        pairs_y = [(48, 49), (50, 51)]
        pairs_x = [(48, 50), (49, 51)]
        vw = {}
        for a, b in pairs_y:
            if u[a] is not None and u[b] is not None:
                mx, avg = max(u[a], u[b]), (u[a] + u[b]) / 2
                vw[a] = vw[b] = (mx, avg)
                self.set_val(f"V{a}", mx, "{:.2f}")
                self.set_val(f"W{a}", avg, "{:.2f}")
                self.set_val(f"V{b}", mx, "{:.2f}")
                self.set_val(f"W{b}", avg, "{:.2f}")
            else:
                vw[a] = vw[b] = (None, None)
                for cid in (f"V{a}", f"W{a}", f"V{b}", f"W{b}"):
                    self.set_val(cid, None)
        xy = {}
        for a, b in pairs_x:
            if u[a] is not None and u[b] is not None:
                mx, avg = max(u[a], u[b]), (u[a] + u[b]) / 2
                xy[a] = xy[b] = (mx, avg)
                self.set_val(f"X{a}", mx, "{:.2f}")
                self.set_val(f"Y{a}", avg, "{:.2f}")
                self.set_val(f"X{b}", mx, "{:.2f}")
                self.set_val(f"Y{b}", avg, "{:.2f}")
            else:
                xy[a] = xy[b] = (None, None)
                for cid in (f"X{a}", f"Y{a}", f"X{b}", f"Y{b}"):
                    self.set_val(cid, None)

        v48, w48 = vw[48]
        v50, w50 = vw[50]
        x48, y48 = xy[48]
        x49, y49 = xy[49]
        v52 = max(v48 / w48 if w48 else 0, v50 / w50 if w50 else 0) if (w48 or w50) else None
        x52 = max(x48 / y48 if y48 else 0, x49 / y49 if y49 else 0) if (y48 or y49) else None
        self.set_val("V52", v52, "{:.3f}")
        self.set_val("X52", x52, "{:.3f}")

        ratio = max(v for v in (v52, x52) if v is not None) if (v52 is not None or x52 is not None) else None
        if hasattr(self, "torsion_note"):
            if ratio is None:
                self.torsion_note.config(text="*Enter torsional irregularity point data above.")
            elif ratio > 1.2:
                self.torsion_note.config(text="*There IS torsional irregularity as the ratio exceeds 1.2.")
            else:
                self.torsion_note.config(text="*There is no torsional irregularity as the ratio is less than 1.2.")

        # Wind lateral-displacement-per-floor table
        self.recalc_wind_table()

        # Checks (auto-derived)
        self.recalc_checks(c35, c36, c37, ratio)

    def recalc_checks(self, c35, c36, c37, torsion_ratio):
        nat1, nat2 = self._get_text("G35"), self._get_text("G36")
        check1_fail = "Torsion" in (nat1, nat2)
        self._set_check("CK1", "NO" if check1_fail else "OK")

        h36 = self.get_val("H36")
        h36_pct = None
        if c35 is not None and c36:
            h36_pct = (c35 - c36) / c36
        check2_ok = h36_pct is not None and abs(h36_pct) >= 0.10
        self._set_check("CK2", "OK" if check2_ok else "NO")

        drift_ok = True
        any_drift = False
        for lc_row in ("H41", "H42", "H43", "H44"):
            limit_row = "I" + lc_row[1:]
            d, lim = self.get_val(lc_row), self.get_val(limit_row)
            if d is not None and lim is not None:
                any_drift = True
                if d > lim:
                    drift_ok = False
        self._set_check("CK3", ("OK" if drift_ok else "NO") if any_drift else "")

        wind_statuses = [row["status"].cget("text") for row in self.wind_rows if row["status"].cget("text")]
        if wind_statuses:
            self._set_check("CK4", "OK" if all(s == "OK" for s in wind_statuses) else "NO")
        else:
            self._set_check("CK4", "")

        if torsion_ratio is None:
            self._set_check("CK5", "")
        else:
            self._set_check("CK5", "EXCEEDS" if torsion_ratio > 1.2 else "OK")

    def _set_check(self, cid, status):
        if cid not in self.cells:
            return
        w = self.cells[cid]
        w.config(text=status)
        if status == "OK":
            w.config(bg="#E2EFDA", fg="#006100")
        elif status in ("NO", "EXCEEDS"):
            w.config(bg="#F4CCCC", fg="#FF0000")
        else:
            w.config(bg="#ffffff", fg="#000000")

    # ---------------- wind displacement table ----------------
    def add_wind_row(self, story="", ht=""):
        parent = self.wind_table_body
        r = len(self.wind_rows) + 1
        story_e = tk.Entry(parent, width=8, font=self.std_font, relief="solid", borderwidth=1, justify="center")
        story_e.insert(0, story)
        ht_e = tk.Entry(parent, width=7, font=self.std_font, relief="solid", borderwidth=1, justify="center", bg="#FBD4B4")
        ht_e.insert(0, ht)
        elev_l = tk.Label(parent, width=8, font=self.std_font, relief="solid", borderwidth=1, bg="#ffffff")
        ux_e = tk.Entry(parent, width=7, font=self.std_font, relief="solid", borderwidth=1, justify="center", bg="#FBD4B4")
        uy_e = tk.Entry(parent, width=7, font=self.std_font, relief="solid", borderwidth=1, justify="center", bg="#FBD4B4")
        h500_l = tk.Label(parent, width=8, font=self.std_font, relief="solid", borderwidth=1, bg="#ffffff")
        status_l = tk.Label(parent, width=8, font=self.std_font, relief="solid", borderwidth=1, bg="#ffffff")

        for j, w in enumerate((story_e, ht_e, elev_l, ux_e, uy_e, h500_l, status_l)):
            w.grid(row=r, column=j, sticky="we", padx=1, pady=1)
        for w in (story_e, ht_e, ux_e, uy_e):
            w.bind("<KeyRelease>", self.recalculate)

        self.wind_rows.append({"story": story_e, "ht": ht_e, "elev": elev_l, "ux": ux_e, "uy": uy_e, "h500": h500_l, "status": status_l})
        self.recalculate(None)

    def remove_wind_row(self):
        if not self.wind_rows:
            return
        row = self.wind_rows.pop()
        for w in row.values():
            w.destroy()
        self.recalculate(None)

    def recalc_wind_table(self):
        n = len(self.wind_rows)
        heights = []
        for row in self.wind_rows:
            try:
                heights.append(float(row["ht"].get().strip()))
            except ValueError:
                heights.append(None)

        # elevation[i] = sum of heights from row i to the bottom-most row
        suffix = [None] * n
        running = 0.0
        ok_all = True
        for i in range(n - 1, -1, -1):
            if heights[i] is None:
                suffix[i] = None
                ok_all = False
            else:
                running += heights[i]
                suffix[i] = running

        for i, row in enumerate(self.wind_rows):
            elev = suffix[i]
            row["elev"].config(text="" if elev is None else f"{elev:.2f}")
            h500 = elev * 1000 / 500 if elev is not None else None
            row["h500"].config(text="" if h500 is None else f"{h500:.0f}")
            try:
                ux = float(row["ux"].get().strip())
            except ValueError:
                ux = None
            try:
                uy = float(row["uy"].get().strip())
            except ValueError:
                uy = None
            if h500 is None or ux is None or uy is None:
                row["status"].config(text="", bg="#ffffff")
            elif ux <= h500 and uy <= h500:
                row["status"].config(text="OK", bg="#E2EFDA", fg="#006100")
            else:
                row["status"].config(text="NO", bg="#F4CCCC", fg="#FF0000")

    # ---------------- floor plan image ----------------
    def insert_floor_plan_image(self):
        if not HAS_PIL:
            messagebox.showerror("Error", "Please install Pillow to insert images:\n\npip install Pillow")
            return
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.gif")])
        if not path:
            return
        try:
            img = Image.open(path)
            img.thumbnail((340, 220))
            self.floor_plan_photo = ImageTk.PhotoImage(img)
            self.floor_plan_label.config(image=self.floor_plan_photo, text="")
            self.floor_plan_path = path
        except Exception as exc:
            messagebox.showerror("Error", f"Could not load image:\n{exc}")

    # ---------------- Excel export ----------------
    def export_to_excel(self):
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter
            from openpyxl.formatting.rule import FormulaRule
            from openpyxl.chart import ScatterChart, Series, Reference
        except ImportError:
            messagebox.showerror("Error", "Please install openpyxl to export to Excel:\n\npip install openpyxl")
            return

        file_path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
        if not file_path:
            return

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Study Sheet"
        ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0

        hair = Side(style="hair")
        border_thin = Border(left=hair, right=hair, top=hair, bottom=hair)

        FILL_ORANGE = PatternFill("solid", fgColor="FBD4B4")
        FILL_WHITE = PatternFill("solid", fgColor="FFFFFF")
        FILL_GREEN = PatternFill("solid", fgColor="E2EFDA")
        FILL_RED = PatternFill("solid", fgColor="F4CCCC")
        FILL_YELLOW = PatternFill("solid", fgColor="FEF2CB")
        FILL_BLUE = PatternFill("solid", fgColor="B4C6E7")
        FILL_GOLD = PatternFill("solid", fgColor="FFE598")
        FILL_GREY = PatternFill("solid", fgColor="DADADA")

        hdr_font = Font(name="Calibri", size=10, bold=True)
        std_font = Font(name="Calibri", size=10)
        small_font = Font(name="Calibri", size=8)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_al = Alignment(horizontal="left", vertical="center", wrap_text=True)
        right_al = Alignment(horizontal="right", vertical="center", wrap_text=True)

        def w(addr, val, font=std_font, fill=None, align=center, border=border_thin):
            cell = ws[addr]
            cell.value = val
            cell.font = font
            if fill is not None:
                cell.fill = fill
            cell.alignment = align
            cell.border = border
            return cell

        def w_field(addr, font=std_font, align=center, numfmt=None):
            """Write a schema-driven cell: formula if defined, else the raw GUI value."""
            if addr in FORMULAS:
                val = FORMULAS[addr]
                fill = FILL_ORANGE if addr in ORANGE_FORMULA_CELLS else FILL_WHITE
            else:
                txt = self._get_text(addr)
                if not txt:
                    val = None
                else:
                    try:
                        val = float(txt.replace("%", ""))
                    except ValueError:
                        val = txt
                fill = FILL_ORANGE
            cell = w(addr, val, font=font, fill=fill, align=align)
            if numfmt:
                cell.number_format = numfmt
            return cell

        # ---- Header ----
        proj = self._get_text("PROJ")
        w("B1", f"Project: {proj}" if proj else "Project:", font=std_font, fill=FILL_WHITE, align=left_al, border=Border())
        w("T1", "Engineer:", font=hdr_font, fill=FILL_WHITE, align=right_al, border=Border())
        w_field("U1", align=center)
        w("AD1", "Date:", font=hdr_font, fill=FILL_WHITE, align=right_al, border=Border())
        w_field("AE1", align=center)
        ws.merge_cells("AE1:AF1")

        # ---- Building Properties ----
        w("B2", "Building Properties", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        bp_rows = [
            (3, "Location", "D3", ""), (4, "Storey", "D4", ""), (5, "Width, B", "D5", "m"),
            (6, "Length, L", "D6", "m"), (7, "Height, H", "D7", "m"), (8, "Ht above ground", "D8", "m"),
            (9, "Ht of typical floor", "D9", "m"), (10, "Ht below ground", "D10", "m"),
            (11, "Aspect Ratio, L/B", "D11", ""), (12, "Slenderness Ratio, H/B", "D12", ""),
            (13, "Slenderness Limit", "D13", ""), (15, "Typical floor area", "D15", "m2"),
            (16, "Etabs Total Area", "D16", "m2"), (17, "Shear Wall Etabs Area", "D17", "m2"),
            (18, "Shear Wall %", "D18", ""),
        ]
        for row, lbl, cid, unit in bp_rows:
            w(f"B{row}", lbl, align=left_al, border=Border())
            numfmt = "0.00%" if cid == "D18" else None
            w_field(cid, numfmt=numfmt)
            if unit:
                w(f"E{row}", unit, align=center, border=Border())
        w("B14", "(IS 16700:2023 Table 2)", font=small_font, fill=FILL_WHITE, align=left_al, border=Border())
        w("B19", "*P= Provisional floor", font=small_font, fill=FILL_WHITE, align=left_al, border=Border())

        # ---- Seismic Parameters ----
        ws.merge_cells("F2:H2")
        w("F2", "Seismic parameters", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        sp_rows = [
            (3, "Zone", "H3", ""), (4, "Category", "H4", ""), (5, "Frame Type", "H5", ""),
            (6, "Site Class", "H6", ""), (7, "R (IS 1893 Part 1: 2016)", "H7", ""),
            (9, "Seismic weight (ETABS)", "H9", "kN"), (10, "Fundamental period", "H10", "s"),
        ]
        for row, lbl, cid, unit in sp_rows:
            w(f"F{row}", lbl, align=left_al, border=Border())
            w_field(cid)
            if unit:
                w(f"I{row}", unit, align=center, border=Border())

        # ---- Base shear calculation ----
        ws.merge_cells("F11:H11")
        w("F11", "Base shear calculation", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        w("F12", "IS 1893", align=center, border=Border())
        w("G12", 2016, align=center, border=Border())
        for row, lbl, cid in [(13, "Z, zone factor", "G13"), (14, "I", "G14"), (15, "R", "G15"), (16, "Sa/g", "G16")]:
            w(f"F{row}", lbl, align=right_al, border=Border())
            w_field(cid, numfmt="0.0000" if cid == "G16" else None)
        for row, lbl, cid in [(20, "Ah", "G20"), (21, "Ah,min", "G21"), (22, "Base shear", "G22")]:
            w(f"F{row}", lbl, align=right_al, border=Border())
            w_field(cid, numfmt="0.0000" if cid in ("G20", "G21") else "0")

        # ---- Base Shear & Moments ----
        w("B23", "Base Shear & Moments", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        for col, h in zip("CDEF", ["Vx", "My", "Vy", "Mx"]):
            w(f"{col}24", h, font=hdr_font, align=center, fill=FILL_BLUE)
        for col, u in zip("CDEF", ["kN", "kNm", "kN", "kNm"]):
            w(f"{col}25", u, align=center)
        w("B26", "EQ", align=center)
        for col in "CDEF":
            w_field(f"{col}26")
        for col, h in zip("CDEF", ["Fx", "Fy", "Cx", "Cy"]):
            w(f"{col}28", h, font=hdr_font, align=center, fill=FILL_BLUE)
        for col in "CDEF":
            w(f"{col}29", "kN", align=center)
        w("B30", "Wind", align=center)
        for col in "CDEF":
            w_field(f"{col}30")
        w("G30", "(Lodha)", font=small_font, fill=FILL_WHITE, align=left_al, border=Border())
        w("B31", "Wind", align=center)
        for col in "CDEF":
            w_field(f"{col}31")
        w("G31", "(Consultant)", font=small_font, fill=FILL_WHITE, align=left_al, border=Border())

        # ---- Modal Participating Mass Ratios ----
        ws.merge_cells("B33:E33")
        w("B33", "Modal Participating Mass Ratios", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        for col, h in zip("BCDEFG", ["Mode", "Period", "UX", "UY", "RZ", "Nature"]):
            w(f"{col}34", h, font=hdr_font, align=center, fill=FILL_BLUE)
        for mode, r in zip((1, 2, 3), MODE_ROWS):
            w(f"B{r}", mode, align=center)
            for col in "CDEF":
                w_field(f"{col}{r}")
            w_field(f"G{r}")
        w_field("H36", numfmt="0.0%")
        w_field("H37", numfmt="0.0%")

        # ---- Gravity Load Check ----
        w("B39", "Gravity Load Check", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        gl_rows = [(40, "DL", "D40", "kN"), (41, "Dead Load Intensity", "D41", "kN/m2"), (42, "SDL", "D42", "kN"),
                   (43, "LL", "D43", "kN"), (44, "Total Load", "D44", "kN"), (45, "Total Load Intensity", "D45", "kN/m2")]
        for row, lbl, cid, unit in gl_rows:
            w(f"B{row}", lbl, align=left_al, border=Border())
            w_field(cid)
            w(f"E{row}", unit, align=center, border=Border())

        # ---- Story Drift Checks ----
        w("G39", "Story Drift Checks", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        for col, h in zip("GHIJ", ["LC", "Drift", "Limit", "Story"]):
            w(f"{col}40", h, font=hdr_font, align=center, fill=FILL_BLUE)
        for row, lc, hcid, lim, story in [(41, "GWX", "H41", "I41", "J41"), (42, "GWY", "H42", "I42", "J42"),
                                           (43, "SpecX", "H43", "I43", "J43"), (44, "SpecY", "H44", "I44", "J44")]:
            w(f"G{row}", lc, align=center, border=Border())
            w_field(hcid, numfmt="0.0000")
            w_field(lim, numfmt="0.000")
            w(f"J{row}", self._get_text(story), align=left_al, border=Border())

        # ---- Crack coefficients ----
        w("B48", "Crack coefficients", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        w("C49", "Service", font=hdr_font, align=center, fill=FILL_BLUE)
        w("D49", "Design", font=hdr_font, align=center, fill=FILL_BLUE)
        for row, lbl, c1, d1 in [(50, "beams", "C50", "D50"), (51, "columns", "C51", "D51"),
                                  (52, "walls", "C52", "D52"), (53, "floors", "C53", "D53")]:
            w(f"B{row}", lbl, align=right_al, border=Border())
            w_field(c1)
            w_field(d1)

        # ---- Structure Mass Index ----
        w("F46", "Structure Mass Index (SMI)", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        for col, h in zip("GHIJ", ["concrete", "concrete", "concrete", "concrete"]):
            w(f"{col}47", h, font=hdr_font, align=center, fill=FILL_BLUE)
        for col, h in zip("GHIJ", ["kN", "%", "m3", "m3/sqft"]):
            w(f"{col}48", h, font=hdr_font, align=center, fill=FILL_BLUE)
        for lbl, r in SMI_ROWS:
            w(f"F{r}", lbl, align=right_al, border=Border())
            w_field(f"G{r}")
            w_field(f"H{r}", numfmt="0%")
            w_field(f"I{r}")
            w_field(f"J{r}", numfmt="0.0000")
        w("F54", "TOTAL", align=right_al, border=Border())
        w_field("G54")
        w_field("H54", numfmt="0%")
        w_field("I54")
        w_field("J54", numfmt="0.0000")

        # ---- Torsional Irregularity Check ----
        w("L37", "Torsional Irregularity check", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        for col, h in zip(["L", "M", "O", "Q", "S", "U", "V", "X"],
                           ["POINTS", "GWX", "GWY", "SPEC-X", "SPEC-Y", "Δ", "Y dir", "X dir"]):
            w(f"{col}46", h, font=hdr_font, align=center, fill=FILL_BLUE)
        for col, h in zip("LMNOPQRSTUVWXY", ["ID", "X", "Y", "X", "Y", "X", "Y", "X", "Y", "mm", "Δmax", "Δavg", "Δmax", "Δavg"]):
            w(f"{col}47", h, font=small_font, align=center, fill=FILL_BLUE)
        out_fills = {"U": FILL_WHITE, "V": FILL_YELLOW, "W": FILL_BLUE, "X": FILL_GOLD, "Y": FILL_GREY}
        for lbl, r in [("1 (31)", 48), ("2 (116)", 49), ("3 (32)", 50), ("4 (115)", 51)]:
            w(f"L{r}", lbl, font=small_font, align=center)
            for col in TORSION_IN_COLS:
                w_field(f"{col}{r}")
            for col in "UVWXY":
                cell = w_field(f"{col}{r}")
                cell.fill = out_fills[col]
        w("T52", "Ratio", align=right_al, border=Border())
        ws.merge_cells("V52:W52")
        w_field("V52", numfmt="0.000")
        ws.merge_cells("X52:Y52")
        w_field("X52", numfmt="0.000")
        note = self.torsion_note.cget("text") if hasattr(self, "torsion_note") else ""
        w("M53", note, font=small_font, fill=FILL_WHITE, align=left_al, border=Border())
        w("M54", "*Same color code represents points considered in the said direction.", font=small_font, fill=FILL_WHITE, align=left_al, border=Border())

        # ---- Checks ----
        w("AB47", "Checks", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        check_defs = [
            (48, "1. First 2 modes translational", "AF48"),
            (49, "2. Time period difference is atleast 10%", "AF49"),
            (50, "3. Drift check", "AF50"),
            (51, "4. Deflection", None),
            (52, "5. Torsional Irregularity", "AF52"),
        ]
        for row, lbl, formula_cid in check_defs:
            w(f"AB{row}", lbl, font=small_font, align=left_al, border=Border())
            if formula_cid:
                w_field(formula_cid)
            else:
                status = self.cells["CK4"].cget("text") if "CK4" in self.cells else ""
                w("AF51", status, font=std_font, align=center)
        w("AB53", "Consider only 2 load cases for wind GWX and GWY only.", font=small_font, fill=FILL_WHITE, align=left_al, border=Border())

        for cid in ("AF48", "AF49", "AF50", "AF52"):
            ws.conditional_formatting.add(cid, FormulaRule(formula=[f'{cid}="OK"'], fill=FILL_GREEN))
            ws.conditional_formatting.add(cid, FormulaRule(formula=[f'{cid}<>"OK"'], fill=FILL_RED))
        af51_status = self.cells["CK4"].cget("text") if "CK4" in self.cells else ""
        ws["AF51"].fill = FILL_GREEN if af51_status == "OK" else (FILL_RED if af51_status == "NO" else FILL_WHITE)

        # ---- Floor plan image ----
        ws.merge_cells("K4:V20")
        if self.floor_plan_path and HAS_PIL:
            try:
                from openpyxl.drawing.image import Image as XLImage
                xl_img = XLImage(self.floor_plan_path)
                xl_img.width, xl_img.height = 480, 300
                ws.add_image(xl_img, "K4")
            except Exception:
                pass

        # ---- Wind lateral displacement per floor + chart ----
        wt_row0 = 60
        w(f"B{wt_row0}", "Lateral displacement due to wind load (per floor)", font=hdr_font, fill=FILL_WHITE, align=left_al, border=Border())
        headers = ["Story", "Ht (m)", "Elevation (m)", "Ux (mm)", "Uy (mm)", "H/500 (mm)", "Status"]
        for j, h in enumerate(headers):
            w(f"{get_column_letter(2 + j)}{wt_row0 + 1}", h, font=hdr_font, align=center, fill=FILL_BLUE)
        data_start = wt_row0 + 2
        for i, row in enumerate(self.wind_rows):
            r = data_start + i
            w(f"B{r}", row["story"].get().strip(), align=center)
            ht_txt = row["ht"].get().strip()
            w(f"C{r}", float(ht_txt) if ht_txt else None, align=center)
            elev_txt = row["elev"].cget("text")
            w(f"D{r}", float(elev_txt) if elev_txt else None, align=center)
            ux_txt = row["ux"].get().strip()
            w(f"E{r}", float(ux_txt) if ux_txt else None, align=center)
            uy_txt = row["uy"].get().strip()
            w(f"F{r}", float(uy_txt) if uy_txt else None, align=center)
            h500_txt = row["h500"].cget("text")
            w(f"G{r}", float(h500_txt) if h500_txt else None, align=center)
            status = row["status"].cget("text")
            cell = w(f"H{r}", status, align=center)
            if status == "OK":
                cell.fill = FILL_GREEN
            elif status == "NO":
                cell.fill = FILL_RED
        data_end = data_start + len(self.wind_rows) - 1

        if len(self.wind_rows) >= 2:
            chart = ScatterChart()
            chart.title = "Lateral Displacement vs Elevation"
            chart.x_axis.title = "Displacement (mm)"
            chart.y_axis.title = "Elevation (m)"
            yref = Reference(ws, min_col=4, min_row=data_start, max_row=data_end)
            for col, name in ((5, "Ux"), (6, "Uy"), (7, "H/500")):
                xref = Reference(ws, min_col=col, min_row=data_start, max_row=data_end)
                series = Series(yref, xref, title=name)
                series.marker.symbol = "circle"
                series.graphicalProperties.line.noFill = False
                chart.series.append(series)
            chart.width, chart.height = 14, 10
            ws.add_chart(chart, f"J{wt_row0}")

        # ---- Column widths ----
        widths = {"A": 2, "B": 14, "C": 8, "D": 9, "E": 6, "F": 14, "G": 9, "H": 8, "I": 6, "J": 6,
                  "L": 7, "M": 6, "N": 6, "O": 6, "P": 6, "Q": 6, "R": 6, "S": 6, "T": 6, "U": 6,
                  "V": 6, "W": 6, "X": 6, "Y": 6, "AB": 26, "AF": 10}
        for col, width in widths.items():
            ws.column_dimensions[col].width = width

        try:
            wb.save(file_path)
            messagebox.showinfo("Success", f"Successfully exported to:\n{file_path}")
        except PermissionError:
            messagebox.showerror("Error", "Cannot save — the file may be open in Excel.\nClose it and try again.")

    # ---------------- UI layout ----------------
    def build_ui(self):
        ORANGE = "#FBD4B4"
        BLUE_BG = "#daeef3"
        WHITE = "#ffffff"

        spax, spay = 4, 4

        # ---------------- Header Row ----------------
        header_f = tk.Frame(self.main_frame, bg=WHITE)
        header_f.pack(side="top", fill="x", pady=spay, padx=spax)

        self.create_label(header_f, "Project:", borderwidth=0).pack(side="left")
        self.create_cell(header_f, cell_id="PROJ", is_input=True, bg=WHITE, width=30).pack(side="left", padx=1)

        self.create_label(header_f, "Engineer:", borderwidth=0).pack(side="left", padx=(10, 0))
        self.create_cell(header_f, cell_id="U1", is_input=True, width=10, bg=ORANGE).pack(side="left", padx=1)

        self.create_label(header_f, "Date:", borderwidth=0).pack(side="left", padx=(10, 0))
        self.create_cell(header_f, cell_id="AE1", is_input=True, width=12, bg=ORANGE).pack(side="left", padx=1)

        print_btn = tk.Button(header_f, text="Export to Excel", command=self.export_to_excel, bg="#4CAF50", fg="white", font=self.bold_font, relief="flat", padx=10, pady=2)
        print_btn.pack(side="right", padx=(10, 0))

        # ---------------- 3 Columns Layout ----------------
        body_f = tk.Frame(self.main_frame, bg=WHITE)
        body_f.pack(side="top", fill="both", expand=True, padx=spax, pady=spay)

        col1 = tk.Frame(body_f, bg=WHITE)
        col1.pack(side="left", fill="y", padx=(0, 15))
        col2 = tk.Frame(body_f, bg=WHITE)
        col2.pack(side="left", fill="y", padx=(0, 15))
        col3 = tk.Frame(body_f, bg=WHITE)
        col3.pack(side="left", fill="y", padx=(0, 10))

        # ================= COLUMN 1 =================
        bp_f = tk.Frame(col1, bg=WHITE)
        bp_f.pack(side="top", fill="x", pady=(0, spay))
        self.create_label(bp_f, "Building Properties", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=3, sticky="we")
        bp_data = [
            ("Location", True, "D3"), ("Storey", True, "D4"), ("Width, B", True, "D5", "m"),
            ("Length, L", True, "D6", "m"), ("Height, H", False, "D7", "m"), ("Ht above ground", True, "D8", "m"),
            ("Ht of typical floor", True, "D9", "m"), ("Ht below ground", True, "D10", "m"),
            ("Aspect Ratio, L/B", False, "D11"), ("Slenderness Ratio, H/B", False, "D12"),
            ("Slenderness Limit", True, "D13"), ("Typical floor area", True, "D15", "m2"),
            ("Etabs Total Area", True, "D16", "m2"), ("Shear Wall Etabs Area", True, "D17", "m2"),
            ("Shear Wall %", False, "D18"),
        ]
        for i, item in enumerate(bp_data):
            lbl, is_input, cell_id = item[0], item[1], item[2]
            unit = item[3] if len(item) > 3 else ""
            self.create_label(bp_f, lbl, width=20).grid(row=i + 1, column=0, sticky="we")
            self.create_cell(bp_f, bg=ORANGE if is_input else WHITE, is_input=is_input, cell_id=cell_id).grid(row=i + 1, column=1, sticky="we")
            if unit:
                self.create_label(bp_f, unit, anchor="c", width=5).grid(row=i + 1, column=2, sticky="we")
        self.create_label(bp_f, "*P- Provisional floor", font=self.small_font, borderwidth=0).grid(row=len(bp_data) + 1, column=0, columnspan=3, sticky="w")

        bsm_f = tk.Frame(col1, bg=WHITE)
        bsm_f.pack(side="top", fill="x", pady=(spay, spay))
        self.create_label(bsm_f, "Base Shear & Moments", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=5, sticky="we")
        for j, h in enumerate(["", "Vx\nkN", "My\nkNm", "Vy\nkN", "Mx\nkNm"]):
            self.create_label(bsm_f, h, anchor="c", bg=BLUE_BG if j else WHITE).grid(row=1, column=j, sticky="we")
        self.create_label(bsm_f, "EQ", anchor="c").grid(row=2, column=0, sticky="we")
        for j, col in enumerate("CDEF"):
            self.create_cell(bsm_f, bg=ORANGE, is_input=True, width=8, cell_id=f"{col}26").grid(row=2, column=j + 1, sticky="we")
        for j, h in enumerate(["", "Fx\nkN", "Fy\nkN", "Cx\nkN", "Cy\nkN"]):
            self.create_label(bsm_f, h, anchor="c", bg=BLUE_BG if j else WHITE).grid(row=3, column=j, sticky="we")
        self.create_label(bsm_f, "Wind", anchor="c").grid(row=4, column=0, sticky="we")
        for j, col in enumerate("CDEF"):
            self.create_cell(bsm_f, bg=ORANGE, is_input=True, width=8, cell_id=f"{col}30").grid(row=4, column=j + 1, sticky="we")
        self.create_label(bsm_f, "(Lodha)", borderwidth=0, font=self.small_font).grid(row=4, column=5, sticky="w")
        self.create_label(bsm_f, "Wind", anchor="c").grid(row=5, column=0, sticky="we")
        for j, col in enumerate("CDEF"):
            self.create_cell(bsm_f, bg=ORANGE, is_input=True, width=8, cell_id=f"{col}31").grid(row=5, column=j + 1, sticky="we")
        self.create_label(bsm_f, "(Consultant)", borderwidth=0, font=self.small_font).grid(row=5, column=5, sticky="w")

        mod_f = tk.Frame(col1, bg=WHITE)
        mod_f.pack(side="top", fill="x", pady=(spay, spay))
        self.create_label(mod_f, "Modal Participating Mass Ratios", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=7, sticky="we")
        for j, h in enumerate(["Mode", "Period", "UX", "UY", "RZ", "Nature", ""]):
            self.create_label(mod_f, h, anchor="c", width=6 if j > 0 else 5, bg=BLUE_BG if h else WHITE).grid(row=1, column=j, sticky="we")
        nature_opts = ["X-trans", "Y-trans", "Torsion"]
        for i, r in enumerate(MODE_ROWS):
            self.create_label(mod_f, str(i + 1), anchor="c", width=5).grid(row=i + 2, column=0, sticky="we")
            self.create_cell(mod_f, bg=ORANGE, is_input=True, cell_id=f"C{r}", width=6).grid(row=i + 2, column=1, sticky="we")
            for j, col in enumerate("DEF"):
                self.create_cell(mod_f, bg=WHITE, is_input=True, cell_id=f"{col}{r}", width=6).grid(row=i + 2, column=j + 2, sticky="we")
            self.create_combo(mod_f, nature_opts, width=8, cell_id=f"G{r}").grid(row=i + 2, column=5, sticky="we")
        self.create_label(mod_f, "Mode1→Mode2 diff:", font=self.small_font, borderwidth=0).grid(row=5, column=0, columnspan=2, sticky="w")
        self.create_cell(mod_f, bg=WHITE, is_input=False, cell_id="H36", width=6).grid(row=5, column=2, sticky="we")
        self.create_label(mod_f, "Mode2→Mode3 diff:", font=self.small_font, borderwidth=0).grid(row=6, column=0, columnspan=2, sticky="w")
        self.create_cell(mod_f, bg=WHITE, is_input=False, cell_id="H37", width=6).grid(row=6, column=2, sticky="we")

        col1_bot = tk.Frame(col1, bg=WHITE)
        col1_bot.pack(side="top", fill="x", pady=(spay, 0))
        col1_bl = tk.Frame(col1_bot, bg=WHITE)
        col1_bl.pack(side="left", fill="y", padx=(0, 10))
        col1_br = tk.Frame(col1_bot, bg=WHITE)
        col1_br.pack(side="left", fill="y")

        gl_f = tk.Frame(col1_bl, bg=WHITE)
        gl_f.pack(side="top", fill="x", pady=(0, spay))
        self.create_label(gl_f, "Gravity Load Check", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=3, sticky="we")
        for i, (lbl, is_input, cid, unit) in enumerate([
            ("DL", True, "D40", "kN"), ("Dead Load Intensity", False, "D41", "kN/m2"), ("SDL", True, "D42", "kN"),
            ("LL", True, "D43", "kN"), ("Total Load", False, "D44", "kN"), ("Total Load Intensity", False, "D45", "kN/m2"),
        ]):
            self.create_label(gl_f, lbl, width=15).grid(row=i + 1, column=0, sticky="we")
            self.create_cell(gl_f, bg=ORANGE if is_input else WHITE, is_input=is_input, cell_id=cid, width=8).grid(row=i + 1, column=1, sticky="we")
            self.create_label(gl_f, unit, anchor="c", width=6).grid(row=i + 1, column=2, sticky="we")

        cc_f = tk.Frame(col1_bl, bg=WHITE)
        cc_f.pack(side="top", fill="x", pady=(spay, 0))
        self.create_label(cc_f, "Crack coefficients", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=3, sticky="we")
        self.create_label(cc_f, "Service", anchor="c").grid(row=1, column=1, sticky="we")
        self.create_label(cc_f, "Design", anchor="c").grid(row=1, column=2, sticky="we")
        for i, (lbl, v1, v2, c1, c2) in enumerate([
            ("beams", "0.50", "0.35", "C50", "D50"), ("columns", "1.00", "0.7", "C51", "D51"),
            ("walls", "1.00", "0.7", "C52", "D52"), ("floors", "0.36", "0.25", "C53", "D53"),
        ]):
            self.create_label(cc_f, lbl, anchor="e", width=15).grid(row=i + 2, column=0, sticky="we")
            self.create_cell(cc_f, text=v1, bg=WHITE, is_input=True, cell_id=c1, width=8).grid(row=i + 2, column=1, sticky="we")
            self.create_cell(cc_f, text=v2, bg=WHITE, is_input=True, cell_id=c2, width=6).grid(row=i + 2, column=2, sticky="we")

        sd_f = tk.Frame(col1_br, bg=WHITE)
        sd_f.pack(side="top", fill="x", pady=(0, spay))
        self.create_label(sd_f, "Story Drift Checks", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=4, sticky="we")
        for j, h in enumerate(["LC", "Drift", "Limit", "Story"]):
            self.create_label(sd_f, h, anchor="c", bg=BLUE_BG).grid(row=1, column=j, sticky="we")
        for i, (lc, cid, lim_cid, lim_default, story_cid, story_default) in enumerate([
            ("GWX", "H41", "I41", "0.002", "J41", "F20"), ("GWY", "H42", "I42", "0.002", "J42", "F19"),
            ("SpecX", "H43", "I43", "0.004", "J43", "F21"), ("SpecY", "H44", "I44", "0.004", "J44", "Park3"),
        ]):
            self.create_label(sd_f, lc, width=6).grid(row=i + 2, column=0, sticky="we")
            self.create_cell(sd_f, bg=ORANGE, is_input=True, width=7, cell_id=cid).grid(row=i + 2, column=1, sticky="we")
            self.create_cell(sd_f, text=lim_default, bg=WHITE, is_input=True, width=6, cell_id=lim_cid).grid(row=i + 2, column=2, sticky="we")
            self.create_cell(sd_f, text=story_default, bg=WHITE, is_input=True, width=8, cell_id=story_cid).grid(row=i + 2, column=3, sticky="we")

        smi_f = tk.Frame(col1_br, bg=WHITE)
        smi_f.pack(side="top", fill="x", pady=(spay, 0))
        self.create_label(smi_f, "Structure Mass Index (SMI)", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=5, sticky="we")
        for j, h in enumerate(["", "concrete\nkN", "%\n", "concrete\nm3", "concrete\nm3/sqft"]):
            self.create_label(smi_f, h, anchor="c", width=7, bg=BLUE_BG if h else WHITE).grid(row=1, column=j, sticky="we")
        for i, (lbl, r) in enumerate(SMI_ROWS):
            self.create_label(smi_f, lbl, anchor="e", width=7).grid(row=i + 2, column=0, sticky="we")
            self.create_cell(smi_f, bg=ORANGE, is_input=True, cell_id=f"G{r}", width=8).grid(row=i + 2, column=1, sticky="we")
            self.create_cell(smi_f, bg=WHITE, is_input=False, width=6, cell_id=f"H{r}").grid(row=i + 2, column=2, sticky="we")
            self.create_cell(smi_f, bg=WHITE, is_input=False, width=8, cell_id=f"I{r}").grid(row=i + 2, column=3, sticky="we")
            self.create_cell(smi_f, bg=WHITE, is_input=False, width=8, cell_id=f"J{r}").grid(row=i + 2, column=4, sticky="we")
        self.create_label(smi_f, "TOTAL", anchor="e", width=7).grid(row=7, column=0, sticky="we")
        self.create_cell(smi_f, bg=WHITE, is_input=False, width=8, cell_id="G54").grid(row=7, column=1, sticky="we")
        self.create_cell(smi_f, bg=WHITE, is_input=False, width=6, cell_id="H54").grid(row=7, column=2, sticky="we")
        self.create_cell(smi_f, bg=WHITE, is_input=False, width=8, cell_id="I54").grid(row=7, column=3, sticky="we")
        self.create_cell(smi_f, bg=WHITE, is_input=False, width=8, cell_id="J54").grid(row=7, column=4, sticky="we")

        # ================= COLUMN 2 =================
        sp_f = tk.Frame(col2, bg=WHITE)
        sp_f.pack(side="top", anchor="w", pady=(0, spay))
        self.create_label(sp_f, "Seismic parameters", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=3, sticky="we")
        for i, (lbl, cid, unit) in enumerate([
            ("Zone", "H3", ""), ("Category", "H4", ""), ("Frame Type", "H5", ""), ("Site Class", "H6", ""),
            ("R (IS 1893 Part 1: 2016)", "H7", ""), ("Seismic weight (ETABS)", "H9", "kN"), ("Fundamental period", "H10", "s"),
        ]):
            self.create_label(sp_f, lbl, width=22).grid(row=i + 1, column=0, sticky="we")
            is_computed = cid == "H10"
            self.create_cell(sp_f, bg=ORANGE, is_input=not is_computed, cell_id=cid, width=8).grid(row=i + 1, column=1, sticky="we")
            if unit:
                self.create_label(sp_f, unit, anchor="c", width=3, borderwidth=0).grid(row=i + 1, column=2, sticky="w", padx=(2, 0))

        bsc_f = tk.Frame(col2, bg=WHITE)
        bsc_f.pack(side="top", anchor="w", pady=(spay, spay))
        self.create_label(bsc_f, "Base shear calculation", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=3, sticky="we")
        self.create_label(bsc_f, "IS 1893", anchor="c", width=12).grid(row=1, column=0, sticky="we")
        self.create_label(bsc_f, "2016", anchor="c", width=8).grid(row=1, column=1, sticky="we")
        row_idx = 2
        for lbl, cid in [("Z, zone factor", "G13"), ("I", "G14"), ("R", "G15"), ("Sa/g", "G16")]:
            self.create_label(bsc_f, lbl, anchor="e").grid(row=row_idx, column=0, sticky="we")
            is_computed = cid == "G16"
            self.create_cell(bsc_f, bg=ORANGE, is_input=not is_computed, cell_id=cid).grid(row=row_idx, column=1, sticky="we")
            row_idx += 1
        tk.Label(bsc_f, text="Ah = (Z/2) × (Sa/g) / (R/I)", bg=WHITE, font=self.small_font).grid(row=row_idx, column=0, columnspan=3, pady=2)
        row_idx += 1
        for lbl, is_input, cid in [("Ah", False, "G20"), ("Ah,min", True, "G21"), ("Base shear", False, "G22")]:
            self.create_label(bsc_f, lbl, anchor="e").grid(row=row_idx, column=0, sticky="we")
            self.create_cell(bsc_f, bg=ORANGE if is_input else WHITE, is_input=is_input, cell_id=cid).grid(row=row_idx, column=1, sticky="we")
            row_idx += 1

        floor_plan_f = tk.Frame(col2, bg="#f2f2f2", width=350, height=220, highlightbackground="#cccccc", highlightthickness=1)
        floor_plan_f.pack(side="top", pady=(spay, 2))
        floor_plan_f.pack_propagate(False)
        self.floor_plan_label = tk.Label(floor_plan_f, text="[ Floor Plan Graphic ]", bg="#f2f2f2", fg="#888888")
        self.floor_plan_label.pack(expand=True)
        tk.Button(col2, text="Insert Floor Plan Image…", command=self.insert_floor_plan_image, font=self.small_font, relief="flat", bg="#dce6f1").pack(side="top", pady=(0, spay))

        tic_f = tk.Frame(col2, bg=WHITE)
        tic_f.pack(side="top", fill="x", pady=(spay, 0))
        for j, h in enumerate(["POINTS", "GWX", "", "GWY", "", "SPEC X", "", "SPEC Y", "", "Δ", "Y dir", "", "X dir", ""]):
            self.create_label(tic_f, h, anchor="c", bg=BLUE_BG if h else WHITE).grid(row=0, column=j, sticky="we")
        for j, h in enumerate(["ID", "X", "Y", "X", "Y", "X", "Y", "X", "Y", "mm", "Δmax", "Δavg", "Δmax", "Δavg"]):
            self.create_label(tic_f, h, font=self.small_font, anchor="c", bg=BLUE_BG).grid(row=1, column=j, sticky="we")
        bgs = {"U": "#ffffff", "V": "#fef2cb", "W": "#b4c6e7", "X": "#ffe598", "Y": "#dadada"}
        for i, (lbl, r) in enumerate([("1 (31)", 48), ("2 (116)", 49), ("3 (32)", 50), ("4 (115)", 51)]):
            self.create_label(tic_f, lbl, font=self.small_font, anchor="c", width=6).grid(row=i + 2, column=0, sticky="we")
            for j, col in enumerate(TORSION_IN_COLS):
                self.create_cell(tic_f, bg=ORANGE, is_input=True, width=4, cell_id=f"{col}{r}").grid(row=i + 2, column=j + 1, sticky="we")
            for j, col in enumerate("UVWXY"):
                self.create_cell(tic_f, bg=bgs[col], is_input=False, width=5, cell_id=f"{col}{r}").grid(row=i + 2, column=9 + j, sticky="we")
        self.create_label(tic_f, "Ratio", anchor="e").grid(row=6, column=9, columnspan=2, sticky="we")
        self.create_cell(tic_f, bg=WHITE, is_input=False, width=5, cell_id="V52").grid(row=6, column=11, sticky="we")
        self.create_cell(tic_f, bg=WHITE, is_input=False, width=10, cell_id="X52").grid(row=6, column=12, columnspan=2, sticky="we")
        self.torsion_note = self.create_label(tic_f, "*Enter torsional irregularity point data above.", font=self.small_font, borderwidth=0)
        self.torsion_note.grid(row=7, column=0, columnspan=14, sticky="w", pady=(5, 0))
        self.create_label(tic_f, "*Same color code represents points considered in the said direction.", font=self.small_font, borderwidth=0).grid(row=8, column=0, columnspan=14, sticky="w")

        # ================= COLUMN 3 =================
        self.create_label(col3, "Chart is generated automatically\nin the exported Excel file from\nthe wind displacement table below.", font=self.small_font, anchor="c", borderwidth=1, bg="#f2f2f2", width=32).pack(side="top", pady=(0, spay), ipady=30)

        chk_f = tk.Frame(col3, bg=WHITE)
        chk_f.pack(side="top", fill="x", pady=(spay, 0))
        self.create_label(chk_f, "Checks", font=self.bold_font, borderwidth=0).grid(row=0, column=0, columnspan=2, sticky="w")
        for i, (lbl, cid) in enumerate([
            ("1. First 2 modes translational", "CK1"), ("2. Time period difference is atleast 10%", "CK2"),
            ("3. Drift check", "CK3"), ("4. Deflection", "CK4"), ("5. Torsional Irregularity", "CK5"),
        ]):
            self.create_label(chk_f, lbl, borderwidth=0, font=self.small_font, width=28).grid(row=i + 1, column=0, sticky="w")
            self.create_cell(chk_f, bg=WHITE, is_input=False, width=10, cell_id=cid).grid(row=i + 1, column=1, sticky="we", padx=(5, 0), pady=1)
        self.create_label(chk_f, "Consider only 2 load cases for wind GWX and GWY only.", font=self.small_font, borderwidth=0).grid(row=6, column=0, columnspan=2, sticky="w", pady=(5, 0))

        # ================= WIND DISPLACEMENT TABLE (full width) =================
        wt_f = tk.Frame(self.main_frame, bg=WHITE)
        wt_f.pack(side="top", fill="x", padx=spax, pady=(0, spay))
        title_row = tk.Frame(wt_f, bg=WHITE)
        title_row.pack(side="top", fill="x")
        self.create_label(title_row, "Lateral Displacement due to Wind (per floor)", font=self.bold_font, borderwidth=0).pack(side="left")
        tk.Button(title_row, text="+ Add Story", command=self.add_wind_row, font=self.small_font, relief="flat", bg="#dce6f1").pack(side="left", padx=(15, 5))
        tk.Button(title_row, text="- Remove Last", command=self.remove_wind_row, font=self.small_font, relief="flat", bg="#f4cccc").pack(side="left")

        header_row = tk.Frame(wt_f, bg=WHITE)
        header_row.pack(side="top", fill="x", pady=(4, 0))
        for j, h in enumerate(["Story", "Ht (m)", "Elevation (m)", "Ux (mm)", "Uy (mm)", "H/500 (mm)", "Status"]):
            self.create_label(header_row, h, anchor="c", bg=BLUE_BG, width=9).grid(row=0, column=j, sticky="we", padx=1)

        self.wind_table_body = tk.Frame(wt_f, bg=WHITE)
        self.wind_table_body.pack(side="top", fill="x")
        for _ in range(6):
            self.add_wind_row()


if __name__ == "__main__":
    app = StudySheetApp()
    app.mainloop()
