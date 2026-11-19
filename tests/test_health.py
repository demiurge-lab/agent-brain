"""Health report: stages from commit subjects, engine state, counts; never note contents."""
import subprocess
from datetime import date, timedelta

from brain import cli, fm, health
from brain.config import Config
from conftest import make_vault


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                   text=True, stderr=subprocess.PIPE).strip()


def repo_vault(tmp_path):
    root = make_vault(tmp_path / "memory")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    for subject in ("sleep1(2026-10-03)", "attribution(2026-10-05)", "sleep1(2026-10-05)", "prime(2026-10-06)"):
        git(tmp_path, "commit", "-q", "--allow-empty", "-m", subject)
    (root / "Conversations/Digests").mkdir(parents=True)
    (root / "Conversations/Digests/2026-10-05.md").write_text(
        "---\nkind: log\n---\n\ncoverage: 2 sessions included, 9 routine sessions and 1 evaluation sessions excluded, 0 turns omitted by the word cap\n")
    (root / "Runtime").mkdir()
    (root / "Runtime/engine-version").write_text("0.1.0\n")
    (root / "Runtime/pending-migration.md").write_text("---\nkind: log\n---\n# pending\n")
    (root / "people/Alice").mkdir(parents=True)
    fm.write(root / "people/Alice/Profile.md", {"tier": "warm"}, "# Alice\nPRIVATE FACT about Alice.\n")
    return root


def test_report_reads_stages_engine_export_and_counts_and_flags_stale(tmp_path):
    root = repo_vault(tmp_path)
    data = health.report(Config.load(root), date(2026, 10, 6))
    assert data["stages"]["sleep1"] == {"last": "2026-10-05", "state": "ok"}
    assert data["stages"]["prime"] == {"last": "2026-10-06", "state": "ok"}
    assert data["stages"]["attribution"]["state"] == "ok"
    assert "sleep2" not in data["stages"]
    assert data["stages"]["image"] == {"last": None, "state": "never"}
    assert data["engine"]["recorded"] == "0.1.0" and data["engine"]["pending_migration_since"] == "uncommitted"
    assert data["export"]["digest"] == "2026-10-05" and data["export"]["coverage"].startswith("2 sessions included")
    assert data["vault"]["open_tasks"] == 1 and data["vault"]["notes"]["warm"] == 1
    assert data["git"]["dirty"] > 0 and data["git"]["ahead"] == "?"
    data = health.report(Config.load(root), date(2026, 10, 9))
    assert data["stages"]["sleep1"]["state"] == "stale"
    assert data["export"] == {"expected": "2026-10-08", "digest": None, "coverage": None}
    assert "no digest for 2026-10-08" in health.render(data)


def test_render_and_write_carry_no_note_contents(tmp_path, capsys):
    root = repo_vault(tmp_path)
    text = health.render(health.report(Config.load(root), date(2026, 10, 6)))
    assert text.startswith("# Health: Agent, 2026-10-06\n") and "PRIVATE" not in text and "Alice" not in text
    assert "Git:" in text
    cli.main(["health", "--vault", str(root), "--date", "2026-10-06", "--write"])
    written = root / "Runtime/health.md"
    assert str(written) in capsys.readouterr().out
    saved = written.read_text()
    assert saved.startswith("---\nkind: log\n---\n# Health:") and "Git:" not in saved
    before = written.stat().st_mtime_ns
    cli.main(["health", "--vault", str(root), "--date", "2026-10-06", "--write"])
    assert written.stat().st_mtime_ns == before
    cli.main(["health", "--vault", str(root), "--date", "2026-10-06", "--json"])
    assert '"stages"' in capsys.readouterr().out


def test_report_outside_git_is_still_a_report(tmp_path):
    root = make_vault(tmp_path / "memory")
    data = health.report(Config.load(root), date(2026, 10, 6))
    assert all(entry["state"] == "never" for entry in data["stages"].values())
    assert "no git repository" in health.render(data)


def test_a_vault_that_does_not_know_whose_it_is_says_so(tmp_path, capsys):
    root = repo_vault(tmp_path)
    data = health.report(Config.load(root), date(2026, 10, 6))
    assert data["setup"] == ["no owner_user_ids, so its owner is treated as a stranger",
                             "no operator_user_ids, so problems only a person can fix reach no one",
                             "human_name is still the template's 'Human'", "agent_name is still the template's 'Agent'"]
    assert "Setup: no owner_user_ids, so its owner is treated as a stranger; no operator_user_ids" in health.render(data)
    (tmp_path / "brain.toml").write_text('[brain]\nvault = "memory"\nhuman_name = "Aamir"\nagent_name = "Iqbal"\n'
                                         'owner_user_ids = ["demiurge:k57"]\noperator_user_ids = ["demiurge:op1"]\n')
    data = health.report(Config.load(root), date(2026, 10, 6))
    assert data["setup"] == [] and "Setup:" not in health.render(data)


