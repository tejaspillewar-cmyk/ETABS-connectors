# ETABS Live Connector

A Tkinter control panel that talks to a running **CSI ETABS** model over its COM
API (OAPI) and runs a **Flexural Design Review (FDR)** on shear-wall piers —
producing an Excel report and AutoCAD `.scr` scripts you can paste straight into
a drawing.

---

## 1. What you need

| Requirement | Version used here | Notes |
|---|---|---|
| Windows | 10 / 11 | COM-only; will not run on macOS or Linux |
| ETABS | 23 | Must be **installed and licensed** on the same machine |
| Python | 3.14.7 | 3.10+ should work; `tkinter` must be included |

### Python modules

Everything is in `requirements.txt`:

```bash
pip install -r requirements.txt
```

| Module | Why it is needed | Required? |
|---|---|---|
| `comtypes` | The COM bridge to ETABS. Creates `ETABSv1.Helper` and generates the `comtypes.gen.ETABSv1` type-library wrapper on first run. | **Yes** |
| `psutil` | Scans running processes to find `ETABS.exe` and its PID so the app can attach to it. | **Yes** |
| `pandas` | Holds the extracted pier/force tables and drives the Excel writer. | **Yes** |
| `openpyxl` | The actual `.xlsx` engine behind pandas, plus the cell fills/borders/fonts in the report. | **Yes** |
| `ezdxf` | Writes real `.dxf` geometry. Only the **Export DXF** button uses it. | Optional |
| `tkinter` | The entire GUI. Ships with CPython on Windows — nothing to install. | **Yes** |

> `fdr_tool.py` will try to `pip install` **openpyxl** and **ezdxf** on demand if
> they are missing, but installing them up front is cleaner and avoids a stall
> mid-export.

---

## 2. Setup

```bash
cd "ETABS Connector"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The first ETABS connection is slower than later ones — `comtypes` is generating
the ETABS type-library wrapper into `comtypes/gen/`. That is normal and happens
once per environment.

---

## 3. Running it

**Normal use — no console window:**

```bash
pythonw etabs_gui.pyw
```

**Debugging — console visible, tracebacks land in the terminal:**

```bash
python etabs_gui.pyw
```

**From inside ETABS** (optional, see §7): *Tools → Add/Show Plugins*.

The app can also be started with `--pid <N>` to attach to one specific ETABS
process, for scripted launches that know the target PID. The plugin (§7)
doesn't use this — it just opens the GUI and you Attach from card 01.

---

## 4. Work strategy — how a run actually flows

### Step 1 — Connect (card 01)

Open ETABS, load your `.EDB`, and **run the analysis first** — the FDR reads
pier *forces*, which do not exist until the model has been solved.

Then either:

- **Attach** — finds the running `ETABS.exe` by PID and grabs its OAPI object.
  This is the normal path. If two instances are open it refuses rather than
  guessing.
- **Launch** — starts ETABS from its `.exe` path. Used when nothing is open yet.

Cards **02 (Active File)** and **03 (Model Inventory)** are quick sanity checks
that the connection is live and pointed at the model you think it is.

### Step 2 — Read the model

**Refresh** pulls the storey list and every load combination out of the model
and fills the two pickers.

### Step 3 — Set the review parameters

| Input | Meaning |
|---|---|
| `fy` | Steel yield strength, MPa (default 500) |
| `fck` | Fallback concrete grade, MPa — used when a pier's own `fck` reads 0 |
| Wind keywords | Substrings that mark a combination as wind-driven. A combo matching any of these is excluded from the non-wind envelope used by the 0.4·fck / 0.2·fck checks. **Get this list right — it changes the results.** |
| Storey | One storey, or *(All stories)* |
| Text height / 3 gaps | Drafting only — text size and the vertical spacing of the label → Pt% → As stack in the CAD output |
| Plot minimum-governed values | If off, piers governed by `As_min` are left **blank** in the output. That blankness is the drafting convention for "minimum governs". |

Selecting nothing in the combination list means **all** combinations.

### Step 4 — Run

Extraction and calculation happen on the worker thread; the console pane streams
progress. When it finishes, the export buttons switch on.

### Step 5 — Export

| Output | Contents |
|---|---|
| **Excel** | Multi-sheet report: per-pier geometry, force envelopes, capacity/demand ratios, required steel. Optionally a `_RawEnvelope.csv` alongside it. |
| **CAD** | Six timestamped `.scr` files in `cad_export/` — `PierLabels`, `PierRectangles`, `RequiredPt`, `As_Required`, `CD_02fck`, `CD_04fck`. Each is an AutoCAD command script: open the drawing and run `SCRIPT`. |
| **DXF** | One storey as real DXF geometry (needs `ezdxf`). |

`cad_export/` is created automatically. It is disposable output — safe to delete.

---

## 5. Why there is a Launch button at all

The obvious design is a single **Connect** button that grabs whatever ETABS is
open. That was tried first and did not work. Two separate walls:

**Wall 1 — UAC / integrity levels.** COM will not cross a privilege boundary.
If ETABS runs elevated and Python does not (or the reverse), Windows blocks the
connection outright. Nothing in the code can fix this — the two processes have
to run at the same privilege level.

**Wall 2 — a contested registry.** The textbook call is
`GetActiveObject("CSI.ETABS.API.ETABSObject")`. That resolves the ProgID through
the registry, then asks the Running Object Table for whoever registered under it.
With **ETABS 20 and ETABS 23 both installed**, that ProgID→CLSID mapping belongs
to whichever version registered last. The lookup either failed or pointed at the
wrong version, and repairing the registry needs Admin rights that were not
available.

### The fix: make Python the parent process

`helper.CreateObject(<absolute path to ETABS.exe>)` skips the registry entirely.
You name the executable, so there is no ProgID to resolve and no ambiguity about
which version you get. Python spawns ETABS, so it owns the master COM pointer
from birth — zero registry lookups, zero permission negotiation.

That is the **Launch** button, and it is why it exists.

### Why Attach now works too

The original code was structured as VS Code `# %%` cells specifically to keep the
Python kernel alive: Cell 1 launched ETABS and held the pointer, Cell 2 opened the
model, and the engineer could then work in the ETABS GUI while Python stood by.
The pointer had to survive between commands.

