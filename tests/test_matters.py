"""A matter's kept state, and what the app is given for it."""
import json
import re
from datetime import date, datetime, timedelta

import pytest

from brain import cli, fm, journal, loops, matters, needs, record, state
from brain.config import Config
from brain.tasks import Task
from brain.vault import Vault

TODAY = date(2026, 11, 11)
NOW = ("## Now\n- **Now:** flights booked; still need a place · 2026-11-10\n"
       "- **Soon:** 2026-11-20 · fly to Lisbon\n- **Last:** 2026-11-09 · talked dates with Rohan\n")


def test_state_reads_the_three_lines():
    assert matters.state("# Lisbon\n" + NOW + "## Findings\n- **Now:** not this one · 2026-11-11\n", TODAY) == {
        "now": {"text": "flights booked; still need a place", "date": "2026-11-10"},
        "soon": {"text": "fly to Lisbon", "date": "2026-11-20"},
        "last": {"text": "talked dates with Rohan", "date": "2026-11-09"}}
    assert matters.state("# Lisbon\n", TODAY) == {"now": None, "soon": None, "last": None}
    assert matters.state("## Now\n- **Now:** no date\n- **Soon:** 2026-13-40 · bad\n", TODAY)["now"] is None


def test_a_stale_now_is_given_as_last():
    body = "## Now\n- **Now:** looking at flights · 2026-10-20\n- **Last:** 2026-10-15 · first talked\n"
    assert matters.state(body, TODAY) == {"now": None, "soon": None,
                                          "last": {"text": "looking at flights", "date": "2026-10-20"}}
    later = "## Now\n- **Now:** looking at flights · 2026-10-20\n- **Last:** 2026-10-25 · booked\n"
    assert matters.state(later, TODAY)["last"] == {"text": "booked", "date": "2026-10-25"}


@pytest.mark.parametrize("body, open_work, expected", [
    (NOW, False, "current"),
    ("## Now\n- **Soon:** 2026-11-30 · exam\n", False, "soon"),
    ("## Now\n- **Soon:** 2026-12-20 · exam\n", False, "fading"),
    ("## Now\n- **Soon:** 2026-11-05 · flew\n", False, "current"),
    ("## Now\n- **Last:** 2026-10-01 · long ago\n", False, "fading"),
    ("## Now\n- **Last:** 2026-10-01 · long ago\n", True, "current"),
    ("", False, "fading"),
])
def test_phase(body, open_work, expected):
    assert matters.phase(matters.state(body, TODAY), TODAY, open_work) == expected


@pytest.mark.parametrize("field", ["Now", "Last", "Soon"])
@pytest.mark.parametrize("age, expected", [(7, "current"), (8, "fading"), (14, "fading")])
def test_a_quiet_week_retires_a_matter(field, age, expected):
    when = (TODAY - timedelta(days=age)).isoformat()
    value = f"flights booked · {when}" if field == "Now" else f"{when} · flights booked"
    kept = matters.state(f"## Now\n- **{field}:** {value}\n", TODAY)
    assert matters.phase(kept, TODAY, False) == expected
    if field == "Now":
        assert (kept["now"] is not None) == (age == 7)


def test_loops_keep_their_hub_line():
    item = loops.Item("waiting", "Hear back on the flat", "2026-11-11 09:00", "Conversations", "Rohan", hub="Trips/Lisbon.md")
    rendered = loops.render(item)
    assert "\n  - hub: Trips/Lisbon.md" in rendered
    assert loops.parse_section("## Open\n" + rendered) == [item]


@pytest.fixture
def lisbon(vault):
    config = Config.load(vault)
    (vault / "Trips").mkdir()
    fm.write(vault / "Trips/Lisbon.md", {"kind": "hub"}, "# Lisbon\n" + NOW)
    fm.write(vault / "Quiet.md", {"kind": "hub"}, "# Quiet\n## Now\n- **Last:** 2026-09-01 · done\n")
    fm.write(vault / "Watch.md", {"kind": "hub", "skill": "brain-watch", "records": "receipts"}, "# Watch\n" + NOW)
    fm.write(vault / "agent/Tasks.md", {}, "# Tasks\n\n" + Task("T-001", "Find a flat", "- **Hub:** [[Lisbon]]\n",
                                                                 {"state": "in_progress"}).render())
    fm.write(loops.path_for(config), {}, "## Open\n\n" + "\n".join(loops.render(item) for item in [
        loops.Item("waiting", "Flat answer", "2026-11-11 09:00", "Conversations", "Rohan", hub="[[Trips/Lisbon]]"),
        loops.Item("owed", "Send the dates", "2026-11-11 09:00", "Quiet", "Rohan", hub="Trips/Lisbon.md"),
        loops.Item("waiting", "Flat deposit", "2026-11-11 09:00", "Quiet", "Rohan", task="T-001"),
        loops.Item("owed", "Pack list", "2026-11-11 09:00", "Trips/Lisbon", "Rohan"),
        loops.Item("owed", "Something else", "2026-11-11 09:00", "Quiet", "Rohan"),
    ]) + "\n")
    fm.write(vault / "CURRENT.md", {}, "# Current\n## Needs you\n- 2026-11-11 · ask · Which neighbourhood? · [[Quiet]]\n"
             "  - hub: Trips/Lisbon\n- 2026-11-11 · ask · Unrelated? · [[Quiet]]\n")
    return config


