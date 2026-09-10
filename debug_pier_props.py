import comtypes.client
import sys

def main():
    try:
        myETABSObject = comtypes.client.GetActiveObject("CSI.ETABS.API.ETABSObject")
        SapModel = myETABSObject.SapModel
    except Exception as e:
        print("Failed to attach to ETABS:", e)
        return

    ret = SapModel.PierLabel.GetSectionProperties(
        "P110", 0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], []
    )
    
    if ret[0] == 0:
        ret = ret[1:] # strip retcode
        
    for i, item in enumerate(ret):
        print(f"Index {i}: {type(item)}")
        if isinstance(item, (list, tuple)):
            print(f"  First item: {item[0]}")
        else:
            print(f"  Value: {item}")

if __name__ == "__main__":
    main()
