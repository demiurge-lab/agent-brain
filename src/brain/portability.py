from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import PurePosixPath

from . import fm, install, responsibilities, runtime, schedule
from .config import Config
from .lock import vault_lock
from .vault import Vault

WORKSPACE_PATH = re.compile(r"(?:/Users/[^/\s]+/|/home/[^/\s]+/|/srv/demiurge/)")
TOML_TOKEN = re.compile(
    r'"""(?:[^"\\]|\\[\s\S]|"(?!""))*""""{0,2}'
    r"|'''[\s\S]*?'''\'{0,2}"
    r'|"(?:[^"\\\r\n]|\\[^\r\n])*"'
    r"|'[^'\r\n]*'"
    r'|\#[^\r\n]*'
    r'|^[ \t]*\[[^\r\n]*\]', re.M)


def schedule_problems(config: Config) -> list[str]:
    source = schedule.path(config)
    try:
        routines = tomllib.loads(source.read_text(encoding="utf-8")).get("routines", {})
    except (OSError, ValueError):
        return []
    if not isinstance(routines, dict):
        return []
    return [f"{source}: routines.{name}.{key}: absolute workspace path; use a repository-relative path or $AGENT_HOME"
            for name, values in routines.items() if isinstance(values, dict)
            for key in ("prompt", "script") if isinstance(values.get(key), str) and WORKSPACE_PATH.search(values[key])]


def _schedule_paths(config: Config) -> list[str]:
    source = schedule.path(config)
    if not source.exists():
        return []
    text = source.read_bytes().decode("utf-8")
    tomllib.loads(text)
    ident = re.escape(runtime.home().name)
    workspace = re.compile(
        r"(?<![\w/.-])(?:/Users/[^/\s]+|/home/[^/\s]+|/srv/demiurge)"
        r"(?:/\.hermes/profiles|/agents)?/" + ident
        + r"(?P<vault>/vault)?(?=/|[\s\"'`,;:)}\]]|\.(?=[\s\"'`,;:)}\]]|$)|$)(?P<slash>/)?")

    def portable(match: re.Match) -> str:
        if match["vault"]:
            rest = match.string[match.end():]
            return "" if match["slash"] and rest and rest[0] not in " \t\r\n\"'`,;:)}]" else "."
        return "$AGENT_HOME" + (match["slash"] or "")

    edits, report, routine = [], [], None
    for token in TOML_TOKEN.finditer(text):
        raw = token[0]
        if raw.lstrip().startswith("["):
            table = tomllib.loads(raw).get("routines", {})
            routine = next(iter(table)) if len(table) == 1 and next(iter(table.values())) == {} else None
            continue
        if routine is None or raw.startswith("#"):
            continue
        prefix = text[text.rfind("\n", 0, token.start()) + 1:token.start()]
        field = re.fullmatch(r'''[ \t]*(prompt|script|"prompt"|"script"|'prompt'|'script')[ \t]*=[ \t]*''', prefix)
        if field is None:
            continue
        value = tomllib.loads("value = " + raw)["value"]
        portable_value = workspace.sub(portable, value)
        if portable_value == value:
            continue
        updated = workspace.sub(portable, raw)
        if tomllib.loads("value = " + updated)["value"] != portable_value:
            updated = json.dumps(portable_value, ensure_ascii=False).replace("\x7f", "\\u007f")
        if updated != raw:
            edits.append((token.start(), token.end(), updated))
            key = field[1].strip("\"'")
            report.append(f"{source}: migrated routines.{routine}.{key}")
    for start, end, updated in reversed(edits):
        text = text[:start] + updated + text[end:]
    if edits:
        tomllib.loads(text)
        fm.write_text(source, text)
    return report


def _service_paths() -> list[str]:
    home = runtime.home()
    workspace = re.compile(r"^(?:/Users/[^/\s]+|/home/[^/\s]+|/srv/demiurge)(?:/\.hermes/profiles|/agents)?/"
                           + re.escape(home.name) + r"(?=/|$)")
    report = []
    for path in sorted((home / "services").glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(spec, dict):
            continue
        portable = lambda value: workspace.sub("~", value) if isinstance(value, str) else value
        changed = dict(spec)
        if isinstance(spec.get("command"), list):
            changed["command"] = [portable(part) for part in spec["command"]]
        if "cwd" in spec:
            changed["cwd"] = portable(spec["cwd"])
        if isinstance(spec.get("env"), dict):
            changed["env"] = {key: portable(value) for key, value in spec["env"].items()}
        if changed != spec:
            fm.write_text(path, (json.dumps(changed, indent=2, ensure_ascii=False) + "\n").encode())
            report.append(f"{path}: workspace paths -> ~")
    return report


def migrate(config: Config) -> list[str]:
    report = []
    with vault_lock(config):
        vault = Vault(config.vault_path)
        for entry in responsibilities.discover(vault):
            records = entry.get("records")
            if not isinstance(records, str) or not PurePosixPath(records).is_absolute():
                continue
            if "/records/" not in records:
                report.append(f"{entry['hub']}: left absolute records path without /records/: {records}")
                continue
            rest = records.rsplit("/records/", 1)[1]
            note = vault.notes[entry["hub"]]
            note.data["records"] = os.path.relpath(install.records_path() / rest, config.vault_path)
            note.save()
            report.append(f"{entry['hub']}: records {records} -> {note.data['records']}")
        report.extend(_schedule_paths(config))
    report.extend(_service_paths())
    return report
