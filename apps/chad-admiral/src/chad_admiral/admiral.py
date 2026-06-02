"""The admiral state machine: chat messages -> reply (+ dispatch side-effects).

Stateless by design — Odysseus sends the full message history on every call, so
the admiral reconstructs its state from the transcript (HUB_ARCHITECTURE's
frontload-heavy loop maps cleanly onto a chat thread):

    1 user turn  (a task list)  -> DISCOVERY -> GATE A (batched clarifications)
    >=2 user turns              -> DISPATCH  (freeze dossiers + spawn captains)

Re-discovery on the answer turn is the cheap git touch again; no server session.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections.abc import Iterator

from .authority import check_authority, scan_changed_files
from .captain import run_captain
from .discovery import discover
from .dispatch import accept_slice, freeze_and_spawn, set_slice_state
from .intake import looks_like_intake, parse_task_list
from .types import DiscoveryResult, TaskItem

# --- S3 in-band escalation -------------------------------------------------
# The admiral is stateless (Odysseus replays the full transcript each turn), so a
# parked captain's resume state must live IN the chat. We embed it in an HTML
# comment on the escalation message: invisible in the rendered chat, but it
# round-trips in the message history Odysseus sends back. On the operator's next
# turn we parse it and resume the exact captain.
_SENTINEL_PREFIX = "<!--ADMIRAL-ESCALATION:"
_SENTINEL_SUFFIX = "-->"


def _captain_state(dossier, label: str, question: str) -> dict:
    """Resume state carried in a sentinel: enough to re-run the exact captain and
    re-show its question on carry-forward. Keys are short to keep the b64 small.
    `o` is the dossier's task_brief — exactly what the captain originally ran."""
    return {"t": dossier.track_id, "r": dossier.repo_path, "o": dossier.task_brief,
            "b": dossier.rlm_ref, "l": label, "q": question}


def _make_sentinel(state: dict) -> str:
    payload = base64.b64encode(json.dumps(state).encode()).decode()
    return f"{_SENTINEL_PREFIX}{payload}{_SENTINEL_SUFFIX}"


def _parse_all_sentinels(content: str) -> list[dict]:
    """All resume-state sentinels in a message, in document order. A single
    parallel-dispatch turn can park several captains, each with its own sentinel."""
    out: list[dict] = []
    pos = 0
    while True:
        i = content.find(_SENTINEL_PREFIX, pos)
        if i < 0:
            break
        j = content.find(_SENTINEL_SUFFIX, i)
        if j < 0:
            break
        try:
            out.append(json.loads(base64.b64decode(content[i + len(_SENTINEL_PREFIX):j]).decode()))
        except Exception:
            pass
        pos = j + len(_SENTINEL_SUFFIX)
    return out


def _pending_escalation(messages: list[dict]) -> tuple[list[dict], str] | None:
    """If the MOST RECENT assistant message parked one or more captains and the
    operator has since replied, return (sentinels_in_order, answer). Only the
    latest assistant message counts, so a resolved escalation (a later message
    with no sentinel) does not re-trigger and carry-forward works."""
    assistant_idxs = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if not assistant_idxs:
        return None
    li = assistant_idxs[-1]
    sentinels = _parse_all_sentinels(messages[li].get("content") or "")
    if not sentinels:
        return None
    answers = [m.get("content") or "" for m in messages[li + 1:] if m.get("role") == "user"]
    if not answers:
        return None
    return sentinels, answers[-1]


def _emit_escalation(state: dict, *, idx: int = 1, total: int = 1) -> Iterator[str]:
    """Stream one captain's escalation block + its carry-forward sentinel. When
    `total` > 1 (parallel dispatch parked several), number it so the operator can
    answer a specific one with a leading `N:`."""
    label = state.get("l") or state.get("o") or "task"
    question = state.get("q") or "(needs a decision)"
    head = f"  ↳ **ESCALATION {idx}/{total} (Gate B)** — " if total > 1 else "  ↳ **ESCALATION (Gate B)** — "
    yield (f"\n{head}`{label}` cannot proceed without a decision:\n\n"
           f"  > {question}\n\n")
    if total > 1:
        yield f"  Answer this one with `{idx}: <your decision>`.\n"
    else:
        yield "  Reply with your answer and I'll resume this captain.\n"
    yield _make_sentinel(state) + "\n"


