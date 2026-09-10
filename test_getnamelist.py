import comtypes.client
import psutil

etabs_processes = [p.info for p in psutil.process_iter(['pid', 'name']) if p.info['name'] and 'ETABS' in p.info['name'].upper()]
pid = etabs_processes[0]['pid']

import comtypes.gen.ETABSv1
helper = comtypes.client.CreateObject('ETABSv1.Helper')
helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
myETABSObject = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", pid)
myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
SapModel = myETABSObject.SapModel

ret = SapModel.RespCombo.GetNameList()
print("GetNameList() length of tuple:", len(ret))
for i, item in enumerate(ret):
    if isinstance(item, tuple):
        print(f"ret[{i}]: tuple of length {len(item)} (first few: {item[:3]})")
    else:
        print(f"ret[{i}]: {item}")

print("Done.")
