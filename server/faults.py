"""
faults.py — fault-injection engine.

Injects realistic hardware/firmware faults so the AI agent can practice
debugging, and restores the project to a clean state afterwards.

Faults:
  * sensor_disconnect : removes the DHT22 data wire from diagram.json
  * blocking_delay    : inserts delay(4000) into loop() in src/main.cpp
"""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAGRAM = ROOT / "diagram.json"
SKETCH = ROOT / "src" / "main.cpp"
BACKUP_DIR = ROOT / ".faults"

DIAGRAM_BAK = BACKUP_DIR / "diagram.json.bak"
SKETCH_BAK = BACKUP_DIR / "main.cpp.bak"

FAULT_MARKER = "// FAULT-INJECTION-POINT"


def _backup_once(src: Path, dst: Path) -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    if not dst.exists():
        shutil.copyfile(src, dst)


def inject_sensor_disconnect() -> str:
    """Remove the wire between the DHT22 data pin and GPIO 4."""
    _backup_once(DIAGRAM, DIAGRAM_BAK)
    diagram = json.loads(DIAGRAM.read_text(encoding="utf-8"))

    before = len(diagram["connections"])
    diagram["connections"] = [
        c for c in diagram["connections"]
        if not ({c[0], c[1]} == {"dht1:SDA", "esp:4"})
    ]
    removed = before - len(diagram["connections"])
    if removed == 0:
        return "sensor_disconnect: already injected (no DHT22 wire found to remove)."

    DIAGRAM.write_text(json.dumps(diagram, indent=2), encoding="utf-8")
    return (
        "sensor_disconnect: injected — removed the DHT22 SDA wire "
        f"({removed} connection). Sensor reads will now return NaN."
    )


def inject_blocking_delay() -> str:
    """Insert a 4 s blocking delay at the end of loop() (starves the command loop)."""
    _backup_once(SKETCH, SKETCH_BAK)
    code = SKETCH.read_text(encoding="utf-8")
    if "delay(4000);  // INJECTED-FAULT" in code:
        return "blocking_delay: already injected."
    if FAULT_MARKER not in code:
        return "blocking_delay: FAILED — fault marker not found in src/main.cpp."

    code = code.replace(
        FAULT_MARKER,
        FAULT_MARKER + "\n  delay(4000);  // INJECTED-FAULT (blocks loop for 4 s)",
    )
    SKETCH.write_text(code, encoding="utf-8")
    return (
        "blocking_delay: injected — loop() now blocks 4 s per iteration. "
        "Firmware must be rebuilt (flash_firmware) for it to take effect."
    )


def clear_faults() -> str:
    """Restore diagram.json and src/main.cpp from backups."""
    restored = []
    if DIAGRAM_BAK.exists():
        shutil.copyfile(DIAGRAM_BAK, DIAGRAM)
        restored.append("diagram.json")
        DIAGRAM_BAK.unlink()
    if SKETCH_BAK.exists():
        shutil.copyfile(SKETCH_BAK, SKETCH)
        restored.append("src/main.cpp")
        SKETCH_BAK.unlink()
    return f"faults cleared: restored {', '.join(restored)}." if restored else "no faults were active."


def fault_status() -> str:
    status = []
    if DIAGRAM_BAK.exists():
        diagram = json.loads(DIAGRAM.read_text(encoding="utf-8"))
        wired = any({c[0], c[1]} == {"dht1:SDA", "esp:4"} for c in diagram["connections"])
        status.append("sensor_disconnect: ACTIVE" if not wired
                      else "sensor_disconnect: backup exists but wire is present")
    if SKETCH_BAK.exists():
        code = SKETCH.read_text(encoding="utf-8")
        status.append("blocking_delay: ACTIVE" if "INJECTED-FAULT" in code
                      else "blocking_delay: backup exists but code is clean")
    return "active faults: " + ("; ".join(status) if status else "none")


def rebuild_firmware_needed(fault: str) -> bool:
    """Faults that require recompiling the firmware to take effect."""
    return fault == "blocking_delay"


FAULTS = {
    "sensor_disconnect": inject_sensor_disconnect,
    "blocking_delay": inject_blocking_delay,
}
