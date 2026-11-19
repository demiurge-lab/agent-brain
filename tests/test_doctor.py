import json
from datetime import datetime, timedelta

from brain import cli, doctor, health
from brain.config import Config
from conftest import make_vault


def issue(kind_of, thing, kind):
    return {"class": kind_of, "key": f"{kind_of}:{thing}", "kind": kind, "text": f"{kind_of} {thing}"}


def setup(tmp_path, monkeypatch, *found):
    root = make_vault(tmp_path / "memory")
    issues = list(found)
    monkeypatch.setattr(health, "report", lambda config, today=None: {"issues": list(issues)})
    return Config.load(root), issues


def at(day, hour=3):
    return datetime(2026, 11, day, hour)


def test_nothing_to_hand_on_prints_nothing(tmp_path, monkeypatch):
    config, _ = setup(tmp_path, monkeypatch)
    assert doctor.check(config, now=at(19)) == ""


def test_mechanical_fixes_run_and_only_what_remains_is_handed_on(tmp_path, monkeypatch):
    config, issues = setup(tmp_path, monkeypatch, issue("unpushed", "origin/main", "mechanical"),
                           issue("engine-mismatch", "0.2.0", "mechanical"))
    def fix(config, found):
        if found["class"] == "engine-mismatch":
            raise RuntimeError("no network")
        issues.remove(found)
        return f"{found['key']}: pushed"
    monkeypatch.setattr(doctor, "_fix", fix)
    out = json.loads(doctor.check(config, now=at(19)))
    assert out["fixed"] == ["unpushed:origin/main: pushed", "engine-mismatch:0.2.0: fix failed: no network"]
    assert [found["key"] for found in out["issues"]] == ["engine-mismatch:0.2.0"]


def test_human_issues_are_reported_once_and_again_only_after_they_went_away(tmp_path, monkeypatch):
    found = issue("setup", "abc", "human")
    config, issues = setup(tmp_path, monkeypatch, found)
    out = json.loads(doctor.check(config, now=at(19)))
    assert out["issues"] == [] and [item["key"] for item in out["report"]] == ["setup:abc"]
    assert doctor.check(config, now=at(20)) == ""
    issues.clear()
    assert doctor.check(config, now=at(21)) == ""
    issues.append(found)
    assert json.loads(doctor.check(config, now=at(22)))["report"][0]["key"] == "setup:abc"


def test_a_key_handed_on_three_nights_becomes_a_repeat_for_the_operator(tmp_path, monkeypatch):
    config, _ = setup(tmp_path, monkeypatch, issue("stage-stale", "sleep1", "judgment"))
    for day in (19, 20, 21):
        assert [item["key"] for item in json.loads(doctor.check(config, now=at(day)))["issues"]] == ["stage-stale:sleep1"]
    out = json.loads(doctor.check(config, now=at(22)))
    assert out["issues"] == [] and out["report"][0]["repeat"] is True
    assert doctor.check(config, now=at(23)) == ""


def test_day_triggers_wake_the_doctor_only_for_something_new_and_not_within_three_hours(tmp_path, monkeypatch):
    config, issues = setup(tmp_path, monkeypatch, issue("hygiene-unfixed", "h1", "judgment"))
    assert doctor.check(config, "run-failed", now=at(19, 10)) == ""
    issues.append(issue("routine-failing", "evening", "judgment"))
    out = json.loads(doctor.check(config, "run-failed", now=at(19, 10)))
    assert [item["key"] for item in out["issues"]] == ["routine-failing:evening"] and out["trigger"] == "run-failed"
    issues.append(issue("structure", "s1", "judgment"))
    assert doctor.check(config, "commit", now=at(19, 12)) == ""
    later = json.loads(doctor.check(config, "commit", now=at(19, 10) + timedelta(hours=3, minutes=1)))
    assert {item["key"] for item in later["issues"]} == {"routine-failing:evening", "structure:s1"}
    assert doctor.check(config, "commit", now=at(19, 18)) == ""
    night = json.loads(doctor.check(config, now=at(20)))
    assert {item["key"] for item in night["issues"]} == {"hygiene-unfixed:h1", "routine-failing:evening", "structure:s1"}


def test_the_command_prints_the_json_or_nothing(tmp_path, monkeypatch, capsys):
    config, issues = setup(tmp_path, monkeypatch)
    cli.main(["doctor", "check", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out == ""
    issues.append(issue("structure", "s1", "judgment"))
    cli.main(["doctor", "check", "--vault", str(config.vault_path), "--trigger", "upgrade"])
    assert json.loads(capsys.readouterr().out)["trigger"] == "upgrade"
