import sys
import logging
import comtypes.client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("etabs_api.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

def start_etabs_direct_path():
    logging.info("Starting script...")
    
    # EXACT path to the ETABS executable based on your folder structure
    # Change to "ETABS 20" if you prefer that version
    etabs_path = r"C:\Program Files\Computers and Structures\ETABS 23\ETABS.exe"
    
    try:
        # 1. Create the helper
        helper = comtypes.client.CreateObject('ETABSv1.Helper')
        helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
        logging.info("Successfully created the ETABS API Helper object.")
    except Exception as e:
        logging.error(f"Failed to create the API Helper: {e}")
        return None
        
    try:
        logging.info(f"ACTION: Forcing Python to launch ETABS directly from: {etabs_path}")
        
        # 2. Launch using the explicit file path, bypassing the broken generic ProgID
        myETABSObject = helper.CreateObject(etabs_path)
        myETABSObject.ApplicationStart()
        logging.info("New ETABS instance launched successfully from the explicit path.")

    except Exception as e:
        logging.error("Failed to launch ETABS from the specified path.")
        logging.error(f"Error details: {e}")
        return None

    try:
        # 3. Cast and Connect to SapModel
        myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
        SapModel = myETABSObject.SapModel
        logging.info("Successfully established connection to SapModel.")
        
        filepath = SapModel.GetModelFilename()
        
        if filepath:
            logging.info(f"Active File: {filepath}")
        else:
            logging.info("ETABS is open and connected, but no file is loaded yet.")
            logging.info("You can now open a file manually in the new window.")

        return SapModel

    except Exception as e:
        logging.error(f"Fatal error connecting to SapModel: {e}")
        return None

if __name__ == "__main__":
    model = start_etabs_direct_path()
    
    if model:
        print("\nSuccess: Connected directly via file path. Python has control.")
    else:
        print("\nProcess terminated with errors. Check logs.")