def _user_turns(messages: list[dict]) -> list[str]:
    return [m.get("content") or "" for m in messages if m.get("role") == "user"]


def _is_probe(messages: list[dict]) -> bool:
    if not messages:
        return True
    last = (messages[-1].get("content") or "").strip().lower()
    return last in ("say ok", "ok") and len(messages) <= 2


def _gate_a(tasks: list[TaskItem], discs: list[DiscoveryResult]) -> str:
    gaps = [g for d in discs for g in d.gaps]
    found = [d for d in discs if d.repo_path]
    lines = [f"**Admiral — discovery across {len(tasks)} task(s).** "
             f"Resolved {len(found)} repo(s) ({', '.join(d.git_head or '?' for d in found) or 'none'})."]
    if not gaps:
        return lines[0] + "\n\nNo open questions — reply `go` and I'll freeze dossiers and spawn the fleet."
    lines.append("\nThings I can't resolve myself — answer once and I'll spawn every captain (Gate A):\n")
    for i, g in enumerate(gaps, 1):
        lines.append(f"{i}. {g}")
    lines.append("\nAnswer these and I'll freeze one dossier per task and spawn the fleet.")
    return "\n".join(lines)


def _dispatch(tasks: list[TaskItem], discs: list[DiscoveryResult], answer: str) -> str:
    clar = {"gate_a_answers": answer}
    out = ["**Frozen.** Dossiers sealed and captains spawned (each an `auto_runtime` track):\n"]
    for task, disc in zip(tasks, discs):
        try:
            dossier = freeze_and_spawn(task, disc, clar)
            out.append(f"- `{task.title}` → track `{dossier.track_id}` · "
                       f"dossier `{dossier.omni_mem_thread_id[:12]}` · repo `{dossier.repo_path}`")
        except Exception as e:  # surface real backend failures, don't fake success
            out.append(f"- `{task.title}` → **dispatch FAILED**: {str(e)[:160]}")
    out.append("\nI go idle now — you'll only hear from me on a Gate-B escalation.")
    return "\n".join(out)


def _run_captain_bg(repo: str, objective: str) -> tuple[dict, list[str], threading.Thread]:
    """Start one captain in a daemon thread; return (result, err, thread)."""
    result: dict = {}
    err: list[str] = []

    def _run():
        try:
            result.update(run_captain(repo, objective))
        except Exception as e:  # noqa: BLE001
            err.append(str(e))

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    return result, err, th


def _settle(label: str, track_id: str | None, repo: str, base_ref: str | None,
            result: dict, err: str | None) -> tuple[str | None, str | None]:
    """Turn one finished captain into either a human-readable result block (and
    record the slice's terminal state) or an escalation question. Returns
    (block_text, escalation_question) — exactly one is non-None."""
    if err:
        return (f"  ↳ `{label}` captain **ERROR**: {err[:160]}\n", None)
    esc = result.get("escalation")
    if esc:
        if track_id:
            set_slice_state(track_id, "blocked", f"escalation: {esc}")
        return (None, esc)
    changed = result.get("files_changed") or []
    # Post-exec gate: path + secret-content. Reject (do NOT accept) on violation.
    violations = scan_changed_files(repo, changed, base_ref=base_ref)
    if violations:
        if track_id:
            set_slice_state(track_id, "rework", f"REJECTED policy violation: {violations}")
        return (f"  ↳ `{label}` **REJECTED** — policy violation: {violations}. "
                f"Slice NOT accepted; manual review required (commit left for inspection).\n", None)
    ev = f"goose_exit={result.get('goose_exit_code')} files_changed={changed}"
    accepted = accept_slice(track_id, ev) if track_id else False
    return (f"  ↳ `{label}` **done** — files_changed `{changed}` · "
            f"slice {'accepted' if accepted else 'recorded'} on track\n", None)


