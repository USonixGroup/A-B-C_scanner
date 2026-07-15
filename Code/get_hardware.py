import pyvisa

def check_agilent_connection():
    # Initialize the Resource Manager with the Python backend
    rm = pyvisa.ResourceManager('@py')
    
    print("--- Scanning for Instruments ---")
    
    # Get all available resources
    resources = rm.list_resources()
    
    if not resources:
        print("❌ No devices detected. Check physical connections.")
        return

    for res in resources:
        connection_type = "USB" if "USB" in res else "LAN" if "TCPIP" in res else "Other"
        
        print(f"\nFound {connection_type} Resource: {res}")
        
        try:
            # Try to connect and identify the device
            inst = rm.open_resource(res, timeout=2000)
            idn = inst.query("*IDN?")
            
            # Check if 'Agilent' or 'Keysight' is in the ID string
            if "Agilent" in idn or "Keysight" in idn:
                print(f"✅ DETECTED: Agilent/Keysight Device")
                print(f"   Identity: {idn.strip()}")
            else:
                print(f"⚠️  Detected non-Agilent device: {idn.strip()}")
                
            inst.close()
            
        except Exception as e:
            print(f"❌ Could not communicate with {res}")
            print(f"   Error: {e}")

if __name__ == "__main__":
    check_agilent_connection()



# import pyvisa
# rm = pyvisa.ResourceManager()
# inst = rm.open_resource('YOUR_VISA_ADDRESS')
# print(inst.query('*IDN?'))
# inst.write('OUTP ON')