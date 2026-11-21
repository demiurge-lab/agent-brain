"""Assemble the generated session startup context from its owning notes."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from . import channel_files, fm, goals, index, loops, works
from .config import SECTIONS, Config
from .tasks import is_open, load_tasks, parse_register
from .vault import Vault

PRIMED_WORDS = 300   # Primed loads in every session; the prime routine writes the most important first
LIBRARY_WORKS = 40   # bound the listing, never the runtime's refusal, conflict or error notices
HOLDING_LOOPS = 30   # open loops in every session; more than this is a backlog the evening should be closing
BEFORE = ("## Before answering\n\n"
          "Answer from what is loaded here and the notes recalled under the message; open a note or run `brain retrieve` "
          "only when they don't hold the answer. When something loaded bears on the request (a trip, a deadline, a pending "
          "decision), say so in a sentence: an answer any assistant could give without this vault is a miss. A task the "
          "owner parked has no question for them, whatever list shows it: it waits until they reopen it, with no choice "
          "to offer about it. "
          "What the vault and this conversation hold is true now, so say it plainly, with no \"if\", \"unconfirmed\" or "
          "\"planned\" unless the answer turns on it: a trip or event whose time has come is happening. A suggestion or "
          "something you make for them (a plan for an evening, a toast) first opens the note behind the goal, person or "
          "project it draws on. A password, token or key pasted into a message is never saved or used, anywhere: say once "
          "that it goes through your platform's safe route, suggest revoking it, and carry on once it is there.\n\n"
          "What a message asks of your work is written before you reply; the write skill has each command in full. Work "
          "handed to you (\"look into this\", \"build that while I sleep\") is a task handed off now (`brain task new "
          "\"<title>\" --source \"<their words>\" --delivers \"<when to stop>\" --handoff`, `--after T-nnn` for the next "
          "stage of earlier work): say in a line that it is underway and they will hear, never that you are working on it "
          "unless its worker runs now. A small change you can make in a few steps is done in this turn instead. In any "
          "thread, what they say about a task is an instruction for its next run (`brain task T-nnn set --next \"…\"`), "
          "and a request in its thread with a result of its own is a task made from it (`--from T-nnn --handoff`). A tip "
          "on method (study more photos, try another tool) is added to the Next they approved, never put in its place, "
          "and Delivers changes only when they name a new finish. When "
          "a task's owner clears what it was waiting on or tells it to go ahead, `brain task T-nnn set --state in_progress "
          "--next \"…\" --clear blocker resume` and say it is carrying on. Asked how a task is going, answer in "
          "one line from what its worker is doing when one runs, else from `brain continue T-nnn`, with a rough estimate "
          "of what remains. What they say is theirs "
          "to do is a `--state yours` task; a reminder someone asks for is a `when` loop (`brain loops --add '<text>' "
          "--kind when --due DATE --source <note>`, `--hub <matter>` when it is about one of `brain matters`) whose text "
          "carries what they will need to act on it that you hold (the number to call, the account, the address). Their word "
          "on their own facts and choices is enough: never ask for proof, and it replaces what you proposed.")
PORTRAIT = ("Who you think {human} is, ranked within each section: `said` they told you, `seen` you watched them do it "
            "twice, `guess` you inferred it and have not tested it. Act on a guess only to prepare or offer, never outward. "
            "This is yours about {human} and is never told to anyone else, whatever a rule allows.")
COMPANY_PORTRAIT = ("What you understand of {human}, ranked within each section: where it is heading, what it holds to, how "
                    "it likes work done, how it decides, where effort goes and what it has decided against. `said` its "
                    "leadership stated it or the roadmap, goals or initiatives say it; `seen` a pattern across its decisions "
                    "and reviews; `guess` your inference. Let it aim your work: each After line's `for:` is where you can help "
                    "the company get there, so look for that work; follow the Taste lines before anyone has to correct "
                    "you, and say when a task serves none of the After lines. Cite `said` and `seen` lines to "
                    "anyone. A guess about direction is never stated to the team: it goes to the operator as a confirm.")
UPCOMING_DAYS = 7
UPCOMING_ITEMS = 12
AHEAD = re.compile(r"\b(due|deadline|until|flight|flying|trip|visit|leav|arriv|call|renew|expire|starts?|ends?|meet)", re.I)   # today's lines that look forward, not today's evidence
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
          "december")
DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
                  r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.? (\d{1,2})(?:st|nd|rd|th)?\b(?:,? (\d{4}))?", re.I)


def open_register(vault: Vault, body: str) -> str:
    registered, _ = load_tasks(vault.config)
    open_ids = {task.tid for task in registered if task.state and is_open(task)}
    spans, _ = parse_register(body)
    sections, seen = [], set()
    for task_id, start, end in spans:
        if task_id in open_ids and task_id not in seen:
            sections.append(body[start:end].rstrip())
        seen.add(task_id)
    return "\n\n".join(sections)


def build(vault: Vault, only: tuple[str, ...] | None = None, *, app_changes: bool = True) -> str:
    """The compiled context. `only` narrows it to the sections one run asks for (a routine's declaration), always within
    the sections the vault itself allows."""
    config = vault.config
    only = tuple("holding" if name == "triggers" else name for name in only) if only is not None else None
    allowed = tuple(name for name in config.context.sections if only is None or name in only)
    named = dict(zip(("start", "current", "portrait", "primed", "goals", "tasks"), config.context_sources))
    optional = (config.human_portrait, config.agent("Primed.md"), config.human_goals)
    sources = [rel for name, rel in named.items() if name in allowed
               and (rel not in optional or (vault.root / rel).is_file())]
    header = "Generated by `brain`; edits are overwritten. Each section names its source note."
    sections = [header]
    for name in SECTIONS:
        if name not in allowed:
            continue
        if name == "before":
            sections.append(BEFORE)
        elif name == "upcoming":
            sections.append(upcoming(vault))
        elif name == "directory":
            sections.append(directory(vault, tasks_loaded=named["tasks"] in sources))
        elif name == "library":
            if block := library():
                sections.append(block)
        elif name == "holding":
            if block := holding(config):
                sections.append(block)
        elif named[name] in sources:
            rel = named[name]
            if rel in vault.malformed:
                continue   # its header is unreadable; validate names it
            _, body = fm.read(vault.root / rel)
            if rel == config.agent("Tasks.md"):
                body = open_register(vault, body)
            elif rel == config.agent("Primed.md"):
                body = capped(body, PRIMED_WORDS, config.link(rel))
            elif rel == config.human_portrait:
                rule = COMPANY_PORTRAIT if config.edition == "enterprise" else PORTRAIT
                body = rule.format(human=config.human_name) + "\n\n" + body.strip()
            elif rel == config.human_goals:
                body = goals.context(config, app_changes=app_changes)
            sections.append(f"## {config.link(rel)}\n\n{body.strip()}")
        elif name == "goals":
            sections.append(f"## {config.link(config.human_goals)}\n\n{goals.context(config, app_changes=app_changes)}")
    return "\n\n".join(sections) + "\n"


def library() -> str:
    body = works.library()
    if not body:
        return ""
    kept, count = [], 0
    for line in body.splitlines():
        if works.ENTRY.match(line):
            count += 1
            if count > LIBRARY_WORKS:
                continue
        kept.append(line)
    if count > LIBRARY_WORKS:
        kept += ["", f"(cut at {LIBRARY_WORKS} works; the rest is in $AGENT_HOME/works/LIBRARY.md)"]
    return "## Library\n\n" + "\n".join(kept).strip()


def holding(config) -> str:
    """Open loops, so every session sees what it promised and what it will do when something happens."""
    rows = loops.open_loops(config)
    if not rows:
        return ""
    lines = [f"## Holding ({config.link(config.agent('Loops'))})", "",
             "What you promised, owe, wait on, and will do when something happens; your owner reads each line as written. "
             "When a message meets a `when` loop's condition, act on it now within existing permissions. Before you "
             "send or do what any loop holds, `brain loops --claim <id>`; a refused claim means another run is acting "
             "on it, so leave it. Then `brain loops --close <id> --outcome done --because '<what happened>'`, or "
             "`brain loops --release <id>` if acting failed.", ""]
    for item in rows[:HOLDING_LOOPS]:
        lines.append(f"- {item.id} · {item.kind} · {item.text}" + (f" · with {item.with_}" if item.with_ else "")
                     + (f" · if {item.if_}" if item.if_ else "") + (f" · wake {item.wake}" if item.wake else ""))
    if len(rows) > HOLDING_LOOPS:
        lines += ["", f"(cut at {HOLDING_LOOPS} loops; the rest are in `brain loops`)"]
    return "\n".join(lines)


def capped(body: str, limit: int, where: str) -> str:
    """The first `limit` words, cut at a line, with a pointer to the rest."""
    lines, kept, words = body.strip().splitlines(), [], 0
    for line in lines:
        n = len(line.split())
        if words + n > limit and kept:
            return "\n".join(kept).rstrip() + f"\n\n(cut at {limit} words; the rest is in {where})"
        kept.append(line)
        words += n
    return "\n".join(kept)


def upcoming(vault: Vault, days: int = UPCOMING_DAYS) -> str:
    """Sentences anywhere in the hubs, profiles, histories and registers that name a date in the coming days, so a
    deadline kept in a hub or a friend's visit noted in their history reaches the agent before the day passes."""
    config, today = vault.config, vault.config.today()
    found: dict[tuple[date, str], str] = {}
    for rel, note in sorted(vault.notes.items()):
        history = rel.endswith("/History.md")   # trips and visits land in a person's history first
        kind = vault.kind_for(rel)
        if kind == "responsibility" and note.data.get("status") == "retired":
            continue
        if kind in ("episode", "reflection", "dream", "primed") or rel.startswith(config.log_paths) \
                or (kind == "log" and not history) or f"/{config.agent('Journal/')}" in f"/{rel}":
            continue
        for line in note.body.splitlines():
            sentences = re.split(r"(?<=[.!?])\s+", line.strip(" -*>#|"))
            for i, sentence in enumerate(sentences):
                shown = f"{sentences[i - 1]} {sentence}" if i and len(sentence) < 80 else sentence   # a short sentence leans on the one before for its subject
                for m in DATE.finditer(sentence):   # only this sentence's own dates; the one before had its turn
                    when = mentioned(m, today)
                    if when and (today < when <= today + timedelta(days=days) or when == today and AHEAD.search(sentence)):
                        text = re.sub(r"\[\[(?:[^]|]*\|)?([^]]*)\]\]", r"\1", shown).replace("**", "").strip()[:240]
                        found.setdefault((when, text), rel[:-3])
    ahead = sorted(item for item in found.items() if item[0][0] > today)
    items = sorted([item for item in found.items() if item[0][0] == today][:4] + ahead)[:UPCOMING_ITEMS]
    lines = [f"## Upcoming: the next {days} days", "",
             "Dates named in the vault's notes.", ""]
    lines += [f"- {when:%a %b %-d} · {text} · [[{rel}]]" for (when, text), rel in items] or ["- nothing dated"]
    blocks = [block for block in (channel_files.calendar(vault, days), channel_files.canvas(vault, days)) if block]
    return "\n\n".join(["\n".join(lines), *blocks])


def mentioned(m: re.Match, today: date) -> date | None:
    try:
        if m.group(1):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        month = [name[:3] for name in MONTHS].index(m.group(4).lower()[:3]) + 1
        when = date(int(m.group(6) or today.year), month, int(m.group(5)))
        return when if m.group(6) or when >= today - timedelta(days=180) else date(today.year + 1, month, int(m.group(5)))
    except ValueError:
        return None


def directory(vault: Vault, tasks_loaded: bool = False) -> str:
    """Most active notes with index lines, then names of everything the vault knows. Open tasks the Tasks section
    already carries are not named again."""
    hubs, people, agents = [], [], []
    for rel, note in sorted(vault.notes.items()):
        kind = vault.kind_for(rel)
        if note.data.get("kind") in ("orientation", "log"):
            continue   # templates and registries' rules are not topics
        if kind == "profile" and rel.startswith(vault.config.agents_dir + "/"):
            agents.append(rel.split("/")[-2].replace("_", " "))
        elif kind == "profile":
            people.append(rel.split("/")[-2].replace("_", " "))
        elif kind == "hub":
            hubs.append(rel[:-3])
    registered, archived = load_tasks(vault.config)
    tasks = [f"{t.tid} {t.title}" for t in (archived if tasks_loaded else registered + archived) if t.state and is_open(t)]
    selected, _, _ = index.hot_selection(vault, vault.config.context.hot_words)
    # Registries, rules and pointer stubs load elsewhere or say nothing; the list is for topics.
    selected = [n for n in selected if n.data.get("kind") != "orientation" and n.path.name != "README.md"
                and not index.index_line(n).startswith("See RESPONSIBILITY")]
    lines = ["## Directory: what the vault knows", ""]
    if selected:
        lines += ["Most active, with what each holds:", ""]
        lines += [f"- [[{vault.config.link(n.rel[:-3])}|{n.stem}]] — {index.index_line(n)}" for n in selected] + [""]
    lines += ['Names only; `brain retrieve "<query>"` opens any of them.', ""]
    lines.append("- Hubs and topics: " + ", ".join(hubs))
    lines.append("- People with profiles: " + (", ".join(people) or "none"))
    lines.append("- Agents registered: " + (", ".join(agents) or "none"))
    if tasks:
        lines.append("- Open tasks: " + "; ".join(tasks))
    return "\n".join(lines)


def summary(text: str, limit: int) -> str:
    words = len(text.split())
    warning = f"; WARNING: over {limit} words" if words > limit else ""
    return f"context: {words} words{warning}"


def write(vault: Vault, out: Path | None = None) -> str:
    text = build(vault, app_changes=False)
    fm.write_text(out if out is not None else vault.config.repo / ".context.md", text)
    return summary(text, vault.config.context.hot_words)
