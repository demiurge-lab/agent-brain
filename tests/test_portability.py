import os
import tomllib
from dataclasses import replace
from datetime import date
from importlib.resources import files

import pytest

from brain import fm, install, ledger, matters, portability, responsibilities, schedule, state, validate
from brain.config import Config
from brain.vault import Vault


@pytest.fixture
def config(tmp_path, monkeypatch):
    home = tmp_path / "amicus"
    monkeypatch.setenv("AGENT_HOME", str(home))
    config = Config(home / "vault")
    config.vault_path.mkdir(parents=True)
    (config.repo / "brain.toml").write_text('[brain]\nvault = "memory"\n')
    return config


def responsibility(config, records, form="folder"):
    if form == "folder":
        path = config.vault_path / "Responsibilities/housekeeping/RESPONSIBILITY.md"
        data = {"version": 1, "asked_by": "Owner", "triggers": [{"kind": "schedule", "when": "every 2h"}]}
    else:
        path = config.vault_path / "Housekeeping.md"
        data = {"kind": "hub", "skill": "brain-housekeeping"}
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {**data, "records": records}, "# Housekeeping\n\nKeep the receipts.\n")
    return path


@pytest.mark.parametrize("form", ["folder", "hub"])
@pytest.mark.parametrize("old, rest", [
    ("/Users/owner/agents/amicus/records/housekeeping", "housekeeping"),
    ("/Users/owner/.hermes/profiles/amicus/records/housekeeping", "housekeeping"),
    ("/srv/demiurge/amicus/records/housekeeping", "housekeeping"),
    ("/home/owner/agents/amicus/records/housekeeping", "housekeeping"),
    ("/old/records/backup/records/nested/housekeeping", "nested/housekeeping"),
    ("../../records/housekeeping", None),
    ("Responsibilities/housekeeping/records", None),
    ("/old/custom/housekeeping", None),
])
def test_migrate_records_and_keep_identity(config, form, old, rest):
    path = responsibility(config, old, form)
    original = path.read_bytes()
    before = responsibilities.discover(Vault(config.vault_path))[0]
    report = portability.migrate(config)
    expected = os.path.relpath(install.records_path() / rest, config.vault_path) if rest else old
    assert fm.read(path)[0]["records"] == expected
    assert fm.read(path)[1] == "# Housekeeping\n\nKeep the receipts.\n"
    after = responsibilities.discover(Vault(config.vault_path))[0]
    assert before["id"] == after["id"]
    if rest:
        assert after["id"] == "housekeeping"
    assert after["records"] == expected
    if rest:
        assert len(report) == 1 and f"{old} -> {expected}" in report[0]
    else:
        assert path.read_bytes() == original
        assert bool(report) == old.startswith("/")
        if report:
            assert "left absolute records path without /records/" in report[0]
    migrated = path.read_bytes()
    rerun = portability.migrate(config)
    assert path.read_bytes() == migrated
    assert rerun == (report if old.startswith("/") and rest is None else [])


@pytest.mark.parametrize("layout", [".", "memory", "AgentThinkTree", "nested/memory"])
def test_records_prompt_resolves_from_repository_cwd(config, layout):
    config = replace(config, _vault_path=config.repo) if layout == "." else replace(config, vault=layout)
    (config.repo / "brain.toml").write_text('[brain]\n' if layout == "." else f'[brain]\nvault = "{layout}"\n')
    path = responsibility(config, "/srv/demiurge/amicus/records/housekeeping")
    portability.migrate(config)
    entry = responsibilities.discover(Vault(config.vault_path))[0]
    routine = responsibilities.derive(config, entry)["housekeeping"]
    records = os.path.relpath(install.records_path() / "housekeeping", config.repo)
    hub = path.relative_to(config.repo).as_posix()
    assert routine.prompt == (f"Your responsibility Housekeeping: read {hub} and do what it says. "
                              f"Its state between runs lives in {records}.")
    assert (config.repo / records).resolve() == install.records_path() / "housekeeping"
    assert not validate.responsibilities(Vault(config.vault_path))


