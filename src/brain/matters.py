"""A matter is something going on in the owner's life, kept as a hub marked `matter: true`. Its `## Now` section
keeps where it stands (`**Now:** <text> · <date>`), the next dated thing and the latest thing that happened
(`**Soon:**` and `**Last:**`, each `<date> · <text>`), and an `**Aside:**` when the owner set it aside. See
docs/matters.md in the demiurge repo."""
import json
import re
import secrets
from datetime import date, timedelta
from pathlib import PurePosixPath

from . import fm

FRESH = timedelta(days=7)
AHEAD = timedelta(days=21)    # a Soon this close makes it soon
RECOGNISED = timedelta(days=60)
FAREWELL = timedelta(days=2)
FAREWELL_TEXT = "I’m putting this matter away on {}. Say “keep it” to keep it here."

LINE = re.compile(r"^[ \t]*-[ \t]+\*\*(Now|Soon|Last|Aside):\*\*[ \t]*(.+?)[ \t]*$", re.M | re.I)
DATE = r"(\d{4}-\d{2}-\d{2})"


def _dated(field: str, value: str) -> dict | None:
    match = (re.fullmatch(r"(.+?)[ \t]*·[ \t]*" + DATE, value) if field == "now" else
             re.fullmatch(DATE + r"[ \t]*·[ \t]*(.+)", value))
    if not match:
        return None
    text, when = (match[1], match[2]) if field == "now" else (match[2], match[1])
    try:
        date.fromisoformat(when)
    except ValueError:
        return None
    return {"text": text.strip(), "date": when}


def state(body: str, today: date) -> dict:
    """The hub's Now, Soon and Last, each `{text, date}` or None. A Now older than FRESH is no longer where the matter
    stands, so it is given as its Last when nothing later happened."""
    span = fm.section(body, "Now")
    out: dict = {"now": None, "soon": None, "last": None}
    for field, value in LINE.findall(body[span[0]:span[1]]) if span else []:
        if out.get(field.lower()) is None:
            out[field.lower()] = _dated(field.lower(), value)
    now = out["now"]
    if now and date.fromisoformat(now["date"]) < today - FRESH:
        if out["last"] is None or out["last"]["date"] < now["date"]:
            out["last"] = now
        out["now"] = None
    return out


def phase(kept: dict, today: date, open_work: bool, *, data: dict | None = None) -> str:
    """current: work moving on it or waiting on the owner, or something happened lately (a past Soon counts as having
    happened);
    soon: a Soon within AHEAD; else fading, which the app does not show."""
    recent = [kept[key]["date"] for key in ("now", "last", "soon") if kept[key]]
    if kept.get("aside") and not open_work and all(when <= kept["aside"]["date"] for when in recent):
        return "fading"
    data = data or {}
    if data.get("merged_into"):
        return "fading"
    farewell = data.get("farewell")
    if farewell and _latest_touch(kept) <= str(farewell) and today < date.fromisoformat(str(farewell)) + FAREWELL:
        return "current"
    if data.get("kept") or open_work or any(today - FRESH <= date.fromisoformat(when) <= today for when in recent):
        return "current"
    if kept["soon"] and today < date.fromisoformat(kept["soon"]["date"]) <= today + AHEAD:
        return "soon"
    return "fading"


def moving(task, today: date, worked: dict[str, str]) -> bool:
    """An open task keeps its matter current only while it moves: made, worked or talked about within FRESH. One the
    owner ignores lets its matter fade like any quiet one. `worked` is each task's last worker chunk."""
    from .tasks import DATE_RE
    days = (*task.uses, *task.uses_inferred, *DATE_RE.findall(task.body), worked.get(task.tid, "")[:10])
    return any((today - FRESH).isoformat() <= day <= today.isoformat() for day in days)


def _latest_touch(kept: dict) -> str:
    return max((kept[key]["date"] for key in ("now", "last") if kept[key]), default="")


SIGHTED = timedelta(days=30)  # a new name becomes a matter when it comes up again within this
UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def _responsibility_hubs(vault) -> set[str]:
    from . import responsibilities
    return {entry["hub"] for entry in responsibilities.discover(vault)}


def _is_topic(vault, source: str, standing: set[str]) -> bool:
    from .state import _public
    note = vault.notes.get(source)
    return (note is not None and source not in standing and vault.kind_for(source) == "hub"
            and note.data.get("kind", "hub") == "hub" and not (note.data.get("skill") and note.data.get("records"))
            and _public(vault, source))


