import socket
from pymeasure.instruments.agilent import Agilent33500
from rig_function import send_command, enable_axis, wait_until_stopped
# ✅ Configuration import
from Setup import A_SCAN_PARAMS, HOST, PORT, SG_ADDRESS, SIGNAL_PARAMS

# ✅ Connect to signal generator
sg = Agilent33500(SG_ADDRESS)
print("✅ Signal generator connected:", sg.id)

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
    x_pulse = mm_to_pulse(A_SCAN_PARAMS["x"])
    y_pulse = mm_to_pulse(A_SCAN_PARAMS["y"])
    z_pulse = mm_to_pulse(A_SCAN_PARAMS["z"])

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((HOST, PORT))
        a_scan(sock, x=x_pulse, y=y_pulse, z=z_pulse, mode=A_SCAN_PARAMS["mode"])

if __name__ == "__main__":
    main()
