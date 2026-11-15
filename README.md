# ESP32 Debug Agent — MCP × Embedded Systems

An AI agent that **debugs a (simulated) ESP32 board over MCP**. The agent can
cross-compile and flash firmware, run the board in the [Wokwi](https://wokwi.com)
simulator, read serial logs, send serial console commands, inject realistic
hardware/firmware faults, and then diagnose what went wrong — an
"AI-in-the-loop embedded debugging" workflow.

```
┌──────────────┐   MCP (stdio)   ┌────────────────┐   subprocess    ┌───────────┐   cloud sim   ┌────────────────┐
│ Groq (Llama) │ ◄─────────────► │  agent + MCP   │ ◄─────────────► │ wokwi-cli │ ◄───────────► │ virtual ESP32  │
│  chat API    │   tool calling  │  server        │                 │           │               │ + DHT22/LED/pot│
└──────────────┘                 └───────┬────────┘                 └───────────┘               └────────────────┘
                                         │ PlatformIO cross-compiles src/main.cpp
                                         ▼
                                  .pio/build/esp32dev/firmware.bin
```

Two entry points:
* **`agent/agent.py`** — a self-contained agent loop: connects to the MCP
  server as a client, feeds its tools to a Groq-hosted model
  (`openai/gpt-oss-120b`), and runs tool-calling until the task is done.
  Works with any OpenAI-compatible provider (change `GROQ_BASE_URL`).
* **Any MCP client** (Claude Desktop, Cline, Cursor) — config in the README below.

## Why this design

* **`flash_firmware` is real** — the source is cross-compiled with PlatformIO
  (a genuine ESP32 toolchain), not faked. The compiled binary is what Wokwi simulates.
* **The firmware has a serial console** (`HELP`, `LED ON`, `STATUS`, `READ`),
  so the agent talks to the "device" exactly like an engineer with a real board.
* **Fault injection** edits the virtual wiring (`diagram.json`) or the firmware
  source, producing bugs that behave like real ones: NaN sensor reads, a
  blocking loop that starves the console, etc.
* Wokwi ships an experimental generic MCP server (`wokwi-cli mcp`); this project
  goes further and builds a *domain-specific debugging agent* on top of the CLI.

## The simulated board

| Part | Wokwi id | Connected to |
|---|---|---|
| ESP32 DevKit-C | `esp` | — |
| DHT22 temp/humidity | `dht1` | GPIO 4 |
| Red status LED + 220Ω | `led1`/`r1` | GPIO 26 |
| Potentiometer (ADC) | `pot1` | GPIO 34 (analog in) |

## MCP tools

| Tool | Purpose |
|---|---|
| `build_firmware()` | Cross-compile `src/main.cpp` with PlatformIO |
| `flash_firmware(code)` | Replace the firmware source, rebuild, boot, return boot log |
| `run_simulation(seconds)` | Run the board for N seconds, return serial output |
| `read_serial_logs(lines)` | Tail the latest serial log |
| `send_serial_command(cmd)` | Talk to the firmware console (scenario: write-serial / wait-serial) |
| `set_part_control(part, ctrl, value)` | Change virtual hardware live: DHT22 temperature/humidity, potentiometer position |
| `get_device_info()` | Parse the firmware `STATUS` response |
| `inject_fault(fault)` | `sensor_disconnect` (unwire DHT22) or `blocking_delay` (delay(4000) in loop) |
| `clear_faults()` / `fault_status()` | Restore / report board state |

## Setup

1. **Python deps** (Python ≥ 3.10):
   ```
   pip install -r server/requirements.txt
   ```
2. **Wokwi CLI**:
   ```
   iwr https://wokwi.com/ci/install.ps1 -useb | iex      # Windows
   # curl -L https://wokwi.com/ci/install.sh | sh        # macOS/Linux
   ```
   (If the installer stalls, download `wokwi-cli-win-x64.exe` from
   https://github.com/wokwi/wokwi-cli/releases into `%USERPROFILE%\.wokwi\bin\wokwi-cli.exe`
   and add that folder to PATH.)
3. **Wokwi API token** — create one at https://wokwi.com/dashboard/ci and set:
   ```
   setx WOKWI_CLI_TOKEN "wok_..."     # Windows (new shells)
   ```
   ⚠️ Free plan quota: 50 simulated minutes / 30 days, max 5 min per run —
   keep `run_simulation` calls short.
4. **First build** (downloads the ESP32 toolchain once):
   ```
   python -m platformio run -e esp32dev
   ```
5. Manual sanity check:
   ```
   wokwi-cli . --timeout 10000
   ```

## Wire it into Claude Desktop

Add to `claude_desktop_config.json` → `mcpServers`:

```json
{
  "mcpServers": {
    "esp32-debug-agent": {
      "command": "python",
      "args": ["C:/Projects/OpenCode-Project/server/main.py"]
    }
  }
}
```

## Run the built-in agent (Groq)

```powershell
setx GROQ_API_KEY "gsk_..."                    # console.groq.com
python agent/agent.py "Turn the LED on and show the device status"
python agent/agent.py --max-steps 30 "Inject a sensor fault, run the board,
    diagnose what is wrong and fix it"
```

The agent connects to the MCP server over stdio, exposes all tools to the
model, and prints every tool call (`🔧 step N: tool(args) → result`) so the
debugging session is readable in a demo recording.

## Demo scenarios (the interview stories)

1. **Sensor fault** — `inject_fault("sensor_disconnect")` → `run_simulation`
   shows `[DATA] temp=NaN` → agent inspects wiring (`fault_status`),
   concludes the sensor line is down, calls `clear_faults`, verifies recovery.
2. **Firmware bug** — `inject_fault("blocking_delay")` → console stops
   responding between 4 s blocks → agent reads logs, patches the code with
   `flash_firmware`, verifies the console is responsive again.
3. **Natural-language control** — "turn the light on and show me the device
   status" → agent uses `send_serial_command("LED ON")` + `get_device_info()`.

## Project layout

```
├── platformio.ini       # PlatformIO build config (ESP32 + Arduino + DHT lib)
├── wokwi.toml           # tells wokwi-cli where the built binary lives
├── diagram.json         # the virtual wiring (what fault injection edits)
├── src/main.cpp         # the "device" firmware with a serial console
├── server/
│   ├── main.py          # MCP server + tool definitions (FastMCP)
│   ├── runner.py        # wokwi-cli / PlatformIO subprocess management
│   └── faults.py        # fault-injection engine (with backup/restore)
├── agent/
│   └── agent.py         # Groq agent loop (MCP client + tool-calling)
├── logs/                # generated: serial logs of every run
├── scenarios/           # generated: wokwi automation scenario YAMLs
└── .faults/             # generated: backups for fault restore
```

## Roadmap ideas

* More faults: Wi-Fi timeouts (add a Wi-Fi part), heap leak (Wokwi's
  `--heap-min-free` monitoring), watchdog resets.
* Self-hosted or private Wokwi deployment for unlimited sim time.
* Additional boards: STM32 (Pico SDK/PlatformIO) — same MCP server, new `wokwi.toml`.
