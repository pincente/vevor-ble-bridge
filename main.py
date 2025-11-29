# Vevor BLE Bridge
# 2024 Bartosz Derleta <bartosz@derleta.com>

import logging
import platform
import json
import time
import os
import sys
import argparse
import paho.mqtt.client as mqtt
import vevor

# = Configuration
# Collect globals after config is loaded
client = None
logger = None
vdh = None
run = True
modes = ["Power Level", "Temperature"]
config = {}
bridge_health_topic = ""


def load_config(exit_on_error=True):
    def _fail(msg):
        if exit_on_error:
            print(msg, file=sys.stderr)
            sys.exit(1)
        raise ValueError(msg)

    required = ["BLE_MAC_ADDRESS", "DEVICE_NAME", "DEVICE_MODEL"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        _fail(f"Missing required environment variables: {', '.join(missing)}")

    def _int_env(name, default):
        raw = os.environ.get(name, default)
        try:
            return int(raw)
        except ValueError:
            _fail(f"Invalid integer for {name}: {raw}")

    ble_mac_address = os.environ["BLE_MAC_ADDRESS"]
    device_id = "BYD-" + ble_mac_address.replace(":", "").upper()
    mqtt_prefix_root = os.environ.get("MQTT_PREFIX", "").strip("/")
    mqtt_prefix = f"{mqtt_prefix_root}/{device_id}" if mqtt_prefix_root else device_id

    cfg = {
        "ble_mac_address": ble_mac_address,
        "ble_passkey": _int_env("BLE_PASSKEY", 1234),
        "ble_poll_interval": _int_env("BLE_POLL_INTERVAL", 2),
        "device_name": os.environ["DEVICE_NAME"],
        "device_manufacturer": os.environ.get("DEVICE_MANUFACTURER", "Vevor"),
        "device_model": os.environ["DEVICE_MODEL"],
        "device_id": device_id,
        "via_device": platform.uname()[1],
        "mqtt_host": os.environ.get("MQTT_HOST", "127.0.0.1"),
        "mqtt_username": os.environ.get("MQTT_USERNAME"),
        "mqtt_password": os.environ.get("MQTT_PASSWORD"),
        "mqtt_port": _int_env("MQTT_PORT", 1883),
        "mqtt_discovery_prefix": os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant"),
        "mqtt_prefix": mqtt_prefix,
        "log_level": os.environ.get("LOG_LEVEL", "INFO").upper(),
    }
    return cfg


def init_logger(log_level):
    logger = logging.getLogger("vevor-ble-bridge")
    level_value = getattr(logging, log_level, logging.INFO)
    logger.setLevel(level_value)
    ch = logging.StreamHandler()
    ch.setLevel(level_value)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S %z"
    )
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


def init_client():
    client = mqtt.Client(client_id=config["device_id"], clean_session=True)
    if config["mqtt_username"] and config["mqtt_password"]:
        logger.info(
            f"Connecting to MQTT broker {config['mqtt_username']}@{config['mqtt_host']}:{config['mqtt_port']}"
        )
        client.username_pw_set(config["mqtt_username"], config["mqtt_password"])
    else:
        logger.info(f"Connecting to MQTT broker {config['mqtt_host']}:{config['mqtt_port']}")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(config["mqtt_host"], port=config["mqtt_port"])
    return client


def get_device_conf():
    conf = {
        "name": config["device_name"],
        "identifiers": config["device_id"],
        "manufacturer": config["device_manufacturer"],
        "model": config["device_id"],
        "via_device": config["via_device"],
        "sw": "Vevor-BLE-Bridge",
    }
    return conf