@pytest.mark.parametrize("form", ["folder", "hub"])
def test_validate_absolute_records_only_on_responsibilities(config, form):
    path = responsibility(config, "/srv/demiurge/amicus/records/housekeeping", form)
    history = config.vault_path / "Conversations/old.md"
    history.parent.mkdir()
    fm.write(history, {"kind": "conversation", "records": "/Users/owner/old/records/chat"},
             "Ran from /Users/owner/agents/amicus/vault.\n")
    fm.write(config.vault_path / "History.md", {"records": "/home/owner/old"}, "/srv/demiurge/amicus\n")
    errors = [p for p in validate.checks(Vault(config.vault_path)) if "records must be relative" in p]
    assert len(errors) == 1
    assert errors[0].startswith(path.relative_to(config.vault_path).as_posix() + ":")
    portability.migrate(config)
    assert not [p for p in validate.checks(Vault(config.vault_path)) if "records must be relative" in p]
    assert fm.read(history)[0]["records"] == "/Users/owner/old/records/chat"


@pytest.mark.parametrize("prefix", ["/Users/owner/agents/amicus", "/Users/owner/.hermes/profiles/amicus",
                                    "/Users/owner/amicus", "/home/owner/agents/amicus",
                                    "/home/owner/.hermes/profiles/amicus", "/srv/demiurge/amicus"])
@pytest.mark.parametrize("layout", [".", "memory", "AgentThinkTree"])
def test_migrate_schedule_paths_preserves_toml_bytes(config, prefix, layout):
    config = replace(config, _vault_path=config.repo) if layout == "." else replace(config, vault=layout)
    config.vault_path.mkdir(parents=True, exist_ok=True)
    (config.repo / "brain.toml").write_text('[brain]\n' if layout == "." else f'[brain]\nvault = "{layout}"\n')
    source = schedule.path(config)
    source.parent.mkdir(parents=True, exist_ok=True)
    relative = (layout + '/' if layout != '.' else '') + 'Responsibilities/x'
    original = (f'# Keep {prefix}/vault in this comment\r\n'
                '[routines.evening] # Keep this spacing\r\n'
                'every = "2h"\r\n'
                f'prompt  = "Run evening in {prefix}/vault using {prefix}/vault/{relative}. '
                f'Read {prefix}/records/state.json; home is {prefix}." # still here\r\n'
                f"script = '{prefix}/scripts/evening.sh'\r\n"
                f"workdir = '{prefix}/vault'\r\n"
                '\r\n[routines.other]\r\n'
                'every = "1h"\r\n'
                'prompt = "Use /srv/demiurge/tars/vault and /Users/owner/agents/amicus-other/vault '
                'and /srv/demiurge/amicus.extra/vault."\r\n')
    source.write_bytes(original.encode())
    report = portability.migrate(config)
    expected = original.replace(f'in {prefix}/vault using {prefix}/vault/{relative}', f'in . using {relative}')
    expected = expected.replace(f'Read {prefix}/records/state.json; home is {prefix}.',
                                'Read $AGENT_HOME/records/state.json; home is $AGENT_HOME.')
    expected = expected.replace(f"script = '{prefix}/scripts/evening.sh'", "script = '$AGENT_HOME/scripts/evening.sh'")
    assert source.read_bytes() == expected.encode()
    assert len(report) == 2 and "routines.evening.prompt" in report[0] and "routines.evening.script" in report[1]
    assert portability.migrate(config) == []
    assert source.read_bytes() == expected.encode()


