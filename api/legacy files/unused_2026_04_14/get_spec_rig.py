import pyvisa

def get_controller_specs(resource_address):
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(resource_address)
    specs = inst.query("*IDN?")
    print("Controller specs:", specs)
    inst.close()
    return specspython 


get_controller_specs("192.168.1.250", 5001)