def test_state_gives_each_matter_its_state_waiting_and_phase(lisbon):
    built = state.build(lisbon, TODAY)
    hubs = {hub["title"]: hub for hub in built["hubs"]}
    ids = {item["text"]: item["id"] for item in built["loops"] + built["needsYou"]}
    lisbon_hub = hubs["Lisbon"]
    assert lisbon_hub["now"]["text"] == "flights booked; still need a place"
    assert lisbon_hub["phase"] == "current"
    assert sorted(lisbon_hub["waiting"]) == sorted(ids[text] for text in (
        "Flat answer", "Send the dates", "Flat deposit", "Pack list", "Which neighbourhood?"))
    assert sorted(hubs["Quiet"]["waiting"]) == sorted([ids["Something else"], ids["Unrelated?"]])
    assert hubs["Quiet"]["phase"] == "current"
    assert "phase" not in hubs["Watch"] and "now" not in hubs["Watch"]


def test_only_live_work_keeps_a_matter_current(vault):
    config = Config.load(vault)
    for title in ("Paper", "Inbox", "Visa"):
        fm.write(vault / f"{title}.md", {"kind": "hub"}, f"# {title}\n")
    fm.write(vault / "agent/Tasks.md", {}, "# Tasks\n\n" + "".join(Task(tid, title, f"- **Hub:** [[{hub}]]\n", fields).render()
             for tid, title, hub, fields in (
                 ("T-001", "Draft the paper", "Paper", {"state": "in_progress"}),
                 ("T-002", "Sort the inbox", "Inbox", {"state": "set_aside"}),
                 ("T-003", "Renew the visa", "Visa", {"state": "yours", "later": "true"}))))
    phases = {hub["title"]: hub["phase"] for hub in state.build(config, TODAY)["hubs"] if hub["title"] in ("Paper", "Inbox", "Visa")}
    assert phases == {"Paper": "current", "Inbox": "fading", "Visa": "fading"}


def test_a_matter_with_no_now_shows_its_live_task_status(vault):
    config = Config.load(vault)
    fm.write(vault / "Paper.md", {"kind": "hub"}, "# Paper\n")
    fm.write(vault / "Visa.md", {"kind": "hub"}, "# Visa\n" + NOW.replace("2026-11-10", "2026-11-11"))
    fm.write(vault / "agent/Tasks.md", {}, "# Tasks\n\n" + "".join(Task(tid, title, body, fields).render()
             for tid, title, body, fields in (
                 ("T-001", "Draft", "- **Hub:** [[Paper]]\n- **Status:** methods drafted\n"
                  "- **Latest evidence:** 2026-11-10 [[Draft]]\n", {"state": "in_progress"}),
                 ("T-002", "Figures", "- **Hub:** [[Paper]]\n- **Status:** figures ready for review\n"
                  "- **Latest evidence:** 2026-11-11 [[Figures]]\n", {"state": "in_progress"}),
                 ("T-003", "Old", "- **Hub:** [[Paper]]\n- **Status:** abandoned outline\n"
                  "- **Latest evidence:** 2026-11-11 [[Old]]\n", {"state": "set_aside"}),
                 ("T-004", "Forms", "- **Hub:** [[Visa]]\n- **Status:** forms filed\n"
                  "- **Latest evidence:** 2026-11-11 [[Forms]]\n", {"state": "in_progress"}))))
    hubs = {hub["title"]: hub for hub in state.build(config, TODAY)["hubs"]}
    assert hubs["Paper"]["now"] == {"text": "figures ready for review", "date": "2026-11-11"}
    assert hubs["Visa"]["now"]["text"] == "flights booked; still need a place"


