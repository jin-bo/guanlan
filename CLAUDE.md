# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

观澜 (GuānLán) is an implementation of the [Karpathy LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): an Agent incrementally builds and maintains a structured, cross-linked markdown knowledge wiki instead of doing fresh RAG retrieval on every query. The full design (in Chinese) is the authoritative spec — read [`docs/DESIGN.md`](docs/DESIGN.md) before any non-trivial change.

**Current status: P4 + zero-LLM half-phases through P4.6, released as v0.1.1 (optional local Web host).** P2's closed loop (`guanlan init` / `ingest` / `query` / `check` / `install-skill`, wired through Agentao) + P3's three on-demand zero-LLM maintenance tools (`guanlan health` / `lint` / `graph`) + P4's **optional** Web host (`guanlan web`, needs `pip install 'guanlan-wiki[web]'`) are all implemented. P4 puts the existing commands in a browser via a thin FastAPI/uvicorn layer (127.0.0.1-only, `workers=1`) under a `guanlan/web/` subpackage that carries *no business intelligence*: it reuses `run_ingest` / `run_check` / `run_health` / `run_lint` / `build_and_write_graph` / `pages.*` and an embedded read-only `Agentao` for chat. **Read/write split** (DESIGN §5.2): the only write job `ingest` reuses the P2 subprocess + single-writer gate (one background worker, FIFO); all Q&A (one-shot + multi-turn) goes through a read-only in-process embedded `Agentao` (`.arun`, token-streamed, memory-only). Web-side `raw/` writes (P4.1 paste / P4.6 promote), writable work-sessions (P4.5), and session persistence (P4.2) are now implemented as half-phases; `query --backfill` and multi-format auto-ingest remain post-P4 / P5 (DESIGN §8). When adding features, match the phase boundaries in DESIGN §4.4 / §7.

