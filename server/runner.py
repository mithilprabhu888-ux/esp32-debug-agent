"""
wokwi_runner.py — drives the Wokwi CLI (wokwi-cli) as a subprocess.

Responsibilities:
  * run the simulation of the compiled firmware and capture serial output
  * generate temporary automation-scenario YAML files for serial interaction
  * persist every run's serial log under logs/ for later inspection
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
SCENARIOS_DIR = ROOT / "scenarios"

WOKWI_CLI = "wokwi-cli"  # must be on PATH
PLATFORMIO = [sys.executable, "-m", "platformio"]

# exit code wokwi-cli uses when --timeout is reached (i.e. normal end of run)
WOKWI_TIMEOUT_EXIT = 42


def _ensure_dirs() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    SCENARIOS_DIR.mkdir(exist_ok=True)


def build_firmware() -> tuple[int, str]:
    """Cross-compile src/main.cpp -> .pio/build/esp32dev/firmware.bin via PlatformIO."""
    proc = subprocess.run(
        [*PLATFORMIO, "run", "-e", "esp32dev"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_simulation(
    timeout_ms: int = 10000,
    scenario: str | None = None,
    label: str = "run",
) -> dict:
    """
    Run wokwi-cli on the project. Returns dict with keys:
      exit_code, serial_log (text), full_output (text), log_file (path)
    """
    _ensure_dirs()
    ts = time.strftime("%Y%m%d_%H%M%S")
    serial_file = LOGS_DIR / f"{label}_{ts}.serial.log"
    full_file = LOGS_DIR / f"{label}_{ts}.full.log"

    cmd = [
        WOKWI_CLI,
        str(ROOT),
        "--timeout", str(timeout_ms),
        "--serial-log-file", str(serial_file),
    ]
    if scenario:
        # wokwi-cli resolves scenario paths relative to the project root
        cmd += ["--scenario", scenario]

    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_ms / 1000 + 180,
        )
        exit_code = proc.returncode
        full_output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        exit_code = -1
        full_output = "ERROR: wokwi-cli did not finish in time (host-side timeout)."
    except FileNotFoundError:
        return {
            "exit_code": -2,
            "serial_log": "",
            "full_output": (
                "ERROR: 'wokwi-cli' not found on PATH. Install it from "
                "https://docs.wokwi.com/wokwi-ci/cli-installation"
            ),
            "log_file": "",
        }

    full_file.write_text(full_output, encoding="utf-8")
    serial_log = serial_file.read_text(encoding="utf-8") if serial_file.exists() else ""
    return {
        "exit_code": exit_code,
        "serial_log": serial_log,
        "full_output": full_output,
        "log_file": str(serial_file),
    }


def latest_serial_log() -> str:
    """Return the contents of the most recent *.serial.log file."""
    _ensure_dirs()
    logs = sorted(LOGS_DIR.glob("*.serial.log"))
    if not logs:
        return "(no simulation has run yet)"
    return logs[-1].read_text(encoding="utf-8", errors="replace")


def write_scenario(command: str, expect: str, boot_marker: str = "Type HELP") -> str:
    """
    Generate a wokwi automation scenario that:
      1. waits for the firmware boot banner,
      2. writes `command` + newline to the serial port,
      3. waits for `expect` (regex) to appear in the output.
    Returns the scenario path (relative to project root).
    """
    _ensure_dirs()
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = SCENARIOS_DIR / f"cmd_{ts}.yaml"

    def yq(s: str) -> str:  # yaml single-quote escaping
        return "'" + s.replace("'", "''") + "'"

    lines = [
        "name: 'send serial command'",
        "version: 1",
        "author: 'esp32-debug-agent'",
        "steps:",
        f"  - wait-serial: {yq(boot_marker)}",
        f'  - write-serial: "{command}\\n"',
    ]
    if expect:
        lines.append(f"  - wait-serial: {yq(expect)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path.relative_to(ROOT))


def send_serial_command(command: str, expect: str) -> str:
    """Send a console command to the running firmware; returns the serial log."""
    scenario = write_scenario(command, expect)
    result = run_simulation(timeout_ms=10000, scenario=scenario, label="cmd")
    return result["serial_log"] or result["full_output"]


def set_part_control(part_id: str, control: str, value) -> str:
    """
    Change a virtual part's value live during simulation via a `set-control`
    scenario step, then read the sensors back with the READ command.
    Known controls: dht1 -> temperature, humidity; pot1 -> position (0..1).
    """
    _ensure_dirs()
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = SCENARIOS_DIR / f"control_{ts}.yaml"
    path.write_text(
        "\n".join([
            "name: 'set part control'",
            "version: 1",
            "author: 'esp32-debug-agent'",
            "steps:",
            "  - wait-serial: 'Type HELP'",
            "  - set-control:",
            f"      part-id: {part_id}",
            f"      name: {control}",
            f"      value: {value}",
            "  - write-serial: \"READ\\n\"",
            "  - wait-serial: 'pot='",
            "  - delay: 300ms",
        ]) + "\n",
        encoding="utf-8",
    )
    result = run_simulation(timeout_ms=10000, scenario=str(path.relative_to(ROOT)), label="control")
    return result["serial_log"] or result["full_output"]


def get_device_info() -> str:
    """Query the firmware STATUS command and parse the device state."""
    log = send_serial_command("STATUS", expect=r"\[STATUS\]")
    m = re.search(r"\[STATUS\][^\r\n]*", log)
    return m.group(0) if m else f"[get_device_info] could not parse STATUS from log:\n{log}"
