import pyvisa

rm = pyvisa.ResourceManager()
resources = rm.list_resources()

print("Connected device resource addresses:")
if resources:
    for resource in resources:
        print(resource)
else:
    print("No VISA devices detected. Please check the connection.")
