# ETABS Connectors

A collection of Python scripts and tools designed to interface directly with the **CSI ETABS COM API**. These scripts automate the extraction of structural data, geometry, load combinations, and perform complex engineering checks.

## Key Features

- Connect to live ETABS sessions or launch new ones via COM.
- Auto-detect ETABS objects and API signature formats (`(count, array)` vs `(retcode, count, array)`).
- Perform deep geometric analysis of piers, frames, and areas.
- **FDR Tool**: An automated Flexural Design Review calculator.

## Files Description

### Core Automation & Extraction Tools

- **`Etabs_live.py`**  
  A foundational module that handles the boilerplate connection to ETABS. It searches for an active ETABS process to attach to using `comtypes`, or launches a new hidden/visible instance if necessary. Provides a live `SapModel` COM object for other scripts to use.

- **`fdr_tool.py`**  
  The **Flexural Design Review (FDR) Tool**. It connects to a running model and pulls massive amounts of data (pier forces, geometries, story information). It automatically performs Capacity/Demand checks (0.4fck and 0.2fck limits) for shear walls/piers. Outputs include comprehensive **Excel reports** and **AutoCAD scripts (.scr)** for plotting reinforcement labels.

- **`Fin_test.py`**  
  A robust, step-by-step diagnostic and extraction script. It:
  1. Validates different methods of ETABS API attachment (`GetActiveObject` vs PID-based `GetObjectProcess`).
  2. Extracts an entire model inventory, classifying Frame elements (Beams vs Columns vs Braces) and Area elements (Walls vs Floors).
  3. Extracts geometric coordinates and sizes for Piers by analyzing bounding boxes of their constituent Area objects, extracting the `(X, Y)` coordinates for the Pier centerlines.

- **`etabs_gui.py` / `etabs_gui.pyw`**  
  A PyQt/Tkinter graphical interface wrapper allowing users to operate the ETABS API extraction routines without needing to interact with the Python terminal directly.

### Debugging & Test Scripts

These scripts are primarily used for reverse-engineering and validating the behavior of the ETABS COM `comtypes` wrapper, particularly regarding `[out]` and `[ref]` parameters.

- **`debug.py`**  
  Used to introspect `comtypes.gen.ETABSv1` and isolate API connection instability issues or enumerations (like `eReturnCode`).

- **`test_getnamelist.py`**  
  A targeted test script to verify exactly how `GetNameList()` methods unpack in Python tuples. Identifies whether the COM interop returns `(count, names)` or `(retcode, count, names)` depending on the CSI class.

- **`test_connector.py`**  
  A simpler connection test script to quickly assert if Python can successfully grab the ETABS OAPI interface.

## Prerequisites

- **Python 3.10+**
- **comtypes** (`pip install comtypes`)
- **psutil** (`pip install psutil`)
- **pandas** (`pip install pandas`)
- **ETABS** (Must be installed and licensed on the machine running the code).

## Usage

1. Open ETABS and load your `.EDB` model file. Run the structural analysis so that forces are available.
2. In your Python environment, run the scripts directly. For example:
   ```bash
   python fdr_tool.py
   # or
   python Fin_test.py
   ```
3. Check the command-line output (or the generated Excel files) for extracted structural results.
