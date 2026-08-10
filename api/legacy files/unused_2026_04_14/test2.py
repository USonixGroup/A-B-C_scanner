import pyvisa
import numpy as np
import matplotlib.pyplot as plt
import struct
import time


def test_lecroy_connection(ip_address):
    rm = pyvisa.ResourceManager()
    # Ensure we use TCPIP for LXI mode
    resource_string = f"TCPIP0::{ip_address}::INSTR"
    
    print(f"--- Connecting to {resource_string} ---")
    
    try:
        # 1. Open Connection
        scope = rm.open_resource(resource_string)
        
        # 2. Set aggressive timeouts and buffers for HDO data
        scope.timeout = 30000        # 30 seconds for large waveforms
        scope.chunk_size = 10*1024*1024  # 10 MB chunk size
        
        # 3. Basic Communication Check
        idn = scope.query("*IDN?")
        print(f"Connected to: {idn.strip()}")
        
        # 4. Preparation: Stop the scope to ensure data is stable
        print("Stopping acquisition to read buffer...")
        scope.write("STOP")
        time.sleep(0.5) # Give it a moment to settle
        
        # 5. Clear any previous buffer errors
        scope.clear()
        
        # 6. Waveform Data Request
        # Using read_raw() is CRITICAL for binary waveform data
        print("Requesting Channel 2 waveform (C4:WF? ALL)...")
        scope.write("C4:WF? ALL")
        
        # Read the raw byte stream
        raw_data = scope.read_raw()
        
        # 7. Verification
        data_len = len(raw_data)
        if data_len > 0:
            print(f"✅ Success! Captured {data_len} bytes.")
            # LeCroy HDO headers usually start with 'DAT1' or a descriptor block
            print(f"Header preview: {raw_data[:15]}")
        else:
            print("❌ Read completed but returned 0 bytes.")

    except pyvisa.errors.VisaIOError as e:
        print(f"❌ VISA Error: {e}")
        if "Timeout" in str(e):
            print("Try: Reducing the 'Max Points' on the scope or increasing timeout further.")
            
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")
        
    finally:
        try:
            scope.close()
            print("Connection closed.")
        except:
            pass


def get_lecroy_data(ip):
    scope = None 
    rm = pyvisa.ResourceManager()
    
    try:
        scope = rm.open_resource(f"TCPIP::{ip}::INSTR")
        scope.timeout = 20000
        scope.write("COMM_HEADER OFF")
        scope.write("COMM_FORMAT DEF9,WORD,BIN")
        
        print("Requesting Waveform...")
        scope.write("C1:WF? ALL")

        raw = b""
        while True:
            try:
                chunk = scope.read_raw()
                raw += chunk
            except pyvisa.errors.VisaIOError as e:
                # If we timeout but have data, we likely hit the end
                break
        
        if not raw:
            raise Exception("No data received from scope.")
        
        # Locate Header
        wd_start = raw.find(b'WAVEDESC')
        
        # Extract Scaling & Lengths
        v_gain = struct.unpack("<f", raw[wd_start + 156 : wd_start + 160])[0]
        v_off = struct.unpack("<f", raw[wd_start + 160 : wd_start + 164])[0]
        h_int = struct.unpack("<f", raw[wd_start + 176 : wd_start + 180])[0]
        h_off = struct.unpack("<d", raw[wd_start + 180 : wd_start + 188])[0]
        
        desc_len = struct.unpack("<i", raw[wd_start + 36 : wd_start + 40])[0]
        text_len = struct.unpack("<i", raw[wd_start + 40 : wd_start + 44])[0]
        num_points = struct.unpack("<i", raw[wd_start + 60 : wd_start + 64])[0]
        
        # The FIX: Use 'count' to ignore trailing bytes
        data_start = wd_start + desc_len + text_len
        adc = np.frombuffer(raw, dtype='<i2', count=num_points, offset=data_start)
        
        # Convert to Volts/Time
        volts = (adc * v_gain) - v_off
        time = np.arange(num_points) * h_int + h_off
        
        print(f"Success! Plotting {num_points} points.")
        plt.plot(time, volts)
        plt.grid(True)
        plt.show()

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if scope:
            scope.close()

# get_lecroy_data("192.168.1.200")

# CHANGE THIS to your scope's actual IP address
test_lecroy_connection("192.168.1.200")
