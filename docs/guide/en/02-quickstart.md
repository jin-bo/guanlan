# Quickstart

This page builds a knowledge base in the fewest commands: **init → ingest → query → maintain**. For per-command detail see [CLI commands](03-cli-commands.md).

> Prerequisite: `pip install guanlan-wiki` (see [Installation](01-installation.md)). `ingest` / `query` need a configured model; everything else is zero-LLM and offline.

## 1. Initialize a knowledge base

```bash
guanlan init my-wiki     # initialize a new directory
guanlan init             # or initialize the current directory in place
```

`init` is **deterministic (zero-LLM)**, **never overwrites** existing files, and is safe to re-run.

Generated layout:

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

> **`-C` selects the KB root**: except for `init`, run every command as `guanlan -C my-wiki <command>` (or `cd my-wiki` first and drop `-C`).

## 2. Ingest a source

Feed a markdown source to the Agent; it reads sources under `raw/` and creates/updates pages under `wiki/`:

```bash
guanlan -C my-wiki ingest path/to/source.md
```

> Non-`.md` files (PDF / DOCX / PPTX / …) must first be turned into `raw/<slug>.md` with [`guanlan convert`](07-convert.md), then ingested.
>
> **`raw/` is read-only and immutable**: the Agent only reads sources, never modifies them; the write gate snapshots `raw/` before and after every write.

## 3. Ask questions

```bash
guanlan -C my-wiki query "your question"
```

Read-only by default (answers from the built wiki). To **persist** the synthesis back into the wiki (one gated write):

```bash
guanlan -C my-wiki query "your question" --backfill
```

## 4. Maintain (zero-LLM, offline)

```bash
guanlan -C my-wiki check     # frontmatter / broken links / sources
guanlan -C my-wiki health    # stub pages + index↔disk sync (--strict → exit 6)
guanlan -C my-wiki lint      # orphans / broken links / missing entities
guanlan -C my-wiki graph     # write graph/graph.json + graph.html (--json-only skips html)
```

`health` / `lint` are **advisory** (non-blocking); `check` is deterministic validation. Exit-code semantics are in [Maintenance](04-maintenance.md#exit-codes).

## 5. Search

```bash
guanlan -C my-wiki search "keyword"     # zero-LLM deterministic BM25 + CJK full-page recall
```

## Optional: use it in a browser

```bash
pip install 'guanlan-wiki[web]'
guanlan -C my-wiki web                 # local Web host, 127.0.0.1 only, opens a browser by default
guanlan -C my-wiki web --port 9000 --no-browser
```

In the browser: browse the wiki and follow `[[wikilink]]` navigation, run check·health·lint, view the graph, trigger ingest from `raw/`, and chat read-only with the agent. **Single-user, local only — never expose the port to a network.** See [Web host](05-web-host.md).

## Optional: expose to MCP clients

```bash
pip install 'guanlan-wiki[mcp]'
guanlan -C my-wiki mcp                 # read-only MCP server over stdio
```

See [MCP host](06-mcp-host.md).

## Next

- Per-command detail → [CLI commands](03-cli-commands.md)
- Keeping a KB fresh → [Maintenance](04-maintenance.md)
- Ingesting multi-format sources → [Convert](07-convert.md)
