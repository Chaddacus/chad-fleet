/* TS compile smoke for the generated contract.
 *
 * Proves the generated `snapshot.ts` compiles and the types are usable (enums included).
 * It deliberately does NOT import the JSON fixture: a `resolveJsonModule` import widens
 * string-literal enums to `string`, which would false-fail on `severity`. Runtime fixture
 * validation is done by ajv against the schema (fixtures/validate.mjs) and by pydantic on
 * the Python side — both enum-aware and deterministic, unlike TS structural assignment.
 */
import type { AppSnapshot, FleetState, InboxItem, SessionSnapshot } from "./snapshot";

const _inbox: InboxItem = { ts: "", channel: "", severity: "warn", title: "", body: "" };
const _session: SessionSnapshot = { id: "", source: "", title: "", updated_at: "" };
const _app: AppSnapshot = {
  id: "",
  name: "",
  state: "",
  mode: "",
  cadence: "",
  owner_brand: "",
  last_progress_at: "",
};
const _check: FleetState = { generated_at: "", apps: [_app], inbox_recent: [_inbox], summary: {} };
void _check;
void _session;