def _is_matter(vault, source: str, standing: set[str]) -> bool:
    return _is_topic(vault, source, standing) and vault.notes[source].data.get("matter") is True


def _unmarked(vault, source: str, standing: set[str]) -> bool:
    note = vault.notes[source]
    return not note.data.get("matter") and not note.data.get("merged_into") and _is_topic(vault, source, standing)


def _unmarked_hub_titled(vault, name: str) -> str | None:
    standing, wanted = _responsibility_hubs(vault), fm.plain(name)
    return next((source for source, note in sorted(vault.notes.items())
                 if fm.plain(note.stem) == wanted and _unmarked(vault, source, standing)), None)


TOPICS_SHOWN = 40


def topics(config) -> list[str]:
    """The titles of the hubs that are not matters, most recently used first, so the record can mark one that is
    itself a matter rather than open a narrower rival."""
    from .vault import Vault
    vault = Vault(config.vault_path)
    standing = _responsibility_hubs(vault)
    found = [(max(map(str, note.data.get("uses") or [""])), note.stem) for source, note in vault.notes.items()
             if _unmarked(vault, source, standing)]
    return [title for _, title in sorted(found, reverse=True)][:TOPICS_SHOWN]


def _mark(config, source: str) -> None:
    path = config.vault_path / source
    data, body = fm.read(path)
    data["matter"] = True
    data.setdefault("matter_id", "m-" + secrets.token_hex(4))
    fm.write(path, data, body)


def _worked(vault, today: date) -> set[str]:
    from . import continuation, loops, needs, tasks
    from .state import _hub
    config = vault.config
    entries, chunks = tasks.all_tasks(config), continuation.last_worked(config)
    hubs = {task.tid: _hub(vault, task.hub) for task in entries if task.hub}
    live = {hubs[task.tid] for task in entries if task.tid in hubs and tasks.is_open(task)
            and task.task_state != "set_aside" and not task.later and moving(task, today, chunks)}
    waiting = loops.open_loops(config) + [item for item in needs.items(vault, today) if item.kind == "ask"]
    live.update(_item_hub(vault, item, hubs) for item in waiting)
    unresolved = {link.removesuffix(".md").casefold() for link in live if link and link not in vault.notes}
    return {source for source, note in vault.notes.items()
            if source in live or source.removesuffix(".md").casefold() in unresolved or note.stem.casefold() in unresolved}


def _item_hub(vault, item, task_hubs: dict[str, str]) -> str:
    from .state import _hub
    return _hub(vault, item.hub) or task_hubs.get(item.task) or _hub(vault, item.source).split("#", 1)[0]


def listed(vault, today: date, *, quiet: bool = False) -> list[tuple[str, str]]:
    """The matters the record is shown by name, (title, source), the most recently touched first: hubs that have not
    faded, those with live work even without a `## Now`. With quiet, only recently faded matters are shown."""
    standing = _responsibility_hubs(vault)
    worked = _worked(vault, today)
    shown = []
    for source, note in vault.notes.items():
        work = source in worked
        if (not (work or fm.section(note.body, "Now") is not None) or note.data.get("merged_into")
                or not _is_matter(vault, source, standing)):
            continue
        state_ = state(note.body, today)
        fading = phase(state_, today, work, data=note.data) == "fading"
        latest = max((state_[key]["date"] for key in ("now", "last", "soon", "aside") if state_.get(key)), default="")
        if fading != quiet or (quiet and not ((today - RECOGNISED).isoformat() <= latest <= today.isoformat())):
            continue
        shown.append((latest, note.stem, source))
    return [(title, source) for _, title, source in sorted(shown, reverse=True)][:20]


def find(vault, name: str, *, follow: bool = True) -> str | None:
    """The matter a record names: a hub with that exact title, case aside, or the note the name resolves to; a matter
    merged into another is that one."""
    standing = _responsibility_hubs(vault)
    wanted = fm.plain(name)
    for source, note in sorted(vault.notes.items()):
        if fm.plain(note.stem) == wanted and _is_matter(vault, source, standing):
            return _survivor(vault, source) if follow else source
    source = vault.resolve(name)
    if not source or not _is_matter(vault, source, standing):
        return None
    return _survivor(vault, source) if follow else source


