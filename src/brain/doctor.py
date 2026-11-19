from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import fm, health, install, roles
from .config import Config

TRIGGERS = ("night", "run-failed", "upgrade", "turn-error")
COOLDOWN = timedelta(hours=3)
REPEAT_NIGHTS = 3
FORGET = timedelta(days=30)
NIGHT_ONLY = {"hygiene-unfixed"}
OUTCOMES = ("fixed", "left", "human")
BLAME = timedelta(hours=24)


def _state_path(config: Config):
    return roles.scratch(config) / "doctor.json"


def _load(config: Config) -> dict:
    try:
        state = json.loads(_state_path(config).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _fix(config: Config, found: dict) -> str:
    if found["class"] == "engine-mismatch":
        install.migrate_check(config)
        return f"{found['key']}: ran migrate-check"
    if found["class"] == "unpushed":
        from .cli import push
        push(config.vault_path)
        return f"{found['key']}: pushed"
    return ""


def _blame(config: Config, now: datetime, new: set[str]) -> dict[str, str]:
    last = (health.git(config.repo, "log", "-1", "--grep=^doctor(", "--format=%h %cI") or "").split()
    if not new or len(last) != 2:
        return {}
    at = datetime.fromisoformat(last[1])
    since = (now if now.tzinfo else now.astimezone()) - at
    return {key: last[0] for key in new} if timedelta(0) <= since < BLAME else {}


def record_error(config: Config, command: str, error: str) -> None:
    entry = {"at": config.now().isoformat(timespec="seconds"), "command": " ".join(command.split())[:300],
             "error": " ".join(error.split())[-300:] or "no error output"}
    with health.errors_path(config).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _clear_error(config: Config, key: str) -> None:
    path = health.errors_path(config)
    kept = [entry for entry in health.turn_errors(config)
            if f"turn-error:{health.text_key(entry['command'] + entry['error'])}" != key]
    fm.write_text(path, "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in kept))


def log(config: Config, key: str, outcome: str, note: str) -> str:
    if key.startswith("turn-error:"):
        _clear_error(config, key)
    path = config.vault_path / config.runtime_dir / "Doctor.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fm.write_text(path, "---\nkind: log\n---\n# Doctor\n\n")
    line = f"- {config.now():%Y-%m-%d %H:%M} · {key} · {outcome} · {' '.join(note.split())}"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    return line


def _day_gate(state: dict, handable: list[dict], now: datetime) -> list[dict]:
    handable = [item for item in handable if item["class"] not in NIGHT_ONLY]
    last = state.get("last") or {}
    if last.get("at") and now - datetime.fromisoformat(last["at"]) < COOLDOWN:
        return []
    return handable if {item["key"] for item in handable} - set(last.get("keys", [])) else []


def check(config: Config, trigger: str = "night", now: datetime | None = None) -> str:
    now = now or config.now()
    today = now.date().isoformat()
    found = health.report(config)["issues"]
    fixed = []
    for item in found:
        if item["kind"] == "mechanical":
            try:
                if line := _fix(config, item):
                    fixed.append(line)
            except Exception as error:
                fixed.append(f"{item['key']}: fix failed: {error}")
    if fixed:
        found = health.report(config)["issues"]

    state = _load(config)
    keys: dict = state.setdefault("keys", {})
    present = {item["key"] for item in found}
    blamed = _blame(config, now, present - set(keys))
    for item in found:
        keys.setdefault(item["key"], {"handed": [], "reported": False})["seen"] = today
    for key in set(keys) - present:
        keys[key]["reported"] = False
    for key in [key for key, entry in keys.items() if entry.get("seen", today) < (now - FORGET).date().isoformat()]:
        del keys[key]

    for_doctor = [item for item in found if item["kind"] != "human"]
    repeat = {item["key"] for item in for_doctor
              if item["key"] in blamed or len(set(keys[item["key"]]["handed"]) - {today}) >= REPEAT_NIGHTS}
    handable = [item for item in for_doctor if item["key"] not in repeat]
    if trigger != "night":
        handable = _day_gate(state, handable, now)
    report = [dict(item, repeat=item["key"] in repeat, **({"after": blamed[item["key"]]} if item["key"] in blamed else {}))
              for item in found
              if (item["kind"] == "human" or item["key"] in repeat) and not keys[item["key"]]["reported"]]

    for item in handable:
        if today not in keys[item["key"]]["handed"]:
            keys[item["key"]]["handed"].append(today)
    for item in report:
        keys[item["key"]]["reported"] = True
    if handable:
        state["last"] = {"at": now.isoformat(), "keys": [item["key"] for item in handable]}
    path = _state_path(config)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    if not handable and not report:
        return ""
    return json.dumps({"trigger": trigger, "fixed": fixed, "issues": handable, "report": report,
                       "operators": config.operator_user_ids}, ensure_ascii=False, indent=2)