def test_issues_carry_class_stable_key_and_kind(tmp_path, monkeypatch):
    root = repo_vault(tmp_path)
    home = tmp_path / "home"
    (home / "harness").mkdir(parents=True)
    (home / "harness/jobs.json").write_text('{"jobs": [{"id": "j1", "name": "evening", "lastStatus": "failed", '
                                             '"lastRunAt": "2026-10-08T20:00:00", "lastError": "boom"}, '
                                             '{"id": "j2", "name": "watch", "lastStatus": "ok"}]}')
    monkeypatch.setenv("AGENT_HOME", str(home))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "state")
    later = date.today() + timedelta(days=3)
    data = health.report(Config.load(root), later)
    by_class = {}
    for found in data["issues"]:
        assert set(found) == {"class", "key", "kind", "text"} and found["key"].startswith(found["class"] + ":")
        by_class.setdefault(found["class"], []).append(found)
    assert {found["kind"] for found in by_class["setup"]} == {"human"}
    assert {found["key"] for found in by_class["stage-stale"]} >= {"stage-stale:sleep1", "stage-stale:image"}
    assert by_class["routine-failing"] == [{"class": "routine-failing", "key": "routine-failing:evening", "kind": "judgment",
                                            "text": "routine evening failed at 2026-10-08T20:00:00: boom"}]
    assert [found["key"] for found in by_class["migration-stuck"]] == ["migration-stuck:0.1.0"]
    again = health.report(Config.load(root), later)
    assert [found["key"] for found in again["issues"]] == [found["key"] for found in data["issues"]]
    line = next(line for line in health.render(data).splitlines() if line.startswith("Issues: "))
    assert "routine-failing" in line and "boom" not in line


def test_a_vault_whose_night_never_ran_is_not_stale(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_HOME", raising=False)
    root = make_vault(tmp_path / "memory")
    data = health.report(Config.load(root), date(2026, 10, 6))
    assert not any(found["class"] in ("stage-stale", "routine-failing", "unpushed") for found in data["issues"])


def test_setup_gate_fails_until_names_and_operators_are_set(tmp_path, capsys):
    import pytest
    root = repo_vault(tmp_path)
    with pytest.raises(SystemExit) as gate:
        cli.main(["health", "--vault", str(root), "--setup"])
    out = capsys.readouterr().out
    assert gate.value.code == 1 and "human_name is still the template's 'Human'" in out and "no owner_user_ids" not in out
    (tmp_path / "brain.toml").write_text('[brain]\nvault = "memory"\nhuman_name = "Srikar"\nagent_name = "Plutus"\n'
                                         'operator_user_ids = ["demiurge:op1"]\n')
    with pytest.raises(SystemExit) as gate:
        cli.main(["health", "--vault", str(root), "--setup"])
    assert gate.value.code == 0 and capsys.readouterr().out == ""


def test_a_fresh_structure_problem_names_the_commit_that_made_it(tmp_path):
    root = repo_vault(tmp_path)
    (root / "Broken.md").write_text("# Broken\nno frontmatter\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "write broken")
    sha = git(tmp_path, "rev-parse", "--short", "HEAD")
    found = [item for item in health.report(Config.load(root), date.today())["issues"] if "Broken.md" in item["text"]]
    assert [(item["class"], item["text"]) for item in found] == [
        ("commit-broke", f"Broken.md: missing frontmatter key tier (since commit {sha})")]
    later = [item for item in health.report(Config.load(root), date.today() + timedelta(days=5))["issues"] if "Broken.md" in item["text"]]
    assert [item["class"] for item in later] == ["structure"]


def test_failed_turn_commands_are_issues_until_the_doctor_logs_them(tmp_path, capsys):
    root = repo_vault(tmp_path)
    cli.main(["doctor", "error", "brain task T-9 set   --next x", "--vault", str(root), "--error", "unknown task T-9\n"])
    cli.main(["doctor", "error", "brain loops --close abc", "--vault", str(root), "--error", ""])
    found = [item for item in health.report(Config.load(root))["issues"] if item["class"] == "turn-error"]
    assert [item["text"].split(" failed")[0] for item in found] == ["`brain task T-9 set --next x`", "`brain loops --close abc`"]
    assert found[0]["text"].endswith(": unknown task T-9") and found[1]["text"].endswith(": no error output")
    cli.main(["doctor", "log", found[0]["key"], "fixed", "--vault", str(root), "--note", "T-9 was archived; used T-10"])
    left = [item["key"] for item in health.report(Config.load(root))["issues"] if item["class"] == "turn-error"]
    assert left == [found[1]["key"]]
