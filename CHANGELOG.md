# Changelog

## 0.1.1 — 2026-10-15

- **Desktop page always uses the local gateway.** It used to send `hermes vitals` to whichever gateway owned the focused chat, so a remote connection's session made it fail with "'vitals' is not a hermes command".
- A request that never settles (gateway restarted mid-call) no longer blocks every later refresh.
- Compact monitor cards: every Bot fits on one screen; watch-level alarms fold away.
- A `provider_auth` alarm links to Key Doctor; MCP sign-in errors get their own `mcp_auth` alarm with the `hermes mcp login` fix.

## 0.1.0 — 2026-10-11

First release: a bedside monitor for every Hermes Bot inside Hermes Desktop.