def _survivor(vault, source: str) -> str:
    """The matter a merged one now lives in, following merges of merges."""
    if not vault.notes[source].data.get("merged_into"):
        return source
    standing = _responsibility_hubs(vault)
    ids = {note.data["matter_id"]: path for path, note in vault.notes.items() if note.data.get("matter_id")}
    seen = {source}
    while ((into := ids.get(vault.notes[source].data.get("merged_into"))) and into not in seen
           and _is_matter(vault, into, standing)):
        seen.add(source := into)
    return source


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.casefold()) if len(word) > 2 and word not in ("the", "and", "for")}


def _within(vault, name: str) -> str | None:
    """The matter whose whole title a record's new name contains ("Demiurge runtime API" is Demiurge's), the longest
    title when several do and none when two tie, so a facet of a matter lands on it instead of starting a rival."""
    standing, wanted = _responsibility_hubs(vault), _words(name)
    fits = [(len(words), source) for source, note in vault.notes.items()
            if (words := _words(note.stem)) and words <= wanted and _is_matter(vault, source, standing)]
    best = max(fits, default=None)
    return _survivor(vault, best[1]) if best and [size for size, _ in fits].count(best[0]) == 1 else None


def write(config, source: str, today: date, *, now: str | None = None, last: str | None = None,
          soon: tuple[str, str] | None = None, aside: str | None = None, drop: tuple[str, ...] = (),
          fields: dict | None = None):
    """Rewrite the hub's `## Now` lines that are given, keeping the others but those in `drop`; the section goes after
    the title when the hub has none. The caller holds the vault lock."""
    path = config.vault_path / source
    data, body = fm.read(path)
    data.update(fields or {})
    if (farewell := data.get("farewell")) and (data.get("kept") or (now or last) and today.isoformat() >= str(farewell)):
        _cancel_farewell(config, source, data)
    span = fm.section(body, "Now")
    lines = {key: f"- **{key.title()}:** {value}" for key, value in (
        ("now", now and f"{_flat(now)} · {today.isoformat()}"), ("soon", soon and f"{soon[0]} · {_flat(soon[1])}"),
        ("last", last and f"{today.isoformat()} · {_flat(last)}"), ("aside", aside and f"{today.isoformat()} · {_flat(aside)}"))
        if value}
    for kept in LINE.finditer(body[span[0]:span[1]]) if span else []:
        if kept[1].lower() not in drop:
            lines.setdefault(kept[1].lower(), kept[0].strip())
    section = "\n" + "\n".join(lines[key] for key in ("now", "soon", "last", "aside") if key in lines) + "\n"
    if span:
        rest = body[span[1]:]
        body = body[:span[0]] + section + ("\n" + rest if rest else "")
    else:
        following = re.compile(r"^## ", re.M).search(body)
        at = following.start() if following else len(body)
        head = body[:at].rstrip("\n")
        body = head + ("\n\n" if head else "") + "## Now" + section + ("\n" + body[at:] if following else "")
    fm.write(path, data, body)


def _cancel_farewell(config, source: str, data: dict) -> None:
    from . import needs
    data.pop("farewell", None)
    needs._prune(config, lambda item: item.kind == "confirm" and item.source == source
                 and item.text == FAREWELL_TEXT.format(item.wake))


def farewells(vault, today: date, report: list[str]) -> set[str]:
    from . import journal, needs
    standing = _responsibility_hubs(vault)
    candidates = [source for source, note in vault.notes.items()
                  if (note.data.get("owner_made") or note.data.get("farewell")) and _is_matter(vault, source, standing)]
    if not candidates:
        return set()
    worked = _worked(vault, today)
    current = vault.notes.get("CURRENT.md")
    notices = {item.source: item.since for item in needs.parse_section(current.body)
               if item.kind == "confirm" and item.text == FAREWELL_TEXT.format(item.wake)} if current else {}
    changed = set()
    tomorrow = today + timedelta(days=1)
    for source in sorted(candidates):
        note = vault.notes[source]
        data = dict(note.data)
        held = state(note.body, today)
        latest = _latest_touch(held)
        if farewell := data.get("farewell"):
            if data.get("kept") or latest > str(farewell) or source in worked:
                _cancel_farewell(vault.config, source, data)
                fm.write(vault.root / source, data, note.body)
                changed.update((source, "CURRENT.md"))
            continue
        if (not data.get("owner_made") or data.get("kept") or data.get("merged_into") or source in worked
                or not latest or latest > (today - FRESH).isoformat()
                or (held.get("aside") and latest <= held["aside"]["date"])
                or phase(held, tomorrow, False) != "fading"):
            continue
        try:
            if source not in notices:
                wake = today + FAREWELL
                journal.add_ask(vault.config, today, FAREWELL_TEXT.format(wake.isoformat()), source,
                                distinct_source=True, kind="confirm", hub=source, wake=wake)
        except ValueError as error:
            report.append(f"matter: skipped farewell for {source}: {error}")
            continue
        data["farewell"] = notices.get(source, today.isoformat())
        fm.write(vault.root / source, data, note.body)
        changed.update((source, "CURRENT.md"))
        report.append(f"needs-you: matter farewell: {source}")
    return changed


