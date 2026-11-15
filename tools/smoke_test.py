"""
smoke_test.py — end-to-end test of the ESP32 Debug Agent pipeline.

Runs (without an AI client) the same calls the MCP tools make:
  1. device status via serial console   (demo scenario 3)
  2. LED control via serial console     (demo scenario 3)
  3. sensor fault -> diagnose -> fix    (demo scenario 1)
  4. blocking-delay fault demo          (demo scenario 2, code-level check)

Usage:  python tools/smoke_test.py
Prereq: WOKWI_CLI_TOKEN env var, wokwi-cli on PATH, firmware built.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import faults
import runner

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 60)
    print("ESP32 Debug Agent — smoke test")
    print("=" * 60)

    # ---- 1. serial console: STATUS -------------------------------
    print("\n--- 1. send_serial_command('STATUS') ---")
    log = runner.send_serial_command("STATUS", expect=r"\[STATUS\]")
    print(log)
    check("STATUS command answered", "[STATUS]" in log)

    # ---- 2. serial console: LED ON -------------------------------
    print("\n--- 2. send_serial_command('LED ON') ---")
    log = runner.send_serial_command("LED ON", expect=r"\[OK\]")
    print(log)
    check("LED ON acknowledged", "[OK] LED=ON" in log)

    # ---- 3. sensor fault injection -------------------------------
    print("\n--- 3. inject_fault('sensor_disconnect') ---")
    print(faults.inject_sensor_disconnect())
    log = runner.run_simulation(timeout_ms=12000, label="fault")["serial_log"]
    print(log)
    check("NaN readings appear", "temp=NaN" in log)

    print("\n--- clear_faults + recovery ---")
    print(faults.clear_faults())
    log = runner.run_simulation(timeout_ms=12000, label="recovery")["serial_log"]
    tail = "\n".join(log.splitlines()[-6:])
    print(tail)
    check("readings recovered", "temp=NaN" not in tail and "temp=" in tail)

    # ---- 4. blocking delay fault (code level) --------------------
    print("\n--- 4. inject_fault('blocking_delay') ---")
    print(faults.inject_blocking_delay())
    status = faults.fault_status()
    print(status)
    check("blocking_delay registered", "blocking_delay: ACTIVE" in status)
    print(faults.clear_faults())
    check("faults cleared", faults.fault_status() == "active faults: none")

    print("\n" + "=" * 60)
    total, passed = len(results), sum(1 for _, ok, _ in results if ok)
    print(f"RESULT: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
