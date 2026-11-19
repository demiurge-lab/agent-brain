from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import health, install, roles
from .config import Config

TRIGGERS = ("night", "run-failed", "upgrade", "turn-error", "commit")
COOLDOWN = timedelta(hours=3)
REPEAT_NIGHTS = 3
FORGET = timedelta(days=30)
NIGHT_ONLY = {"hygiene-unfixed"}


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
    for item in found:
        keys.setdefault(item["key"], {"handed": [], "reported": False})["seen"] = today
    for key in set(keys) - present:
        keys[key]["reported"] = False
    for key in [key for key, entry in keys.items() if entry.get("seen", today) < (now - FORGET).date().isoformat()]:
        del keys[key]

    for_doctor = [item for item in found if item["kind"] != "human"]
    repeat = {item["key"] for item in for_doctor if len(set(keys[item["key"]]["handed"]) - {today}) >= REPEAT_NIGHTS}
    handable = [item for item in for_doctor if item["key"] not in repeat]
    if trigger != "night":
        handable = _day_gate(state, handable, now)
    report = [dict(item, repeat=item["key"] in repeat) for item in found
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