def _flat(text: str) -> str:
    return " ".join(text.replace("·", "-").split())


def _sightings_path():
    from . import runtime
    home = runtime.configured()
    return home / "records" / "matters.json" if home else None


def _name(name: str) -> str:
    return _flat(UNSAFE.sub(" ", name))[:60].strip(" .")


def _create(vault, name: str, *, opened: date | None = None, owner_made: bool = False) -> str:
    name = _name(name)
    path = vault.root / f"{name}.md"
    if not name or path.exists():
        raise ValueError(f"{name!r} cannot be a matter: a note of that name exists or the name is empty")
    data = {"kind": "hub", "tier": "warm", "matter": True, "matter_id": "m-" + secrets.token_hex(4)}
    if opened is not None:
        data["opened"] = opened.isoformat()
    if owner_made:
        data["owner_made"] = True
    fm.write(path, data, f"# {name}\n")
    vault.load()
    return path.relative_to(vault.root).as_posix()


def mark(config, name: str) -> str:
    """Make the hub titled `name` a matter, with its id, and return its path. Caller holds the vault lock."""
    from .vault import Vault
    vault = Vault(config.vault_path)
    source = find(vault, name, follow=False) or _unmarked_hub_titled(vault, name)
    if source is None:
        raise ValueError(f"no hub named {name!r}")
    _mark(config, source)
    return source


def open_(config, name: str, today: date, what: str | None = None) -> tuple[str, str]:
    """Return (source, "opened" | "revived" | "kept") for the owner's request: a faded matter comes back, a live one keeps
    its body. Caller holds the vault lock."""
    from .vault import Vault
    vault = Vault(config.vault_path)
    source = find(vault, name)
    if source is not None:
        path = config.vault_path / source
        data, body = fm.read(path)
        if "matter_id" not in data:
            fm.write(path, {**data, "matter_id": "m-" + secrets.token_hex(4)}, body)
        if phase(state(body, today), today, False, data=data) == "fading":
            write(config, source, today, now=what, last="brought back by the owner", drop=("aside",))
            return source, "revived"
        return source, "kept"
    if source := _unmarked_hub_titled(vault, name):
        _mark(config, source)
        write(config, source, today, now=what, last="made a matter by the owner", fields={"owner_made": True})
        return source, "opened"
    source = _create(vault, name, owner_made=True)
    write(config, source, today, now=what, last=_file_earlier(config, source, today) or "opened by the owner")
    return source, "opened"


def keep(config, source: str, today: date) -> None:
    write(config, source, today, drop=("aside",), fields={"kept": True})


def _linked_work(vault, source: str) -> dict:
    from . import continuation, loops, needs, tasks
    from .state import _hub
    config = vault.config
    entries, chunks = tasks.all_tasks(config), continuation.last_worked(config)
    hubs = {task.tid: _hub(vault, task.hub) for task in entries if task.hub}
    current = vault.notes.get("CURRENT.md")
    dismissed = needs.dismissed_ids(config)
    waiting = loops.open_loops(config) + ([item for item in needs.parse_section(current.body)
                                           if item.id not in dismissed] if current else [])
    return {"tasks": [task.tid for task in entries if tasks.is_open(task) and hubs.get(task.tid) == source],
            "closed": [task.tid for task in entries if not tasks.is_open(task) and hubs.get(task.tid) == source],
            "items": [item.id for item in waiting if _item_hub(vault, item, hubs) == source]}


