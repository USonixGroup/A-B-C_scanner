# # Standard imports
# import time
# import socket

# # === Custom modules ===
# from rig_function import send_command, enable_axis, wait_until_stopped

# # ✅ Configuration import
# from Setup import A_SCAN_PARAMS

# HOST = "192.168.1.250"
# PORT = 5001

# # ✅ Utility functions
def mm_to_pulse(mm):
    return int(round(mm * 5000))


# def main():
#     """Perform the A-scan movement. This function defers heavy/hardware
#     imports until runtime so the module is safe to import from a GUI.
#     """
#     # defer imports that touch instruments
#     try:
#         import importlib
#         pm = importlib.import_module('pymeasure.instruments.agilent')
#         Agilent33500 = getattr(pm, 'Agilent33500', None)
#         try:
#             ResourceManager = getattr(importlib.import_module('pyvisa'), 'ResourceManager', None)
#         except Exception:
#             ResourceManager = None
#         # If drivers are missing we continue — move still uses rig_function.
#     except Exception:
#         Agilent33500 = None
#         ResourceManager = None

#     # Convert configured mm values to pulses (in-place)
#     try:
#         if "X" in A_SCAN_PARAMS:
#             A_SCAN_PARAMS["X"] = mm_to_pulse(A_SCAN_PARAMS["X"])
#         if "Y" in A_SCAN_PARAMS:
#             A_SCAN_PARAMS["Y"] = mm_to_pulse(A_SCAN_PARAMS["Y"])
#         if "Z" in A_SCAN_PARAMS:
#             A_SCAN_PARAMS["Z"] = mm_to_pulse(A_SCAN_PARAMS["Z"])
#     except Exception:
#         # guard against missing keys or bad values
#         pass

#     # Establish TCP connection and move axes
#     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
#         sock.connect((HOST, PORT))

#         # Set incremental mode
#         send_command(sock, "INC")

#         # Move X/Y/Z if provided
#         for axis in ["X", "Y", "Z"]:
#             distance = A_SCAN_PARAMS.get(axis)
#             if distance:
#                 enable_axis(sock, axis)
#                 send_command(sock, f"{axis}{distance}")
#                 wait_until_stopped(sock, axis)

#     print("✅ A Scan movement complete.")


# if __name__ == "__main__":
#     main()