def test_a_private_hub_line_is_not_exported(lisbon, vault):
    fm.write(vault / "Secret.md", {"kind": "hub", "tier": "private"}, "# Secret\n")
    fm.write(loops.path_for(lisbon), {}, "## Open\n\n" + loops.render(
        loops.Item("owed", "Hidden", "2026-11-11 09:00", "Trips/Lisbon", "Rohan", hub="Secret.md")) + "\n")
    built = state.build(lisbon, TODAY)
    assert built["loops"][0]["hub"] == ""
    assert built["loops"][0]["id"] in next(hub for hub in built["hubs"] if hub["title"] == "Lisbon")["waiting"]


AT = datetime(2026, 11, 11, 14, 5)


@pytest.fixture
def owner(vault, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Config, "today", lambda self: TODAY)
    (tmp_path / "home").mkdir()
    fm.write(vault / "Lisbon trip.md", {"kind": "hub", "tier": "warm"}, "# Lisbon trip\n\n## Findings\n- Alfama is hilly.\n")
    return Config.load(vault)


def talk(config, headline, about, at=AT, **fields):
    if fields.get("needsYou"):
        fields.setdefault("needsYouWhy", "Your choice decides how this continues; no leaves it as it is.")
    journal.append_entry(config, {"headline": headline, "outcome": "progress", "about": about, **fields}, "cli", at)


def test_a_named_matter_gets_its_now_and_last_and_owns_what_the_record_opens(owner):
    talk(owner, "Talked dates with Rohan", [{"name": "lisbon TRIP", "now": "flights booked; still need a place"}],
         loops=[{"kind": "waiting", "text": "Rohan confirms the dates", "with": "Rohan", "due": None}],
         needsYou="Want the Alfama flat or the one in Graça?")
    body = fm.read(owner.vault_path / "Lisbon trip.md")[1]
    assert body.index("## Now") < body.index("## Findings")
    assert "- **Now:** flights booked; still need a place · 2026-11-11\n- **Last:** 2026-11-11 · Talked dates with Rohan\n" in body
    assert loops.open_loops(owner)[0].hub == "Lisbon trip.md"
    assert "  - hub: Lisbon trip.md" in (owner.vault_path / "CURRENT.md").read_text()
    talk(owner, "Picked a flat", [{"name": "Lisbon trip", "now": None}], at=datetime(2026, 11, 12, 9, 0))
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], date(2026, 11, 12))
    assert kept["now"]["text"] == "flights booked; still need a place"
    assert kept["last"] == {"text": "Picked a flat", "date": "2026-11-12"}


def test_a_new_name_becomes_a_matter_on_its_second_sighting(owner):
    talk(owner, "Mentioned the Ohio move", [{"name": "Ohio move", "now": "thinking about it"}])
    assert not (owner.vault_path / "Ohio move.md").exists()
    talk(owner, "Ohio move again", [{"name": "Ohio move", "now": "picked Columbus"}], at=datetime(2026, 11, 20, 9, 0))
    data, body = fm.read(owner.vault_path / "Ohio move.md")
    assert re.fullmatch(r"m-[0-9a-f]{8}", data.pop("matter_id"))
    assert data == {"kind": "hub", "tier": "warm"}
    assert "- **Now:** picked Columbus · 2026-11-20" in body


def test_a_sighting_a_month_old_does_not_count(owner):
    talk(owner, "Mentioned a kayak", [{"name": "Kayak", "now": None}], at=datetime(2026, 9, 1, 9, 0))
    talk(owner, "Mentioned a kayak", [{"name": "Kayak", "now": None}])
    assert not (owner.vault_path / "Kayak.md").exists()


def test_a_first_sighting_that_opens_a_loop_is_a_matter_at_once(owner):
    talk(owner, "Planned Rohan's visit", [{"name": "Rohan's visit", "now": "he lands Dec 3"}],
         loops=[{"kind": "promised", "text": "Send Rohan the address", "with": "Rohan", "due": None}])
    assert (owner.vault_path / "Rohan's visit.md").exists()
    assert loops.open_loops(owner)[0].hub == "Rohan's visit.md"


def test_only_the_owner_writes_matters(owner):
    journal.append_entry(owner, {"headline": "Kevin talked about Lisbon", "outcome": "progress",
                                 "about": [{"name": "Lisbon trip", "now": "cancelled"}]}, "demiurge", AT, user_id="k1")
    assert "## Now" not in fm.read(owner.vault_path / "Lisbon trip.md")[1]


