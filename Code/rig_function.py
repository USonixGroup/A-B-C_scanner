import os
import socket
import time

# Device IP and port
DEFAULT_HOST = "192.168.1.250"
DEFAULT_PORT = 5001
HOST = os.getenv("RIG_HOST", DEFAULT_HOST)
PORT = int(os.getenv("RIG_PORT", str(DEFAULT_PORT)))


def connect_to_rig(host=HOST, port=PORT, timeout=5.0, log_func=print):
    """Open a TCP connection to the rig controller."""
    log_func(f"Connecting to rig at {host}:{port}...")
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    log_func("✅ Rig connection established.")
    return sock

def send_command(sock, command):
    """
    Send a command to PMX-4ET-SA and receive a response.
    """
    command += '\x00'  # Null terminator
    if sock is None:
        raise ValueError("A connected socket is required before sending commands.")

    sock.sendall(command.encode())
    response = sock.recv(1024).decode(errors="ignore").strip('\x00')
    # print(f"Command: {command.strip()}, Response: {response}")
    return response


def check_connection(host=HOST, port=PORT, timeout=5.0, log_func=print):
    """Verify that the rig is reachable over TCP without sending a motion command."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            log_func(f"✅ Rig reachable at {host}:{port}")
            return True
    except OSError as exc:
        log_func(f"⚠️ Unable to reach rig at {host}:{port}: {exc}")
        return False

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
def is_axis_moving(sock, axis, log_func=print):
    speed_map = {"X": 0, "Y": 1, "Z": 2}

    while True:
        speeds = send_command(sock, "PS")
        if speeds.strip() == "OK":
            log_func("⚠️ `PS` returned OK, retrying...")
            time.sleep(1)
            continue

        try:
            speed_values = [int(v) for v in speeds.split(":")]
            return speed_values[speed_map[axis]] != 0
        except ValueError:
            log_func(f"⚠️ Invalid `PS` response: {speeds}, retrying...")
            time.sleep(1)
            continue

# Wait until the given axis has stopped
def wait_until_stopped(sock, axis, timeout=10, log_func=print):
    start_time = time.time()
    
    while is_axis_moving(sock, axis, log_func=log_func):
        time.sleep(0.5)
        if time.time() - start_time > timeout:
            log_func(f"⚠️ {axis}-axis did not stop within timeout!")
            return False

    log_func(f"✅ {axis}-axis has stopped.")
    return True

# Move X/Y/Z to a specified absolute position
def move_to_position(sock, x=None, y=None, z=None, log_func=print):
    # Always set move mode to INC before moving
    send_command(sock, "INC")
    if x is not None and x != 0:
        enable_axis(sock, "X")
        send_command(sock, f"X{int(round(x))}")
        wait_until_stopped(sock, "X", log_func=log_func)
    if y is not None and y != 0:
        enable_axis(sock, "Y")
        send_command(sock, f"Y{int(round(y))}")
        wait_until_stopped(sock, "Y", log_func=log_func)
    if z is not None and z != 0:
        enable_axis(sock, "Z")
        send_command(sock, f"Z{int(round(z))}")
        wait_until_stopped(sock, "Z", log_func=log_func)

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