def publish_ha_config():
    client.publish(bridge_health_topic, "online")
    bridge_status_conf = {
        "device": get_device_conf(),
        "device_class": "connectivity",
        "name": "Bridge Status",
        "unique_id": f"{config['device_id']}-bridge",
        "payload_on": "online",
        "payload_off": "offline",
        "state_topic": bridge_health_topic,
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/binary_sensor/{config['device_id']}-bridge/config",
        json.dumps(bridge_status_conf),
    )

    start_conf = {
        "device": get_device_conf(),
        "icon": "mdi:radiator",
        "name": "Start",
        "unique_id": f"{config['device_id']}-000",
        "command_topic": f"{config['mqtt_prefix']}/start/cmd",
        "availability_topic": f"{config['mqtt_prefix']}/start/av",
        "enabled_by_default": True,
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/button/{config['device_id']}-000/config",
        json.dumps(start_conf),
    )

    stop_conf = {
        "device": get_device_conf(),
        "icon": "mdi:radiator-off",
        "name": "Stop",
        "unique_id": f"{config['device_id']}-001",
        "command_topic": f"{config['mqtt_prefix']}/stop/cmd",
        "availability_topic": f"{config['mqtt_prefix']}/stop/av",
        "enabled_by_default": True,
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/button/{config['device_id']}-001/config",
        json.dumps(stop_conf),
    )

    status_conf = {
        "device": get_device_conf(),
        "expire_after": 10,
        "name": "Status",
        "unique_id": f"{config['device_id']}-010",
        "state_topic": f"{config['mqtt_prefix']}/status/state",
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/sensor/{config['device_id']}-010/config",
        json.dumps(status_conf),
    )

    room_temperature_conf = {
        "device": get_device_conf(),
        "expire_after": 10,
        "name": "Room temperature",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "icon": "mdi:home-thermometer",
        "unique_id": f"{config['device_id']}-011",
        "state_topic": f"{config['mqtt_prefix']}/room_temperature/state",
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/sensor/{config['device_id']}-011/config",
        json.dumps(room_temperature_conf),
    )

    heater_temperature_conf = {
        "device": get_device_conf(),
        "expire_after": 10,
        "name": "Heater temperature",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "icon": "mdi:thermometer-lines",
        "unique_id": f"{config['device_id']}-012",
        "state_topic": f"{config['mqtt_prefix']}/heater_temperature/state",
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/sensor/{config['device_id']}-012/config",
        json.dumps(heater_temperature_conf),
    )

    voltage_conf = {
        "device": get_device_conf(),
        "expire_after": 10,
        "name": "Supply voltage",
        "device_class": "voltage",
        "unit_of_measurement": "V",
        "icon": "mdi:car-battery",
        "unique_id": f"{config['device_id']}-013",
        "state_topic": f"{config['mqtt_prefix']}/voltage/state",
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/sensor/{config['device_id']}-013/config",
        json.dumps(voltage_conf),
    )

    altitude_conf = {
        "device": get_device_conf(),
        "expire_after": 10,
        "name": "Altitude",
        "device_class": "distance",
        "unit_of_measurement": "m",
        "icon": "mdi:summit",
        "unique_id": f"{config['device_id']}-014",
        "state_topic": f"{config['mqtt_prefix']}/altitude/state",
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/sensor/{config['device_id']}-014/config",
        json.dumps(altitude_conf),
    )

    mode_select_conf = {
        "device": get_device_conf(),
        "name": "Mode",
        "availability_topic": f"{config['mqtt_prefix']}/mode/av",
        "command_topic": f"{config['mqtt_prefix']}/mode/cmd",
        "state_topic": f"{config['mqtt_prefix']}/mode/state",
        "enabled_by_default": True,
        "unique_id": f"{config['device_id']}-021",
        "options": modes,
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/select/{config['device_id']}-021/config",
        json.dumps(mode_select_conf),
    )

    level_conf = {
        "device": get_device_conf(),
        "name": "Power Level",
        "availability_topic": f"{config['mqtt_prefix']}/level/av",
        "command_topic": f"{config['mqtt_prefix']}/level/cmd",
        "state_topic": f"{config['mqtt_prefix']}/level/state",
        "enabled_by_default": True,
        "icon": "mdi:speedometer",
        "unique_id": f"{config['device_id']}-020",
        "min": 1.0,
        "max": 10.0,
        "step": 1.0,
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/number/{config['device_id']}-020/config",
        json.dumps(level_conf),
    )

    temperature_conf = {
        "device": get_device_conf(),
        "name": "Temperature",
        "availability_topic": f"{config['mqtt_prefix']}/temperature/av",
        "command_topic": f"{config['mqtt_prefix']}/temperature/cmd",
        "state_topic": f"{config['mqtt_prefix']}/temperature/state",
        "enabled_by_default": True,
        "icon": "mdi:thermometer",
        "unique_id": f"{config['device_id']}-022",
        "min": 8.0,
        "max": 36.0,
        "step": 1.0,
    }
    client.publish(
        f"{config['mqtt_discovery_prefix']}/number/{config['device_id']}-022/config",
        json.dumps(temperature_conf),
    )   