def test_a_responsibility_or_unsafe_name_is_never_a_matter(owner):
    fm.write(owner.vault_path / "Watch.md", {"kind": "hub", "skill": "brain-watch", "records": "receipts"}, "# Watch\n")
    talk(owner, "Checked the watch", [{"name": "Watch", "now": "fine"}, {"name": "../../etc", "now": None}],
         needsYou="Want the watch to run hourly?")
    assert "## Now" not in fm.read(owner.vault_path / "Watch.md")[1]
    assert not (owner.vault_path / ".." / "etc.md").exists()
    assert (owner.vault_path / "etc.md").exists()


def test_the_record_is_shown_the_matters_by_name(owner):
    talk(owner, "Talked dates", [{"name": "Lisbon trip", "now": "flights booked"}])
    assert "Matters in your owner's life; name these exactly in about:\n- Lisbon trip" in record.held(owner)


def test_a_first_sighting_with_a_date_is_a_matter_and_keeps_the_sooner_soon(owner):
    talk(owner, "Heard Theo is visiting", [{"name": "Theo's visit", "now": None, "soon": "2026-12-03 · Theo lands"}])
    body = fm.read(owner.vault_path / "Theo's visit.md")[1]
    assert "- **Soon:** 2026-12-03 · Theo lands" in body
    talk(owner, "Theo's return flight", [{"name": "Theo's visit", "now": None, "soon": "2026-12-07 · Theo flies home"}])
    talk(owner, "Theo's dinner", [{"name": "Theo's visit", "now": None, "soon": "2026-12-01 · book dinner"}])
    assert matters.state(fm.read(owner.vault_path / "Theo's visit.md")[1], date(2026, 11, 12))["soon"] == {
        "text": "book dinner", "date": "2026-12-01"}


def test_an_ask_opens_and_links_its_matter_without_judging_its_wording(owner):
    talk(owner, "Mentioned the Ohio move", [{"name": "Ohio move", "now": None}], needsYou="Proceed?")
    assert (owner.vault_path / "Ohio move.md").exists()
    question, = needs.items(owner, owner.today())
    assert question.text == "Proceed?" and question.hub == "Ohio move.md"


def test_a_matter_named_by_its_path_is_found(owner, vault):
    (vault / "Trips").mkdir()
    fm.write(vault / "Trips/Lisbon.md", {"kind": "hub"}, "# Lisbon\n")
    talk(owner, "Talked Lisbon", [{"name": "Trips/Lisbon", "now": "booked"}])
    assert "**Now:** booked" in fm.read(vault / "Trips/Lisbon.md")[1]
    assert not (vault / "Trips Lisbon.md").exists()


def test_a_task_serving_a_matter_moves_its_last_and_soon(owner):
    from brain import tasks
    fm.write(owner.vault_path / "agent/Tasks.md", {}, "# Tasks\n\n" + "\n".join(task.render() for task in [
        Task("T-001", "Find a flat in Alfama", "- **Hub:** [[Lisbon trip]]\n- **Due:** 2026-11-18\n", {"state": "ready"}),
        Task("T-002", "Tidy the watch", "- **Hub:** [[Watch]]\n", {"state": "ready"}),
    ]))
    fm.write(owner.vault_path / "Watch.md", {"kind": "hub", "skill": "brain-watch", "records": "receipts"}, "# Watch\n")
    tasks.hand_off(owner, "T-001", TODAY, "")
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], TODAY)
    assert kept["last"] == {"text": "handed off Find a flat in Alfama", "date": "2026-11-11"}
    assert kept["soon"] == {"text": "Find a flat in Alfama", "date": "2026-11-18"}
    tasks.close(owner, "T-001", date(2026, 11, 12), True, "Booked the flat on Rua X", by_owner=True)
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], date(2026, 11, 12))
    assert kept["last"] == {"text": "finished Find a flat in Alfama", "date": "2026-11-12"}
    tasks.close(owner, "T-002", TODAY, False, "No longer needed", by_owner=True)
    assert "## Now" not in fm.read(owner.vault_path / "Watch.md")[1]


