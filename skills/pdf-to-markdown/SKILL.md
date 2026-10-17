---
name: pdf-to-markdown
description: >
  Convert documents (PDF, DOCX, PPTX, XLSX, EPUB, HTML, images) to Markdown using a
  tiered backend strategy: MinerU first, then marker-pdf, then a pure-python pypdf
  fallback. Trigger on: "convert PDF to markdown", "PDF to markdown", "DOCX/Word to
  markdown", "PPTX to markdown", "XLSX/Excel to markdown", "convert document to
  markdown", "transform to text", "mineru", "marker-pdf", "extract markdown from
  PDF", or any mention of a .pdf/.docx/.pptx/.xlsx/.epub/.html/image file when
  markdown output is the desired result.
---

# Document to Markdown

## Overview

Converts documents to high-quality Markdown using a **tiered backend strategy** — the
best available tool wins, and the script degrades gracefully when a tool is missing or
fails:

| Tier | Backend | Command | When it runs | Strength |
|---|---|---|---|---|
| 1 | **MinerU** (4.x preferred, 3.x supported) | `mineru parse` / `mineru -p` | Used first whenever on `PATH` | Highest-fidelity layout, tables, formulas, OCR |
| 2 | **marker-pdf** | `marker_single` | Fallback if MinerU missing/fails | Strong layout + optional Gemini LLM cleanup |
| 3 | **pure-python** | `pypdf` | Last resort | Plain text-layer extraction (PDF only, no OCR) |

With the default `--backend auto`, the script tries tiers **1 → 2 → 3** in order,
moving to the next one when a backend is **unavailable** (not installed) *or*
**fails** (non-zero exit / no output). It prints which backend produced the result on
`stderr` (`[done] backend=...`) and the output `.md` path on `stdout`.

Force a single tier with `--backend mineru|marker|python`.

## Supported Input Formats

| Category | Extensions | MinerU | marker | python |
|---|---|:--:|:--:|:--:|
| PDF | `.pdf` | ✅ | ✅ | ✅ |
| Legacy Office / ODF / RTF | `.doc`, `.ppt`, `.xls`, `.rtf`, `.odt`, `.ods`, `.odp` | ✅ (4.x) | — | — |
| Other (4.x only) | `.ofd`, `.csv`, `.tsv`, `.mhtml`, `.mht` | ✅ (4.x) | — | — |
| Word | `.docx` | ✅ | ✅ | — |
| PowerPoint | `.pptx` | ✅ | ✅ | — |
| Excel | `.xlsx` | ✅ | ✅ | — |
| E-book | `.epub` | ✅ | ✅ | — |
| Web | `.html`, `.htm` | ✅ | ✅ | — |
| Images | `.png`, `.jpg`, `.jpeg`, `.webp`, `.tif`, `.tiff`, `.bmp`, `.gif` | ✅ | ✅ | — |

The pure-python tier only handles PDFs **with a text layer** (no OCR). A scanned PDF
with no text layer fails the python tier and requires MinerU or marker.

## Quick Start

```bash
python scripts/convert.py /path/to/document.pdf      # auto: mineru -> marker -> python
python scripts/convert.py /path/to/report.docx
python scripts/convert.py /path/to/slides.pptx
```

The output `.md` path is printed on `stdout`; progress/backend logs go to `stderr`.

## Setup

### Tier 1 — MinerU (preferred)

```bash
uv tool install -U mineru        # or: uv add mineru  (MinerU 4.x)
```

The script reads `mineru --version` and picks the matching CLI:

