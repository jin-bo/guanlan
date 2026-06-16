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

### 1.1 Updating `SCHEMA.md` with LLM help

`SCHEMA.md` is the human-facing convention file for this knowledge base: domain boundaries, page-type usage, custom rules, and evolving organizational assumptions. It is not a machine-parsed API schema. After the KB has grown for a while, use an LLM to analyze the current state and update `SCHEMA.md` when scope, tags, naming, section structure, or organizational assumptions have changed.

You can give this prompt to an LLM that can read and write the current directory:

```text
You are maintaining a Guanlan knowledge base. Analyze the real current state of the knowledge base, then update the root SCHEMA.md accordingly.

Goal:
Make SCHEMA.md accurately reflect the current domain boundary, page-type usage, naming/tag/section rules, and the organizational assumptions that have emerged. SCHEMA.md is free-form Markdown for humans and Agents. It is not a machine-parsed API schema; do not turn it into a program configuration file.

Follow these steps:

1. Read and understand these files/directories:
   - SCHEMA.md
   - wiki/index.md
   - wiki/overview.md
   - wiki/log.md
   - Samples from each wiki/ directory, prioritizing coverage of source/entity/concept/synthesis pages
   - If needed, sample raw/ source titles or frontmatter provenance

2. Summarize the current KB state:
   - What are the main domains/topics?
   - Which topics or source types have reached meaningful scale?
   - How are source/entity/concept/synthesis pages actually used?
   - What tags are common, and are there synonyms, duplicates, quote-style drift, or inconsistent granularity?
   - What real conventions exist for page titles, filenames, wikilinks, and section structure?
   - Which organizational assumptions are stable enough to record under "Evolving assumptions / biases"?
   - Which old rules no longer match the current state?

3. Update SCHEMA.md:
   - Preserve its role as this KB's convention file
   - Do not add pseudo-configuration fields that current tools cannot execute
   - Do not claim unsupported behavior, such as "SCHEMA.md is parsed by the program"
   - Do not invent topics, tags, or rules that are not present
   - Consolidate, clarify, and deduplicate existing rules
   - If you find only isolated bad pages, do not turn those outliers into rules; record the dominant, stable practices worth continuing

4. The updated content should cover these sections:
   - ## Domain / Topics
   - ## Enabled Page Types
   - ## Custom Rules for This KB
   - ## Evolving Assumptions / Biases

5. Writing requirements:
   - Write in the same language as the existing SCHEMA.md unless the user asks otherwise
   - Use concise, actionable rules
   - Keep the Markdown readable
   - Tables are fine, but do not put overly detailed or quickly stale facts into tables
   - Be specific about tag vocabularies, naming rules, and section rules
   - Make it clear that "Evolving Assumptions / Biases" are working assumptions; counterexamples should be recorded in the relevant page's contradictions/uncertainties section

6. After finishing, output:
   - A short analysis of the current KB state
   - The main changes made to SCHEMA.md
   - The full updated SCHEMA.md
   - Points that deserve human review

Boundaries:
- SCHEMA.md is not parsed by Guanlan Python code; it is a human/Agent convention file.
- Do not modify raw/.
- Do not batch-edit wiki pages.
- Unless the user explicitly asks otherwise, only update SCHEMA.md.
- If you find page-structure problems, list them as recommendations at the end; do not migrate pages as a side effect.
```

If you want the LLM to write the file directly, append:

```text
Please directly edit the root SCHEMA.md. After finishing, run:
uv run guanlan check
uv run guanlan health

If checks fail, only fix problems caused by SCHEMA.md; do not modify raw/ or batch-edit wiki pages.
```

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