Attaching was revisited later and now works — because **card 01's Attach does not
use `GetActiveObject`**. It uses:

```python
helper.GetObjectProcess('CSI.ETABS.API.ETABSObject', pid)
```

`psutil` finds the PID first, then CSI's helper targets *that exact process*.
Naming the process sidesteps the Running Object Table, which is where the
two-versions-installed problem actually lived. This is why Attach is the normal
path today and Launch is the fallback.

So the buttons are not redundant:

| | Use when |
|---|---|
| **Attach** | ETABS is already open. Normal path. Works on the model you are looking at. |
| **Launch** | Nothing is open, *or* attach fails for environmental reasons. |
| **Diagnose** | Nothing works and you need to know why. Prints the full environment — see below. |

### How the connection resolves itself

Attach never reads a configured path — it finds ETABS by process id. That has a useful
consequence: **attaching once teaches the app where ETABS lives.** `psutil` reports the exact
binary behind the process it connected to, which is saved and then used by Launch. So on most
machines nothing ever needs configuring.

The ETABS location is resolved in this order:

1. A currently running ETABS (authoritative — it is the binary actually in use)
2. The saved path from a previous run, if it still exists
3. A scan of `Program Files\Computers and Structures\ETABS*\ETABS.exe`, newest first
4. Otherwise the app asks, once, with a file picker

Card 01 shows which install Launch will use, with a **Change…** link to correct it. The setting
lives in `%LOCALAPPDATA%\ETABSLiveConnector\settings.json`.

The scan checks for `ETABS.exe` itself rather than the versioned folder, because uninstalling
ETABS can leave the folder behind containing only `CSiLicensing` — offering that hollow
directory would be worse than finding nothing.

### When several ETABS are open

Attach lists them **by the model each has open**, not by process id, and asks which one you mean.

### When ETABS is running as administrator

COM cannot cross a privilege boundary, and the raw failure says nothing useful. The app detects
this specifically: `psutil` can read an elevated process's name but not its executable path, so
"visible but unreadable" is a reliable signature. You get told to either reopen ETABS normally
or run the app elevated too, instead of a bare COM error.

### My take on this

**The diagnosis was right and the workaround was the correct call** — without
Admin rights to repair the registry, direct-path launch was the only reliable
door. Three observations on where it stands now:

1. **The real culprit was `GetActiveObject`, not attaching as a concept.** It is
   worth being precise about this, because "you cannot attach to a running
   instance" is a much scarier conclusion than the true one: "you cannot attach
   *through a ProgID lookup* when two versions fight over that ProgID." Switching
   to `GetObjectProcess(progid, pid)` solved it without any registry repair.
   The PID is the disambiguator the registry could not provide.

2. **The `# %%` persistent-kernel trick has been superseded — correctly.**
   Keeping the notebook kernel alive to hold the COM pointer was the right
   instinct, and `EtabsWorker` is that same idea made robust: one long-lived
   thread that calls `CoInitialize()` once and owns every pointer for the life of
   the app. It is a notebook kernel that cannot be accidentally restarted, cannot
   be used from the wrong thread, and does not freeze the UI. Good evolution.

3. **The hardcoded path was the last weak spot — now fixed.** It used to be a literal
   `...\ETABS 23\ETABS.exe` with no way to change it, so Launch broke on any
   machine with ETABS installed elsewhere. It is now resolved at runtime (see
   "How the connection resolves itself" above) and remembered. Attach was never
   affected, since it has never read the path.

---

## 6. Architecture — and the one rule that matters

```
etabs_gui.pyw          Tk UI, cards, worker thread, FDR panel
   ├── app_paths.py    settings, crash logs, ETABS discovery (no Tk, no deps)
   └── fdr_tool.py     FDRConfig + FDRTool: extraction, checks, exports
          └── ETABS COM (comtypes) ──> running ETABS.exe
```

`app_paths.py` deliberately imports nothing beyond the standard library at module
level, because the crash handler uses it before Tk is up and has to keep working on a
machine where the dependencies were never installed.