def test_the_morning_sets_soon_from_the_calendar_and_opens_at_most_two(owner):
    matters.soon(owner, "Lisbon trip", date(2026, 11, 20), "fly to Lisbon", TODAY)
    matters.soon(owner, "lisbon trip", date(2026, 11, 25), "fly home", TODAY)
    assert matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], TODAY)["soon"] == {
        "text": "fly to Lisbon", "date": "2026-11-20"}
    with pytest.raises(ValueError, match="--open"):
        matters.soon(owner, "Dentist", date(2026, 11, 14), "cleaning", TODAY)
    with pytest.raises(ValueError, match="between today"):
        matters.soon(owner, "Lisbon trip", date(2026, 12, 20), "too far", TODAY)
    matters.soon(owner, "Dentist", date(2026, 11, 14), "cleaning", TODAY, open_=True)
    matters.soon(owner, "Chem exam", date(2026, 11, 19), "final", TODAY, open_=True)
    with pytest.raises(ValueError, match="already opened today"):
        matters.soon(owner, "Move", date(2026, 11, 28), "movers", TODAY, open_=True)
    data, _ = fm.read(owner.vault_path / "Dentist.md")
    assert re.fullmatch(r"m-[0-9a-f]{8}", data.pop("matter_id"))
    assert data == {"kind": "hub", "tier": "warm", "opened": "2026-11-11"}
    listed = matters.lines(owner, TODAY)
    assert "- Lisbon trip · soon 2026-11-20: fly to Lisbon" in listed and len(listed) == 3


def test_a_task_the_conversation_made_takes_its_matter(owner):
    from brain import tasks
    fm.write(owner.vault_path / "agent/Tasks.md", {}, "# Tasks\n\n" + "\n".join(task.render() for task in [
        Task("T-001", "Send Theo the address", "- **Report:** demiurge:abc\n", {"state": "yours"}),
        Task("T-002", "Book a table", "- **Report:** demiurge:abc\n- **Hub:** [[Dinners]]\n", {"state": "ready"}),
        Task("T-003", "Other chat's task", "- **Report:** demiurge:xyz\n", {"state": "ready"}),
    ]))
    journal.append_entry(owner, {"headline": "Planned Theo's visit", "outcome": "progress",
                                 "about": [{"name": "Theo's visit", "now": None, "soon": "2026-12-03 · Theo lands"}]},
                         "cli", AT, session="demiurge:abc")
    hubs = {task.tid: task.hub for task in tasks.all_tasks(owner)}
    assert hubs == {"T-001": "Theo's visit", "T-002": "Dinners", "T-003": None}


def test_the_record_is_shown_where_each_matter_stands(owner):
    talk(owner, "Talked dates", [{"name": "Lisbon trip", "now": "flights booked"}])
    assert "- Lisbon trip · now: flights booked" in record.held(owner)


@pytest.mark.parametrize("field", ["Now", "Last", "Soon", "Aside"])
@pytest.mark.parametrize("age, recognised", [(60, True), (61, False)])
def test_the_record_recognises_recently_quiet_matters(owner, capsys, field, age, recognised):
    when = (TODAY - timedelta(days=age)).isoformat()
    value = f"taking a break · {when}" if field == "Now" else f"{when} · taking a break"
    fm.write(owner.vault_path / "Kayak trip.md", {"kind": "hub", "matter_id": "m-1234abcd"},
             f"# Kayak trip\n## Now\n- **{field}:** {value}\n")
    cli.main(["record-spec", "--vault", str(owner.vault_path), "--json"])
    prompt = json.loads(capsys.readouterr().out)["held"]
    assert ("Matters that went quiet lately; if one comes up again, name it exactly in about:\n- Kayak trip" in prompt) == recognised
    assert "Matters in your owner's life" not in prompt
    if recognised:
        talk(owner, "Looked at paddling routes again", [{"name": "Kayak trip", "now": "planning a spring outing"}])
        data, body = fm.read(owner.vault_path / "Kayak trip.md")
        assert data["matter_id"] == "m-1234abcd"
        kept = matters.state(body, TODAY)
        assert matters.phase(kept, TODAY, False) == "current"
        assert "aside" not in kept
        assert [source for source in Vault(owner.vault_path).notes if "Kayak" in source] == ["Kayak trip.md"]


def test_quiet_matters_use_the_latest_date_and_exclude_private_and_standing_notes(owner):
    body = "# Paused\n## Now\n- **Last:** 2026-08-01 · planned\n- **Aside:** 2026-11-10 · paused\n"
    for title, fields in (("Paused", {}), ("Private", {"tier": "private"}),
                          ("Watch", {"skill": "brain-watch", "records": "receipts"})):
        fm.write(owner.vault_path / f"{title}.md", {"kind": "hub", **fields}, body)
    fm.write(owner.vault_path / "Active trip.md", {"kind": "hub"}, "# Active trip\n" + NOW)
    fm.write(owner.vault_path / "Undated.md", {"kind": "hub"}, "# Undated\n## Now\n")
    fm.write(owner.vault_path / "Working.md", {"kind": "hub"}, body)
    fm.write(owner.vault_path / "agent/Tasks.md", {}, "# Tasks\n" + Task(
        "T-001", "Pack gear", "- **Hub:** [[Working]]\n", {"state": "in_progress"}).render())
    assert matters.listed(Vault(owner.vault_path), TODAY, quiet=True) == [("Paused", "Paused.md")]


