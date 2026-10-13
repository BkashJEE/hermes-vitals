"""``hermes vitals`` CLI command and ``/vitals`` slash command.

The desktop page runs ``hermes --profile <p> vitals --json`` through the gateway's
``cli.exec`` RPC: a fresh read-only process per refresh, so no gateway restart is needed.
"""

from __future__ import annotations

import json
import sys

from . import vitals_data as data

HELP = """Usage: /vitals [days]
A bedside monitor for every Hermes Bot: HR (tool calls/hour), BP (failed/stuck),
TEMP (errors.log), SAT (memory fill vs cap), RR (loops), plus alarms with a fix.
Open Command Center > Vitals in Hermes Desktop for the live monitor."""


def slash_command(raw_args: str) -> str:
    raw = (raw_args or "").strip()
    if raw in ("help", "-h", "--help", "?"):
        return HELP
    days = int(raw) if raw.isdigit() else 7
    return data.render_text(data.collect(days=max(1, min(days, 30))))


def setup_argparse(subparser) -> None:
    subparser.add_argument("--days", type=int, default=7, help="Window in days (1-30, default 7)")
    subparser.add_argument("--all", action="store_true", help="Include profiles with no activity in the window")
    subparser.add_argument("--json", action="store_true", help="Emit JSON for the desktop page")
    subparser.set_defaults(func=handle_cli)


def handle_cli(args) -> None:
    days = max(1, min(int(getattr(args, "days", 7) or 7), 30))
    try:
        payload = data.collect(days=days, include_idle=bool(getattr(args, "all", False)))
    except Exception as e:  # the page shows this instead of crashing
        payload = {"error": f"vitals failed: {type(e).__name__}"}
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
    elif "error" in payload:
        print(payload["error"])
    else:
        print(data.render_text(payload))
