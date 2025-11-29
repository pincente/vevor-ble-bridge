import argparse
import os
import re
from typing import Dict, List, Optional, Tuple

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
parser.add_argument(
    "--write-env",
    type=str,
    default=None,
    help="Path to write a .env file using .env.sample defaults and the best match",
)
parser.add_argument(
    "--mqtt-host",
    type=str,
    default=None,
    help="Override MQTT_HOST when writing .env",
)
parser.add_argument(
    "--sample",
    type=str,
    default=".env.sample",
    help="Path to the env sample file to copy from when writing .env",
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

def pick_best(devices: List) -> Optional:
    if not devices:
        return None
    return sorted(devices, key=lambda d: d.rssi, reverse=True)[0]


def _parse_kv(line: str) -> Optional[Tuple[str, str]]:
    # Simple KEY=VALUE parser; ignores comments
    if "=" not in line:
        return None
    if line.strip().startswith("#"):
        return None
    key, value = line.split("=", 1)
    val = value.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    return key.strip(), val


def _format_value(val: str) -> str:
    # Keep numbers unquoted, otherwise quote
    if re.fullmatch(r"-?\d+(\.\d+)?", val):
        return val
    return f'"{val}"'


def write_env(
    sample_path: str,
    output_path: str,
    overrides: Dict[str, str],
) -> None:
    lines: List[str] = []
    seen = set()
    if os.path.exists(sample_path):
        with open(sample_path, "r", encoding="ascii") as f:
            for line in f.readlines():
                parsed = _parse_kv(line)
                if parsed:
                    key, _ = parsed
                    if key in overrides:
                        lines.append(f"{key}={_format_value(overrides[key])}\n")
                        seen.add(key)
                        continue
                lines.append(line)
    else:
        print(f"Sample env file not found at {sample_path}; creating minimal .env")

    # Append any missing override keys
    for key, val in overrides.items():
        if key not in seen:
            lines.append(f"{key}={_format_value(val)}\n")
    with open(output_path, "w", encoding="ascii") as f:
        f.writelines(lines)
    print(f"Wrote {output_path} with BLE_MAC_ADDRESS and overrides applied.")


selected = pick_best(matches)

if selected:
    print("\nSuggested .env snippet for the best match:")
    print(f"BLE_MAC_ADDRESS={selected.addr}")
    print("BLE_PASSKEY=1234  # adjust if your heater uses a different key")
else:
    print("\nNo device match found for the given filter.")

if args.write_env:
    overrides = {}
    if selected:
        overrides["BLE_MAC_ADDRESS"] = selected.addr
    if args.mqtt_host:
        overrides["MQTT_HOST"] = args.mqtt_host
    if overrides:
        write_env(args.sample, args.write_env, overrides)
    else:
        print("No overrides available; not writing .env.")
