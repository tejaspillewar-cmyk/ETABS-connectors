# %%
# =====================================================================
#  ETABS Live Connector
#  Reusable connection module for ETABS COM API.
#
#  Usage:
#      from Etabs_live import connect_etabs
#      etabs_obj, SapModel = connect_etabs()
#
#  Or run cells interactively in VS Code (Ctrl+Enter on each # %% block)
# =====================================================================

import comtypes.client

DEFAULT_ETABS_PATH = r"C:\Program Files\Computers and Structures\ETABS 23\ETABS.exe"


def connect_etabs(etabs_path=None, attach_first=True):
    """Connect to ETABS via COM API.

    Parameters
    ----------
    etabs_path : str, optional
        Full path to ETABS.exe. Defaults to ETABS 23 standard install path.
    attach_first : bool, optional
        If True (default), tries to attach to a running ETABS instance
        before launching a new one. Set False to always launch fresh.

    Returns
    -------
    tuple : (myETABSObject, SapModel)
        The ETABS API object and the active SapModel.
    """
    if etabs_path is None:
        etabs_path = DEFAULT_ETABS_PATH

    myETABSObject = None

    if attach_first:
        try:
            myETABSObject = comtypes.client.GetActiveObject(
                "CSI.ETABS.API.ETABSObject"
            )
            print("Attached to running ETABS instance.")
        except Exception:
            myETABSObject = None

    if myETABSObject is None:
        print("Launching new ETABS instance...")
        helper = comtypes.client.CreateObject('ETABSv1.Helper')
        helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
        myETABSObject = helper.CreateObject(etabs_path)
        myETABSObject.ApplicationStart()
        print("ETABS launched.")

    myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
    SapModel = myETABSObject.SapModel

    filepath = SapModel.GetModelFilename()
    if filepath:
        print(f"Active model: {filepath}")
    else:
        print("No file open yet. Open your .edb file in ETABS, then proceed.")

    return myETABSObject, SapModel


# %%
# CELL 1: RUN THIS ONCE TO LAUNCH ETABS (interactive mode)
# ---------------------------------------------------------
# When running cells interactively, this cell calls connect_etabs()
# and stores the result in module-level variables so subsequent cells
# can use SapModel directly.

if __name__ == "__main__":
    _etabs_obj, SapModel = connect_etabs()

# %%
# CELL 2: RUN THIS ANYTIME TO GET DATA
filepath = SapModel.GetModelFilename()
if filepath:
    print(f"Active file: {filepath}")
else:
    print("No file open yet.")

# %%
# CELL 3: EXTRACT STRUCTURAL DATA
num_points = SapModel.PointObj.Count()
num_frames = SapModel.FrameObj.Count()

print("=== MODEL INVENTORY ===")
print(f"Total Nodes (Points): {num_points}")
print(f"Total Frame Elements: {num_frames}")

# %%
# CELL 4: EXTRACT LOAD COMBINATIONS
print("=== LOAD COMBINATIONS ===")
ret = SapModel.RespCombo.GetNameList(0, [])
if ret[0] == 0:
    num_combos = ret[1]
    combo_names = ret[2]
    print(f"Found {num_combos} load combinations:")
    for combo in combo_names:
        print(f"  - {combo}")
else:
    print(f"Failed to get load combinations (ret={ret[0]}). The model might have none defined.")

# %%
