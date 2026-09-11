import os
import pandas as pd
from fdr_tool import FDRTool, FDRConfig

class MockSapModel:
    def GetModelFilename(self):
        return r"C:\Temp\MockModel.edb"

class MockFDRTool(FDRTool):
    def __init__(self):
        self.SapModel = MockSapModel()
        self.config = FDRConfig()
        
        self._df_raw = pd.DataFrame([
            {'Story': 'Story 1', 'Pier_ID': 'P1', 'Combo': 'DL', 'P': -1000}
        ])
        
        self._df_calc = pd.DataFrame([
            {'Story': 'Story 1', 'Pier_ID': 'P1', 'b': 300, 'd': 1500, 'fck': 30,
             'x1': 0, 'y1': 0, 'x2': 1500, 'y2': 0,
             'Pmin': -1500, 'Combo_Pmin': 'Comb1', 'Pmax': 200, 'Combo_Pmax': 'Comb2',
             'Pmin_NoWind': -1000, 'Combo_Pmin_NoWind': 'Comb1',
             'Pmax_NoWind': 100, 'Combo_Pmax_NoWind': 'Comb2',
             'As_min': 1125, 'Asc': 0, 'Ast': 230, 'As_max': 1125,
             'Pt_percent': 0.25, 'Governing': 'Minimum',
             'Pu_capacity_04': 5400, 'Demand_04': 1000, 'CD_Ratio_04': 5.4, 'Status_04': 'Adequate',
             'Pu_02fck': 2700, 'Demand_02': 1000, 'CD_Ratio_02': 2.7, 'Ductile_Detailing_Reqd': 'No',
             'labelX': 750, 'labelY': -300, 'valueX': 750, 'valueY': -500,
             'asValueX': 750, 'asValueY': -700}
        ])

def run_tests():
    print("Testing mock FDRTool...")
    tool = MockFDRTool()
    
    # Test export_excel
    excel_path = "test_report.xlsx"
    print("Testing Excel export...")
    tool.export_excel(excel_path)
    print("Excel export successful.")
    
    # Test export_dxf
    dxf_path = "test_dxf.dxf"
    print("Testing DXF export...")
    tool.export_dxf(dxf_path, "Story 1")
    print("DXF export successful.")

if __name__ == '__main__':
    run_tests()
