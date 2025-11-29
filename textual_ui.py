import asyncio
import json
import logging
from typing import Optional

from bluepy.btle import DefaultDelegate, Scanner
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Log,
    Select,
    Static,
)

from main import load_config
from vevor import DieselHeater


class _ScanDelegate(DefaultDelegate):
    def handleDiscovery(self, dev, isNewDev, isNewData):
        # Quiet delegate; we log after scan completes.
        return


class BridgeApp(App):
    """Interactive TUI for scanning and testing the heater connection."""

    CSS = """
    Screen {
        layout: vertical;
        padding: 1 2;
    }
    #main-panels {
        height: 1fr;
    }
    #controls {
        width: 42%;
        min-width: 36;
        max-width: 48;
        padding-right: 1;
    }
    #log-panel {
        border: solid $accent 10%;
        padding: 1;
    }
    .section-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    .field-label {
        text-style: bold;
    }
    .compact-input {
        width: 100%;
        margin-bottom: 1;
    }
    #actions Button {
        width: 100%;
        margin-bottom: 1;
    }
    #status {
        border: solid $accent 10%;
        padding: 1;
        height: 10;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "scan", "Scan BLE"),
        ("g", "status", "Get Status"),
        ("r", "reset_heater", "Reconnect"),
    ]

    def __init__(self):
        super().__init__()
        self.config = None
        self._heater: Optional[DieselHeater] = None
        self._logger = logging.getLogger("vevor-tui")
        self._logger.addHandler(logging.NullHandler())

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-panels"):
            with Vertical(id="controls"):
                yield Static("BLE Bridge Controls", classes="section-title")
                yield Static("", id="config-info")
                yield Static("Scan Options", classes="field-label")
                yield Input(placeholder="Name filter (optional)", id="filter", classes="compact-input")
                yield Input(value="10", placeholder="Timeout seconds", id="timeout", classes="compact-input")
                yield Static("Command Inputs", classes="field-label")
                yield Input(value="5", placeholder="Level 1-36", id="level", classes="compact-input")
                yield Select(
                    options=[("Power Level", "1"), ("Temperature", "2")],
                    value="1",
                    id="mode",
                )
                with Vertical(id="actions"):
                    yield Button("Scan BLE", id="scan")
                    yield Button("Get Status", id="status")
                    yield Button("Start Heater", id="start")
                    yield Button("Stop Heater", id="stop")
                    yield Button("Set Level", id="set-level")
                    yield Button("Set Mode", id="set-mode")
                    yield Button("Reconnect", id="reconnect")
            with Vertical(id="log-panel"):
                yield Static("Activity Log", classes="section-title")
                yield Log(id="log", highlight=True)
                yield Static("Last Status", classes="section-title")
                yield Static("No status yet.", id="status")
        yield Footer()

    async def on_mount(self) -> None:
        try:
            self.config = load_config(exit_on_error=False)
            self._update_config_info()
            self._log("Loaded configuration.")
        except Exception as exc:  # pylint: disable=broad-except
            self.config = None
            self._log(f"[red]Config error:[/red] {exc}")
            self.query_one("#config-info", Static).update("Fix env vars and restart.")

    def _update_config_info(self):
        info = "\n".join(
            [
                f"BLE: {self.config['ble_mac_address']} (passkey {self.config['ble_passkey']})",
                f"MQTT host: {self.config['mqtt_host']} (prefix {self.config['mqtt_prefix']})",
                f"Device: {self.config['device_name']} / {self.config['device_model']}",
            ]
        )
        self.query_one("#config-info", Static).update(info)

    def _log(self, message: str):
        log_widget = self.query_one(Log)
        log_widget.write(message)

    def _set_status(self, payload: str):
        self.query_one("#status", Static).update(payload)

    async def _run_in_thread(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    def _ensure_heater(self, reset: bool = False) -> DieselHeater:
        if reset or self._heater is None:
            self._heater = DieselHeater(
                self.config["ble_mac_address"],
                self.config["ble_passkey"],
                logger=self._logger,
            )
        return self._heater

    def _scan(self, name_filter: Optional[str], timeout: float):
        scanner = Scanner().withDelegate(_ScanDelegate())
        devices = scanner.scan(timeout)
        results = []
        for dev in devices:
            name = dev.getValueText(9) or ""
            adv = dev.getScanData()
            if name_filter:
                combined = " ".join([dev.addr, name] + [val for _, _, val in adv])
                if name_filter.lower() not in combined.lower():
                    continue
            results.append(
                {
                    "addr": dev.addr,
                    "rssi": dev.rssi,
                    "name": name,
                    "data": adv,
                }
            )
        return results

    async def _run_scan(self):
        if not self.config:
            self._log("[red]Cannot scan: configuration missing.[/red]")
            return
        filter_value = self.query_one("#filter", Input).value or ""
        try:
            timeout = float(self.query_one("#timeout", Input).value or "10")
        except ValueError:
            timeout = 10
        self._log(f"Scanning for {timeout}s ...")
        try:
            results = await self._run_in_thread(self._scan, filter_value, timeout)
        except Exception as exc:  # pylint: disable=broad-except
            self._log(f"[red]Scan failed:[/red] {exc}")
            return
        if not results:
            self._log("No devices matched.")
            return
        for dev in results:
            name = f" name={dev['name']}" if dev["name"] else ""
            self._log(f"[green]{dev['addr']}[/green]{name} RSSI={dev['rssi']} dB")
        first = results[0]
        self._log(
            f"Use BLE_MAC_ADDRESS={first['addr']} in .env (first match)."
        )

    async def _heater_call(self, method_name: str, *args):
        if not self.config:
            self._log("[red]Configuration missing; cannot talk to heater.[/red]")
            return None
        try:
            heater = self._ensure_heater()
        except Exception as exc:  # pylint: disable=broad-except
            self._heater = None
            self._log(f"[red]Connect failed:[/red] {exc}")
            return None

        def _invoke():
            method = getattr(heater, method_name)
            return method(*args)

        try:
            return await self._run_in_thread(_invoke)
        except Exception as exc:  # pylint: disable=broad-except
            self._log(f"[red]{method_name} failed:[/red] {exc}")
            self._heater = None
            return None

    async def _update_status(self):
        result = await self._heater_call("get_status")
        if not result:
            self._set_status("No status (offline?)")
            return
        data = result.data()
        summary = (
            f"{result.running_step_msg} | "
            f"Cab {result.cab_temperature}°C | "
            f"Case {result.case_temperature}°C | "
            f"Voltage {result.supply_voltage}V"
        )
        self._set_status(summary)
        self._log(json.dumps(data, indent=2))

    async def _start(self):
        res = await self._heater_call("start")
        if res:
            self._log("Start command sent.")

    async def _stop(self):
        res = await self._heater_call("stop")
        if res:
            self._log("Stop command sent.")

    async def _set_level(self):
        try:
            level = int(self.query_one("#level", Input).value or "5")
        except ValueError:
            self._log("[red]Level must be a number.[/red]")
            return
        res = await self._heater_call("set_level", level)
        if res:
            self._log(f"Set level to {level}.")

    async def _set_mode(self):
        mode_value = self.query_one("#mode", Select).value or "1"
        res = await self._heater_call("set_mode", int(mode_value))
        if res:
            label = "Power Level" if mode_value == "1" else "Temperature"
            self._log(f"Set mode to {label}.")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "scan": self._run_scan,
            "status": self._update_status,
            "start": self._start,
            "stop": self._stop,
            "set-level": self._set_level,
            "set-mode": self._set_mode,
            "reconnect": lambda: self._heater_call("get_status"),
        }
        handler = mapping.get(event.button.id)
        if handler:
            await handler()

    async def action_scan(self):
        await self._run_scan()

    async def action_status(self):
        await self._update_status()

    async def action_reset_heater(self):
        self._heater = None
        self._log("Resetting heater connection; next command will reconnect.")


if __name__ == "__main__":
    BridgeApp().run()
