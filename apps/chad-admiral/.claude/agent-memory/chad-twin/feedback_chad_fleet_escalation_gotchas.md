---
name: chad-fleet-escalation-gotchas
description: Non-obvious gotchas wiring the chad-fleet admiral — escalation detection, Odysseus round-trip, stateless multi-task resume
metadata:
  type: feedback
---

Gotchas burned building the chad-fleet admiral. Non-obvious; cost real debugging.

**1. Escalation detection false-positives on the system-prompt echo.** goose
echoes its own system prompt into its log/summary. The prompt tells goose to emit
`ESCALATE: <one-line question>` — so a naive `re.search(r"ESCALATE:\s*(.+)")`
matches the INSTRUCTION TEMPLATE, not a real escalation.
- **Why:** any marker named in the prompt appears in the log when the prompt is
  echoed, so you can't just search for the marker.
- **How to apply:** when detecting an agent-emitted marker whose name you put in
  the prompt, reject the template echo — skip captures containing the placeholder
  (`<one-line question>`) or instruction phrasing (`stop immediately`), and
  `finditer` for the last genuine match. See `captain.py::_detect_escalation`.

**2. Stateless resume rides on HTML-comment sentinels — verified to survive
Odysseus.** The admiral can't get Odysseus's `session_id` (would need patching 4
layers of an external repo), so resume state is embedded in the assistant message
as `<!--ADMIRAL-ESCALATION:<b64 json>-->`. Works because (verified by source +
empirical drive-through): Odysseus stores assistant content verbatim
(`_extract_thinking_meta` only strips `<think>`, not HTML comments) and replays
full assistant history (`get_context_messages` + `_sanitize_llm_messages` preserve
`content`).
- **How to apply:** prefer stateless-via-transcript over propagating IDs through an
  external repo's call stack. Parse sentinels off the MOST-RECENT assistant message
  only. Confirm any "survives the round-trip" claim empirically — [[proof-before-reporting]].

**3. Multi-task parallel escalation needs carry-forward.** When N captains run in
parallel and several escalate, all their sentinels live in ONE assistant message.
The operator answers one (leading `N:`); resuming it produces a NEW assistant
message that must **re-emit the un-answered captains' sentinels** or they're lost
(since `_pending_escalation` only reads the latest message). So the sentinel must
also carry the question text (`q`) to re-show it. See `admiral.py::_resume_stream`
+ `_captain_state`.

**4. Odysseus `stream_llm` uses the endpoint URL verbatim** (openai branch:
`target_url = url`) — it does NOT append `/chat/completions`. A session's
`endpoint_url` must be the FULL chat-completions URL or chat-mode POSTs to `/v1`
and 404s. Registered model-endpoints get this via `build_chat_url`; a hand-created
session needs the full path. Related: [[chad-fleet-admiral]].

**5. AgentOps (`~/automation_architecture`) imports use `.js` extensions** even for
`.ts` source (TS resolves `.js`→`.ts`); `tsconfig` lacks `allowImportingTsExtensions`.
Importing `from "./types.ts"` fails `npm run typecheck` (TS5097). Use `.js`. The
repo runs via `tsx` (which tolerates either) so a `.ts` import works at runtime but
breaks typecheck — a latent trap.
