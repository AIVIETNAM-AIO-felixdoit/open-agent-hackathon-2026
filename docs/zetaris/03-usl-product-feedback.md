# USL product improvements (written as a PM)

These come from building `dd_usl` against Zetaris 2.4.3.1 running locally. Each item is something that cost us time, how often a builder will hit it, and what we would ship.

## P0: errors that stop a first-time builder

**1. Activation fails on nullability with no auto-fix.**
*What happened:* `ACTIVATE USL TABLE ... company` failed with "Column 'cik': nullable -> NOT NULL". Every REST-sourced column is nullable, so **every** `NOT NULL` column in a REST-backed USL hits this.
*Proposal:* when the declared column is `NOT NULL` and the query column is nullable, activate with an implicit `COALESCE`/filter and report the dropped or defaulted rows as a DQ result instead of failing. At minimum, include the exact `COALESCE(col, '') AS col` rewrite in the error.
*Metric:* first-activation success rate.

**2. `DESCRIBE BY` rejects parentheses, and the error comes from a different layer.**
`CREATE LIGHTNING DATABASE ... DESCRIBE BY "... (submissions endpoint)"` returned "Description is invalid, it must be alphanumeric...". A description is free text for humans; restricting it surprises everyone.
*Proposal:* allow any printable text, or escape it server-side.

## P1: friction for agent builders

**3. Programmatic access is unclear.** The REST guide uses an API key created in the GUI; the local platform's verify script logs in through the UI and uses an `idToken`. We used the latter. *Proposal:* one documented "service account token" flow, plus a Python client (`pip install zetaris`) with `query(sql) -> rows`. Agent frameworks are the fastest-growing consumers of a semantic layer and they need a 5-line integration.

**4. XBRL shape differences need per-source code.** Balance-sheet facts have no `start`, so a shared projection failed with `FIELD_NOT_FOUND start`. *Proposal:* a `TRY_FIELD(struct, 'name')` (null when absent) or schema-tolerant `explode` for REST tables whose items vary.

**5. Not idempotent.** `CREATE SCHEMASTORE CONTAINER` has no `IF NOT EXISTS`, and `COMPILE USL IF NOT EXISTS` re-run behaviour is undocumented. Our setup script treats "exists" errors as skips. *Proposal:* `IF NOT EXISTS` everywhere and `CREATE OR REPLACE` for activation, so setup scripts can be re-run safely in CI.

## P2: making the USL agent-native

**6. Describe the model to an LLM.** An agent needs the model's tables, columns, FKs and descriptions as compact text to write SQL itself. *Proposal:* `DESCRIBE USL ... FOR LLM` returning a token-efficient schema card (or an MCP tool), with column descriptions and sample values.

**7. Row-level lineage in query results.** We added `accession` and `filed` columns by hand so the agent can cite the filing. *Proposal:* an optional `_source` column (REST endpoint + fetch time) on every USL row, so provenance survives joins automatically. For an "explain why" agent this is the most valuable feature a semantic layer can offer.

**8. DQ results as data.** `RUN DQ` returns counts; we pasted them into the evidence text. *Proposal:* a DQ result table that can be joined to facts (`WHERE dq_valid`), so an agent can exclude invalid rows in one query.
