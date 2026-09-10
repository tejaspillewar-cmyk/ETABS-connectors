import comtypes.client
import psutil

# Find ETABS
etabs_processes = [p.info for p in psutil.process_iter(['pid', 'name']) if p.info['name'] and 'ETABS' in p.info['name'].upper()]
if not etabs_processes:
    print("No ETABS found")
    exit(1)

import comtypes.gen.ETABSv1
helper = comtypes.client.CreateObject('ETABSv1.Helper')
helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)

myETABSObject = None
for p in etabs_processes:
    pid = p['pid']
    print(f"Trying PID {pid}...")
    obj = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", pid)
    if obj:
        myETABSObject = obj
        break

if not myETABSObject:
    print("Failed to get object process from any PID")
    
    print("\n--- eReturnCode constants anyway ---")
    results = []
    for name in dir(comtypes.gen.ETABSv1):
        if name.startswith('eReturnCode'):
            val = getattr(comtypes.gen.ETABSv1, name)
            results.append((name, val))

    target = 132
    print(f"Flags that sum to {target}:")
    for name, val in results:
        if val != 0 and (target & val) == val:
            print(f"  {name} = {val}")
    exit(1)

myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
SapModel = myETABSObject.SapModel
print("Attached to:", SapModel.GetModelFilename())

print("\n--- Testing GetNameList ---")
try:
    ret = SapModel.RespCombo.GetNameList()
    print("GetNameList() ->", ret)
except Exception as e:
    print("GetNameList() error:", e)

try:
    ret = SapModel.RespCombo.GetNameList(0, [])
    print("GetNameList(0, []) ->", ret)
except Exception as e:
    print("GetNameList(0, []) error:", e)

print("\n--- eReturnCode constants ---")
results = []
for name in dir(comtypes.gen.ETABSv1):
    if name.startswith('eReturnCode'):
        val = getattr(comtypes.gen.ETABSv1, name)
        results.append((name, val))

target = 132
print(f"Flags that sum to {target}:")
for name, val in results:
    if val != 0 and (target & val) == val:
        print(f"  {name} = {val}")