def _dispatch_stream(tasks: list[TaskItem], discs: list[DiscoveryResult], answer: str) -> Iterator[str]:
    """Streaming dispatch (S5): freeze + spawn all, authority-gate each, then run
    every cleared captain CONCURRENTLY. Completions stream back labeled per task as
    they finish; escalations are gathered and presented as one numbered Gate-B list."""
    clar = {"gate_a_answers": answer}
    yield "**Frozen.** Spawning captains (each an `auto_runtime` track):\n\n"
    prepared: list[tuple[TaskItem, object]] = []
    for task, disc in zip(tasks, discs):
        try:
            dossier = freeze_and_spawn(task, disc, clar)
        except Exception as e:
            yield f"- `{task.title}` → **dispatch FAILED**: {str(e)[:160]}\n"
            continue
        yield (f"- `{task.title}` → track `{dossier.track_id}` · "
               f"dossier `{dossier.omni_mem_thread_id[:12]}` · repo `{dossier.repo_path}`\n")
        prepared.append((task, dossier))

    runnable: list[tuple[TaskItem, object]] = []
    for task, dossier in prepared:
        verdict = check_authority(dossier.repo_path)
        if not verdict.allowed:
            yield (f"  ↳ `{task.title}` **execution BLOCKED** by ContractKernel — "
                   f"{verdict.action}/{verdict.tier} ({verdict.reason}). Captain parked.\n")
            continue
        runnable.append((task, dossier))
    if not runnable:
        yield "\nNo captains cleared to execute.\n"
        return

    yield f"\n**Executing {len(runnable)} captain(s) in parallel.**"
    handles = [(_run_captain_bg(d.repo_path, t.raw), t, d) for t, d in runnable]
    done: set[int] = set()
    escalations: list[dict] = []
    while len(done) < len(handles):
        newly = [i for i, ((res, err, th), _, _) in enumerate(handles)
                 if i not in done and not th.is_alive()]
        if not newly:
            time.sleep(4)
            yield " ."
            continue
        for i in sorted(newly):
            done.add(i)
            (res, err, _th), task, dossier = handles[i]
            block, esc = _settle(task.title, dossier.track_id, dossier.repo_path,
                                  dossier.rlm_ref, res, err[0] if err else None)
            if esc is not None:
                escalations.append(_captain_state(dossier, task.title, esc))
            else:
                yield "\n" + block

    if escalations:
        yield f"\n**{len(escalations)} captain(s) need a decision (Gate B):**\n"
        for n, st in enumerate(escalations, 1):
            yield from _emit_escalation(st, idx=n, total=len(escalations))
    else:
        yield "\nI go idle now — you'll only hear from me on a Gate-B escalation."


def _parse_leading_index(answer: str) -> tuple[int | None, str]:
    """Parse a leading `N:` (or `N.` / `N)`) off the operator's answer, used to
    target one of several parked captains. Returns (index_or_None, remainder)."""
    m = re.match(r"\s*(\d+)\s*[:.)]\s*(.*)", answer, re.DOTALL)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, answer.strip()


