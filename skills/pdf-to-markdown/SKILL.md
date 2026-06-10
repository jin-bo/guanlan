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
| 1 | **MinerU** | `mineru` | Used first whenever on `PATH` | Highest-fidelity layout, tables, formulas, OCR |
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
pip install -U "mineru[core]"
```

MinerU downloads its models on first run. CLI: `mineru -p <input> -o <output_dir>`.

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

MinerU-specific:
  --method CHOICE         auto (default) | txt | ocr
  --lang LANG             OCR language hint, e.g. "ch", "en"
  --mineru-backend NAME   MinerU -b backend, e.g. "pipeline", "vlm-auto-engine"

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

Force MinerU with Chinese OCR:
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
subtree (backends nest differently — MinerU uses `<stem>/<method>/<stem>.md`, marker
uses `<stem>/<stem>.md`) and prints the resolved path:

```
<input_parent>/<stem>/.../<stem>.md
```

Example: `/home/user/docs/paper.pdf` → `/home/user/docs/paper/.../paper.md`

## Error Handling

The script only exits non-zero when **all** eligible tiers are exhausted; it then
lists why each tier was skipped. Common per-tier causes:

| Message | Tier | Cause | Fix |
|---|---|---|---|
| `'mineru' not found on PATH` | mineru | Not installed | `pip install -U "mineru[core]"` |
| `'marker_single' not found on PATH` | marker | Not installed | `pip install marker-pdf` |
| `pypdf not installed` | python | Not installed | `pip install pypdf` |
| `no extractable text (scanned PDF?...)` | python | Scanned PDF, no text layer | Use mineru/marker (OCR) |
| `pure-python fallback only supports PDF` | python | Non-PDF input | Use mineru/marker |
| `file not found` / `unsupported file type` | — | Bad path/extension | Check path / use a supported format |

## scripts/

- **`convert.py`** — Tiered conversion driver. Validates the input, loads `.env`,
  then attempts MinerU → marker-pdf → pure-python (or the single `--backend` chosen),
  falling back on missing/failed tiers. Locates the produced markdown by globbing the
  per-backend output subtree and prints its path. Self-contained (no third-party
  imports beyond the backend tools themselves).