def _refile(config, source: str, today: date, task_ids, item_ids, *, unfiled: bool = False) -> dict:
    from . import items, loops, needs, tasks
    moved = {"tasks": [], "items": []}
    for tid in task_ids:
        tasks.set_fields(config, tid, today, values={"hub": source.removesuffix(".md")})
        moved["tasks"].append(tid)
    if not item_ids:
        return moved
    for path, title, read in ((loops.path_for(config), "Open", loops.read),
                              (config.vault_path / "CURRENT.md", "Needs you", needs.read)):
        if not path.is_file():
            continue
        data, body = fm.read(path)
        updated = body
        for item in items.parse(body, title, read):
            if item.id in item_ids and (not unfiled or not item.hub):
                updated = items.set_details(updated, title, read, item.id, {"hub": source})
                moved["items"].append(item.id)
        if updated != body:
            fm.write(path, data, updated)
    return moved


def merge(config, vault, source: str, target: str, today: date) -> dict:
    from . import tasks
    if source == target:
        raise ValueError("a matter cannot merge into itself")
    origin = vault.notes[source]
    destination = vault.notes[target]
    if destination.data.get("merged_into"):
        raise ValueError(f"{destination.stem} has already been merged into another matter")
    target_id = destination.data.get("matter_id") or "m-" + secrets.token_hex(4)
    if origin.data.get("merged_into") not in (None, target_id):
        raise ValueError(f"{origin.stem} has already been merged into another matter")
    origin_id = origin.data.get("matter_id") or "m-" + secrets.token_hex(4)
    linked = _linked_work(vault, source)
    moved = _refile(config, target, today, linked["tasks"], linked["items"])
    # Finished work moves too, so the survivor keeps the absorbed matter's history.
    for task in tasks.all_tasks(config):
        if task.tid in linked["closed"]:
            task.body = tasks._replace_field(task.body, "hub", tasks._field_value(config, "hub", target.removesuffix(".md")))
            tasks.save_task(config, task, today)
            moved["tasks"].append(task.tid)
    write(config, target, today, last=f"merged with {origin.stem}", drop=("aside",), fields={"matter_id": target_id})
    write(config, source, today, aside=f"merged into {destination.stem}", drop=("now", "soon"),
          fields={"matter_id": origin_id, "merged_into": target_id})
    return {"into": target_id, "absorbed": origin_id, "moved": moved}


def split(config, vault, source: str, name: str, today: date, task_ids: list[str], item_ids: list[str]) -> dict:
    if find(vault, name) or find(vault, _name(name)):
        raise ValueError(f"a matter named {name!r} already exists")
    linked = _linked_work(vault, source)
    for kind, requested in (("tasks", task_ids), ("items", item_ids)):
        if missing := sorted(set(requested) - set(linked[kind])):
            raise ValueError(f"not open {kind} linked to {vault.notes[source].stem}: {', '.join(missing)}")
    target = _create(vault, name)
    moved = _refile(config, target, today, dict.fromkeys(task_ids), item_ids)
    write(config, target, today, last=f"split from {vault.notes[source].stem}")
    write(config, source, today, last=f"split off {vault.notes[target].stem}")
    return {"source": target, "id": vault.notes[target].data["matter_id"], "moved": moved}


def _sightings() -> dict:
    store = _sightings_path()
    return json.loads(store.read_text(encoding="utf-8")) if store and store.is_file() else {}


def _save_sightings(seen: dict) -> None:
    if store := _sightings_path():
        store.parent.mkdir(parents=True, exist_ok=True)
        fm.write_text(store, json.dumps(seen, indent=1))


def _file_earlier(config, source: str, today: date, seen: dict | None = None) -> str | None:
    from . import tasks
    store = _sightings() if seen is None else seen
    key = fm.plain(PurePosixPath(source).stem)
    sighting = store.get(key)
    if not isinstance(sighting, dict):
        return None
    eligible = [task.tid for task in tasks.all_tasks(config)
                if tasks.is_open(task) and not task.hub and task.tid in sighting.get("tasks", [])]
    moved = _refile(config, source, today, eligible, sighting.get("items", []), unfiled=True)
    del store[key]
    if seen is None:
        _save_sightings(store)
    counts = [f"{len(ids)} earlier {kind[:-1] if len(ids) == 1 else kind}" for kind, ids in moved.items() if ids]
    return " and ".join(counts) + " filed here" if counts else None