def test_quiet_matters_are_shown_most_recent_first_and_capped(owner):
    for age in range(8, 30):
        when = (TODAY - timedelta(days=age)).isoformat()
        fm.write(owner.vault_path / f"Trip {age}.md", {"kind": "hub"},
                 f"# Trip {age}\n## Now\n- **Last:** {when} · planned\n")
    assert [title for title, _ in matters.listed(Vault(owner.vault_path), TODAY, quiet=True)] == [
        f"Trip {age}" for age in range(8, 28)]


def test_the_night_backfills_ids_once_and_state_keeps_them_after_a_move(owner, capsys):
    root = owner.vault_path
    fm.write(root / "Quiet.md", {"kind": "hub", "matter_id": "m-1234abcd"},
             "# Quiet\n## Now\n- **Last:** 2026-08-01 · finished\n")
    fm.write(root / "Watch.md", {"kind": "hub", "skill": "brain-watch", "records": "receipts"}, "# Watch\n")
    fm.write(root / "Private.md", {"kind": "hub", "tier": "private"}, "# Private\n")
    before = {hub["title"]: hub["id"] for hub in state.build(owner, TODAY)["hubs"]}
    assert before["Lisbon trip"] is None and before["Quiet"] == "m-1234abcd"
    argv = ["nightly", "--vault", str(root), "--date", TODAY.isoformat(), "--write", "--stage", "before"]
    cli.main(argv)
    capsys.readouterr()
    data, body = fm.read(root / "Lisbon trip.md")
    assert re.fullmatch(r"m-[0-9a-f]{8}", data["matter_id"])
    assert fm.read(root / "Quiet.md")[0]["matter_id"] == "m-1234abcd"
    assert all("matter_id" not in fm.read(root / source)[0] for source in ("Watch.md", "Private.md"))
    saved = (root / "Lisbon trip.md").read_bytes(), (root / "Lisbon trip.md").stat().st_mtime_ns
    cli.main(argv)
    capsys.readouterr()
    assert ((root / "Lisbon trip.md").read_bytes(), (root / "Lisbon trip.md").stat().st_mtime_ns) == saved
    (root / "Trips").mkdir()
    (root / "Lisbon trip.md").rename(root / "Trips/Spring holiday.md")
    cli.main(argv)
    capsys.readouterr()
    moved = next(hub for hub in state.build(owner, TODAY)["hubs"] if hub["source"] == "Trips/Spring holiday.md")
    assert moved["id"] == data["matter_id"]
    assert fm.read(root / "Trips/Spring holiday.md")[1] == body


@pytest.mark.parametrize("what", [None, "choosing a route"])
def test_the_owner_opens_a_matter_without_a_second_sighting(owner, capsys, what):
    talk(owner, "Mentioned paddling", [{"name": "Kayak trip", "now": None}])
    sightings = matters._sightings_path().read_bytes()
    argv = ["matters", "--vault", str(owner.vault_path), "--open", "Kayak trip", "--json"]
    cli.main(argv + (["--what", what] if what else []))
    opened = json.loads(capsys.readouterr().out)
    assert opened["source"] == "Kayak trip.md" and opened["opened"] is True
    assert re.fullmatch(r"m-[0-9a-f]{8}", opened["id"])
    path = owner.vault_path / opened["source"]
    kept = matters.state(fm.read(path)[1], TODAY)
    assert kept["now"] == ({"text": what, "date": TODAY.isoformat()} if what else None)
    assert matters.phase(kept, TODAY, False) == "current"
    assert matters._sightings_path().read_bytes() == sightings
    saved = path.read_bytes(), path.stat().st_mtime_ns
    cli.main(["matters", "--vault", str(owner.vault_path), "--open", "kayak TRIP", "--what", "replace this", "--json"])
    assert json.loads(capsys.readouterr().out) == {**opened, "opened": False}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == saved
    assert next(hub for hub in state.build(owner, TODAY)["hubs"] if hub["title"] == "Kayak trip")["id"] == opened["id"]


