# ▶️ 信号参数
SIGNAL_PARAMS = {
    "shape": "SIN",
    "frequency": 1000000,
    "amplitude": 1,
    "burst_ncycles": 1,
}

# ▶️ 扫描参数for A scan（单位为 mm））
A_SCAN_PARAMS = {
    "X": 0,       # mm
    "Y": 0,          # mm
    "Z": 0,       # mm
    "mode": "INC" 
}

# ▶️ 扫描参数for B scan（单位为 mm）
SCAN_PARAMS = {
    "scan_axis": "X",
    "cross_axis": "Z",
    "scan_length": 20,       # mm
    # "scan_step": 0.2,         # mm
    "scan_points": 2,
    "cross_length": 20,      # mm
    # "cross_step": 0.2,        # mm
    "cross_points": 2,
    "start_col": 2,
}


# ▶️ 扫描装置 TCP 地址
HOST = "192.168.1.250"
PORT = 5001

# ▶️ 信号发生器 VISA 地址
SG_ADDRESS = "USB0::0x0957::0x0407::MY44023097::INSTR" # example: "USB0::0x0957::0x1607::MY50004553::INSTR"

# ▶️ 示波器 VISA 地址
OSC_ADDRESS = "USB0::0x05FF::0x1023::3502N04522::INSTR" # example: USB0::0x05FF::0x1023::3557N06479::INSTR

