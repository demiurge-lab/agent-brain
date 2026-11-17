"""A matter is a hub that is not a responsibility: something the owner's life holds now or soon. Its `## Now` section
keeps where it stands (`**Now:** <text> · <date>`), the next dated thing and the latest thing that happened
(`**Soon:**` and `**Last:**`, each `<date> · <text>`), and an `**Aside:**` when the owner set it aside. See
docs/matters.md in the demiurge repo."""
import re
from datetime import date, timedelta

from . import fm

FRESH = timedelta(days=14)    # a Now or Last this recent keeps the matter current
AHEAD = timedelta(days=21)    # a Soon this close makes it soon

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


def phase(kept: dict, today: date, open_work: bool) -> str:
    """current: open work on it, or something happened lately (a past Soon counts as having happened);
    soon: a Soon within AHEAD; else fading, which the app does not show."""
    recent = [kept[key]["date"] for key in ("now", "last", "soon") if kept[key]]
    if kept.get("aside") and not open_work and all(when <= kept["aside"]["date"] for when in recent):
        return "fading"
    if open_work or any(today - FRESH <= date.fromisoformat(when) <= today for when in recent):
        return "current"
    if kept["soon"] and today < date.fromisoformat(kept["soon"]["date"]) <= today + AHEAD:
        return "soon"
    return "fading"


SIGHTED = timedelta(days=30)  # a new name becomes a matter when it comes up again within this
UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def _responsibility_hubs(vault) -> set[str]:
    from . import responsibilities
    return {entry["hub"] for entry in responsibilities.discover(vault)}


def _is_matter(vault, source: str, standing: set[str]) -> bool:
    from .state import _public
    note = vault.notes.get(source)
    return (note is not None and source not in standing and vault.kind_for(source) == "hub"
            and note.data.get("kind", "hub") == "hub" and not (note.data.get("skill") and note.data.get("records"))
            and _public(vault, source))


def _worked(vault, today: date) -> set[str]:
    """The hubs with live work, by name casefolded: an open task neither set aside nor put off, or an open loop or ask
    naming the hub. As in the app, these keep a matter current however old its Now."""
    from . import loops, needs, tasks
    config = vault.config
    named = [task.hub for task in tasks.all_tasks(config)
             if tasks.is_open(task) and task.task_state != "set_aside" and not task.later]
    named += [item.hub for item in loops.open_loops(config)]
    named += [item.hub for item in needs.items(vault, today) if item.kind == "ask"]
    return {hub.removesuffix(".md").casefold() for hub in named if hub}


def listed(vault, today: date) -> list[tuple[str, str]]:
    """The matters the record is shown by name, (title, source), the most recently touched first: hubs that have not
    faded, those with live work even without a `## Now`."""
    standing = _responsibility_hubs(vault)
    worked = _worked(vault, today)
    shown = []
    for source, note in vault.notes.items():
        work = {note.stem.casefold(), source.removesuffix(".md").casefold()} & worked
        if not (work or fm.section(note.body, "Now") is not None) or not _is_matter(vault, source, standing):
            continue
        state_ = state(note.body, today)
        if phase(state_, today, bool(work)) == "fading":
            continue
        latest = max((state_[key]["date"] for key in ("now", "last", "soon") if state_[key]), default="")
        shown.append((latest, note.stem, source))
    return [(title, source) for _, title, source in sorted(shown, reverse=True)][:20]


def find(vault, name: str) -> str | None:
    """The matter a record names: a hub with that exact title, case aside, or the note the name resolves to."""
    standing = _responsibility_hubs(vault)
    wanted = fm.plain(name)
    for source, note in sorted(vault.notes.items()):
        if fm.plain(note.stem) == wanted and _is_matter(vault, source, standing):
            return source
    source = vault.resolve(name)
    return source if source and _is_matter(vault, source, standing) else None


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.casefold()) if len(word) > 2 and word not in ("the", "and", "for")}


def _within(vault, name: str) -> str | None:
    """The matter whose whole title a record's new name contains ("Demiurge runtime API" is Demiurge's), the longest
    title when several do and none when two tie, so a facet of a matter lands on it instead of starting a rival."""
    standing, wanted = _responsibility_hubs(vault), _words(name)
    fits = [(len(words), source) for source, note in vault.notes.items()
            if (words := _words(note.stem)) and words <= wanted and _is_matter(vault, source, standing)]
    best = max(fits, default=None)
    return best[1] if best and [size for size, _ in fits].count(best[0]) == 1 else None


def write(path, today: date, *, now: str | None = None, last: str | None = None, soon: tuple[str, str] | None = None,
          aside: str | None = None, drop: tuple[str, ...] = ()):
    """Rewrite the hub's `## Now` lines that are given, keeping the others but those in `drop`; the section goes after
    the title when the hub has none. The caller holds the vault lock."""
    data, body = fm.read(path)
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


def _flat(text: str) -> str:
    return " ".join(text.replace("·", "-").split())


