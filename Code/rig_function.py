import socket
import time

# Device IP and port
HOST = "192.168.1.250"
PORT = 5001

def send_command(sock, command):
    """
    Send a command to PMX-4ET-SA and receive a response.
    """
    command += '\x00'  # Null terminator
    sock.sendall(command.encode())
    response = sock.recv(1024).decode().strip('\x00')
    # print(f"Command: {command.strip()}, Response: {response}")
    return response

# Read encoder (PE) and pulse position (PP)
def get_position(sock, axis):
    encoder_pos = int(send_command(sock, f"PE{axis}"))
    pulse_pos = int(send_command(sock, f"PP{axis}"))
    return encoder_pos, pulse_pos

# Enable axis output (EO)
def enable_axis(sock, axis):
    axis_map = {"X": "EO1", "Y": "EO2", "Z": "EO3"}
    if axis in axis_map:
        send_command(sock, f"{axis_map[axis]}=1")
        time.sleep(0.5)

# Set EO to be enabled by default on boot
def set_eo_boot(sock):
    send_command(sock, "EOBOOT=15")  # 0b1111 enables all axes on boot

# Determine homing direction based on current encoder position
def determine_home_direction(sock, axis):
    encoder_pos, _ = get_position(sock, axis)
    return "+" if encoder_pos < 0 else "-"

# Perform homing for a specific axis
def home_axis(sock, axis, mode=0):
    """
    Perform homing:
    - `mode` selects homing method:
      0: Home Input Only (High Speed)
      1: Limit Input Only
      2: Home Input + Z-Index
      3: Z-Index Only
      4: Home Input (High + Low Speed)
    """
    direction = determine_home_direction(sock, axis)
    send_command(sock, f"H{axis}{direction}{mode}")
    time.sleep(5)

    enc, pulse = get_position(sock, axis)
    if abs(enc) < 5 and abs(pulse) < 5:
        print(f"{axis}-axis homing successful!")
    else:
        print(f"{axis}-axis homing failed! Current position: Encoder={enc}, Pulse={pulse}")

# Perform homing for all axes
def home_all_axes(sock, mode=0):
    axes = ["X", "Y", "Z"]
    set_eo_boot(sock)

    for axis in axes:
        enable_axis(sock, axis)

    for axis in axes:
        home_axis(sock, axis, mode)

    print("✅ All axes homed successfully!")

# Move a single axis by a given step
def move_axis(sock, axis, step_size):
    enable_axis(sock, axis)
    initial_enc, initial_pulse = get_position(sock, axis)

    send_command(sock, f"{axis}{step_size}")
    time.sleep(2)

    final_enc, final_pulse = get_position(sock, axis)

    print(f"✅ {axis}-axis step move complete:")
    print(f" - Initial: Encoder={initial_enc}, Pulse={initial_pulse}")
    print(f" - Step: {step_size}")
    print(f" - Final: Encoder={final_enc}, Pulse={final_pulse}")

# Check if axis is still moving based on pulse speed (PS)
def is_axis_moving(sock, axis):
    speed_map = {"X": 0, "Y": 1, "Z": 2}

    while True:
        speeds = send_command(sock, "PS")
        if speeds.strip() == "OK":
            print("⚠️ `PS` returned OK, retrying...")
            time.sleep(0.5)
            continue

        try:
            speed_values = [int(v) for v in speeds.split(":")]
            return speed_values[speed_map[axis]] != 0
        except ValueError:
            print(f"⚠️ Invalid `PS` response: {speeds}, retrying...")
            time.sleep(0.5)
            continue

# Wait until the given axis has stopped
def wait_until_stopped(sock, axis, timeout=10):
    start_time = time.time()
    
    while is_axis_moving(sock, axis):
        time.sleep(0.5)
        if time.time() - start_time > timeout:
            print(f"⚠️ {axis}-axis did not stop within timeout!")
            return False

    print(f"✅ {axis}-axis has stopped.")
    return True

# Move X/Y/Z to a specified absolute position
def move_to_position(sock, x=None, y=None, z=None):
    if x is not None:
        send_command(sock, f"X{x}")
        wait_until_stopped(sock, "X")
    if y is not None:
        send_command(sock, f"Y{y}")
        wait_until_stopped(sock, "Y")
    if z is not None:
        send_command(sock, f"Z{z}")
        wait_until_stopped(sock, "Z")

# Trigger A Scan at a given (x, y, z)
def a_scan(sock, x, y, z, mode):
    send_command(sock, "BF")
    enable_axis(sock, "X")
    enable_axis(sock, "Y")
    enable_axis(sock, "Z")

    print(f"🚀 Moving to A Scan position: X={x}, Y={y}, Z={z}")
    send_command(sock, mode)
    send_command(sock, f"X{x}")
    wait_until_stopped(sock, "X")
    send_command(sock, f"Y{y}")
    wait_until_stopped(sock, "Y")
    send_command(sock, f"Z{z}")
    wait_until_stopped(sock, "Z")

    print("✅ A Scan complete.")

# Perform B Scan (raster scan)
def b_scan(sock, scan_axis=None, cross_axis=None, 
           scan_length=None, scan_step=None, 
           cross_length=None, cross_step=None, 
           fixed_axis=None, fixed_value=None):
    scan_steps = int(scan_length / scan_step)
    cross_steps = int(cross_length / cross_step)
    subsequent_scan_steps = scan_steps - 1

    send_command(sock, "INC")  # Set incremental mode
    enable_axis(sock, scan_axis)
    enable_axis(sock, cross_axis)

    if fixed_axis:
        send_command(sock, f"{fixed_axis}{fixed_value}")
        wait_until_stopped(sock, fixed_axis)

    print(f"🔄 Starting B Scan: {scan_axis} {scan_steps} steps, {scan_step} each | {cross_axis} {cross_steps} lines, {cross_step} each")

    for cross in range(cross_steps):
        print(f"📏 Scanning row {cross + 1}/{cross_steps}")

        current_scan_steps = scan_steps if cross == 0 else subsequent_scan_steps
        scan_direction = scan_step if cross % 2 == 0 else -scan_step

        for _ in range(current_scan_steps):
            send_command(sock, f"{scan_axis}{scan_direction}")
            wait_until_stopped(sock, scan_axis)

        if cross < cross_steps - 1:
            send_command(sock, f"{cross_axis}{cross_step}")
            wait_until_stopped(sock, cross_axis)

    print("✅ B Scan complete.")
