"""Operational report for a vault: stage runs, engine state, export freshness and counts. Never note contents."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version as installed_version
from pathlib import Path

from . import fm, install, roles, runtime, validate
from .config import Config
from .tasks import is_open, parse_register
from .vault import Vault

STAGES = ("archive", "attribution", "night-before", "sleep1", "people", "image", "night-after", "prime", "context")
STAGE_RE = re.compile(r"^(" + "|".join(STAGES) + r")\((\d{4}-\d{2}-\d{2})\)")
COVERAGE_RE = re.compile(r"^coverage: (.*)$", re.M)
KNOWLEDGE = ("hub", "profile", "reflection", "episode")


def git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# The night stages that serve one person, absent from an enterprise intellect.
PERSONAL_STAGES = ("prime",)


def stages(repo: Path, today: date, now: datetime, edition: str = "personal") -> dict[str, dict]:
    """Last recorded run of each night stage, from commit subjects; ok when it covers yesterday or later."""
    last: dict[str, str] = {}
    for line in (git(repo, "log", f"--since={(now - timedelta(days=21)).isoformat(timespec='seconds')}", "--format=%s") or "").splitlines():
        match = STAGE_RE.match(line)
        if match and match.group(1) not in last:
            last[match.group(1)] = match.group(2)
    floor = (today - timedelta(days=1)).isoformat()
    return {stage: {"last": last.get(stage),
                    "state": "never" if stage not in last else ("ok" if last[stage] >= floor else "stale")}
            for stage in STAGES if edition == "personal" or stage not in PERSONAL_STAGES}


def setup(config: Config) -> list[str]:
    """What a vault made from the template still lacks to know whose it is. Unset, its owner is a stranger to it."""
    defaults = Config.__dataclass_fields__
    out = []
    if not config.owner_user_ids:
        out.append("no owner_user_ids, so its owner is treated as a stranger")
    if not config.operator_user_ids:
        out.append("no operator_user_ids, so problems only a person can fix reach no one")
    if config.human_name == defaults["human_name"].default:
        out.append(f"human_name is still the template's {config.human_name!r}")
    if config.agent_name == defaults["agent_name"].default:
        out.append(f"agent_name is still the template's {config.agent_name!r}")
    return out


def issue(kind_of: str, thing: str, kind: str, text: str) -> dict:
    return {"class": kind_of, "key": f"{kind_of}:{thing}", "kind": kind, "text": text}


def text_key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def errors_path(config: Config) -> Path:
    return roles.scratch(config) / "errors.jsonl"


def turn_errors(config: Config) -> list[dict]:
    try:
        lines = errors_path(config).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and all(isinstance(entry.get(key), str) for key in ("at", "command", "error")):
            out.append(entry)
    return out


def issues(config: Config, data: dict, structure: list[str], hygiene: list[str], today: date) -> list[dict]:
    """Every finding as {class, key, kind, text}. The key names the thing, so a problem keeps its key from night to night;
    kind is mechanical (the check fixes it), judgment (the doctor) or human (only a person can)."""
    out = [issue("setup", text_key(text), "human", text) for text in data["setup"]]
    live = any(entry["last"] for entry in data["stages"].values())
    out += [issue("stage-stale", stage, "judgment", f"night stage {stage} last ran {entry['last'] or 'never'}")
            for stage, entry in data["stages"].items() if entry["state"] == "stale" or (live and entry["state"] == "never")]
    engine = data["engine"]
    since = engine["pending_migration_since"]
    if since and since != "uncommitted" and since < (today - timedelta(days=1)).isoformat():
        out.append(issue("migration-stuck", engine["recorded"] or "unknown", "judgment", f"a migration has been pending since {since}"))
    if not since and engine["installed"] != "unknown" and engine["recorded"] and engine["installed"] != engine["recorded"]:
        out.append(issue("engine-mismatch", engine["installed"], "mechanical",
                         f"engine {engine['installed']} installed but the vault records {engine['recorded']}"))
    tree = config.vault_path.relative_to(config.repo).as_posix() if config.vault_path != config.repo else ""
    floor = (today - timedelta(days=1)).isoformat()
    for text in structure:
        note = text.split(": ", 1)[0]
        last = (git(config.repo, "log", "-1", "--format=%h %cs", "--", f"{tree}/{note}" if tree else note) or "").split()
        if len(last) == 2 and last[1] >= floor:
            out.append(issue("commit-broke", text_key(text), "judgment", f"{text} (since commit {last[0]})"))
        else:
            out.append(issue("structure", text_key(text), "judgment", text))
    for entry in turn_errors(config):
        out.append(issue("turn-error", text_key(entry["command"] + entry["error"]), "judgment",
                         f"`{entry['command']}` failed in a conversation at {entry['at']}: {entry['error']}"))
    out += [issue("hygiene-unfixed", text_key(text), "judgment", text) for text in hygiene]
    home = runtime.configured()
    for job in runtime.jobs(home) if home else []:
        if job.get("enabled", True) and job.get("last_status") == "failed":
            out.append(issue("routine-failing", job.get("name") or str(job.get("id")), "judgment",
                             f"routine {job.get('name')} failed at {job.get('last_run_at')}: {job.get('last_error') or 'no error recorded'}"))
    oldest = next(iter((git(config.repo, "log", "origin/main..HEAD", "--reverse", "--format=%cs") or "").splitlines()), None)
    if oldest and oldest < (today - timedelta(days=1)).isoformat():
        out.append(issue("unpushed", "origin/main", "mechanical", f"vault commits since {oldest} are not on origin"))
    return out


def report(config: Config, today: date | None = None) -> dict:
    today = today or config.today()
    vault = Vault(config.vault_path)
    yesterday = (today - timedelta(days=1)).isoformat()
    digest = config.vault_path / "Conversations" / "Digests" / f"{yesterday}.md"
    coverage = None
    if digest.exists():
        match = COVERAGE_RE.search(digest.read_text(encoding="utf-8"))
        coverage = match.group(1) if match else "no coverage line"
    tiers = {"hot": 0, "warm": 0, "cold": 0}
    for rel, note in vault.notes.items():
        if vault.kind_for(rel) in KNOWLEDGE and note.data.get("tier") in tiers:
            tiers[note.data["tier"]] += 1
    tasks = config.vault_path / config.agent("Tasks.md")
    open_tasks = sum(1 for task in parse_register(fm.read(tasks)[1])[1] if is_open(task)) if tasks.exists() else 0
    pending = config.vault_path / config.runtime_dir / "pending-migration.md"
    pending_since = None
    if pending.exists():
        rel = pending.relative_to(config.repo).as_posix()
        # The commit that added this file, not the last one touching the path: that can be the deletion of an older one.
        tracked = not (git(config.repo, "status", "--porcelain", "--", rel) or "").startswith(("??", "A"))
        pending_since = (tracked and git(config.repo, "log", "-1", "--diff-filter=A", "--format=%cs", "--", rel)) or "uncommitted"
    try:
        installed = installed_version("agent-brain")
    except PackageNotFoundError:
        installed = "unknown"
    status = git(config.repo, "status", "--porcelain")
    counts = (git(config.repo, "rev-list", "--left-right", "--count", "origin/main...HEAD") or "? ?").split()
    structure = validate.run(vault, set(vault.notes))
    hygiene = validate.hygiene(vault) + validate.broken_bytes(vault)
    data = {
        "agent": config.agent_name,
        "date": today.isoformat(),
        "setup": setup(config),
        "stages": stages(config.repo, today, config.now(), config.edition),
        "engine": {"installed": installed, "recorded": install.recorded_version(config),
                   "pending_migration_since": pending_since},
        "export": {"expected": yesterday, "digest": yesterday if digest.exists() else None, "coverage": coverage},
        "vault": {"validation_problems": len(structure) + len(hygiene), "notes": tiers,
                  "open_tasks": open_tasks},
        "git": {"dirty": None if status is None else len(status.splitlines()), "behind": counts[0], "ahead": counts[1]},
    }
    data["issues"] = issues(config, data, structure, hygiene, today)
    return data


def render(data: dict, *, include_git: bool = True) -> str:
    stage_text = ", ".join(f"{stage} {entry['last'] or 'never'} {entry['state']}" for stage, entry in data["stages"].items())
    engine = data["engine"]
    engine_text = f"{engine['installed']} installed, {engine['recorded']} recorded"
    if engine["pending_migration_since"]:
        engine_text += f", migration pending since {engine['pending_migration_since']}"
    export = data["export"]
    export_text = f"digest {export['digest']}, {export['coverage']}" if export["digest"] else f"no digest for {export['expected']}"
    vault = data["vault"]
    notes = vault["notes"]
    lines = [f"# Health: {data['agent']}, {data['date']}", "",
             *([f"Setup: {'; '.join(data['setup'])}"] if data.get("setup") else []),
             f"Stages: {stage_text}",
             f"Issues: {issues_text(data.get('issues', []))}",
             f"Engine: {engine_text}",
             f"Export: {export_text}",
             f"Vault: {vault['validation_problems']} validation problems; {notes['hot']} hot, {notes['warm']} warm, "
             f"{notes['cold']} cold notes; {vault['open_tasks']} open tasks"]
    if include_git:
        git_state = data["git"]
        tree = "no git repository" if git_state["dirty"] is None else (
            "clean" if git_state["dirty"] == 0 else f"{git_state['dirty']} uncommitted")
        lines.append(f"Git: {tree}, {git_state['ahead']} ahead, {git_state['behind']} behind origin/main")
    return "\n".join(lines) + "\n"


def issues_text(found: list[dict]) -> str:
    """Counts by class only: hygiene texts quote notes, and this file never carries note contents."""
    if not found:
        return "none"
    counts: dict[str, int] = {}
    for item in found:
        counts[item["class"]] = counts.get(item["class"], 0) + 1
    return f"{len(found)} (" + ", ".join(f"{n} {name}" for name, n in counts.items()) + ")"


def write(config: Config, data: dict) -> Path:
    """The committed copy: stable across the hour, so refreshes only commit real change."""
    path = config.vault_path / config.runtime_dir / "health.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "---\nkind: log\n---\n" + render(data, include_git=False)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def to_json(data: dict) -> str:
    return json.dumps(data, indent=2)
