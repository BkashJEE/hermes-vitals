"""Vendored from hermes-session-health 0.1.0 (MIT, same author): the failure, ending and stuck rules
that Bot Health and Hermes Vitals share. Only the helpers are used here."""
#!/usr/bin/env python3
"""Session health: where Hermes Bots struggled, from their own session logs.

Reads every profile's ``state.db`` read-only and prints counts and categories, never message text
(session logs hold private chats, mail and tokens). A run saves a small JSON snapshot so the next run
can say what changed since last week.

    session_health.py                 # last 7 days, markdown, saves a snapshot
    session_health.py --days 30       # any window
    session_health.py --json          # machine-readable
    session_health.py --no-save       # don't write a snapshot

Stdlib only. Safe to run from a Hermes cron job (`--no-agent --script`) or a Bot's terminal.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# How a session ended, judged from its last assistant message (first match wins). Approximate by design.
ENDINGS = [
    ("needs sign-in/2FA/CAPTCHA", r"\b(sign[ -]?in|log[ -]?in|logged out|2fa|two[- ]factor|captcha|verification code)\b"),
    ("waiting on approval", r"\b(requires approval|needs? (your )?approval|awaiting (your )?approval|single-query mode)\b"),
    ("tool missing", r"\b(not available in this session|tool is unavailable|unknown tool|no such tool)\b"),
    ("hit a limit", r"\b(tool-call limit|max(imum)? turns|iteration limit|timed out|budget)\b"),
    ("site refused", r"\b(can.t provide a secure connection|connection (refused|reset)|rate limit(ed)?|http 4(03|29))\b"),
    ("screen problem", r"\b(no active window|display (is )?not reachable|capture failed)\b"),
    ("asked a question", r"\?\s*$"),
]
STUCK = {"needs sign-in/2FA/CAPTCHA", "waiting on approval", "tool missing", "hit a limit", "site refused", "screen problem"}
MARKS = (("✅", "done"), ("✋", "needs you"), ("⚠️", "blocked"), ("⏳", "scheduled"), ("💬", "answered"))


def real_home() -> Path:
    """Hermes points HOME at the calling profile; the databases live under the user's own home."""
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        return Path.home()


def hermes_root() -> Path:
    env = os.environ.get("HERMES_HOME")
    root = Path(env) if env else real_home() / ".hermes"
    if root.parent.name == "profiles":  # called from inside a profile
        root = root.parent.parent
    return root


def profile_homes(root: Path):
    yield "default", root
    prof = root / "profiles"
    if prof.is_dir():
        for p in sorted(prof.iterdir()):
            if p.is_dir():
                yield p.name, p


def is_failure(content: str) -> bool:
    """A real failure from the tool's own fields: terminal exit_code != 0 or a non-empty error,
    ok/success false, not_found true. Non-JSON results only when they start as an error."""
    try:
        d = json.loads(content)
    except (ValueError, TypeError):
        return bool(re.match(r"\s*(error|blocked|traceback|exception)\b", content or "", re.I))
    if not isinstance(d, dict):
        return False
    if d.get("ok") is False or d.get("success") is False or d.get("not_found") is True:
        return True
    code = d.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool) and code != 0:
        return True
    return bool(d.get("error"))


def ending(text: str) -> str:
    for name, pat in ENDINGS:
        if re.search(pat, text, re.I | re.M):
            return name
    return "finished/other"


def mark(text: str):
    t = text.lstrip()
    return next((name for m, name in MARKS if t.startswith(m)), None)