**Every ETABS call runs on one long-lived background thread.**

This is the single most important design decision in the codebase. A COM pointer
belongs to the apartment that created it, so building `SapModel` on one thread
and using it from another is what makes naive ETABS scripts fail intermittently
and inexplicably. `EtabsWorker` calls `CoInitialize()` once and then serialises
every job through a queue, which buys three things:

1. COM pointers are only ever touched by their owning apartment.
2. ETABS is never asked to do two things at once.
3. The Tk event loop stays free, so the window never greys out.

Results come back to the UI through a second queue that the Tk thread drains on
a timer (`UI_POLL_MS`) — never by touching widgets from the worker.

**`fdr_tool.py` knows nothing about the GUI.** It takes a live `SapModel` plus an
`FDRConfig` and returns data. That is why it can also be run standalone
(`python fdr_tool.py` attaches to ETABS on its own), and why the engineering
logic can be tested without opening a window.

**All magic numbers live in `FDRConfig`** (`fdr_tool.py`). Change defaults there,
not scattered through the calculation code.

---

## 7. The ETABS plugin (optional)

`PyLauncherPlugin/` is a small VB shim that adds this tool to ETABS's plugin
menu. It resolves `etabs_gui.pyw` one directory up from its own DLL at
runtime, so nothing needs editing or building.

The GUI works perfectly well without it — this is a convenience, not a dependency.

**To install:**

1. In ETABS: *Tools → Add/Show Plugins → Add* → browse to
   `PyLauncherPlugin\PyLauncherPlugin.dll` in this folder.
2. Click "Live Connector" (or whatever you named it) at the bottom of the
   Tools menu whenever you want to launch the GUI.

### Why this replaced the earlier `etabs_plugin`

An earlier version of the plugin (`etabs_plugin/`, since removed) chained
three separate steps: hand-compile a C# DLL with `build.ps1`, COM-register it
with `regasm`, then have a Python script (`register_etabs.py`) hand-edit
ETABS's own `ETABS.ini` to add the menu entry. All three were unnecessary
complexity, and the last one was actively broken:

- COM registration never did anything useful — ETABS's ".NET plugin" loader
  finds the plugin class by reflection, not COM, so that whole step was dead
  weight.
- ETABS rewrites `ETABS.ini` itself on exit, and silently drops `[PlugIn]`
  entries that weren't added through its own *Add/Show Plugins* dialog. The
  script-inserted entry didn't survive an ETABS restart, so the menu item
  would randomly vanish.
- The registry/DLL names the two registration paths produced drifted apart
  over time (stale filenames, mismatched CLSIDs), which is exactly the kind
  of thing that's invisible until the plugin silently stops loading.

`PyLauncherPlugin` needs none of that: it's added once through ETABS's own
dialog — the one path ETABS actually persists reliably — and has no config
file or build step to go stale.

---

## 8. Files

| Path | Role |
|---|---|
| `etabs_gui.pyw` | The application. Entry point. |
| `fdr_tool.py` | All engineering logic and exporters. |
| `app_paths.py` | Settings, crash-log location, and ETABS discovery. |
| `requirements.txt` | Python dependencies. |
| `PyLauncherPlugin/` | Optional ETABS menu launcher (VB), added manually via Tools → Add/Show Plugins. |
| `cad_export/` | Generated output. Not in version control, safe to delete. |

To hand this tool to someone else, `etabs_gui.pyw` + `fdr_tool.py` + `app_paths.py` +
`requirements.txt` is the whole thing. No paths need editing — the ETABS location is
worked out on first run.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| *"No running ETABS process found"* | ETABS is not open, or is running elevated while Python is not. Match their privilege levels. |
| *"Several ETABS instances are running"* | No longer an error — Attach asks which model you mean. |
| *"Attach failed. Is the model fully loaded?"* | ETABS was still opening the `.EDB`. Wait for it to finish, then retry. |
| **Launch** fails or opens the wrong ETABS version | Use **Change…** on card 01 to point at the right `ETABS.exe`. The choice is remembered. |
| Attach fails but ETABS is clearly open | Privilege mismatch — the app now detects this and says so. Reopen ETABS normally, or run the app elevated too. |
| **Nothing happens when you double-click the app** | It no longer fails silently: you get a dialog, and a full report in `%LOCALAPPDATA%\ETABSLiveConnector\logs\`. |
| Anything else connection-related | Press **Diagnose** on card 01 and send the output — it lists Python, packages, ETABS installs, and every running instance. |
| Forces are all zero / empty results | The model has not been analysed. Run the analysis in ETABS first. |
| 0.4·fck and 0.2·fck checks look wrong | Your wind keywords do not match this model's combination naming, so wind cases are leaking into the non-wind envelope. |
| Blank Pt% / As on some piers | Expected — those piers are governed by `As_min`. Tick *Plot minimum-governed values* to show them. |
| Very slow first connection | `comtypes` is generating its ETABS type-library wrapper. One-time cost. |
| Excel export stalls | It is `pip install`-ing `openpyxl` in the background. Install it up front instead. |
