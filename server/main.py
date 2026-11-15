"""
ESP32 Debug Agent — MCP server

Exposes a simulated ESP32 board (Wokwi) to any MCP-compatible AI client
(Claude Desktop, Claude Code, Cursor, ...). The agent can compile and flash
firmware, run the simulation, read serial logs, send console commands,
inject faults and inspect the device state.

Run:            python server/main.py        (stdio transport)
Configure in:   claude_desktop_config.json / .mcp.json  -> see README.md
"""

import sys
from pathlib import Path

# allow running as a script: `python server/main.py`
sys.path.insert(0, str(Path(__file__).resolve().parent))

import faults
import runner
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("esp32-debug-agent")


# ---------------------------------------------------------------- building

@mcp.tool()
def build_firmware() -> str:
    """Cross-compile the ESP32 firmware (src/main.cpp) with PlatformIO.

    Run this after editing the firmware code. Returns the build log tail.
    """
    code, log = runner.build_firmware()
    tail = "\n".join(log.splitlines()[-40:])
    return f"build {'SUCCEEDED' if code == 0 else 'FAILED'} (exit {code})\n\n{tail}"


@mcp.tool()
def flash_firmware(code: str) -> str:
    """Replace the firmware source, rebuild it, and boot the new firmware.

    Args:
        code: complete Arduino C++ source to write to src/main.cpp.
              Keep the line `// FAULT-INJECTION-POINT` inside loop() so the
              fault-injection engine keeps working.

    Returns build status plus the serial output of the first boot.
    """
    sketch = runner.ROOT / "src" / "main.cpp"
    sketch.write_text(code, encoding="utf-8")
    build_code, build_log = runner.build_firmware()
    if build_code != 0:
        tail = "\n".join(build_log.splitlines()[-40:])
        return f"flash FAILED — build error (exit {build_code}). Code was NOT flashed.\n\n{tail}"
    result = runner.run_simulation(timeout_ms=8000, label="flash")
    return (
        "flash OK — firmware rebuilt and booted. Serial output:\n\n"
        + result["serial_log"]
    )


# ------------------------------------------------------------- simulating

@mcp.tool()
def run_simulation(seconds: int = 10) -> str:
    """Run the simulated board for N seconds and return its serial output.

    Use this after flashing to watch the firmware boot and stream sensor data.
    """
    result = runner.run_simulation(timeout_ms=max(3, min(60, seconds)) * 1000)
    return result["serial_log"] or result["full_output"]


@mcp.tool()
def read_serial_logs(lines: int = 50) -> str:
    """Read the last N lines of the most recent simulation's serial log."""
    log = runner.latest_serial_log()
    return "\n".join(log.splitlines()[-lines:])


# ------------------------------------------------------- talking to device

@mcp.tool()
def send_serial_command(command: str) -> str:
    """Send a console command to the firmware's serial interface.

    Valid commands: HELP, LED ON, LED OFF, STATUS, READ.
    The board is rebooted into a fresh 10 s simulation for each command.
    """
    cmd = command.strip().upper()
    if cmd not in {"HELP", "LED ON", "LED OFF", "STATUS", "READ"}:
        return f"[ERR] unknown command '{command}'. Valid: HELP, LED ON, LED OFF, STATUS, READ"
    return runner.send_serial_command(cmd, expect=r"\[(OK|ERR|STATUS|DATA)\]")


@mcp.tool()
def set_part_control(part_id: str, control: str, value: float) -> str:
    """Change a virtual part's value live during the simulation.

    Args:
        part_id: Wokwi part id (dht1 = DHT22 sensor, pot1 = potentiometer).
        control: control name — dht1: 'temperature' (°C) or 'humidity' (%);
                 pot1: 'position' (0.0 .. 1.0).
        value: value to set.

    Returns the serial log including a fresh READ of the sensors, so you can
    verify the change took effect.
    """
    return runner.set_part_control(part_id, control, value)


@mcp.tool()
def get_device_info() -> str:
    """Query device state via the STATUS command (uptime, led, free heap)."""
    return runner.get_device_info()


# ------------------------------------------------------------------ faults

@mcp.tool()
def inject_fault(fault: str) -> str:
    """Inject a realistic fault into the simulated board.

    Faults:
      sensor_disconnect - disconnects the DHT22 data wire (sensor reads -> NaN)
      blocking_delay    - inserts delay(4000) in loop(); needs re-flash to appear
    """
    fn = faults.FAULTS.get(fault)
    if fn is None:
        return f"[ERR] unknown fault '{fault}'. Available: {', '.join(faults.FAULTS)}"
    result = fn()
    if faults.rebuild_firmware_needed(fault):
        build_code, _ = runner.build_firmware()
        if build_code == 0:
            result += "\nFirmware rebuilt — the fault is now present in the flashed image."
        else:
            result += "\nWARNING: rebuild FAILED — flash_firmware to apply the fault."
    return result


@mcp.tool()
def clear_faults() -> str:
    """Restore the board to a clean state (undoes all injected faults)."""
    return faults.clear_faults()


@mcp.tool()
def fault_status() -> str:
    """Report which faults are currently injected."""
    return faults.fault_status()


if __name__ == "__main__":
    mcp.run()  # stdio transport
