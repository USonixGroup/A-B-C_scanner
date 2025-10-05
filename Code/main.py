import time
from pymeasure.instruments.agilent import Agilent33500
from Signal_function import Continuous_generate, Trigger_generate, Burst_generate, stop_output
from Code.oscilloscope import read_oscilloscope_and_save, send_burst, create_scan_folder
import socket
from rig_function import send_command, enable_axis, wait_until_stopped, b_scan


# ✅ Connect to signal generator
sg = Agilent33500("USB0::0x0957::0x1607::MY50004553::INSTR")
print("✅ Successfully connected to signal generator:", sg.id)

# ✅ IP address and port of the scanning rig
HOST = "192.168.1.250"
PORT = 5001


def main():     
    # Create a new folder for saving scan data
    scan_folder = create_scan_folder()

    # Initialize indices
    cross = 0
    s = 1
    start_col = 2

    # Send initial test burst
    Burst_generate(
        sg,
        shape="SIN",
        frequency=100,
        amplitude=1,
        burst_ncycles=60,
    )
    print("trigger_count")
    time.sleep(1)  
    read_oscilloscope_and_save(cross, s, scan_folder)
    time.sleep(0.5) 
    print(f"📡 Captured data for row {cross+1}, column {s}...")

    # Establish socket connection to the rig
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((HOST, PORT))

        # Scanning parameters
        scan_axis = "X"
        cross_axis = "Z"
        scan_length = 10000
        scan_step = 10000
        cross_length = 10000
        cross_step = 10000
        
        scan_steps = int(scan_length / scan_step)
        cross_steps = int(cross_length / cross_step) + 1

        send_command(sock, "INC")  # Set to incremental mode
        enable_axis(sock, scan_axis)
        enable_axis(sock, cross_axis)

        print(f"🔄 Starting B Scan: {scan_axis}-axis {scan_steps} steps, step size {scan_step}, {cross_axis}-axis {cross_steps} rows, step size {cross_step}")

        for cross in range(cross_steps):  
            print(f"📏 Scanning row {cross+1}/{cross_steps}")

            # Determine scan direction (zig-zag pattern)
            scan_direction = -scan_step if (cross + 1) % 2 == 0 else scan_step 

            for scan in range(scan_steps):
                if cross == 0:
                    s = scan + start_col
                else:
                    s = scan_steps - scan if (cross + 1) % 2 == 0 else scan + start_col 

                # Move scan axis
                send_command(sock, f"{scan_axis}{scan_direction}")
                wait_until_stopped(sock, scan_axis)

                # Trigger signal and read data
                Burst_generate(
                    sg,
                    shape="SIN",
                    frequency=100,
                    amplitude=1,
                    burst_ncycles=60,
                )
                print("trigger_count")
                time.sleep(1)  
                read_oscilloscope_and_save(cross, s, scan_folder)
                time.sleep(0.5) 
                print(f"📡 Captured data for row {cross+1}, column {s}...")

            # Move to next row if not the last one
            if cross < cross_steps - 1:
                cross += 1
                s = scan_steps + 1 if (cross + 1) % 2 == 0 else 1

                # Move cross axis
                send_command(sock, f"{cross_axis}{cross_step}")
                wait_until_stopped(sock, cross_axis)

                # Trigger and capture data after row move
                Burst_generate(
                    sg,
                    shape="SIN",
                    frequency=100,
                    amplitude=1,
                    burst_ncycles=60,
                )
                print("trigger_count")
                time.sleep(1)  
                read_oscilloscope_and_save(cross, s, scan_folder)
                time.sleep(0.5) 
                print(f"📡 Captured data for row {cross+1}, column {s} after row move...")
            
            print(f"🔄")

        print("✅ B Scan complete")

    sg.shutdown()
    print("✅ All tasks completed!")


if __name__ == "__main__":
    main()
