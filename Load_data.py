# %%
# =====================================================================
#  Load_data.py -- ETABS Load Data Extraction
#  Run cells one by one (Ctrl+Enter) in VS Code to isolate issues.
# =====================================================================

# %%
# =================================================================
# CELL 1: CONNECT TO ETABS -- Find PID & attach
# =================================================================
import comtypes.client
import psutil

SapModel = None
myETABSObject = None

# --- Find ETABS processes ---
etabs_processes = [
    p.info for p in psutil.process_iter(['pid', 'name'])
    if p.info['name'] and 'ETABS' in p.info['name'].upper()
]
print(f"ETABS processes found: {etabs_processes}")

if not etabs_processes:
    raise RuntimeError("[FAIL] No running ETABS.exe process found. Open ETABS first!")

pid = etabs_processes[0]['pid']
print(f"Using PID: {pid}")

# --- Connect using the exact pattern that worked in Pier_properties.py ---
helper = comtypes.client.CreateObject('ETABSv1.Helper')
print(f"  Helper created: {helper}")

helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
print(f"  Helper QI'd to cHelper: {helper}")

myETABSObject = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", pid)
print(f"  GetObjectProcess returned: {myETABSObject}")

if myETABSObject is None:
    print("[WARN] GetObjectProcess returned None. Trying GetObject (no PID)...")
    try:
        myETABSObject = helper.GetObject("CSI.ETABS.API.ETABSObject")
        print(f"  GetObject returned: {myETABSObject}")
    except Exception as e:
        print(f"  GetObject also failed: {e}")

if myETABSObject is None:
    raise RuntimeError(
        f"[FAIL] Could not attach to ETABS PID {pid}.\n"
        "  Make sure ETABS is fully loaded (not just the splash screen).\n"
    )

myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
SapModel = myETABSObject.SapModel

filepath = SapModel.GetModelFilename()
print(f"\n[OK] Attached to ETABS!")
print(f"  Active file: {filepath or '(no file open)'}")


# %%
# =================================================================
# CELL 2: LOAD DATA INVENTORY -- Patterns, Cases, and Combos
# =================================================================
print("=" * 60)
print("  LOAD DATA INVENTORY")
print("=" * 60)

# --- Helper: parse GetNameList return ---
def parse_namelist(ret):
    """Parse GetNameList return tuple. Returns (success:bool, count:int, names:list)."""
    if ret is None or len(ret) < 2:
        return False, 0, []

    # Format A: (retcode=0, count, names_array)
    if len(ret) >= 3 and ret[0] == 0 and isinstance(ret[1], int):
        names = list(ret[2]) if ret[2] else []
        return True, ret[1], names

    # Format B: (count, names_array)
    if isinstance(ret[0], int) and ret[0] > 0:
        names = list(ret[1]) if ret[1] else []
        return True, ret[0], names

    # Format A with error: retcode != 0
    if len(ret) >= 3 and isinstance(ret[0], int) and ret[0] != 0:
        return False, 0, []

    # Count = 0 (empty list)
    if ret[0] == 0:
        return True, 0, []

    return False, 0, []


# --- 1. Load Patterns ---
print("\n  --- Load Patterns ---")
try:
    ret_lp = SapModel.LoadPatterns.GetNameList(0, [])
    ok, n_lp, lp_names = parse_namelist(ret_lp)
    if ok:
        print(f"  Found {n_lp} Load Patterns:")
        for lp in lp_names:
            print(f"    - {lp}")
    else:
        print(f"  [WARN] LoadPatterns.GetNameList failed (raw={ret_lp})")
except Exception as e:
    print(f"  [FAIL] Could not get Load Patterns: {e}")