def test_opening_a_live_matter_only_backfills_its_id(owner, capsys):
    talk(owner, "Booked flights", [{"name": "Lisbon trip", "now": "flights booked"}])
    path = owner.vault_path / "Lisbon trip.md"
    data = fm.read(path)[0]
    fm.write(path, {key: value for key, value in data.items() if key != "matter_id"}, fm.read(path)[1])
    body = fm.read(path)[1]
    cli.main(["matters", "--vault", str(owner.vault_path), "--open", "Lisbon trip.md", "--what", "ignored"])
    assert capsys.readouterr().out == "Lisbon trip.md: already a matter\n"
    data, kept = fm.read(path)
    assert re.fullmatch(r"m-[0-9a-f]{8}", data["matter_id"])
    assert kept == body


@pytest.mark.parametrize("what, as_json", [(None, False), ("wait until spring", True)])
def test_the_owner_sets_a_matter_aside(owner, capsys, what, as_json):
    talk(owner, "Booked flights", [{"name": "Lisbon trip", "now": "flights booked", "soon": "2026-11-20 · fly"}])
    argv = ["matters", "--vault", str(owner.vault_path), "--aside", "lisbon TRIP"]
    cli.main(argv + (["--what", what] if what else []) + (["--json"] if as_json else []))
    output = capsys.readouterr().out
    reason = what or "set aside by the owner"
    if as_json:
        assert json.loads(output) == {"source": "Lisbon trip.md", "id": None, "aside": reason}
    else:
        assert output == f"Lisbon trip.md: set aside · {reason}\n"
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], TODAY)
    assert kept["now"] is None and kept["soon"] is None
    assert kept["aside"] == {"date": TODAY.isoformat(), "text": reason}
    assert matters.phase(kept, TODAY, False) == "fading"


@pytest.mark.parametrize("name, data", [("Missing", None), ("Private", {"kind": "hub", "tier": "private"}),
                                      ("Watch", {"kind": "hub", "skill": "brain-watch", "records": "receipts"}),
                                      ("Draft", {"kind": "draft"})])
def test_aside_rejects_names_that_are_not_matters(owner, capsys, name, data):
    if data:
        fm.write(owner.vault_path / f"{name}.md", data, f"# {name}\n")
    with pytest.raises(SystemExit) as error:
        cli.main(["matters", "--vault", str(owner.vault_path), "--aside", name, "--json"])
    assert error.value.code == 1
    assert f"no matter named {name!r}" in capsys.readouterr().err


@pytest.mark.parametrize("open_first", [False, True])
def test_bare_open_still_modifies_soon(owner, capsys, open_first):
    argv = ["matters", "--vault", str(owner.vault_path)]
    event = ["--soon", "Kayak trip", "--date", "2026-11-20", "--what", "paddle downstream"]
    cli.main(argv + (["--open"] + event if open_first else event + ["--open"]))
    assert capsys.readouterr().out == "Kayak trip.md: soon 2026-11-20 · paddle downstream\n"
    data, body = fm.read(owner.vault_path / "Kayak trip.md")
    assert re.fullmatch(r"m-[0-9a-f]{8}", data["matter_id"])
    assert data["opened"] == TODAY.isoformat()
    assert matters.state(body, TODAY)["soon"] == {"text": "paddle downstream", "date": "2026-11-20"}


@pytest.mark.parametrize("args, message", [(["--open"], "--open needs a matter name"),
    (["--open", "Kayak", "--soon", "Lisbon trip"], "pass --open without a name"),
    (["--open", "Kayak", "--aside", "Lisbon trip"], "use either --open or --aside")])
def test_ambiguous_open_commands_fail_clearly(owner, args, message):
    with pytest.raises(SystemExit, match=message):
        cli.main(["matters", "--vault", str(owner.vault_path), *args])


def test_a_matter_set_aside_fades_until_it_comes_up_again(owner):
    talk(owner, "Booked flights", [{"name": "Lisbon trip", "now": "flights booked", "soon": "2026-11-20 · fly out"}])
    talk(owner, "Shelved Lisbon", [{"name": "Lisbon trip", "now": None, "aside": True}], at=datetime(2026, 11, 12, 9, 0))
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], date(2026, 11, 12))
    assert kept["now"] is None and kept["soon"] is None and kept["aside"]["text"] == "Shelved Lisbon"
    assert matters.phase(kept, date(2026, 11, 12), False) == "fading"
    assert matters.phase(kept, date(2026, 11, 12), True) == "current"
    talk(owner, "Unknown aside", [{"name": "Kayak", "now": None, "aside": True}], loops=[
        {"kind": "promised", "text": "Look at kayaks", "with": None, "due": None}])
    assert not (owner.vault_path / "Kayak.md").exists()
    talk(owner, "Back on Lisbon", [{"name": "Lisbon trip", "now": "looking at May"}], at=datetime(2026, 11, 13, 9, 0))
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], date(2026, 11, 13))
    assert "aside" not in kept and matters.phase(kept, date(2026, 11, 13), False) == "current"


