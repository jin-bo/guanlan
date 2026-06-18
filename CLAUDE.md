# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

观澜 (GuānLán) is an implementation of the [Karpathy LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): an Agent incrementally builds and maintains a structured, cross-linked markdown knowledge wiki instead of doing fresh RAG retrieval on every query. The full design (in Chinese) is the authoritative spec — read [`docs/DESIGN.md`](docs/DESIGN.md) before any non-trivial change.

**Current status:** released through v0.1.14. Implemented = P2 minimal closed loop + P3 health/graph family + P4 optional host layer (Web + MCP) + P5 retrieval/multi-format ingest, including every half-phase P2.1–P5.4, plus the P4.13/P4.14 pure-frontend rich-render half-phases (Web in-browser mermaid diagrams; KaTeX+mhchem math/chemistry + highlight.js code highlighting — both vendored, server-zero-change, fail-keep-source) and P4.15 (Web tool-confirm / `ask_user` human-in-the-loop: workspace-write ASK decisions are confirmed in the browser instead of silently auto-approved, with hard timeout + cancel + disconnect-releases-lock; `--confirm {ask,auto}`); no roadmap spec remains unimplemented.

CLAUDE.md does **not** restate phase history or per-decision detail — authoritative sources:
- Per-version change detail → [`CHANGELOG.md`](CHANGELOG.md)
- Milestone table → [`docs/DESIGN.md`](docs/DESIGN.md) §7
- Single-phase design/decisions → `docs/P*.md` (one file per phase)

When adding features, match the phase boundaries in DESIGN §4.4 / §7 and preserve the Invariants below.

## Commands

```bash
uv run guanlan init /tmp/demo            # scaffold a knowledge base (deterministic, zero-LLM)
uv run guanlan -C /tmp/demo check        # deterministic validation (frontmatter / broken links / sources)
uv run guanlan -C /tmp/demo health       # P3: stub pages + index↔disk sync (advisory; --strict → exit 6)
uv run guanlan -C /tmp/demo lint         # P3: orphans / broken links / missing entities (advisory)
uv run guanlan -C /tmp/demo graph        # P3: write graph/graph.json + graph.html (--json-only skips html)
uv run guanlan -C /tmp/demo reindex      # P3.4: register disk pages missing from index.md (zero-LLM; --dry-run / --prune)
uv run guanlan -C /tmp/demo convert 报告.pdf  # P5.2: multi-format → raw/<slug>.md via pdf-to-markdown skill (zero-LLM host write; --dry-run / --overwrite / --ingest / --backend)
uv run guanlan -C /tmp/demo web --no-browser   # P4: optional local Web host (needs guanlan-wiki[web]; 127.0.0.1 only)
uv run guanlan -C /tmp/demo mcp          # P4.10: optional read-only MCP server over stdio (needs guanlan-wiki[mcp])
uv run pytest                            # run all tests
uv run pytest tests/test_web.py          # P4 Web host tests (skipped if guanlan-wiki[web] absent)
uv run pytest tests/test_mcp.py          # P4.10 MCP host tests (skipped if guanlan-wiki[mcp] absent)
uv run pytest tests/test_convert.py      # P5.2 convert tests (mock skill backend; zero-LLM)
uv run pytest tests/test_init.py::test_init_is_idempotent_and_non_destructive  # single test
```

(`ingest` / `query` and the Web host's chat drive Agentao + the skill and need a configured model; `init` / `check` / `health` / `lint` / `graph` / `reindex` are the zero-LLM ones runnable offline (`convert` is also zero-LLM from guanlan's side, but shells out to the `pdf-to-markdown` skill's external backends — MinerU/marker/pypdf — so a real conversion needs at least one installed). `guanlan web` needs the optional `web` extra: `uv pip install 'fastapi>=0.110' 'uvicorn>=0.29' 'markdown>=3'`, or `pip install 'guanlan-wiki[web]'`. `guanlan mcp` needs the optional `mcp` extra: `pip install 'guanlan-wiki[mcp]'` — a read-only MCP **server** over stdio, distinct from DESIGN's reverse-direction "Tool 注入" where Agentao is the MCP *client*.)

Python 3.10+ (`pyproject.toml`: `requires-python = ">=3.10"`), dependencies managed by `uv` (see `uv.lock`). The package depends on `agentao` (the governed Agent runtime executing LLM-driven `ingest`/`query` via subprocess, and — in P4 — embedded read-only for Web chat). The `web` extra (fastapi/uvicorn/markdown) is optional and not part of the core install.

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