- **MinerU 4.x** — `mineru parse <file> -o <stem>/<stem>.md --json`. Parsing goes through
  a local background server; the script runs `mineru server start` automatically on
  `server_not_running` (stop it with `mineru server stop`). Things 4.x changed that the
  script handles for you:
  - **`--pages all` is always passed for PDFs.** Without it MinerU 4 parses only the
    first 10 pages. `--start/--end` (0-based) become 4.x's 1-based syntax (`3-r1`, where
    `r1` = last page).
  - **`--wait` defaults to 1800 s** (MinerU's own default is 60 s). On timeout the parse
    keeps running in the background; re-running the same command picks up the cached
    result.
  - **Tiers:** `flash` (text-only, runs with no models) < `basic` < `standard` <
    `advanced`. Quality tiers apply to PDFs/images only; Office/HTML/EPUB/CSV always use
    `flash`. With no `--tier`, MinerU picks the best local engine; if none is enabled
    the script retries with `--tier flash` and, in `auto` mode, falls through to marker
    when flash yields nothing (e.g. a scanned PDF).
  - **Enable a local quality engine** (needed for OCR / layout / tables / formulas):
    `mineru config set parse_server.local.mode managed` (tier:
    `mineru config set parse_server.local.managed_tier standard`).
- **MinerU ≤ 3.x** — legacy `mineru -p <input> -o <output_dir>` with `--method`,
  `--lang`, `--mineru-backend`. These flags are ignored (with a warning) on 4.x.

**Privacy:** parsing is local unless you pass `--remote`, which uploads the file to
mineru.net — don't use it for confidential documents. MinerU 4 also has usage
telemetry; check with `mineru telemetry status`, turn off with
`mineru telemetry disable`.

### Tier 2 — marker-pdf (fallback)

```bash
pip install marker-pdf
```

(Despite the name, `marker-pdf` also handles DOCX, PPTX, XLSX, EPUB, HTML, and images.)

Optional LLM enhancement (marker tier only) — set a Gemini key and the script adds
`--use_llm` automatically; **without a key it runs marker without LLM enhancement** so
the fallback still works:

```
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-flash-latest   # optional; default model
```

`.env` is searched from the current working directory upward to the filesystem root;
`GEMINI_API_KEY` / `GEMINI_MODEL` may also be exported as environment variables.

### Tier 3 — pure-python (last resort)

```bash
pip install pypdf
```

## Usage

```
python scripts/convert.py <input_path> [options]

Arguments:
  input_path              Path to the input document

Backend selection:
  --backend CHOICE        auto (default) | mineru | marker | python

Page range (backend-agnostic, 0-based inclusive):
  --start N               First page (0-based)
  --end N                 Last page (0-based, inclusive)

MinerU 4.x:
  --tier CHOICE           flash | basic | standard | advanced (default: best local engine)
  --wait SECONDS          Max seconds to wait for a parse (default: 1800)
  --force                 Ignore MinerU's parse cache and re-parse
  --remote                Use the mineru.net remote service (UPLOADS the file)

MinerU 3.x (legacy CLI):
  --method CHOICE         auto (default) | txt | ocr
  --lang LANG             OCR language hint, e.g. "ch", "en"
  --mineru-backend NAME   MinerU -b backend, e.g. "pipeline", "hybrid-engine"

marker-specific:
  --page-range RANGE      marker page syntax, e.g. "0,5-10,20"
  --disable-ocr           Use text layer only, skip OCR
  --disable-images        Do not extract images
  --model MODEL           Gemini model (default: gemini-flash-latest)
```

### Examples

Convert with automatic tiering (recommended):
```bash
python scripts/convert.py ~/Documents/report.pdf
```

Force MinerU 4 at the highest local tier:
```bash
python scripts/convert.py ~/Documents/report.pdf --backend mineru --tier advanced
```

Legacy MinerU 3.x with Chinese OCR:
```bash
python scripts/convert.py ~/Documents/report.pdf --backend mineru --lang ch
```

Force the marker fallback explicitly:
```bash
python scripts/convert.py ~/Documents/report.pdf --backend marker
```

Convert only pages 1–5:
```bash
python scripts/convert.py ~/Documents/report.pdf --start 0 --end 4
```

Quick, dependency-light text dump from a born-digital PDF:
```bash
python scripts/convert.py ~/Documents/report.pdf --backend python
```

## Output Location

Every backend writes into a subdirectory named after the input stem, inside the
input file's parent directory. The script locates the produced `.md` by globbing that
subtree (backends nest differently — MinerU 4 and marker write `<stem>/<stem>.md`
(MinerU 4 writes a single `.md`; images stay in its cache),
MinerU 3 uses `<stem>/<method>/<stem>.md`) and prints the resolved path:

```
<input_parent>/<stem>/.../<stem>.md
```

Example: `/home/user/docs/paper.pdf` → `/home/user/docs/paper/.../paper.md`

## Error Handling

The script only exits non-zero when **all** eligible tiers are exhausted; it then
lists why each tier was skipped. Common per-tier causes:

| Message | Tier | Cause | Fix |
|---|---|---|---|
| `'mineru' not found on PATH` | mineru | Not installed | `uv tool install -U mineru` |
| `retrying with --tier flash` | mineru 4 | No local quality engine | `mineru config set parse_server.local.mode managed` |
| `mineru still parsing after Ns` | mineru 4 | `--wait` exceeded | Re-run (cached) or raise `--wait` |
| `marker does not support '.doc'` | marker | 4.x-only format | Use MinerU 4 |
| `'marker_single' not found on PATH` | marker | Not installed | `pip install marker-pdf` |
| `pypdf not installed` | python | Not installed | `pip install pypdf` |
| `no extractable text (scanned PDF?...)` | python | Scanned PDF, no text layer | Use mineru/marker (OCR) |
| `pure-python fallback only supports PDF` | python | Non-PDF input | Use mineru/marker |
| `file not found` / `unsupported file type` | — | Bad path/extension | Check path / use a supported format |

## scripts/

- **`convert.py`** — Tiered conversion driver. Validates the input, loads `.env`,
  then attempts MinerU (4.x `mineru parse`, or the 3.x CLI) → marker-pdf → pure-python (or the single `--backend` chosen),
  falling back on missing/failed tiers. Locates the produced markdown by globbing the
  per-backend output subtree and prints its path. Self-contained (no third-party
  imports beyond the backend tools themselves).