@pytest.mark.parametrize("quote", ['"', "'", '"""', "'''"])
def test_schedule_migration_edits_only_routine_prompt_and_script_strings(config, quote):
    source = schedule.path(config)
    source.parent.mkdir(parents=True)
    old = "/srv/demiurge/amicus/vault"
    text = (f'# prompt = "{old}"\n[routines."evening"]\nevery = "2h"\n'
            f'"prompt" = {quote}Run in {old}.{quote}\n'
            f"'script' = '{old}/check.sh'\n")
    if len(quote) == 3:
        text = text.replace(f'Run in {old}.', f'Run in {old}.\n[routines.fake]\nprompt = "{old}"')
    source.write_text(text)
    portability.migrate(config)
    assert source.read_text() == text.replace(f'Run in {old}', 'Run in .').replace(
        f'prompt = "{old}"{quote}', f'prompt = "."{quote}').replace(
        f"'script' = '{old}/check.sh'", "'script' = 'check.sh'")


@pytest.mark.parametrize("key", ["prompt", "script"])
@pytest.mark.parametrize("path", ["/Users/someone/agents/other/vault", "/home/person/records/x",
                                  "/srv/demiurge/other/scripts/x"])
def test_validate_schedule_absolute_workspace_paths(config, key, path):
    schedule.save(config, {"evening": schedule.Routine(every="2h", **{key: f"Run {path}"})})
    errors = validate.schedule_hygiene(Vault(config.vault_path))
    assert len(errors) == 1
    assert f"routines.evening.{key}: absolute workspace path" in errors[0]


def test_validate_accepts_portable_schedule_paths(config):
    schedule.save(config, {"evening": schedule.Routine(every="2h", prompt="Run in memory using ../records/x.",
                                                       script="$AGENT_HOME/scripts/evening.sh")})
    assert validate.schedule_hygiene(Vault(config.vault_path)) == []


@pytest.mark.parametrize("prompt", [
    r'"Run /srv/demiurge/amicus/vault\nThen /srv/demiurge/amicus/records/x"',
    r'"Run \u002fsrv/demiurge/amicus/vault"',
    r'"Use \"/srv/demiurge/amicus/vault\"."',
    r'"Run /srv/demiurge/amicus/vault/ using its skill"',
])
def test_migrate_escaped_toml_strings(config, prompt):
    source = schedule.path(config)
    source.parent.mkdir(parents=True)
    source.write_text('[routines.evening]\nevery = "2h"\nprompt = ' + prompt + ' # keep this\n')
    original = tomllib.loads('prompt = ' + prompt)["prompt"]
    assert len(portability.migrate(config)) == 1
    expected = original.replace('/srv/demiurge/amicus/vault/', '.').replace(
        '/srv/demiurge/amicus/vault', '.').replace('/srv/demiurge/amicus', '$AGENT_HOME')
    assert schedule.load(config)["evening"].prompt == expected
    assert source.read_text().endswith(' # keep this\n')
    assert portability.migrate(config) == []


def test_legacy_readers_use_records_basename(config):
    responsibility(config, "../../records/housekeeping", "hub")
    vault = Vault(config.vault_path)
    assert ledger.routine_for_skill(config, "brain-housekeeping") == "housekeeping"
    hubs = state._hubs(vault, [], [], [], date(2026, 11, 17))
    assert state._matches_routine(config, hubs[0], "housekeeping")
    assert [r["name"] for r in state._routines(config, hubs, [])] == ["housekeeping"]
    assert not matters._is_matter(vault, "Housekeeping.md", set())


def test_migrate_check_applies_portability_before_queuing_versioned_prompt(config, monkeypatch, capsys):
    path = responsibility(config, "/srv/demiurge/amicus/records/housekeeping")
    install.record_version(config, "0.204.6")
    monkeypatch.setattr(install, "installed_version", lambda name: "0.205.0")
    pending = install.migrate_check(config)
    assert fm.read(path)[0]["records"] == "../../records/housekeeping"
    assert "## 0.205.0" in pending.read_text()
    assert "records /srv/demiurge/amicus/records/housekeeping -> ../../records/housekeeping" in capsys.readouterr().err
    assert install.recorded_version(config) == "0.204.6"
    assert install.migrate_done(config) == "0.205.0"
    assert install.pending_migrations(config) == []
    assert install.migrate_check(config) is None
    migration = files("brain").joinpath("migrations/0.205.0.md").read_text()
    assert len(migration.split()) < 300 and "unattended: true" in migration
