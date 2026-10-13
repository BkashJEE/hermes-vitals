"""Hermes Vitals collector tests on a synthetic Hermes root (no real data)."""

import json
import sqlite3
import sys
import time
from pathlib import Path

import importlib.util  # noqa: E402

# The repo folder is "hermes-vitals" (not importable by name): load it as package "hermes_vitals".
_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("hermes_vitals", _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["hermes_vitals"] = _pkg
_spec.loader.exec_module(_pkg)
data = importlib.import_module("hermes_vitals.vitals_data")

NOW = 1_800_000_000.0


def make_profile(home: Path, *, tools=(), users=0, mem="", user="", errors=(), name=None, ts=None):
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "state.db")
    db.execute("create table sessions (id text, started_at real)")
    db.execute("create table messages (session_id text, role text, content text, tool_name text, timestamp real)")
    db.execute("insert into sessions values ('s1', ?)", (NOW - 3600,))
    t = ts or NOW - 1800
    for _ in range(users):
        db.execute("insert into messages values ('s1','user','hi',NULL,?)", (t,))
    for tool, content in tools:
        db.execute("insert into messages values ('s1','tool',?,?,?)", (content, tool, t))
    db.execute("insert into messages values ('s1','assistant','Done.',NULL,?)", (t,))
    db.commit()
    db.close()
    (home / "memories").mkdir(exist_ok=True)
    (home / "memories" / "MEMORY.md").write_text(mem, encoding="utf-8")
    (home / "memories" / "USER.md").write_text(user, encoding="utf-8")
    (home / "logs").mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    (home / "logs" / "errors.log").write_text("".join(f"{stamp},000 {lvl} {src}: {msg}\n" for lvl, src, msg in errors))
    if name:
        (home / "profile.yaml").write_text(f"display_name: {name} — Test Bot\n", encoding="utf-8")


def test_healthy_bot_is_stable(tmp_path):
    make_profile(tmp_path / "profiles" / "ok", tools=[("terminal", json.dumps({"exit_code": 0}))] * 30, name="Okay")
    p = data.collect(tmp_path, now=NOW)
    (b,) = p["bots"]
    assert b["status"] == "stable" and b["name"] == "Okay" and b["alarms"] == []
    assert b["vitals"]["hr"] == 30 and b["vitals"]["fail_pct"] == 0.0


def test_failures_memory_and_vision_raise_alarms(tmp_path):
    fails = [("computer_use", json.dumps({"ok": False}))] * 10 + [("computer_use", json.dumps({"ok": True}))] * 20
    make_profile(tmp_path / "profiles" / "sick", tools=fails, user="x" * 1200,
                 errors=[("ERROR", "tools.vision_tools", "Error analyzing image: Error code: 400")] * 25)
    (b,) = data.collect(tmp_path, now=NOW)["bots"]
    assert b["status"] == "alert"
    assert set(b["bad"]) >= {"bp", "temp", "sat"}
    findings = {a["finding"] for a in b["alarms"]}
    assert "Image analysis failing" in findings and "USER.md nearly full" in findings
    assert any(a["level"] == "alert" for a in b["alarms"])


def test_payload_never_contains_message_or_log_text(tmp_path):
    secret = "SECRET-TOKEN-12345"
    make_profile(tmp_path / "profiles" / "p", tools=[("terminal", json.dumps({"exit_code": 1, "error": secret}))] * 25,
                 mem=secret, errors=[("WARNING", "tools.memory_tool", f"Memory entry from MEMORY.md blocked at load time: {secret}")])
    blob = json.dumps(data.collect(tmp_path, now=NOW))
    assert secret not in blob
    assert "A memory entry is blocked on every start" in blob


def test_next_memory_review_and_no_pulse(tmp_path):
    make_profile(tmp_path / "profiles" / "quiet", tools=[("terminal", "{}")] * 3, users=13, ts=NOW - 3 * 86400)
    (b,) = data.collect(tmp_path, now=NOW)["bots"]
    assert b["next_memory_review"] == 7  # 13 % 10 = 3 -> 7 to go
    assert b["no_pulse"] and any(a["finding"] == "No pulse" for a in b["alarms"])


def test_idle_profiles_hidden_unless_asked(tmp_path):
    make_profile(tmp_path / "profiles" / "idle", ts=NOW - 30 * 86400)
    assert data.collect(tmp_path, now=NOW)["bots"] == []
    assert len(data.collect(tmp_path, now=NOW, include_idle=True)["bots"]) == 1


def test_plugin_load_warning_is_watch_not_alert(tmp_path):
    make_profile(tmp_path / "profiles" / "w", tools=[("terminal", "{}")] * 25,
                 errors=[("WARNING", "hermes_cli.plugins", "Failed to load plugin x: No __init__.py")] * 200)
    (b,) = data.collect(tmp_path, now=NOW)["bots"]
    assert b["status"] == "watch"


if __name__ == "__main__":  # runs without pytest: python3 tests/test_vitals.py
    import tempfile
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
        print("ok", fn.__name__)
    print(f"{len(tests)} passed")
