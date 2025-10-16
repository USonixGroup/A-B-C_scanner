import socket
from rig_function import send_command, enable_axis, wait_until_stopped
# ✅ Configuration import
from Setup import A_SCAN_PARAMS, HOST, PORT

# Note: we avoid creating instrument connections at import time so this
# module is safe to import. The signal generator is not required by
# the a_scan() function below and should be constructed by callers if
# needed.

# ✅ mm → pulse conversion function
def mm_to_pulse(mm):
    return int(mm * 1000)

def a_scan(sock, x, y, z, mode):
    send_command(sock, mode)
    enable_axis(sock, "X")
    enable_axis(sock, "Y")
    enable_axis(sock, "Z")

    print(f"🚀 Moving to A Scan position: X={x}, Y={y}, Z={z}")
    send_command(sock, f"X{x}")
    wait_until_stopped(sock, "X")
    send_command(sock, f"Y{y}")
    wait_until_stopped(sock, "Y")
    send_command(sock, f"Z{z}")
    wait_until_stopped(sock, "Z")

    print("✅ A Scan complete.")

def main():
    # ✅ Convert mm units to pulse
    x_pulse = mm_to_pulse(A_SCAN_PARAMS.get("x", 0))
    y_pulse = mm_to_pulse(A_SCAN_PARAMS.get("y", 0))
    z_pulse = mm_to_pulse(A_SCAN_PARAMS.get("z", 0))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((HOST, PORT))
        a_scan(sock, x=x_pulse, y=y_pulse, z=z_pulse, mode=A_SCAN_PARAMS.get("mode", "INC"))

if __name__ == "__main__":
    main()
