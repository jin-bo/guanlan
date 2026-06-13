# CLI commands

Per-command reference for the core commands. Every command (except `init`) accepts the global `-C/--dir` to select the KB root, which may go before or after the subcommand:

```bash
guanlan -C my-wiki check     # git-style
guanlan check -C my-wiki     # equivalent
cd my-wiki && guanlan check  # drop -C (defaults to the current directory)
```

`init` / `check` / `search` are zero-LLM and offline; `ingest` / `query` need a configured model (via Agentao). Exit-code semantics are in [Maintenance](04-maintenance.md#exit-codes).

---

## `guanlan init [path]`

Generate a minimal KB template in a directory. **Deterministic, zero-LLM**; never overwrites existing files; safe to re-run.

```bash
guanlan init my-wiki     # initialize a new directory
guanlan init             # initialize the current directory in place
guanlan init -C my-wiki  # equivalent to the positional arg
```

Target precedence: positional `path` > global `-C/--dir` > current directory. Generates `AGENTAO.md` / `SCHEMA.md` / `raw/` / `wiki/` (layout in [Quickstart](02-quickstart.md#1-initialize-a-knowledge-base)).

---

## `guanlan ingest <target>`

Ingest one **`.md`** source: the Agent reads the source under `raw/` and creates/updates `wiki/` pages. **Needs a model.**

```bash
guanlan -C my-wiki ingest raw/source.md
guanlan -C my-wiki ingest raw/source.md --model <model-id>   # override the default model
```

| Arg | Meaning |
|---|---|
| `target` | A `.md` file under `raw/`, e.g. `raw/x.md` (**`.md` only**) |
| `--model` | Override the default Agentao model |

Notes:

- **`.md` only.** Convert non-`.md` (PDF/DOCX/…) into `raw/<slug>.md` with [`guanlan convert`](07-convert.md) first.
- **`raw/` is read-only and immutable**: the write gate snapshots `raw/` (name + size + mtime, SHA256 if needed) around the Agentao call. If the Agent mutates/deletes `raw/`, exit code `4` (`EXIT_RAW_MUTATED`).
- This is a governed write: Agentao subprocess + single-writer gate.

---

## `guanlan query <question>`

Ask the knowledge base. **Read-only by default** (answers from the built wiki, no disk write). **Needs a model.**

```bash
guanlan -C my-wiki query "What is X?"
guanlan -C my-wiki query "What is X?" --backfill        # persist a good answer back into the wiki (gated)
guanlan -C my-wiki query "What is X?" --model <model-id>
```

| Arg | Meaning |
|---|---|
| `question` | The question text |
| `--backfill` | Persist the synthesis into `wiki/syntheses/` through the **full write gate** (same subprocess + `raw/` snapshot path as ingest) |
| `--model` | Override the default Agentao model |

`--backfill` upgrades a one-shot Q&A into a governed write; without it the query is purely read-only.

---

## `guanlan check`

Deterministic baseline validation: **frontmatter + broken links + sources**. **Zero-LLM.**

```bash
guanlan -C my-wiki check
guanlan -C my-wiki check --json     # JSON contract (for scripts/CI)
```

Failure exits with code `3` (`EXIT_CHECK_FAILED`). This is the **write gate's gatekeeper** — alias collisions/duplicates are also blocked here.

---

## `guanlan search <query>`

Deterministic full-page recall: **BM25 + CJK 2-gram**, title/alias field boost, top-N pages by descending score. **Zero-LLM, no persisted derivative.**

```bash
guanlan -C my-wiki search "keyword"
guanlan -C my-wiki search "keyword" --limit 20
guanlan -C my-wiki search "keyword" --json
```

| Arg | Meaning |
|---|---|
| `query` | The search terms |
| `--limit` | Number of results (default 10, must be ≥ 1) |
| `--json` | JSON contract |

It is the recall front-end for `query`/skill, and is reused by the Web `/api/search` endpoint and the embedded chat's `guanlan_search` tool (same kernel).

---

## Other commands

- **Maintenance** `health` / `lint` / `graph` / `reindex` / `heal` → [Maintenance](04-maintenance.md)
- **Multi-format** `convert` → [Convert](07-convert.md)
- **Hosts** `web` / `mcp` → [Web host](05-web-host.md) / [MCP host](06-mcp-host.md)
- **`install-skill`**: install the bundled `guanlan-wiki` skill into `~/.agentao/skills/` (for external real bases; not needed in dev mode, see [Installation](01-installation.md#develop-from-source)). `--force` reinstalls over an existing copy.