def record(config, vault, today: date, about: list[dict], headline: str, *, session: str = "",
           item_ids: list[str] | tuple[str, ...] = ()) -> str | None:
    """Apply a conversation record's `about` to the owner's matters: each named matter gets its Now (when the record
    gave one), its Soon (when it named a date sooner than the one kept) and a Last of what happened; a matter the owner
    set aside trades its Now and Soon for an Aside, so it fades until it comes up again. A new name holding a
    matter's whole title is that matter; any other new name becomes a matter on its second sighting within SIGHTED, or
    on its first when it names a date, marking the hub of that title when there is one; until then it is only
    remembered. Files the conversation's new tasks and loops under the first matter it finds, and returns its path for
    the ask. The caller holds the vault lock."""
    seen = _sightings()
    cutoff = (today - SIGHTED).isoformat()
    sighted = set()
    first = None
    for entry in about[:2]:
        given = _flat(entry.get("name") or "")
        name = _name(given)
        if not name:
            continue
        soon = _dated("soon", " ".join((entry.get("soon") or "").split()))
        source = find(vault, given) or find(vault, name) or _within(vault, name)
        if entry.get("aside"):
            if source:
                write(config, source, today, aside=headline, drop=("now", "soon"))
            continue
        filed = None
        if source is None:
            key = fm.plain(name)
            previous = seen.get(key, [])
            dates = previous["dates"] if isinstance(previous, dict) else previous
            recent = any(day >= cutoff for day in dates)
            if not (soon or recent):
                sighted.add(key)
                continue
            try:
                if source := _unmarked_hub_titled(vault, given) or _unmarked_hub_titled(vault, name):
                    _mark(config, source)
                    vault.load()
                else:
                    source = _create(vault, name)
            except ValueError:
                sighted.add(key)
                continue   # a note of that name that is not a matter (private, a responsibility) is left alone
            filed = _file_earlier(config, source, today, seen)
            seen.pop(key, None)
            sighted.discard(key)
        kept = state(fm.read(config.vault_path / source)[1], today)["soon"]
        if soon and kept and today.isoformat() <= kept["date"] < soon["date"]:
            soon = None   # the sooner thing stays next
        write(config, source, today, now=_now_unless_report(config, entry.get("now")), last=filed or headline,
              soon=(soon["date"], soon["text"]) if soon else None, drop=("aside",))
        first = first or source
    loose_tasks = _conversation_tasks(config, session)
    if first:
        _refile(config, first, today, loose_tasks, item_ids, unfiled=True)
    for key in sorted(sighted):
        prior = seen.get(key, {})
        prior = prior if isinstance(prior, dict) else {}
        task_ids = list(dict.fromkeys(prior.get("tasks", []) + (loose_tasks if not first else [])))
        remembered = list(dict.fromkeys([*prior.get("items", []), *(item_ids if not first else ())]))
        days = [today.isoformat()]
        seen[key] = {"dates": days, "tasks": task_ids, "items": remembered} if task_ids or remembered else days
    _save_sightings({key: value for key, value in seen.items()
                     if any(day >= cutoff for day in (value["dates"] if isinstance(value, dict) else value))})
    return first


NOW_LONGEST = 120


def _now_unless_report(config, now: str | None) -> str | None:
    named = config.human_name != type(config).__dataclass_fields__["human_name"].default
    first = config.human_name.split()[0] if named and config.human_name.split() else ""
    if not now or len(now) > NOW_LONGEST or (first and re.search(rf"\b{re.escape(first)}\b", now, re.I)):
        return None
    return now


def task_moved(config, task, today: date, what: str) -> None:
    """A task serving a matter moves its Last (`finished <title>`, `handed off <title>`) and, while it is open with a
    due day ahead, its Soon. No model: the task already says what happened. The caller holds the vault lock."""
    if not task.hub:
        return
    from .vault import Vault
    vault = Vault(config.vault_path)
    source = vault.resolve(task.hub)
    if not source or not _is_matter(vault, source, _responsibility_hubs(vault)):
        return
    source = _survivor(vault, source)
    path = config.vault_path / source
    due, soon = fm.field(task.body, "Due") or "", None
    if what.startswith("handed off") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) and due >= today.isoformat():
        kept = state(fm.read(path)[1], today)["soon"]
        if not (kept and today.isoformat() <= kept["date"] < due):
            soon = (due, task.title)
    write(config, source, today, last=what, soon=soon)


