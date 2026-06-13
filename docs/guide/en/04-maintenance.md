# Maintenance

Keeping a knowledge base fresh relies on a set of **zero-LLM deterministic maintenance tools**. `health` / `lint` are **advisory (non-blocking by default)**; `graph` / `reindex` write derivatives / fix the index; `heal` is the only LLM-backed materializing write (gated).

---

## `guanlan health`

File-level structural check: **stub pages + index↔disk sync**.

```bash
guanlan -C my-wiki health
guanlan -C my-wiki health --json
guanlan -C my-wiki health --strict     # fail with exit 6 if there are findings (for CI/nightly)
```

| Arg | Meaning |
|---|---|
| `--json` | JSON contract |
| `--strict` | Exit `6` (`EXIT_LINT_FINDINGS`) on findings, to turn advisories into failures in CI/nightly |

Advisory by default: exits `0` even with findings. `index_missing_page` (a page on disk not in the index) is fixed by [`reindex`](#guanlan-reindex).

---

## `guanlan lint`

Graph-aware structural lint: **orphans / broken links / missing entities**, plus advisory graph-topology findings (hub nodes / thin inter-community links / isolated communities / bridge edges / cut vertices).

```bash
guanlan -C my-wiki lint
guanlan -C my-wiki lint --json
guanlan -C my-wiki lint --strict
```

| Arg | Meaning |
|---|---|
| `--json` | JSON contract |
| `--strict` | Exit `6` on findings |

Advisory by default. `missing_entity` (frequent broken links) is materialized into pages by [`heal`](#guanlan-heal).

---

## `guanlan graph`

Deterministic graph build: `[[wikilink]]` → `graph/graph.json` + a self-contained zero-JS `graph/graph.html` (with communities, topology hints, bridges/cut-vertices).

```bash
guanlan -C my-wiki graph
guanlan -C my-wiki graph --json-only     # write graph.json only, skip graph.html
```

| Arg | Meaning |
|---|---|
| `--json-only` | Write `graph.json` only, skip `graph.html` |

The graph is an **idempotently rebuildable derivative**, never a source of truth. LLM-inferred edges are deliberately excluded (to preserve rebuildability).

---

## `guanlan reindex`

Index backfill: **register** content pages that exist on disk but are missing from `index.md`. **Zero-LLM**, fixes `health.index_missing_page`.

```bash
guanlan -C my-wiki reindex
guanlan -C my-wiki reindex --dry-run     # print the worklist only, no disk write
guanlan -C my-wiki reindex --prune       # also remove dangling lines pointing at missing files
```

| Arg | Meaning |
|---|---|
| `--dry-run` | Print the worklist only; read-only, no disk write |
| `--prune` | Also remove index lines pointing at non-existent files (`index_dangling`); off by default |
| `--json` | JSON contract |

Applies by default (writes `wiki/index.md`). The one-line summary is left blank for a later `ingest`/human (decision P3.4-1). Writes only `index.md` — no `raw/`, no gate, no `log.md`.

---

## `guanlan heal`

Missing-entity materialization: turn frequent broken links (referenced by many pages via `[[...]]` but having no page) into entity pages **on demand via LLM**. **Goes through the P2 write gate. Needs a model.**

```bash
guanlan -C my-wiki heal --dry-run                 # print the worklist only (read-only, zero-LLM)
guanlan -C my-wiki heal                            # materialize (default limit/threshold)
guanlan -C my-wiki heal --limit 3 --min-refs 2
```

| Arg | Meaning |
|---|---|
| `--limit` | Max to materialize this batch (by descending reference count; must be ≥ 1) |
| `--min-refs` | Selection threshold: materialize only if referenced by ≥ this many pages (aligned with lint; must be ≥ 1) |
| `--dry-run` | Print the worklist only; read-only, zero-LLM, does not touch Agentao |
| `--model` | Override the Agentao model |
| `--json` | Structured JSON for worklist/receipt |

Use `--dry-run` to see what would be built before deciding. Materialization is a governed write (subprocess + `raw/` snapshot, same as ingest).

---

## Exit codes

| Code | Name | Meaning |
|---|---|---|
| `0` | `EXIT_OK` | Success (advisory commands exit 0 even with findings, unless `--strict`) |
| `1` | `EXIT_USAGE` | Usage/environment error (missing optional dep, bad args, IO failure) |
| `3` | `EXIT_CHECK_FAILED` | `check` validation failed (write-gate gatekeeper) |
| `4` | `EXIT_RAW_MUTATED` | `raw/` was mutated during a write (snapshot mismatch) |
| `5` | `EXIT_AGENT_ERROR` | Agentao runtime / Agent error |
| `6` | `EXIT_LINT_FINDINGS` | `health`/`lint` has findings under `--strict` |

**Advisory-not-gate**: `health`/`lint` exit `0` by default and only fail with `6` under `--strict` — use them to read reports day-to-day, use `--strict` as a gate in CI/nightly.

See: repo [`docs/P3-健康与图谱.md`](../../P3-健康与图谱.md), [`docs/P3.4-索引回填.md`](../../P3.4-索引回填.md), [`docs/P3.5-图谱分析.md`](../../P3.5-图谱分析.md).