def on_connect(client, userdata, flags, rc):
    global run
    if rc:
        run = False
        raise RuntimeError("Cannot connect to MQTT broker (error %d)" % rc)
    logger.info("Connected to MQTT broker")
    client.subscribe(
        [
            (f"{config['mqtt_prefix']}/start/cmd", 2),
            (f"{config['mqtt_prefix']}/stop/cmd", 2),
            (f"{config['mqtt_prefix']}/level/cmd", 2),
            (f"{config['mqtt_prefix']}/temperature/cmd", 2),
            (f"{config['mqtt_prefix']}/mode/cmd", 2),
        ]
    )
    publish_ha_config()


def dispatch_result(result):
    stop_pub = False
    start_pub = False
    level_pub = False
    temperature_pub = False
    mode_pub = False
    if result:
        logger.debug(str(result.data()))
        msg = result.running_step_msg
        if result.error:
            msg = f"{msg} ({result.error_msg})"
        client.publish(f"{config['mqtt_prefix']}/status/state", msg)
        client.publish(f"{config['mqtt_prefix']}/room_temperature/state", result.cab_temperature)
        if result.running_mode:
            client.publish(f"{config['mqtt_prefix']}/mode/av", "online")
            client.publish(f"{config['mqtt_prefix']}/mode/state", modes[result.running_mode - 1])
            mode_pub = True
        if result.running_step:
            client.publish(f"{config['mqtt_prefix']}/voltage/state", result.supply_voltage)
            client.publish(f"{config['mqtt_prefix']}/altitude/state", result.altitude)
            client.publish(
                f"{config['mqtt_prefix']}/heater_temperature/state", result.case_temperature
            )
            client.publish(f"{config['mqtt_prefix']}/level/state", result.set_level)
            if result.set_temperature is not None:
                client.publish(f"{config['mqtt_prefix']}/temperature/state", result.set_temperature)
            if ((result.running_mode == 0) or (result.running_mode == 1)) and (result.running_step < 4):
                client.publish(f"{config['mqtt_prefix']}/level/av", "online")
                level_pub = True
            if result.running_mode == 2:
                client.publish(f"{config['mqtt_prefix']}/temperature/av", "online")
                temperature_pub = True
            if (result.running_step > 0) and (result.running_step < 4):
                client.publish(f"{config['mqtt_prefix']}/stop/av", "online")
                stop_pub = True
        else:
            client.publish(f"{config['mqtt_prefix']}/start/av", "online")
            start_pub = True
    if not stop_pub:
        client.publish(f"{config['mqtt_prefix']}/stop/av", "offline")
    if not start_pub:
        client.publish(f"{config['mqtt_prefix']}/start/av", "offline")
    if not level_pub:
        client.publish(f"{config['mqtt_prefix']}/level/av", "offline")
    if not temperature_pub:
        client.publish(f"{config['mqtt_prefix']}/temperature/av", "offline")
    if not mode_pub:
        client.publish(f"{config['mqtt_prefix']}/mode/av", "offline")
    client.publish(bridge_health_topic, "online" if result else "offline")


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    if msg.topic == f"{config['mqtt_prefix']}/start/cmd":
        logger.info("Received START command")
        dispatch_result(vdh.start())
    elif msg.topic == f"{config['mqtt_prefix']}/stop/cmd":
        logger.info("Received STOP command")
        dispatch_result(vdh.stop())
    elif msg.topic == f"{config['mqtt_prefix']}/level/cmd":
        try:
            payload = int(msg.payload)
        except ValueError:
            logger.warning("Received invalid LEVEL payload: %s", msg.payload)
            return
        logger.info("Received LEVEL=%d command", payload)
        dispatch_result(vdh.set_level(payload))
    elif msg.topic == f"{config['mqtt_prefix']}/temperature/cmd":
        try:
            payload = int(msg.payload)
        except ValueError:
            logger.warning("Received invalid TEMPERATURE payload: %s", msg.payload)
            return
        logger.info("Received TEMPERATURE=%d command", payload)
        dispatch_result(vdh.set_level(payload))
    elif msg.topic == f"{config['mqtt_prefix']}/mode/cmd":
        try:
            payload_decoded = msg.payload.decode("ascii")
        except UnicodeDecodeError:
            logger.warning("Received MODE payload that is not ascii: %s", msg.payload)
            return
        if payload_decoded not in modes:
            logger.warning("Received MODE payload not in %s: %s", modes, payload_decoded)
            return
        logger.info("Received MODE=%s command", payload_decoded)
        dispatch_result(vdh.set_mode(modes.index(payload_decoded) + 1))
    logger.debug("%s %s", msg.topic, str(msg.payload))