OPENS_A_DAY = 2   # matters the evening may open from the calendar, so a busy week never floods the owner's list


def soon(config, name: str, when: date, what: str, today: date, *, open_: bool = False) -> str:
    """Set a matter's Soon from a calendar event, keeping a sooner one still ahead; with `open_`, a significant event
    with no matter opens one (marked `opened:` with the day, at most OPENS_A_DAY a day). Returns the matter's path. The
    caller holds the vault lock."""
    from .vault import Vault
    if not today <= when <= today + AHEAD:
        raise ValueError(f"the date must be between today and {(today + AHEAD).isoformat()}")
    what = _flat(what)
    if not what:
        raise ValueError("--what must say what happens")
    vault = Vault(config.vault_path)
    source = find(vault, name)
    filed = None
    if source is None:
        if not open_:
            raise ValueError(f"no matter named {name!r}; pass --open for a significant event with none")
        opened = sum(note.data.get("opened") == today.isoformat() for note in vault.notes.values())
        if opened >= OPENS_A_DAY:
            raise ValueError(f"{OPENS_A_DAY} matters were already opened today; leave this one")
        if source := _unmarked_hub_titled(vault, name):
            _mark(config, source)
        else:
            source = _create(vault, name, opened=today)
        filed = _file_earlier(config, source, today)
    path = config.vault_path / source
    kept = state(fm.read(path)[1], today)["soon"]
    if not (kept and today.isoformat() <= kept["date"] < when.isoformat()):
        write(config, source, today, last=filed, soon=(when.isoformat(), what))
    return source


def held(config, today: date, *, quiet: bool = False) -> list[str]:
    """The matters a conversation record is shown, each with where it stands, so a new Now carries the whole matter
    forward instead of only the latest exchange."""
    from .vault import Vault
    vault = Vault(config.vault_path)
    out = []
    for title, source in listed(vault, today, quiet=quiet):
        kept = state(vault.notes[source].body, today)["now"]
        out.append(f"- {title}" + (f" · now: {kept['text']}" if kept else ""))
    return out


def lines(config, today: date) -> list[str]:
    """The owner's current and soon matters, one line each, for the morning to link the calendar to."""
    from .vault import Vault
    vault = Vault(config.vault_path)
    out = []
    for title, source in listed(vault, today):
        kept = state(vault.notes[source].body, today)
        parts = [title] + [f"{key} {kept[key]['date']}: {kept[key]['text']}" for key in ("now", "soon") if kept[key]]
        out.append("- " + " · ".join(parts))
    return out


def _conversation_tasks(config, conversation: str) -> list[str]:
    from . import tasks
    # Ids are unique; the platform before them differs between where the record and the task were written.
    conversation = conversation.split(":", 1)[-1]
    if not conversation:
        return []
    return [task.tid for task in tasks.all_tasks(config)
            if tasks.is_open(task) and not task.hub and (task.report or "").split(":", 1)[-1] == conversation]


RECURRING = re.compile(r"_\d{8}(T\d{6}Z?)?$")   # Google's id for one instance of a repeating event


def calendar_offers(config, vault, today: date) -> list[dict]:
    """The one-off events from tomorrow through AHEAD that the evening has not been offered yet, each `{date, event}`,
    marked offered so each is decided once. A repeating event (by its id, or a title seen twice in the window) is never
    one: classes and standing meetings are not matters."""
    import json
    from . import channel_files, runtime
    home = runtime.configured()
    if home is None:
        return []
    store = home / "records" / "calendar-offers.json"
    offered = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {}
    found, _ = channel_files.events(vault, AHEAD.days)
    titles = [fm.plain(event.title) for event in found]
    out = []
    for event in sorted(found, key=lambda event: event.start):
        key = event.id or f"{event.title}|{event.start.isoformat()}"
        if (event.start.date() <= today or key in offered or RECURRING.search(event.id)
                or titles.count(fm.plain(event.title)) > 1):
            continue
        offered[key] = event.start.date().isoformat()
        out.append({"date": event.start.date().isoformat(), "event": event.line(today).removeprefix("- ")})
    store.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(store, json.dumps({key: day for key, day in offered.items() if day >= today.isoformat()}, indent=1))
    return out
