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

- **`.md` only.** Convert non-`.md` (PDF/DOCX/…) into `raw/<slug>.md` with [`guanlan convert`](07-convert.md) first; for a whole book or long report, [`guanlan import`](#guanlan-import-src) does it in one step.
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

## `guanlan zip`

Pack the knowledge base into a zip you can hand to someone else. **Zero-LLM, read-only on the KB**; private data and hidden files are excluded by default.

```bash
guanlan -C my-wiki zip                    # writes ./my-wiki-YYYYMMDD.zip (run inside the KB, it goes to the KB's parent directory)
guanlan -C my-wiki zip -o share/kb.zip    # choose the output path
guanlan -C my-wiki zip --dry-run          # only show what would be included and excluded
```

| Arg | Meaning |
|---|---|
| `-o` / `--output` | Output path (must not be inside the KB) |
| `--no-raw` | Leave out `raw/` and share only the wiki (e.g. when the sources have copyright concerns) |
| `--with-graph` | Also include `graph/` (left out by default; the recipient can rebuild it with `guanlan graph`) |
| `--dry-run` | Preview only, no zip is written |
| `--force` | Overwrite the output file if it exists |
| `--json` | JSON contract (with the full file list) |

Only `AGENTAO.md`, `SCHEMA.md`, `wiki/` and `raw/` (optionally `graph/`) go in; `.env`, `agentao.log*`, `.agentao/` (sessions etc.),
`workspace/`, `.trash/` and any other file at the KB root are always left out. Hidden files, system clutter such as `Thumbs.db`, and symlinks
inside the allowlisted directories are skipped too. The zip's top level is a directory named after the KB, so the recipient just unzips it into a parent directory.
**It filters by path only and does not inspect content** — before sharing, check yourself that the config and pages hold nothing that should not leave your hands.
Design detail: [`P3.12-知识库打包分享.md`](../../P3.12-知识库打包分享.md) (Chinese).

---

## `guanlan import [src]`

One-click import: take one original (PDF/DOCX/…, or already `.md`) all the way from parsing to ingest. Meant for whole books and long reports — after conversion they carry running headers, page numbers and sentences cut at page breaks, and they are far longer than one `ingest` should handle. **Needs a model** (each part is ingested; a book with a table of contents also gets one TOC review).

```bash
guanlan -C my-wiki import book.pdf --dry-run   # parse, normalize, split and print the summary; no raw/ writes, no ingest
guanlan -C my-wiki import book.pdf             # summary → interactive confirm → land in raw/ → ingest part by part
guanlan -C my-wiki import ./sources/           # a whole directory as one task: prepare each file → confirm once → ingest part by part
guanlan -C my-wiki import --status             # progress of the unfinished task (zero-LLM)
guanlan -C my-wiki import --resume             # continue from where it stopped
guanlan -C my-wiki import --cancel             # abandon a task that is awaiting confirmation or paused
```

Steps:

1. **Parse**: the same backends as `convert` (`.docx` tries pandoc first; everything else falls back MinerU → marker → pypdf). The raw result stays untouched in `workspace/parsed/<name>.origin.md`.
2. **Normalize** (zero-LLM): remove running headers, footers, page numbers and clear-pattern watermarks, and rejoin sentences cut at page breaks. If the book has a table of contents, heading levels follow it.
3. **Split** (zero-LLM): up to about 40,000 characters stays whole; longer text is split at chapter headings into parts of at most about 40,000 characters, and small neighboring chapters are merged.
4. **Confirm**: print the number of parts, each part's length, the estimated time and the `raw/` conflict pre-check.
5. **Land in `raw/` and ingest**: an unsplit document lands as `raw/<name>.md`; a split one as `raw/<name>/<name>-NN-<chapter>.md`, without a whole-book copy. Then each part is ingested in turn.

**If any step before confirmation fails, `raw/` is not touched.**

| Arg | Meaning |
|---|---|
| `src` | The file or directory to import; any path (it need not be inside the KB) |
| `--dry-run` | Parse, normalize, split and print the summary only; no task, no `raw/` writes, no ingest. A TOC is still reviewed once by the model |
| `--yes` | Skip the interactive confirmation (required on a non-interactive terminal) |
| `--overwrite` | Allow overwriting **the same set of** `raw/` files that an earlier one-click import wrote |
| `--resume` / `--status` / `--cancel` | Continue / show / abandon this KB's unfinished task (including one started from the Web) |
| `--json` | Structured confirmation summary / receipt |
| `--model` / `--thinking` | Passed through to each ingest |
| `--backend` | Conversion backend (as in `convert`; default `auto`) |
| `--no-toc-review` | Parse the TOC by rules only, without the model review (this makes `--dry-run` zero-LLM) |

Notes:

- **Pause and resume**: during preparation (parse · clean · split), Ctrl+C stops at once and the task is dropped; just import again (MinerU caches parses, so re-parsing the same file is fast). During ingest, the first Ctrl+C pauses after the current part finishes; the second aborts at once (that part may be half-written — run `guanlan check` afterwards). A failed ingest also pauses the task; fix the cause and `--resume` retries that part. Progress and logs live in `workspace/tasks/`, so a task survives a restart.
- **One import task per KB at a time**; finish or abandon the old one before starting a new one. The CLI and the Web "Import" panel share task manifests and can resume each other's tasks.
- **KB write lock**: during preparation and during landing/ingest the task holds `.guanlan.lock` at the KB root (an OS file lock, released automatically when the process exits). Other writes (`ingest`, `heal`, Web write jobs, …) fail at once and show who is writing. No lock is held while a task awaits confirmation or is paused.
- **Overwrite rules**: `raw/<name>.md` and `raw/<name>/` are checked together. `--overwrite` works only when an earlier one-click import wrote exactly the same set of files. A different set, or files that no import task wrote, is a conflict: the only option is to abandon and clean up `raw/` yourself.
- **A whole directory**: when `src` is a directory, the PDF / DOCX / PPTX / XLSX / HTML / `.md` / image files at its top level become **one** task. They are parsed, cleaned and split one by one in file-name order (add a number prefix if you want another order). When all are ready you confirm **once**, then the parts are ingested in turn. Subdirectories are not entered; hidden files, subdirectories, unsupported formats and empty files are skipped and listed in the summary. At most 50 files per batch. If two files would write to the same place (e.g. `report.pdf` and `report.md`), the import is refused; rename one and try again. **If any file fails to prepare, nothing from the batch is imported**; a conflict means the whole batch can only be abandoned — conflicting files are never skipped. The write lock is held for the whole preparation, which takes a while for many files. A batch task can only be confirmed in the terminal; the Web panel can show, resume, pause and abandon it. Design: [`P4.32-目录批量入库.md`](../../P4.32-目录批量入库.md) (Chinese).
- When everything is done, the change in the wiki's broken-link count is reported; if it is high, `guanlan heal --dry-run` shows the pages worth filling. **Heal is never run automatically.**
- Exit codes: `0` when all parts succeed; `1` for usage errors, conflicts or a busy KB; the failed part's exit code when a failure pauses the task; a user pause via Ctrl+C counts as success (`0`).

Design detail: [`P4.30-一键入库.md`](../../P4.30-一键入库.md) (Chinese).

---

## Other commands

- **Maintenance** `health` / `lint` / `graph` / `reindex` / `heal` → [Maintenance](04-maintenance.md)
- **Multi-format** `convert` → [Convert](07-convert.md) (for whole-book import see [`guanlan import`](#guanlan-import-src) above)
- **Hosts** `web` / `mcp` / `im` (plus `im-login` / `im-identify`) → [Web host](05-web-host.md) / [MCP host](06-mcp-host.md) / [IM host](08-im-host.md)
- **`install-skill`**: install the three bundled skills into `~/.agentao/skills/` (for external real bases; not needed in dev mode, see [Installation](01-installation.md#develop-from-source)). `--force` reinstalls over an existing copy.
  - `guanlan-wiki` — the maintenance engine (`ingest` / `query` / Web chat all activate it).
  - `pdf-to-markdown` — parses uploaded PDF/DOCX/… into staged markdown (used by `convert` and Web uploads).
  - `flint-chart-author` — turns structured data into ` ```flint ` chart blocks the Web host can render (see [Web host · Rich rendering](05-web-host.md)).
  The latter two are activated by the Agent **on demand**; they are orthogonal to wiki conventions.
