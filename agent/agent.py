"""
agent.py — Groq-powered debugging agent over MCP.

Connects to the ESP32 Debug Agent MCP server (server/main.py) as an MCP
client, exposes its tools to Groq (OpenAI-compatible API), and runs a
tool-calling loop until the task is complete.

Setup:
    setx GROQ_API_KEY "gsk_..."         # your Groq key (console.groq.com)
    setx GROQ_MODEL "llama-3.3-70b-versatile"   # optional (auto-detected)

Usage:
    python agent/agent.py "Turn the LED on and show the device status"
    python agent/agent.py --max-steps 30 "Inject a sensor fault, run the
        board, diagnose what is wrong and fix it"
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent

# emoji-safe output on Windows consoles
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

XAI_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "25"))

SYSTEM_PROMPT = """\
You are an expert embedded-systems debugging agent. You control a simulated
ESP32 board (Wokwi) through the provided MCP tools.

Board: ESP32 DevKit-C + DHT22 sensor (GPIO 4) + status LED (GPIO 26) +
potentiometer (GPIO 34). The firmware has a serial console: HELP, LED ON,
LED OFF, STATUS, READ.

Guidelines:
- Work step by step; run the simulation or read logs before drawing
  conclusions, and verify fixes before claiming success.
- flash_firmware replaces src/main.cpp AND rebuilds + boots automatically.
- Fault notes: 'sensor_disconnect' changes the wiring (no rebuild needed);
  'blocking_delay' changes the code (rebuild required, inject_fault does it
  for you). Use clear_faults to restore the board, fault_status to inspect.
- Keep final answers concise: root cause -> fix -> verification evidence.
"""


def to_openai_tools(mcp_tools) -> list:
    """Convert MCP tool definitions to OpenAI function-calling schema."""
    out = []
    for t in mcp_tools:
        out.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        })
    return out


async def execute_tool(session: ClientSession, name: str, args_json: str) -> str:
    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        args = {}
    try:
        result = await session.call_tool(name, args)
        parts = [b.text for b in result.content if getattr(b, "text", None)]
        return "\n".join(parts) or "(no output)"
    except Exception as exc:  # tool errors must not kill the agent loop
        return f"[tool error] {type(exc).__name__}: {exc}"


def pick_model(models: list) -> str:
    """Verify the configured model exists; fall back to the next best option."""
    ids = models
    if not ids:
        print(f"⚠ could not list models, using '{MODEL}' as configured")
        return MODEL
    if MODEL in ids:
        return MODEL
    fallback = next((i for i in ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")
                     if i in ids), ids[0])
    print(f"⚠ model '{MODEL}' not available; using '{fallback}' (available: {', '.join(ids[:8])})")
    return fallback


async def run(task: str, max_steps: int) -> None:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("GROQ_API_KEY is not set. Get a key at https://console.groq.com and run:\n"
                         '  setx GROQ_API_KEY "gsk_..."')

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "server" / "main.py")],
        # MCP's stdio client strips most env vars by default; pass the full
        # environment so WOKWI_CLI_TOKEN / PATH reach the server subprocess.
        env={**os.environ},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            oai_tools = to_openai_tools(mcp_tools)
            print(f"connected to MCP server: {len(mcp_tools)} tools available")

            headers = {"Authorization": f"Bearer {api_key}"}
            async with httpx.AsyncClient(base_url=XAI_BASE_URL, headers=headers, timeout=180) as xai:
                try:
                    models_resp = await xai.get("/models")
                    model_ids = [m["id"] for m in models_resp.json().get("data", [])]
                except Exception:
                    model_ids = []
                model = pick_model(model_ids)
                print(f"agent ready | model: {model} | task: {task}\n" + "-" * 60)

                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": task},
                ]

                for step in range(1, max_steps + 1):
                    import time as _time
                    t0 = _time.monotonic()
                    print(f"… step {step}: waiting for {model} …", flush=True)
                    try:
                        resp = await asyncio.wait_for(
                            xai.post("/chat/completions", json={
                                "model": model,
                                "messages": messages,
                                "tools": oai_tools,
                                "tool_choice": "auto",
                                "temperature": 0.2,
                            }),
                            timeout=240,
                        )
                    except asyncio.TimeoutError:
                        print(f"⚠ chat completion timed out after 240 s (step {step})", flush=True)
                        return
                    print(f"   ← responded in {_time.monotonic() - t0:.1f}s", flush=True)
                    resp.raise_for_status()
                    msg = resp.json()["choices"][0]["message"]
                    tool_calls = msg.get("tool_calls") or []

                    if not tool_calls:
                        print("-" * 60)
                        print("🤖 Agent:\n" + (msg.get("content") or "(empty response)"))
                        return

                    messages.append({
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        "tool_calls": tool_calls,
                    })

                    for call in tool_calls:
                        fname = call["function"]["name"]
                        fargs = call["function"].get("arguments") or "{}"
                        print(f"\n🔧 step {step}: {fname}({fargs})", flush=True)
                        out = await execute_tool(session, fname, fargs)
                        preview = " | ".join(out.splitlines())[:300]
                        print(f"   → {preview}", flush=True)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": out,
                        })

                print(f"\n⚠ reached --max-steps ({max_steps}) without a final answer")


def main() -> None:
    ap = argparse.ArgumentParser(description="Groq agent for the ESP32 Debug Agent MCP server")
    ap.add_argument("task", nargs="+", help="what the agent should do")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    args = ap.parse_args()
    asyncio.run(run(" ".join(args.task), args.max_steps))


if __name__ == "__main__":
    main()
