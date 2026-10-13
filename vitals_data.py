"""Hermes Vitals: a bedside monitor for every Hermes Bot, from its own files.

Read-only. Every profile's state.db is opened with ``mode=ro``; memory files and
errors.log are read, never written. Tool results are read in-process only to
decide pass/fail (the Session Health rules); no message text, file name or log
line ever leaves this module, only counts, times and fixed rule labels.

Vital signs (doctor's names, Hermes meanings):
  HR    tool calls per active hour
  BP    failed tool calls / stuck sessions   (fail % against a threshold)
  TEMP  ERROR lines in errors.log
  SAT   memory fill against the hard cap     (max of MEMORY.md and USER.md)
  RR    loops: the same failing step repeated
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .session_health import STUCK, ending, hermes_root, is_failure, profile_homes

# Our thresholds, not Hermes'. Shipped in the payload so the page can show "normal".
THRESHOLDS = {
    "fail_pct": 5.0,       # BP: failed tool calls above this share
    "errors": 5,           # TEMP: ERROR lines in the window
    "mem_pct": 80.0,       # SAT: either memory file above this share of its cap
    "loops": 2,            # RR: loop warnings in the window
    "stuck_pct": 10.0,     # sessions that ended waiting on a person
    "no_pulse_hours": 48,  # no tool call for this long
}
# Hermes defaults (hermes_cli/config_defaults.py), overridden by each profile's config.yaml.
MEMORY_DEFAULTS = {"memory_char_limit": 2200, "user_char_limit": 1375, "nudge_interval": 10}

# Known error-log signatures -> fixed, content-free labels. Only the label and a count are emitted.
LOG_RULES = [
    ("vision", re.compile(r"Error analyzing image"), "Image analysis failing",
     "Point vision at a model that can see images"),
    ("memory_blocked", re.compile(r"blocked at load time"), "A memory entry is blocked on every start",
     "Review that MEMORY.md entry and remove it if it is not yours"),
    ("plugin_load", re.compile(r"Failed to load plugin"), "A plugin fails to load on every start",
     "Repair or remove the broken plugin folder"),
    ("provider_auth", re.compile(r"\b(401|403)\b.*(auth|key|token)|invalid api key", re.I), "Provider rejects the key",
     "Open Key Doctor (/keys): it names the benched or blocked login and the fix"),
]
# Only error-level signatures can raise a red ALERT (rule -> minimum lines); startup warnings stay WATCH.
ALERT_RULES = {"vision": 20, "provider_auth": 5}
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (WARNING|ERROR|CRITICAL) (?:\[[^\]]*\] )?([\w.]+)")


def _yaml(path: Path) -> dict:
    try:
        import yaml  # Hermes ships PyYAML
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def display_name(name: str, home: Path) -> str:
    raw = str(_yaml(home / "profile.yaml").get("display_name") or "").strip()
    if not raw:
        return name
    return re.split(r"\s+[—-]\s+|\s{2,}", raw)[0].split()[0]


def memory_vitals(home: Path) -> dict:
    cfg = {**MEMORY_DEFAULTS, **{k: v for k, v in (_yaml(home / "config.yaml").get("memory") or {}).items()
                                 if k in MEMORY_DEFAULTS and isinstance(v, int)}}
    out = {"nudge_interval": cfg["nudge_interval"]}
    for key, fname, limit in (("memory", "MEMORY.md", cfg["memory_char_limit"]), ("user", "USER.md", cfg["user_char_limit"])):
        p = home / "memories" / fname
        try:
            chars = len(p.read_text(encoding="utf-8"))
        except OSError:
            chars = None
        out[key] = {"chars": chars, "limit": limit,
                    "pct": round(100.0 * chars / limit, 1) if chars is not None and limit else None}
    return out


def errorlog_vitals(home: Path, since: float) -> dict:
    levels, sources = Counter(), Counter()
    rules = {rid: {"count": 0, "last": None, "source": Counter()} for rid, *_ in LOG_RULES}
    logs = home / "logs"
    for f in sorted(logs.glob("errors.log*")) if logs.is_dir() else []:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            m = LOG_LINE.match(line)
            if not m:
                continue
            try:
                t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                continue
            if t < since:
                continue
            levels[m.group(2)] += 1
            sources[m.group(3)] += 1
            for rid, rx, *_ in LOG_RULES:
                if rx.search(line):
                    r = rules[rid]
                    r["count"] += 1
                    r["source"][m.group(3)] += 1
                    r["last"] = max(r["last"] or t, t)
    hits = []
    for rid, _rx, title, fix in LOG_RULES:
        r = rules[rid]
        if r["count"]:
            hits.append({"rule": rid, "title": title, "treatment": fix, "count": r["count"], "last": r["last"],
                         "source": r["source"].most_common(1)[0][0]})
    return {"errors": levels.get("ERROR", 0) + levels.get("CRITICAL", 0), "warnings": levels.get("WARNING", 0),
            "top_sources": [{"source": s, "count": n} for s, n in sources.most_common(3)], "rules": hits}


def session_vitals(home: Path, since: float, now: float, hours: int) -> dict | None:
    db = home / "state.db"
    if not db.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        cols = {r[1] for r in c.execute("pragma table_info(messages)")}
        if not {"session_id", "role", "content", "timestamp"} <= cols:
            c.close()
            return None
        tool_col = "tool_name" if "tool_name" in cols else "NULL"
        rows = c.execute(f"select session_id, role, coalesce(content,''), {tool_col}, timestamp from messages "
                         "where timestamp >= ? order by rowid", (since,)).fetchall()
        last = c.execute("select max(timestamp) from messages where role in ('tool','assistant')").fetchone()[0]
        latest = c.execute("select id from sessions order by started_at desc limit 1").fetchone()
        latest_users = (c.execute("select count(*) from messages where session_id=? and role='user'", (latest[0],)).fetchone()[0]
                        if latest else 0)
        c.close()
    except sqlite3.Error:
        return None
    hourly = [0] * hours
    per_tool, per_tool_fail = Counter(), Counter()
    p = Counter()
    replies = defaultdict(list)
    for sid, role, content, tool, ts in rows:
        try:
            t = float(ts)
        except (TypeError, ValueError):
            continue
        p["_sessions_" + str(sid)] = 1
        if role == "tool":
            p["tool_calls"] += 1
            per_tool[tool or "?"] += 1
            i = int((t - since) // 3600)
            if 0 <= i < hours:
                hourly[i] += 1
            if is_failure(content):
                p["failures"] += 1
                per_tool_fail[tool or "?"] += 1
            if re.search(r"tool loop warning|same_tool_failure", content, re.I):
                p["loops"] += 1
        elif role == "assistant" and content.strip():
            replies[sid].append(content)
    sessions = sum(1 for k in p if k.startswith("_sessions_"))
    stuck = sum(1 for msgs in replies.values() if ending(msgs[-1]) in STUCK)
    worst = sorted(((t, per_tool_fail[t], per_tool[t]) for t in per_tool_fail), key=lambda x: -x[1])[:3]
    return {
        "sessions": sessions, "tool_calls": p["tool_calls"], "failures": p["failures"], "loops": p["loops"],
        "stuck": stuck, "active_hours": sum(1 for h in hourly if h), "hourly": hourly,
        "last_activity": float(last) if last is not None else None, "latest_session_user_messages": latest_users,
        "worst_tools": [{"tool": t, "failures": f, "calls": n} for t, f, n in worst],
    }


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else 0.0


def assess(s: dict, mem: dict, log: dict, now: float, th: dict = THRESHOLDS) -> dict:
    fail_pct = _pct(s["failures"], s["tool_calls"])
    pcts = [x["pct"] for x in (mem["memory"], mem["user"]) if x["pct"] is not None]
    sat = max(pcts) if pcts else None
    idle_h = (now - s["last_activity"]) / 3600 if s["last_activity"] else None
    vitals = {
        "hr": round(s["tool_calls"] / s["active_hours"]) if s["active_hours"] else 0,
        "bp": [s["failures"], s["stuck"]], "fail_pct": fail_pct,
        "temp": log["errors"], "sat": sat, "rr": s["loops"],
    }
    bad = []
    if s["tool_calls"] >= 20 and fail_pct > th["fail_pct"]:
        bad.append("bp")
    if log["errors"] > th["errors"]:
        bad.append("temp")
    if sat is not None and sat > th["mem_pct"]:
        bad.append("sat")
    if s["loops"] > th["loops"]:
        bad.append("rr")
    no_pulse = idle_h is not None and idle_h > th["no_pulse_hours"]
    stuck_pct = _pct(s["stuck"], s["sessions"])
    alarms = []

    def alarm(level, finding, reading, normal, evidence, treatment, rule=None):
        alarms.append({"level": level, "finding": finding, "reading": reading, "normal": normal,
                       "evidence": evidence, "treatment": treatment, "rule": rule})

    for hit in log["rules"]:
        level = "alert" if hit["rule"] in ALERT_RULES and hit["count"] >= ALERT_RULES[hit["rule"]] else "watch"
        title, treatment, rule = hit["title"], hit["treatment"], hit["rule"]
        # An MCP server's own sign-in, not a model key: Key Doctor would rightly show nothing for it.
        if rule == "provider_auth" and "mcp" in str(hit["source"]).lower():
            title, treatment, rule = "An MCP server needs signing in again", "Run `hermes mcp login <server>` (or reauth) for the MCP server that lost its sign-in", "mcp_auth"
        alarm(level, title, f"{hit['count']} log lines this window", "0",
              {"file": "errors.log", "source": hit["source"], "count": hit["count"], "last": hit["last"]}, treatment, rule)
    if "temp" in bad and not log["rules"]:
        top = log["top_sources"][0] if log["top_sources"] else {"source": "?", "count": 0}
        alarm("watch", "Errors in the log", f"{log['errors']} errors", f"0–{th['errors']}",
              {"file": "errors.log", "source": top["source"], "count": top["count"], "last": None},
              "Open errors.log and fix the top source")
    for key, label in (("memory", "MEMORY.md"), ("user", "USER.md")):
        m = mem[key]
        if m["pct"] is not None and m["pct"] > th["mem_pct"]:
            alarm("watch", f"{label} nearly full", f"{m['chars']:,} of {m['limit']:,} characters ({m['pct']}%)",
                  f"under {th['mem_pct']:.0f}%", {"file": f"memories/{label}", "count": m["chars"], "last": None},
                  f"Trim {label} below {th['mem_pct']:.0f}% so new memories fit")
    if "bp" in bad:
        w = s["worst_tools"][0] if s["worst_tools"] else {"tool": "?", "failures": 0, "calls": 0}
        alarm("watch", f"Tool calls failing: {w['tool']}", f"{s['failures']} of {s['tool_calls']} failed ({fail_pct}%)",
              f"under {th['fail_pct']:.0f}%", {"file": "state.db", "source": w["tool"], "count": w["failures"], "last": None},
              "Run the Bot Health fix prompt for this tool")
    if "rr" in bad:
        alarm("watch", "Repeating the same failing step", f"{s['loops']} loop warnings", f"0–{th['loops']}",
              {"file": "state.db", "count": s["loops"], "last": None}, "Tell it to stop and report after 2 identical failures")
    if s["sessions"] >= 5 and stuck_pct > th["stuck_pct"]:
        alarm("watch", "Sessions getting stuck", f"{s['stuck']} of {s['sessions']} sessions stopped waiting ({stuck_pct}%)",
              f"under {th['stuck_pct']:.0f}%", {"file": "state.db", "count": s["stuck"], "last": None},
              "Allow the safe action, or sign it in once on its screen")
    if no_pulse:
        alarm("watch", "No pulse", f"no tool call for {idle_h / 24:.1f} days", f"under {th['no_pulse_hours']} h",
              {"file": "state.db", "count": 0, "last": s["last_activity"]}, "Check its gateway, or retire the profile")

    if any(a["level"] == "alert" for a in alarms) or len(bad) >= 2:
        status = "alert"
    elif alarms or bad:
        status = "watch"
    elif not s["tool_calls"]:
        status = "idle"
    else:
        status = "stable"
    interval = mem["nudge_interval"]
    next_review = interval - (s["latest_session_user_messages"] % interval) if interval else None
    return {"status": status, "vitals": vitals, "bad": bad, "no_pulse": no_pulse, "alarms": alarms,
            "next_memory_review": next_review}


def collect(root: Path | None = None, days: int = 7, now: float | None = None, include_idle: bool = False) -> dict:
    root = Path(root) if root else hermes_root()
    now = time.time() if now is None else now
    since = now - days * 86400
    hours = days * 24
    bots = []
    for name, home in profile_homes(root):
        s = session_vitals(home, since, now, hours)
        if s is None or (not include_idle and not s["tool_calls"]):
            continue
        mem = memory_vitals(home)
        log = errorlog_vitals(home, since)
        a = assess(s, mem, log, now)
        bots.append({"profile": name, "name": display_name(name, home), **a,
                     "sessions": s["sessions"], "tool_calls": s["tool_calls"], "failures": s["failures"],
                     "stuck": s["stuck"], "loops": s["loops"], "hourly": s["hourly"], "last_activity": s["last_activity"],
                     "worst_tools": s["worst_tools"], "memory": mem, "errors": {k: log[k] for k in ("errors", "warnings", "top_sources")}})
    order = {"alert": 0, "watch": 1, "stable": 2, "idle": 3}
    bots.sort(key=lambda b: (order[b["status"]], -b["tool_calls"]))
    count = Counter(b["status"] for b in bots)
    return {"generated_at": now, "window_days": days, "since": since, "thresholds": THRESHOLDS,
            "ward": {"bots": len(bots), "alert": count["alert"], "watch": count["watch"], "stable": count["stable"]},
            "bots": bots, "privacy": "read-only; counts and rule labels only, no message text"}


def render_text(payload: dict) -> str:
    w = payload["ward"]
    lines = [f"Hermes Vitals · last {payload['window_days']} days · {w['bots']} bots · "
             f"{w['alert']} alert · {w['watch']} watch · {w['stable']} stable"]
    for b in payload["bots"]:
        v = b["vitals"]
        sat = f"{v['sat']:.0f}%" if v["sat"] is not None else "–"
        lines.append(f"  {b['status'].upper():6} {b['name']:<10} HR {v['hr']}/h  BP {v['bp'][0]}/{v['bp'][1]} ({v['fail_pct']}%)  "
                     f"TEMP {v['temp']}  SAT {sat}  RR {v['rr']}")
        for a in b["alarms"]:
            lines.append(f"         ! {a['finding']}: {a['reading']} -> {a['treatment']}")
    return "\n".join(lines)