# --- 2. Load Cases ---
print("\n  --- Load Cases ---")
try:
    ret_lc = SapModel.LoadCases.GetNameList(0, [])
    ok, n_lc, lc_names = parse_namelist(ret_lc)
    if ok:
        print(f"  Found {n_lc} Load Cases:")
        for lc in lc_names:
            print(f"    - {lc}")
    else:
        print(f"  [WARN] LoadCases.GetNameList failed (raw={ret_lc})")
except Exception as e:
    print(f"  [FAIL] Could not get Load Cases: {e}")

# --- 3. Load Combinations ---
print("\n  --- Load Combinations ---")
combo_data = {}

try:
    ret_combos = SapModel.RespCombo.GetNameList(0, [])
    ok, n_combos, combo_names = parse_namelist(ret_combos)
    
    if ok:
        print(f"  Found {n_combos} Load Combinations:")
        
        for combo in combo_names:
            print(f"\n    Combo: '{combo}'")
            try:
                # 3a. Get combination type
                ret_type = SapModel.RespCombo.GetTypeOAPI(combo, 0)
                
                # Parse return tuple based on typical COM mappings
                if isinstance(ret_type, (list, tuple)):
                    if len(ret_type) >= 2 and ret_type[0] == 0:
                        combo_type = ret_type[1]
                    else:
                        combo_type = ret_type[0]
                else:
                    combo_type = ret_type
                    
                type_map = {
                    0: "Linear Add", 
                    1: "Envelope", 
                    2: "Absolute Add", 
                    3: "SRSS", 
                    4: "Range Add"
                }
                type_str = type_map.get(combo_type, f"Unknown ({combo_type})")
                print(f"      Type: {type_str}")

                # 3b. Get load cases/combos inside this combination
                ret_cases = SapModel.RespCombo.GetCaseList(combo, 0, [], [], [])
                
                if ret_cases is not None and len(ret_cases) >= 4:
                    if len(ret_cases) >= 5 and ret_cases[0] == 0 and isinstance(ret_cases[1], int):
                        count = ret_cases[1]
                        c_types = ret_cases[2]
                        c_names = ret_cases[3]
                        sfs = ret_cases[4]
                    else:
                        count = ret_cases[0]
                        c_types = ret_cases[1]
                        c_names = ret_cases[2]
                        sfs = ret_cases[3]
                        
                    combo_data[combo] = {
                        "type": type_str,
                        "items": []
                    }
                    
                    eq_parts = []
                    for i in range(count):
                        item_type = "Combo" if c_types[i] == 1 else "Load Case"
                        item_name = c_names[i]
                        sf = float(sfs[i])
                        
                        combo_data[combo]["items"].append({
                            "type": item_type,
                            "name": item_name,
                            "sf": sf
                        })
                        
                        # Format sf nicely (e.g., 1.5 instead of 1.5000)
                        sf_str = f"{sf:g}"
                        eq_parts.append(f"{sf_str}{item_name}")
                        
                    combo_eq = " + ".join(eq_parts)
                    combo_data[combo]["equation"] = combo_eq
                    print(f"        Equation: {combo} ==> {combo_eq}")
                else:
                    print(f"      [WARN] Could not get case list for combo '{combo}'. Return: {ret_cases}")
            except Exception as e:
                print(f"      [FAIL] Error getting details for combo '{combo}': {e}")
    else:
        print(f"  [WARN] RespCombo.GetNameList failed (raw={ret_combos})")
except Exception as e:
    print(f"  [FAIL] Could not get Load Combinations: {e}")

# --- 4. Summary Table ---
if combo_data:
    print("\n" + "=" * 80)
    print("  LOAD COMBINATIONS SUMMARY TABLE")
    print("=" * 80)
    print(f"  {'Combo Name':<25} | {'Type':<15} | {'Equation'}")
    print("  " + "-" * 78)
    for combo, data in combo_data.items():
        print(f"  {combo:<25} | {data['type']:<15} | {data.get('equation', '')}")

print("\n" + "=" * 80)
print("  DATA EXTRACTION COMPLETE")
print("=" * 80)

# %%
