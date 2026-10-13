# Hermes Vitals

A bedside monitor for every Hermes Bot, inside Hermes Desktop.

Each Bot gets a monitor: a heartbeat trace drawn from its real hourly activity, five vital signs and alarms that say what is wrong, how far from normal, where the number came from and what to do.

| Vital | Hermes meaning | Normal (our threshold) | Source |
|---|---|---|---|
| **HR** | tool calls per active hour | – | `state.db` |
| **BP** | failed tool calls / stuck sessions | under 5% failed | `state.db`, Bot Health rules |
| **TEMP** | ERROR lines in the log | 0–5 | `logs/errors.log` |
| **SAT** | memory fill vs the hard cap | under 80% | `memories/MEMORY.md`, `USER.md` |
| **RR** | loops: the same failing step again | 0–2 | `state.db` |
| **NEXT** | messages until the next memory review | – | `state.db`, `memory.nudge_interval` |

Alarms come from fixed rules (image analysis failing, a memory entry blocked at load, a plugin that fails to load, a rejected provider key, memory nearly full, sessions getting stuck, no pulse). Only error-level problems can raise a red ALERT.

## Privacy

Read-only. Every `state.db` is opened with `mode=ro`; memory files and logs are read, never written. Tool results are read in-process only to decide pass/fail; **no message text, memory content or log line leaves the collector**, only counts, times and fixed rule labels (a test plants a secret and checks it never appears). No network calls.

Counts only what the logs record: zero failures is not the same as "it worked". The normal ranges are Hermes Vitals thresholds, not Hermes defaults.

## Install

```sh
hermes plugins install https://github.com/BkashJEE/hermes-vitals
hermes plugins enable hermes-vitals
```

Then turn on the desktop half in **Capabilities → Plugins** and open **Vitals** in the sidebar (or ⌘K → Open Vitals). `/vitals` and `hermes vitals` print the same monitor as text.

## How it works

The desktop page (`desktop/plugin.js`, plain ESM using the Desktop Plugin SDK) runs `hermes --profile <p> vitals --json` through the gateway's `cli.exec` RPC every 15 seconds while the page is visible. Colours come only from the theme's `--ui-*` variables, so it follows your skin.

## Tests

```sh
python3 tests/test_vitals.py
```

## Credits

The failure rules come from Bot Health / hermes-session-health, which grew out of @mr_r0b0t's "mine your sessions" tip (h/t @Teknium). The desktop wiring follows the Desktop Plugin SDK guide and the `cli.exec` pattern used by hassan-kamel/hermes-activity.

MIT licensed.
