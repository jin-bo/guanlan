<p align="center">
  <img src="docs/guanlan.png" alt="GuānLán 观澜" width="160">
</p>

<h1 align="center">GuānLán (观澜)</h1>

[中文](README.md) | **English**

[![PyPI](https://img.shields.io/pypi/v/guanlan-wiki)](https://pypi.org/project/guanlan-wiki/) [![Python](https://img.shields.io/pypi/pyversions/guanlan-wiki)](https://pypi.org/project/guanlan-wiki/) [![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE) ![Status](https://img.shields.io/badge/status-CLI%20loop%20%2B%20Web%2FMCP%20hosts-brightgreen)

> "There is an art to observing water — one must observe its ripples." (*Mencius*) — discerning patterns and trends in an ocean of information.

GuānLán lets an **Agent incrementally build and continuously maintain a structured, cross-linked knowledge wiki**, instead of doing fresh retrieval over raw documents on every question (classic RAG). You just feed sources, ask questions, and give direction; summarizing, cross-linking, and archiving are left to the Agent. Knowledge is "compiled" once and kept fresh, compounding with every new source and every question.

This is an implementation of the [Karpathy LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## Core ideas

- **Markdown is the only source of truth** — the whole KB is a set of local markdown files; any index/graph/cache is an idempotently rebuildable derivative.
- **The Agent owns the wiki layer; humans don't write it directly** — humans feed, ask, and steer; generation and maintenance are the Agent's job.
- **`raw/` is read-only and immutable** — the Agent only reads sources, never modifies them, keeping facts traceable.
- **Deterministic first** — structure/broken-link/frontmatter checks are plain scripts (zero-LLM, offline); the LLM-backed `ingest`/`query` are governed via the Agentao runtime.

## What it can do

| Command | Purpose | Needs a model? |
|---|---|---|
| `guanlan init` | Initialize a knowledge base (deterministic template) | no |
| `guanlan ingest` | Feed a source; the Agent creates/updates wiki pages | yes |
| `guanlan query` | Ask the KB (`--backfill` persists the answer back into the wiki) | yes |
| `guanlan search` | Full-page full-text search (BM25 + CJK tokenization) | no |
| `guanlan check` / `health` / `lint` | Validate / health-check / structural lint | no |
| `guanlan graph` | Build an interactive `[[wikilink]]` knowledge graph | no |
| `guanlan web` | Browse, ask, and maintain in a browser (optional add-on) | partly |
| `guanlan mcp` | Expose the wiki read-only to MCP clients (optional add-on) | partly |

> There are also `reindex` (index backfill), `heal` (missing-entity materialization), `audit` (semantic audit: re-review drifted sources whose `raw/` changed but the wiki wasn't re-synthesized), `remove` (source retraction: move a mis-ingested/retracted source into `.trash/`), `convert` (PDF/DOCX/… → markdown), etc. Per-command detail is in the **[User Guide](docs/guide/)**.

## Installation

```bash
pip install guanlan-wiki
```

> The PyPI name is `guanlan-wiki` (the bare `guanlan` is taken by an unrelated project); the **CLI and import name are still `guanlan`** after install. Requires **Python 3.10+**.
>
> `init` / `check` / `health` / `lint` / `graph` / `search` are zero-LLM and run offline; `ingest` / `query` / Web chat need a configured model (via the Agentao runtime).

Optional hosts (add-on layers, install on demand):

```bash
pip install 'guanlan-wiki[web]'    # browser host: guanlan web
pip install 'guanlan-wiki[mcp]'    # read-only MCP server: guanlan mcp
```

## Quickstart

```bash
# 1. Initialize a knowledge base (deterministic, zero-LLM, re-runnable without overwriting)
guanlan init my-wiki

# 2. Feed sources / ask questions (needs a model)
guanlan -C my-wiki ingest path/to/source.md
guanlan -C my-wiki query "your question"

# 3. Maintain (zero-LLM, offline)
guanlan -C my-wiki check     # frontmatter / broken links / sources
guanlan -C my-wiki health    # stub pages + index↔disk sync
guanlan -C my-wiki lint      # orphans / broken links / missing entities
guanlan -C my-wiki graph     # write graph/graph.json + graph.html
```

Use it in a browser (optional):

```bash
pip install 'guanlan-wiki[web]'
guanlan -C my-wiki web       # local Web host, 127.0.0.1 only, opens a browser by default
```

In the browser: browse the wiki and follow `[[wikilink]]` navigation, run check·health·lint reports, view the graph, trigger ingest and other write jobs from `raw/` (incl. heal, audit drift-review, backfill), and chat read-only with the agent. **Single-user, local only — never expose the port to a network.**

For a full walkthrough see the **[User Guide → Quickstart](docs/guide/en/02-quickstart.md)**.

## Generated layout

```
my-wiki/
├── AGENTAO.md       # Agent behavior constraints + pointers
├── SCHEMA.md        # this base's schema: domain / page types / custom rules
├── raw/             # raw sources (read-only, source of truth)
└── wiki/            # Agent-owned generated layer
    ├── index.md     # full page catalog
    ├── log.md       # append-only timeline
    └── overview.md  # living cross-source overview
```

## Documentation

- 📖 **[User Guide `docs/guide/`](docs/guide/)** — installation, quickstart, every command, Web/MCP hosts (bilingual)
- 🏗️ [Design doc `docs/DESIGN.md`](docs/DESIGN.md) — full design (developer-facing, authoritative spec; in Chinese)
- 📋 [CHANGELOG.md](CHANGELOG.md) — versions and milestone progress

## Development

```bash
uv run guanlan init /tmp/demo   # run the CLI
uv run pytest                   # run tests
```

The maintenance engine is `skills/guanlan-wiki/` (`SKILL.md` + `references/conventions.md` + scripts); in dev mode it is found via Agentao's repo-root skill discovery (`<working-dir>/skills/`) with no install. See [`CLAUDE.md`](CLAUDE.md).

## License

[Apache License 2.0](LICENSE)
