import os
import socket
import time

# Device IP and port
DEFAULT_HOST = "192.168.1.250"
DEFAULT_PORT = 5001
HOST = os.getenv("RIG_HOST", DEFAULT_HOST)
PORT = int(os.getenv("RIG_PORT", str(DEFAULT_PORT)))
DEFAULT_STOP_TIMEOUT = float(os.getenv("RIG_STOP_TIMEOUT", "30"))
DEFAULT_MOVE_SETTLE_SECONDS = float(os.getenv("RIG_MOVE_SETTLE_SECONDS", "3.0"))


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


def _parse_axis_value(response, axis):
    """Parse controller responses that may be single-value or multi-axis."""
    axis_index = {"X": 0, "Y": 1, "Z": 2}
    axis = str(axis).upper()
    if axis not in axis_index:
        raise ValueError(f"Unsupported axis: {axis}")

    raw = str(response).strip()
    if not raw:
        raise ValueError("Empty controller response")

    # Common case: PE/PP reply is already one integer for one axis.
    try:
        return int(raw)
    except ValueError:
        pass

    # Fallback: parse colon-delimited payloads such as "0:357:-10439:0".
    values = []
    for token in raw.split(":"):
        token = token.strip()
        if not token:
            continue
        try:
            values.append(int(token))
        except ValueError:
            continue

    if len(values) >= 4:
        # Replies can include a leading status field before X/Y/Z.
        return values[axis_index[axis] + 1]
    if len(values) >= 3:
        return values[axis_index[axis]]

    raise ValueError(f"Unable to parse axis value from response: {response!r}")

# Read encoder (PE) and pulse position (PP)
def get_position(sock, axis):
    encoder_pos = _parse_axis_value(send_command(sock, f"PE{axis}"), axis)
    pulse_pos = _parse_axis_value(send_command(sock, f"PP{axis}"), axis)
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
    enable_axis(sock, axis)
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
    time.sleep(DEFAULT_MOVE_SETTLE_SECONDS)

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
            if len(speed_values) >= 4:
                # Some controllers prepend a status field before X/Y/Z speeds.
                axis_speed = speed_values[speed_map[axis] + 1]
            elif len(speed_values) >= 3:
                axis_speed = speed_values[speed_map[axis]]
            else:
                raise ValueError(f"Unexpected PS field count: {len(speed_values)}")
            return axis_speed != 0
        except ValueError:
            log_func(f"⚠️ Invalid `PS` response: {speeds}, retrying...")
            time.sleep(1)
            continue

# Wait until the given axis has stopped
def wait_until_stopped(
    sock,
    axis,
    timeout=DEFAULT_STOP_TIMEOUT,
    log_func=print,
    stable_reads_required=3,
    pulse_stable_reads_required=3,
    pulse_tolerance=1,
):
    start_time = time.time()
    stable_reads = 0
    pulse_stable_reads = 0
    last_pulse = None

    while True:
        moving = is_axis_moving(sock, axis, log_func=log_func)
        pulse_is_stable = False

        try:
            _, current_pulse = get_position(sock, axis)
            if last_pulse is not None:
                if abs(int(current_pulse) - int(last_pulse)) <= pulse_tolerance:
                    pulse_stable_reads += 1
                else:
                    pulse_stable_reads = 0
            last_pulse = int(current_pulse)
            pulse_is_stable = pulse_stable_reads >= pulse_stable_reads_required
        except (ValueError, OSError, TimeoutError) as exc:
            # Keep operating with PS-only fallback if pulse read briefly fails.
            log_func(f"⚠️ {axis}-axis pulse read failed during stop check: {exc}")
            pulse_stable_reads = 0

        if moving:
            stable_reads = 0
        else:
            stable_reads += 1
            if stable_reads >= stable_reads_required and pulse_is_stable:
                log_func(f"✅ {axis}-axis has stopped.")
                return True

        if time.time() - start_time > timeout:
            log_func(f"⚠️ {axis}-axis did not stop within timeout!")
            return False

        time.sleep(0.25)


def wait_until_target_reached(
    sock,
    axis,
    target_position,
    timeout=DEFAULT_STOP_TIMEOUT,
    log_func=print,
    tolerance=2,
    stable_reads_required=3,
    poll_interval=0.25,
):
    """Wait until axis motion stops, then verify encoder target reached stably."""
    start_time = time.time()
    stop_reads = 0
    position_stable_reads = 0
    last_verified_position = None

    while True:
        moving = is_axis_moving(sock, axis, log_func=log_func)
        if moving:
            stop_reads = 0
            position_stable_reads = 0
            last_verified_position = None
        else:
            stop_reads += 1
            if stop_reads >= stable_reads_required:
                try:
                    current_position, _ = get_position(sock, axis)
                except (ValueError, OSError, TimeoutError) as exc:
                    log_func(f"⚠️ {axis}-axis position read failed during target check: {exc}")
                    current_position = None

                if current_position is not None:
                    err = abs(int(current_position) - int(target_position))
                    if err <= tolerance:
                        if (
                            last_verified_position is not None
                            and abs(int(current_position) - int(last_verified_position)) <= tolerance
                        ):
                            position_stable_reads += 1
                        else:
                            position_stable_reads = 1
                        last_verified_position = int(current_position)
                        if position_stable_reads >= stable_reads_required:
                            log_func(
                                f"✅ {axis}-axis reached target encoder position {int(target_position)} (current={int(current_position)})."
                            )
                            return True
                    else:
                        position_stable_reads = 0
                        last_verified_position = int(current_position)

        if time.time() - start_time > timeout:
            cur_txt = (
                "unknown"
                if last_verified_position is None
                else str(int(last_verified_position))
            )
            log_func(
                f"⚠️ {axis}-axis did not reach target within timeout! "
                f"target={int(target_position)}, current={cur_txt}"
            )
            return False

        time.sleep(poll_interval)

# Move X/Y/Z to a specified absolute position
def move_to_position(sock, x=None, y=None, z=None, log_func=print):
    should_move = any(value is not None and value != 0 for value in (x, y, z))

    if not should_move:
        return True

    send_command(sock, "INC")
    moved_axes = []
    if x is not None and x != 0:
        enable_axis(sock, "X")
        send_command(sock, f"X{int(round(x))}")
        moved_axes.append("X")
    if y is not None and y != 0:
        enable_axis(sock, "Y")
        send_command(sock, f"Y{int(round(y))}")
        moved_axes.append("Y")
    if z is not None and z != 0:
        enable_axis(sock, "Z")
        send_command(sock, f"Z{int(round(z))}")
        moved_axes.append("Z")

    for axis in moved_axes:
        wait_until_stopped(sock, axis, log_func=log_func)

    return True

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