def _to_epoch(ts):
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).strip()
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def mine(root: Path, since: float, until: float) -> dict:
    per = {}
    tools, fails = Counter(), Counter()
    endings, marks = Counter(), Counter()
    for name, home in profile_homes(root):
        db = home / "state.db"
        if not db.exists():
            continue
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            cols = {r[1] for r in c.execute("pragma table_info(messages)")}
            if not {"session_id", "role", "content"} <= cols:
                continue
            tool_col = "tool_name" if "tool_name" in cols else "NULL"
            ts_col = "timestamp" if "timestamp" in cols else "NULL"
            rows = c.execute(f"select session_id, role, coalesce(content,''), {tool_col}, {ts_col} "
                             "from messages order by rowid").fetchall()
            c.close()
        except sqlite3.Error:
            continue
        sessions = defaultdict(list)
        for sid, role, content, tool, ts in rows:
            t = _to_epoch(ts)
            if t is None or not since <= t < until:
                continue
            sessions[sid].append((role, content, tool))
        p = Counter()
        for msgs in sessions.values():
            p["sessions"] += 1
            for role, content, tool in msgs:
                if role != "tool":
                    continue
                tool = tool or "?"
                p["tool_calls"] += 1
                tools[tool] += 1
                if is_failure(content):
                    p["failures"] += 1
                    fails[tool] += 1
                if "human_has_control" in content:
                    p["takeovers"] += 1
                if re.search(r"tool loop warning|same_tool_failure", content, re.I):
                    p["loops"] += 1
            replies = [m[1] for m in msgs if m[0] == "assistant" and m[1].strip()]
            if replies:
                e = ending(replies[-1])
                endings[e] += 1
                p["stuck"] += e in STUCK
                if (mk := mark(replies[-1])):
                    marks[mk] += 1
        if p["sessions"]:
            per[name] = dict(p)
    total = Counter()
    for p in per.values():
        total.update(p)
    top_fail = sorted(((t, fails[t], tools[t]) for t in tools if fails[t]), key=lambda x: -x[1])[:5]
    return {
        "window": {"since": since, "until": until},
        "totals": {k: total.get(k, 0) for k in ("sessions", "tool_calls", "failures", "stuck", "takeovers", "loops")},
        "per_profile": per,
        "top_failing_tools": [{"tool": t, "failures": f, "calls": n} for t, f, n in top_fail],
        "endings": dict(endings.most_common()),
        "marks": dict(marks.most_common()),
    }


def pct(a, b):
    return f"{(100 * a / b):.0f}%" if b else "–"


def delta(now, before, key):
    if not before:
        return ""
    d = now["totals"][key] - before["totals"].get(key, 0)
    return f" ({'+' if d >= 0 else ''}{d} vs last)"


def render(r: dict, prev: dict | None, days: int) -> str:
    t = r["totals"]
    day = lambda x: datetime.fromtimestamp(x).strftime("%b %d")  # noqa: E731
    lines = [f"**Bot health, last {days} days** ({day(r['window']['since'])} – {day(r['window']['until'])})", ""]
    if not t["sessions"]:
        return "\n".join(lines + ["No sessions in this window."])
    lines += [
        f"- Sessions: {t['sessions']}{delta(r, prev, 'sessions')}",
        f"- Tool calls: {t['tool_calls']}, failed: {t['failures']} ({pct(t['failures'], t['tool_calls'])}){delta(r, prev, 'failures')}",
        f"- Ended stuck (approval, login, limit, site, tool, screen): {t['stuck']} ({pct(t['stuck'], t['sessions'])}){delta(r, prev, 'stuck')}",
        f"- Human takeovers refused a Bot's click: {t['takeovers']} · loop warnings: {t['loops']}",
    ]
    if r["top_failing_tools"]:
        lines += ["", "Most failing tools:"]
        lines += [f"- {x['tool']}: {x['failures']} of {x['calls']} ({pct(x['failures'], x['calls'])})" for x in r["top_failing_tools"]]
    stuck = {k: v for k, v in r["endings"].items() if k in STUCK}
    if stuck:
        lines += ["", "Why sessions got stuck: " + ", ".join(f"{k} {v}" for k, v in stuck.items())]
    if r["marks"]:
        lines += ["Bots' own status marks: " + ", ".join(f"{k} {v}" for k, v in r["marks"].items())]
    lines += ["", "By Bot (sessions · failed calls · stuck):"]
    for name, p in sorted(r["per_profile"].items(), key=lambda kv: -kv[1].get("sessions", 0)):
        lines.append(f"- {name}: {p.get('sessions', 0)} · {p.get('failures', 0)}/{p.get('tool_calls', 0)} · {p.get('stuck', 0)}")
    lines += ["", "_Counts only, read-only. Endings are pattern-matched from each session's last reply, so treat them as approximate._"]
    return "\n".join(lines)


def snapshot_dir(root: Path) -> Path:
    return root / "session-health"


def latest_snapshot(root: Path, before: float):
    d = snapshot_dir(root)
    if not d.is_dir():
        return None
    snaps = sorted(p for p in d.glob("*.json"))
    for p in reversed(snaps):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("window", {}).get("until", 0) <= before + 1:
            return data
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Where your Hermes Bots struggled, from their own session logs")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--root", help="Hermes root (default: detected)")
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else hermes_root()
    until = time.time()
    since = until - a.days * 86400
    report = mine(root, since, until)
    prev = latest_snapshot(root, since)
    if a.json:
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        print(render(report, prev, a.days))
    if not a.no_save and report["totals"]["sessions"]:
        d = snapshot_dir(root)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{datetime.fromtimestamp(until).strftime('%Y-%m-%d')}.json").write_text(json.dumps(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