def _resume_stream(sentinels: list[dict], answer: str) -> Iterator[str]:
    """Resume parked captain(s) (S3 + S5). One sentinel → the answer resumes it.
    Several (parallel dispatch parked multiple) → the operator targets one with a
    leading `N:`; the rest are re-emitted (carry-forward) so they survive the turn."""
    if not sentinels:
        yield "No parked captain to resume."
        return
    n, rest = _parse_leading_index(answer)
    if len(sentinels) == 1:
        target = sentinels[0]
        ans = rest if n == 1 else answer.strip()
        others: list[dict] = []
    elif n is not None and 1 <= n <= len(sentinels):
        target = sentinels[n - 1]
        ans = rest
        others = [s for k, s in enumerate(sentinels) if k != n - 1]
    else:
        yield (f"You have {len(sentinels)} parked captains — prefix your answer with the "
               f"number, e.g. `1: <decision>`:\n")
        for k, s in enumerate(sentinels, 1):
            yield f"  {k}. `{s.get('l') or s.get('o')}` — {s.get('q')}\n"
        for k, s in enumerate(sentinels, 1):  # carry forward all (unresolved)
            yield from _emit_escalation(s, idx=k, total=len(sentinels))
        return

    repo, track_id = target.get("r"), target.get("t")
    base_ref, label = target.get("b"), (target.get("l") or "task")
    if not repo:
        yield "Couldn't parse the parked captain's resume state. Re-issue the task.\n"
        return
    yield f"**Resuming captain** `{label}` on `{repo}` with your decision.\n\n"
    folded = (f"{target.get('o') or ''}\n\n[Operator answered your escalation question]: {ans}\n"
              "Proceed with this decision. Do not escalate again unless a genuinely "
              "new blocker arises.")
    result, err, th = _run_captain_bg(repo, folded)
    yield "  ↳ captain executing"
    while th.is_alive():
        th.join(timeout=4)
        yield " ."
    block, esc = _settle(label, track_id, repo, base_ref, result, err[0] if err else None)
    if esc is not None:  # captain re-escalated
        yield from _emit_escalation(_captain_state_from_sentinel(target, esc), idx=1, total=1)
    else:
        yield "\n" + block

    if others:  # carry forward the captains the operator didn't answer this turn
        yield f"\n**Still awaiting your decision on {len(others)} captain(s):**\n"
        for k, s in enumerate(others, 1):
            yield from _emit_escalation(s, idx=k, total=len(others))


def _captain_state_from_sentinel(sentinel: dict, question: str) -> dict:
    """Rebuild resume state from an existing sentinel with a fresh question (used
    when a resumed captain re-escalates)."""
    s = dict(sentinel)
    s["q"] = question
    return s


def _route(messages: list[dict]):
    """Shared routing: returns ('text', str), ('resume', state, answer), or
    ('stream', tasks, discs, answer)."""
    if _is_probe(messages):
        return ("text", "OK")
    pend = _pending_escalation(messages)
    if pend:
        return ("resume", pend[0], pend[1])  # (sentinels, answer)
    users = _user_turns(messages)
    if not users:
        return ("text", "Admiral online. Hand me the task list and I'll discover, "
                "then ask what I can't answer myself.")
    tasks = parse_task_list(users[0])
    if not tasks or not looks_like_intake(users[0]):
        return ("text", "That doesn't parse as a task list. Give me bulleted items like "
                "`- <repo>: <what to do>` and I'll discover across them.")
    discs = [discover(t) for t in tasks]
    if len(users) == 1:
        return ("text", _gate_a(tasks, discs))
    return ("stream", tasks, discs, users[-1])


def reply(messages: list[dict]) -> str:
    """Non-streaming response (dispatch runs synchronously)."""
    routed = _route(messages)
    if routed[0] == "text":
        return routed[1]
    if routed[0] == "resume":
        return "".join(_resume_stream(routed[1], routed[2]))
    _, tasks, discs, answer = routed
    return _dispatch(tasks, discs, answer)


def reply_stream(messages: list[dict]) -> Iterator[str]:
    """Streaming response — used by the OpenAI server so dispatch can execute
    captains inline while keeping the connection alive."""
    routed = _route(messages)
    if routed[0] == "text":
        yield routed[1]
        return
    if routed[0] == "resume":
        yield from _resume_stream(routed[1], routed[2])
        return
    _, tasks, discs, answer = routed
    yield from _dispatch_stream(tasks, discs, answer)
