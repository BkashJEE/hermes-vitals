"""hermes-vitals: a bedside monitor for every Hermes Bot, inside Hermes Desktop.

Registers:
  * ``hermes vitals`` CLI command (``--json`` for the desktop page);
  * ``/vitals`` slash command with a text summary.

The desktop UI lives in ``desktop/plugin.js`` and reads data by running
``hermes vitals --json`` through the gateway's ``cli.exec`` RPC. Everything is
read-only over local files; there are no network calls and no message text leaves it.
"""

from __future__ import annotations

from . import commands


def register(ctx) -> None:
    ctx.register_command(
        "vitals",
        handler=commands.slash_command,
        description="Bot vital signs and alarms (hermes-vitals)",
        args_hint="[days]",
    )
    ctx.register_cli_command(
        name="vitals",
        help="Vital signs for every Bot: HR, BP, TEMP, SAT, RR and alarms (hermes-vitals plugin)",
        setup_fn=commands.setup_argparse,
        handler_fn=commands.handle_cli,
    )