def log_startup_summary():
    logger.info(
        "Starting Vevor BLE bridge targeting BLE %s (passkey %s)",
        config["ble_mac_address"],
        config["ble_passkey"],
    )
    logger.info(
        "MQTT %s:%s prefix=%s discovery_prefix=%s device_id=%s",
        config["mqtt_host"],
        config["mqtt_port"],
        config["mqtt_prefix"],
        config["mqtt_discovery_prefix"],
        config["device_id"],
    )


def run_diag():
    logger.info("Running diagnostics (one-shot status pull)")
    try:
        diag_vdh = vevor.DieselHeater(
            config["ble_mac_address"], config["ble_passkey"], logger=logger
        )
        result = diag_vdh.get_status()
        if result is None:
            logger.error("No response from heater during diagnostics")
            sys.exit(2)
        print(json.dumps(result.data(), indent=2))
    except Exception as exc:
        logger.error("Diagnostic run failed: %s", exc)
        sys.exit(2)


def main():
    global client, logger, vdh, run, config, bridge_health_topic
    parser = argparse.ArgumentParser(description="Vevor BLE <-> MQTT bridge")
    parser.add_argument(
        "--diag",
        action="store_true",
        help="Run a single BLE status query and exit",
    )
    args = parser.parse_args()

    config = load_config()
    bridge_health_topic = f"{config['mqtt_prefix']}/bridge/state"
    logger = init_logger(config["log_level"])
    log_startup_summary()

    if args.diag:
        run_diag()
        return

    client = init_client()
    vdh = vevor.DieselHeater(
        config["ble_mac_address"], config["ble_passkey"], logger=logger
    )
    client.loop_start()

    try:
        while run:
            try:
                result = vdh.get_status()
                dispatch_result(result)
            except Exception as exc:
                logger.error("Error while polling heater: %s", exc)
                client.publish(bridge_health_topic, "offline")
            time.sleep(config["ble_poll_interval"])
    except KeyboardInterrupt:
        logger.info("Stopping bridge")
    finally:
        client.publish(bridge_health_topic, "offline")
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
