---
name: {prefix}doctor
description: Fix what brain doctor check found that needs judgment, and report what only a person can fix
version: 1.1.0
metadata:
  runtime:
    tags: [routine, night, health]
    category: brain
    context: []
---
# Doctor

One job: fix the issues in the Script Output above, one JSON from `brain doctor check` (run it yourself if there is none; when it prints nothing, there is nothing to do). You start from nothing on purpose: work from the issues and what the vault shows, never from what you remember about it. The mechanical fixes already ran; `fixed` lists them.

## Procedure
1. **Each issue in `issues`.** Read its `class`, then do what that class says below. Check it fixed with the same command that found it (`brain health --json`, `brain validate`), then log it: `brain doctor log <key> fixed --note "<what you did, one clause>"`. When you cannot fix it safely, `brain doctor log <key> left --note "<why>"`. When only a person can, `brain doctor log <key> human --note "<what they must do>"`.
   - `stage-stale`: read the stage's last commit and `brain schedule list`. Re-run a stage only through its own command or script; a routine that keeps failing is `left` with what its error says.
   - `migration-stuck`: read `{runtime_dir}/pending-migration.md` and do what it says, then `brain migrate-done`.
   - `engine-mismatch`: the `migrate-check` fix failed; read its error in `fixed` and run `brain migrate-check` again.
   - `structure`, `hygiene-unfixed`: `brain validate` names each problem and usually its fix. Fix it the way the owning routine would (Sleep 1 for CURRENT, evening for Needs you and loops).
   - `routine-failing`: read `last_error` in the text. Fix what is in the vault (a bad path, a missing note, a malformed file); anything in the runtime is `left`.
   - `commit-broke`: the text names the commit that left the note broken; read it with `git show <commit> -- <note>` and repair the note so it keeps what that commit meant to write.
   - `turn-error`: a `brain` command failed in a conversation. Find why (`brain <command> --help`, the note or task it named) and fix what is wrong in the vault; when the command was simply misused, there is nothing to fix, so log it `fixed` with what the right call was. If the reply to the person was wrong because of it, that is the weekly review's, not yours.
   - `unpushed`: the push failed; `brain pull`, then `brain commit --push -m "doctor(<date>): push"`.
2. **Each item in `report`.** These reach the operators (`operators`), never {human_name} unless they are an operator. For each Demiurge operator (`demiurge:@<handle>`), send one `demiurge_message`: the issue's text in plain words, what you tried, and for a `repeat`, that it keeps coming back so the engine is likely at fault, with the smallest steps that bring it back. Never quote a conversation or a note's contents; describe the pattern. Log each with `brain doctor log <key> human --note "reported"`.
3. `brain commit -m "doctor(<date>): <the keys you fixed>" --push` if anything changed.

## Boundaries
- Never delete or close anything {human_name} or anyone else made: a task, loop, item, rule, note or line. If clearing an issue would need that, it is `human`.
- Never edit SOUL.md, `brain.toml` or the schedule, and never touch anything outside the vault.
- Never guess a name or an id. Setup issues are always `human`.
- No messages but the operator reports in step 2.