def test_a_matter_with_live_work_is_listed_whatever_its_now(vault):
    config = Config.load(vault)
    fm.write(vault / "Paper.md", {"kind": "hub"}, "# Paper\n")
    fm.write(vault / "Visa.md", {"kind": "hub"}, "# Visa\n\n## Now\n- **Now:** forms filed · 2026-09-01\n")
    fm.write(vault / "Inbox.md", {"kind": "hub"}, "# Inbox\n\n## Now\n- **Now:** sorted · 2026-09-01\n")
    fm.write(vault / "agent/Tasks.md", {}, "# Tasks\n\n" + "".join(Task(tid, title, f"- **Hub:** [[{hub}]]\n", fields).render()
             for tid, title, hub, fields in (
                 ("T-001", "Draft the paper", "Paper", {"state": "in_progress"}),
                 ("T-002", "Book the interview", "Visa", {"state": "in_progress"}),
                 ("T-003", "Sort the inbox", "Inbox", {"state": "set_aside"}))))
    shown = {title for title, _ in matters.listed(Vault(config.vault_path), TODAY)}
    assert {"Paper", "Visa"} <= shown and "Inbox" not in shown


def test_the_evening_is_offered_each_one_off_event_once(owner, monkeypatch):
    from brain import channel_files, signals
    from brain.channel_files import CalendarEvent
    at = lambda day, hour: datetime(2026, 11, day, hour, 0, tzinfo=owner.zone)
    found = [CalendarEvent("Flight to Lisbon", at(20, 9), at(20, 12), False, "a1", "", ""),
             CalendarEvent("Contracts", at(13, 9), at(13, 10), False, "c1_20261113T140000Z", "", ""),
             CalendarEvent("Team sync", at(12, 9), at(12, 10), False, "s1", "", ""),
             CalendarEvent("Team sync", at(19, 9), at(19, 10), False, "s2", "", ""),
             CalendarEvent("Lunch today", at(11, 12), at(11, 13), False, "l1", "", "")]
    monkeypatch.setattr(channel_files, "events", lambda vault, days: (found, []))
    monkeypatch.setattr(owner.__class__, "today", lambda self: TODAY)
    monkeypatch.setattr(signals, "_check", lambda config: AT.replace(tzinfo=config.zone))
    talk(owner, "Booked flights", [{"name": "Lisbon trip", "now": "flights booked"}])
    signal = json.loads(signals.evening(owner))["calendar"]
    assert [entry["date"] for entry in signal["events"]] == ["2026-11-20"]
    assert "Flight to Lisbon" in signal["events"][0]["event"]
    assert signal["matters"] == ["- Lisbon trip · now: flights booked"]
    assert "calendar" not in json.loads(signals.evening(owner) or "{}")


def test_a_new_name_holding_a_matters_title_lands_on_it(owner):
    fm.write(owner.vault_path / "Garden.md", {"kind": "hub"}, "# Garden\n")
    fm.write(owner.vault_path / "House.md", {"kind": "hub"}, "# House\n")
    fm.write(owner.vault_path / "House Garden.md", {"kind": "hub"}, "# House Garden\n")
    talk(owner, "Sketched the irrigation plan", [{"name": "Garden irrigation plan", "now": "layout agreed"}])
    assert "- **Now:** layout agreed" in fm.read(owner.vault_path / "Garden.md")[1]
    assert not (owner.vault_path / "Garden irrigation plan.md").exists()
    talk(owner, "House planting", [{"name": "Garden for House planting", "now": "beds ready"}])
    assert "beds ready" in fm.read(owner.vault_path / "House Garden.md")[1]
    talk(owner, "Kayak", [{"name": "Kayak trip", "now": "looking"}])
    assert not (owner.vault_path / "Kayak trip.md").exists()


def test_opening_a_matter_set_aside_brings_it_back(owner, capsys):
    talk(owner, "Booked flights", [{"name": "Lisbon trip", "now": "flights booked"}])
    cli.main(["matters", "--vault", str(owner.vault_path), "--aside", "Lisbon trip"])
    capsys.readouterr()
    path = owner.vault_path / "Lisbon trip.md"
    assert matters.phase(matters.state(fm.read(path)[1], TODAY), TODAY, False) == "fading"
    cli.main(["matters", "--vault", str(owner.vault_path), "--open", "Lisbon trip", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["opened"] is False and out["revived"] is True
    assert matters.phase(matters.state(fm.read(path)[1], TODAY), TODAY, False) == "current"