def _sightings_path():
    from . import runtime
    home = runtime.configured()
    return home / "records" / "matters.json" if home else None


def record(config, vault, today: date, about: list[dict], headline: str, opens: bool) -> str | None:
    """Apply a conversation record's `about` to the owner's matters: each named matter gets its Now (when the record
    gave one), its Soon (when it named a date sooner than the one kept) and a Last of what happened; a matter the owner
    set aside trades its Now and Soon for an Aside, so it fades until it comes up again. A new name holding a
    matter's whole title is that matter; any other new name becomes
    a matter on its second sighting within SIGHTED, or on its first when it names a date or the same record opens a loop
    or an ask; until then it is only remembered. Returns the first matter's path, for the loops and asks this record
    opens. The caller holds the vault lock."""
    import json
    store = _sightings_path()
    seen = json.loads(store.read_text(encoding="utf-8")) if store and store.is_file() else {}
    first = None
    for entry in about[:2]:
        given = _flat(entry.get("name") or "")
        name = _flat(UNSAFE.sub(" ", given))[:60].strip(" .")
        if not name:
            continue
        soon = _dated("soon", " ".join((entry.get("soon") or "").split()))
        source = find(vault, given) or find(vault, name) or _within(vault, name)
        if entry.get("aside"):
            if source:
                write(config.vault_path / source, today, aside=headline, drop=("now", "soon"))
            continue
        if source is None:
            key = fm.plain(name)
            recent = [day for day in seen.get(key, []) if date.fromisoformat(day) >= today - SIGHTED]
            if not (opens or soon or recent):
                seen[key] = [today.isoformat()]
                continue
            path = config.vault_path / f"{name}.md"
            if path.exists():
                continue   # a note of that name that is not a matter (private, a responsibility) is left alone
            fm.write(path, {"kind": "hub", "tier": "warm"}, f"# {name}\n")
            vault.load()
            seen.pop(key, None)
            source = path.relative_to(config.vault_path).as_posix()
        kept = state(fm.read(config.vault_path / source)[1], today)["soon"]
        if soon and kept and today.isoformat() <= kept["date"] < soon["date"]:
            soon = None   # the sooner thing stays next
        write(config.vault_path / source, today, now=entry.get("now") or None, last=headline,
              soon=(soon["date"], soon["text"]) if soon else None, drop=("aside",))
        first = first or source
    if store:
        store.parent.mkdir(parents=True, exist_ok=True)
        fm.write_text(store, json.dumps({key: days for key, days in seen.items()
                                         if any(date.fromisoformat(day) >= today - SIGHTED for day in days)}, indent=1))
    return first


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
    path = config.vault_path / source
    due, soon = fm.field(task.body, "Due") or "", None
    if what.startswith("handed off") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) and due >= today.isoformat():
        kept = state(fm.read(path)[1], today)["soon"]
        if not (kept and today.isoformat() <= kept["date"] < due):
            soon = (due, task.title)
    write(path, today, last=what, soon=soon)


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
    if source is None:
        if not open_:
            raise ValueError(f"no matter named {name!r}; pass --open for a significant event with none")
        name = _flat(UNSAFE.sub(" ", name))[:60].strip(" .")
        opened = sum(note.data.get("opened") == today.isoformat() for note in vault.notes.values())
        if opened >= OPENS_A_DAY:
            raise ValueError(f"{OPENS_A_DAY} matters were already opened today; leave this one")
        path = config.vault_path / f"{name}.md"
        if not name or path.exists():
            raise ValueError(f"{name!r} cannot be a matter: a note of that name exists or the name is empty")
        fm.write(path, {"kind": "hub", "tier": "warm", "opened": today.isoformat()}, f"# {name}\n")
        source = path.relative_to(config.vault_path).as_posix()
    path = config.vault_path / source
    kept = state(fm.read(path)[1], today)["soon"]
    if not (kept and today.isoformat() <= kept["date"] < when.isoformat()):
        write(path, today, soon=(when.isoformat(), what))
    return source


def held(config, today: date) -> list[str]:
    """The matters a conversation record is shown, each with where it stands, so a new Now carries the whole matter
    forward instead of only the latest exchange."""
    from .vault import Vault
    vault = Vault(config.vault_path)
    out = []
    for title, source in listed(vault, today):
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


def link_tasks(config, today: date, source: str, conversation: str) -> list[str]:
    """The open tasks this conversation made that name no hub take the matter it was about, so closing or handing them
    off moves it; a task the conversation only touched keeps its own hub. Returns their ids. The caller holds the vault
    lock."""
    from . import tasks
    # Ids are unique; the platform before them differs between where the record and the task were written.
    conversation = conversation.split(":", 1)[-1]
    if not conversation:
        return []
    linked = []
    for task in tasks.all_tasks(config):
        if tasks.is_open(task) and not task.hub and (task.report or "").split(":", 1)[-1] == conversation:
            tasks.set_fields(config, task.tid, today, values={"hub": source.removesuffix(".md")})
            linked.append(task.tid)
    return linked


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
