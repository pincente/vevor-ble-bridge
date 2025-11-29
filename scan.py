import argparse
from bluepy.btle import Scanner, DefaultDelegate


class ScanDelegate(DefaultDelegate):
    def __init__(self):
        DefaultDelegate.__init__(self)

    def handleDiscovery(self, dev, isNewDev, isNewData):
        if isNewDev:
            print("Discovered device", dev.addr)
        elif isNewData:
            print("Received new data from", dev.addr)

parser = argparse.ArgumentParser(description="Scan for nearby BLE devices")
parser.add_argument("--timeout", type=float, default=10.0, help="Scan duration in seconds")
parser.add_argument(
    "--name",
    type=str,
    help="Filter results to devices whose name or advertisement contains this text",
)
args = parser.parse_args()

print("Creating scanner...")
scanner = Scanner().withDelegate(ScanDelegate())
print(f"Scanning for {args.timeout} seconds...")
devices = scanner.scan(args.timeout)
print("Finished.")

matches = []
for dev in devices:
    name = dev.getValueText(9) or ""
    adv_fields = dev.getScanData()
    advert_text = " ".join([value for _, _, value in adv_fields])
    name_match = False
    if args.name:
        name_match = args.name.lower() in dev.addr.lower() or args.name.lower() in name.lower() or args.name.lower() in advert_text.lower()
    if not args.name or name_match:
        matches.append(dev)
    print(f"Device {dev.addr} ({dev.addrType}), RSSI={dev.rssi} dB")
    for adtype, desc, value in dev.getScanData():
        print(f"{desc} = {value}")
    if name:
        print(f"Name = {name}")

if matches and args.name:
    selected = matches[0]
    print("\nSuggested .env snippet for the first match:")
    print(f"BLE_MAC_ADDRESS={selected.addr}")
    print("BLE_PASSKEY=1234  # adjust if your heater uses a different key")
