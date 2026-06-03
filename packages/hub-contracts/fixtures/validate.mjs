/* Node half of the cross-language golden-fixture guard.
 *
 * Validates the shared fixture against the GENERATED JSON Schema using ajv (enum-aware,
 * deterministic). The Python half validates the same fixture against the pydantic source.
 * Both derive from the one canonical source (pydantic), so passing both proves the fixture
 * — and therefore the contract — is consistent across languages.
 *
 * Run: `node fixtures/validate.mjs` (wired into `npm run check`). Exits non-zero on failure.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import Ajv from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const here = dirname(fileURLToPath(import.meta.url));
const ajv = new Ajv({ allErrors: true, strict: false });
addFormats(ajv);

const pairs = [
  ["snapshot.schema.json", "snapshot.example.json"],
  ["admiral-chat.schema.json", "admiral-chat.example.json"],
];

let failed = false;
for (const [schemaFile, fixtureFile] of pairs) {
  const schema = JSON.parse(readFileSync(join(here, "..", "schema", schemaFile), "utf8"));
  const fixture = JSON.parse(readFileSync(join(here, fixtureFile), "utf8"));
  const validate = ajv.compile(schema);
  if (!validate(fixture)) {
    console.error(`[hub-contracts] ${fixtureFile} FAILED ${schemaFile}:`);
    console.error(JSON.stringify(validate.errors, null, 2));
    failed = true;
  } else {
    console.log(`[hub-contracts] ${fixtureFile} validates against ${schemaFile} ✓`);
  }
}
if (failed) process.exit(1);
