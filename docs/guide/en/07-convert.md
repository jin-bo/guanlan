# Convert

`guanlan convert` fills a CLI gap: `ingest` accepts `.md` only, but many sources are PDF/DOCX/PPTX/XLSX/HTML/images. `convert` turns them — via the **`pdf-to-markdown` skill** (MinerU → marker → pypdf tiered fallback) — into a `raw/<slug>.md` source, then the unchanged `.md` ingest takes over.

> It is **not a new main flow** and **not a replacement** for the Web upload → parse → review → promote path (which is stronger). It fills the CLI user's official "PDF/DOCX → raw → ingest" entry.

## Usage

```bash
guanlan -C my-wiki convert report.pdf                 # convert and land raw/report.md
guanlan -C my-wiki convert report.pdf --dry-run        # print to stdout only, zero raw/ write (review)
guanlan -C my-wiki convert report.pdf --ingest         # chain ingest after a successful convert
guanlan -C my-wiki convert report.pdf --name notes     # override the slug → raw/notes.md
guanlan -C my-wiki convert a.pdf --backend marker      # pick a backend
```

| Arg | Default | Meaning |
|---|---|---|
| `src` | — | The file to convert (PDF/DOCX/PPTX/XLSX/HTML/image…) |
| `--name` | source stem | Override the target slug |
| `--origin` | the pre-conversion `src` path | Explicit provenance (written as `origin`) |
| `--overwrite` | off | Explicitly overwrite an existing same-name `raw/` file (off by default) |
| `--dry-run` | off | Print the result to stdout only; zero `raw/` write |
| `--ingest` | off | Chain `ingest raw/<slug>.md` after a successful convert |
| `--backend` | `auto` | Conversion backend: `auto`/`mineru`/`marker`/`python` (passed through to the skill's `convert.py`) |

## Behavior contract

- **Two-step by default, no auto-ingest**: `convert` only does "convert + land source" (deterministic host write to `raw/`, no Agentao, no gate, no snapshot, no `log.md`). Page building is a separate `guanlan ingest` (or chain with `--ingest`).
- **Script is zero-LLM**: GuānLán carries no LLM client/key, only shells out to the converter. Whether marker uses Gemini is the **user environment's** choice (no `--model`, no env scrub, no model pass-through).
- **Images land with the source** (P5.2.1): images extracted by the converter land at `raw/images/<slug>/<slug>-N.ext`, and markdown image references are rewritten to the new relative paths so `raw/<slug>.md` is **self-consistent**. Three capacity gates: 20 MiB per image / 200 MiB cumulative / 500 images — exceeding any errors out (no silent image dropping). `--dry-run` writes zero images too.
- **No new runtime deps, no new extra**: reuses the skill's tiered backends; any one available is enough; if all are unavailable it degrades gracefully with an error (exit code `1`).

> A real conversion needs at least one backend installed (MinerU / marker / pypdf). MCP **does not do convert** (writing `raw/` conflicts with the read-only posture).

See: repo [`docs/P5.2-多格式摄入.md`](../../P5.2-多格式摄入.md), [`docs/P5.2.1-图片落盘.md`](../../P5.2.1-图片落盘.md), [CLI commands → ingest](03-cli-commands.md#guanlan-ingest-target).