The **P2 spec** (module layout, deterministic gate, `raw/` snapshot + `check` contracts, exit codes, Agentao integration) is [`docs/P2-最小闭环.md`](docs/P2-最小闭环.md); the **P3 spec** (`pages.py` shared primitives with strict/lenient frontmatter tiers, `graph`/`health`/`lint` contracts, `EXIT_LINT_FINDINGS`, advisory-not-gate exit semantics) is [`docs/P3-健康与图谱.md`](docs/P3-健康与图谱.md); the **P4 spec** (`guanlan/web/` layout, the read/write split, single-worker `ingest` job, read-only embedded chat with the four embedding pitfalls + token-streaming transport contract, HTTP API + SSE contracts, no new exit codes) is [`docs/P4-Web宿主.md`](docs/P4-Web宿主.md) — together they document how the current code is structured. The **P3.1 spec** (optional `aliases` frontmatter feeding the single `pages.py` resolution point: `alias_index` / `link_resolution_index` / alias-aware `link_target_stems`, `check` uniqueness validation `aliases.collides_stem`/`aliases.duplicate`, alias edges in `graph`, Web `[[alias]]` linking — a zero-LLM half-phase enhancement, not a new milestone; P5 still = multi-format) is [`docs/P3.1-别名解析.md`](docs/P3.1-别名解析.md). Two **landing designs for P4 §10 deferred items** (**both implemented**) are [`docs/P4.1-Web投喂.md`](docs/P4.1-Web投喂.md) (**P4.1** — `POST /api/raw` paste-to-save: a human source-add, *not* a gated agent write; serialized through the existing single-writer `JobQueue` to avoid the `run_guarded_write` `raw/` snapshot window; default no-overwrite, slug + `.md`) and [`docs/P4.2-会话落盘.md`](docs/P4.2-会话落盘.md) (**P4.2** — session persistence/restore: reuse `agentao.embedding.{save_session,load_session,list_sessions,delete_session}` under `<kb>/.agentao/sessions/`, conversation id → agentao UUID, lazy restore that re-applies the read-only two-point posture; thin adaptation over agentao's *snapshot-file* semantics — dedup the catalog by `session_id` and prune to one snapshot per session *before* each save (since `save_session` rotates by file-count right after writing) so the 10-*file* rotation cap behaves as a 10-*session* cap). Both are zero-LLM Web-host half-phases that stay inside the P4 boundary (no new exit codes, no SSE changes, no writable sessions). A third Web-host half-phase, [`docs/P4.3-Web-heal.md`](docs/P4.3-Web-heal.md) (**P4.3** — implemented), lands the Web-heal item deferred in P3.2 §11 / P3.3 §10: a read-only `GET /api/heal/preview` (zero-LLM worklist) plus a `POST /api/heal` write job that `enqueue`s heal as a new job kind on the existing single-writer FIFO `JobQueue` (serialized with `ingest`/raw-write, polled via `/api/jobs/{id}`). It carries heal's structured `HealResult` by an additive `heal.py` refactor — a non-printing core `run_heal_result(...) -> HealRun` (mirroring P3.2's `run_guarded_write_result` split; `run_heal` stays a byte-identical CLI shell) — plus an optional `Job.result` field; heal uses the subprocess runner (so the P4-8 embedding pitfalls don't apply), adds no new exit codes/SSE, and never writes `raw/`. The **P3.4 spec** (`docs/P3.4-索引回填.md`, **implemented**) adds `guanlan reindex` — the zero-LLM deterministic *fixer* paired with `health.index_missing_page` (as `heal` pairs with `lint.missing_entity`, but zero-LLM since registering an existing page needs no generation): it sinks the index↔disk sync detection into a single `pages.index_sync_state`归口 (shared by `health` and `reindex`, no drift), then registers each unindexed content page into its `index.md` section (dir→section, frontmatter `title` anchor, `aliases` tail-note; the only LLM-shaped ingredient — the prose one-line summary — is left blank for a later `ingest`/human, decision P3.4-1). Default applies; `--dry-run` previews, `--prune` removes `index_dangling` lines (off by default). Only writes `wiki/index.md` (config catalog) — no `raw/`, no Agentao, no gate, no `log.md`, no new exit codes. Three further Web-host half-phases (**all implemented**) extend the embedded chat: [`docs/P4.4-Web斜杠命令.md`](docs/P4.4-Web斜杠命令.md) (**P4.4** — Web slash-commands + read-only introspection `/status` `/context` `/skills` `/tools` `/mode`, stop button, SSE `start`·`stopped`), [`docs/P4.5-可写Web工作会话.md`](docs/P4.5-可写Web工作会话.md) (**P4.5** — writable work-session `/mode workspace-write` with a three-layer write guard + shared-lock single-writer + undo; Agent may write `workspace/`, `raw/` stays hard read-only), and [`docs/P4.6-Web上传与晋级.md`](docs/P4.6-Web上传与晋级.md) (**P4.6** — `POST /api/upload` staging to `workspace/uploads/`, dual-use as chat `<attachment>` / image-vision *or* a parse→human-review→promote path that writes `raw/`; plus `workspace/` browse/preview/delete endpoints). P4.6 also ships the auxiliary **`pdf-to-markdown` skill** (`skills/pdf-to-markdown/`, force-included into the wheel and installed globally by `guanlan/skill.py` alongside `guanlan-wiki`) so the writable-session Agent can parse uploaded PDF/DOCX/… into `workspace/parsed/` (the multi-format auto-parse pipeline itself is still P5).

## Commands

```bash
uv run guanlan init /tmp/demo            # scaffold a knowledge base (deterministic, zero-LLM)
uv run guanlan -C /tmp/demo check        # deterministic validation (frontmatter / broken links / sources)
uv run guanlan -C /tmp/demo health       # P3: stub pages + index↔disk sync (advisory; --strict → exit 6)
uv run guanlan -C /tmp/demo lint         # P3: orphans / broken links / missing entities (advisory)
uv run guanlan -C /tmp/demo graph        # P3: write graph/graph.json + graph.html (--json-only skips html)
uv run guanlan -C /tmp/demo reindex      # P3.4: register disk pages missing from index.md (zero-LLM; --dry-run / --prune)
uv run guanlan -C /tmp/demo web --no-browser   # P4: optional local Web host (needs guanlan-wiki[web]; 127.0.0.1 only)
uv run pytest                            # run all tests
uv run pytest tests/test_web.py          # P4 Web host tests (skipped if guanlan-wiki[web] absent)
uv run pytest tests/test_init.py::test_init_is_idempotent_and_non_destructive  # single test
```

(`ingest` / `query` and the Web host's chat drive Agentao + the skill and need a configured model; `init` / `check` / `health` / `lint` / `graph` / `reindex` are the zero-LLM ones runnable offline. `guanlan web` needs the optional `web` extra: `uv pip install 'fastapi>=0.110' 'uvicorn>=0.29' 'markdown>=3'`, or `pip install 'guanlan-wiki[web]'`.)

Python 3.12+, dependencies managed by `uv` (see `uv.lock`). The package depends on `agentao` (the governed Agent runtime executing LLM-driven `ingest`/`query` via subprocess, and — in P4 — embedded read-only for Web chat). The `web` extra (fastapi/uvicorn/markdown) is optional and not part of the core install.

## Architecture

The project deliberately separates three concerns. Internalize this split before editing — most design decisions follow from it.

1. **`guanlan/` — the thin CLI wrapper (this package).** It carries *no business intelligence*. Its only jobs are: (a) `init` (deterministic template generation, zero LLM), and (b) in P2, orchestrating Agentao + the skill and enforcing deterministic gates on write operations. `cli.py` is argparse-only; `init.py` does the file generation.

2. **`skills/guanlan-wiki/` — the maintenance engine.** This is where the actual wiki-maintenance workflows live (`SKILL.md` = workflows, `references/conventions.md` = default page/frontmatter/naming conventions, and `scripts/*.py` = deterministic checks, to be added in P2/P3). The engine is shipped/installed *once* and is **not** copied into each knowledge base. It is intended to run under Agentao's skill discovery.

3. **User knowledge base (generated by `guanlan init`).** Holds only data + per-base config: `AGENTAO.md` (Agent behavior hard-constraints + pointers), `SCHEMA.md` (this base's domain/page-types/custom rules), `raw/` (read-only sources), `wiki/` (Agent-owned generated layer: `index.md`, `log.md`, `overview.md`, plus `sources/ entities/ concepts/ syntheses/`).

### Two run modes (do not mix them)

- **Development = repo root *is* a sample wiki.** Set Agentao's `working_directory` to this repo root; `skills/guanlan-wiki/` then hits Agentao's repo-root discovery path (`<wd>/skills/`), so the engine is found with no install. Sample wiki data (`raw/`, `wiki/`, `graph/`) and the dev-copied `AGENTAO.md`/`SCHEMA.md` are `.gitignore`d — they may contain machine-local paths and never get committed.
- **External real wiki = global install.** The skill is installed to `~/.agentao/skills/guanlan-wiki/` (cwd-independent), with the user's base as `working_directory`. There is intentionally no "discover repo skills/ from an external wiki" path.

### init template duality (`guanlan/init.py`)

`init` copies a template tree. `_templates_dir()` resolves two locations by priority: bundled `guanlan/_templates/` (installed wheel) → repo-root `examples/` (development). The wheel's `force-include` in `pyproject.toml` copies `examples/{AGENTAO.md,SCHEMA.md,wiki}` into `guanlan/_templates/` at build time — so **`examples/` is the single source of truth for init templates**; edit templates there, not in `_templates/`. `init` never overwrites existing files (idempotent) and substitutes a `__DATE__` token in `wiki/` seed files.

## Invariants that drive the design

These come up repeatedly in DESIGN and the skill; preserve them in any change:

- **Markdown is the only source of truth.** Any index / graph / cache is a derivative that must be idempotently rebuildable from markdown — it never becomes authoritative.
- **`raw/` is read-only and immutable.** In P2 this is enforced *deterministically by the wrapper* via a before/after snapshot (filename + size + mtime, SHA256 if needed) around the Agentao call — not by permission config, since a snapshot also catches shell `mv`/`rm`/`python` writes that bypass `write_file`.
- **Zero-LLM scripts vs. LLM-only workflows.** Deterministic work (frontmatter/wikilink/structure checks, graph building) is plain Python scripts with no LLM. LLM is used *only* for `ingest` and `query`, and always via the Agentao runtime — scripts must never carry their own LLM client or API keys.
- **`SCHEMA.md` / `AGENTAO.md` / `index.md` / `log.md` / `overview.md` are config, not content** — exclude them from index/graph/lint scans.
- **Data conventions** (frontmatter fields, kebab-case vs TitleCase naming, `[[wikilink]]` resolution, `index.md`/`log.md` formats, the `## ⚠️ 矛盾与存疑` contradiction-marking format) are specified in `skills/guanlan-wiki/references/conventions.md` and DESIGN §4.5. A base's `SCHEMA.md` may override defaults.

## Conventions

The codebase (code comments, docstrings, design docs, user-facing CLI output) is written in **Chinese**. Match that when editing existing files.
