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
- **Records which backend parsed it** (decision P5.2-13): landing a source adds a `parsed_by` frontmatter key holding the backend that **actually ran** (`mineru` / `marker` / `python`; MinerU 4 adds the tier actually used, e.g. `mineru/standard` — see "Checking the tier actually used" below). `--backend auto` is a tiered fallback whose three tiers differ by an order of magnitude in quality, and since `raw/` is immutable and the wiki layer grows out of it, without this there is no field that can answer "which pages in this base were built on degraded text". It sits alongside `origin` (which file it came from) as provenance: one records where from, the other how. Only the final backend is recorded (not the fallback chain), and if no backend marker can be read the key is simply omitted. The Web "parse → promote" path records it too, and it survives promotion.
- **Images land with the source** (P5.2.1): images extracted by the converter land at `raw/images/<slug>/<slug>-N.ext`, and markdown image references are rewritten to the new relative paths so `raw/<slug>.md` is **self-consistent**. Three capacity gates: 20 MiB per image / 200 MiB cumulative / 500 images — exceeding any errors out (no silent image dropping). `--dry-run` writes zero images too.
- **No new runtime deps, no new extra**: reuses the skill's tiered backends; any one available is enough; if all are unavailable it degrades gracefully with an error (exit code `1`).

> A real conversion needs at least one backend installed (MinerU / marker / pypdf). MCP **does not do convert** (writing `raw/` conflicts with the read-only posture).

## MinerU parse tiers

`convert`, `import` and the Web staging parse all run the same skill script, so this section applies to all three. The tier and hardware data come from the MinerU 4.0.12 package description (`mineru-4.0.12.dist-info/METADATA`) and the official [Tiers and runtimes](https://opendatalab.github.io/MinerU/usage/tiers/) page; after a MinerU upgrade, the official docs win.

### How GuānLán relates to MinerU

- Installing GuānLán does **not** install MinerU. MinerU is an external CLI you install yourself (`uv tool install -U mineru`); with no `mineru` on `PATH`, `--backend auto` goes straight to marker, then pypdf.
- MinerU 4's own default install ships ONNX + llama.cpp, but **its local parse engine is off by default** (`parse_server.local.mode = disabled`). Then only `flash` runs locally: PDFs give their text layer only, and scanned pages give no text.
- `basic`/`standard`/`advanced` need the models downloaded and the managed engine turned on (below). GuānLán never downloads models or changes MinerU config for you.

### The four tiers

| Tier | Quality and speed | Use for |
|---|---|---|
| `flash` | Lowest quality, fastest; no models | Preview and indexing. Office/HTML/EPUB/CSV and other non-PDF formats always use it |
| `basic` | Basic quality, moderate speed | Local parsing on limited resources |
| `standard` | Standard high quality; about as fast as `basic` on suitable hardware | Default for normal reading and complex documents |
| `advanced` | Same as `standard` on ordinary documents, better on difficult ones; slowest | Difficult documents when a longer wait is acceptable |

With no `--tier`, MinerU picks `standard`, then `basic`; it never picks `advanced` on its own.

### Hardware and models

| Tier | Model engines | Model download | Min RAM | Accelerator |
|---|---|---|---|---|
| `basic` | ONNX | ~0.8 GB | 2 GB | None (CPU works) |
| `basic` | PyTorch | ~0.8 GB | 8 GB | GPU/MPS recommended |
| `standard`/`advanced` | ONNX + llama.cpp | ~2 GB | 8 GB | CPU works, Vulkan recommended |
| `standard`/`advanced` | PyTorch + llama.cpp | ~2 GB | 16 GB | GPU/MPS required, 8 GB+ VRAM |
| `standard`/`advanced` | PyTorch + vLLM/lmdeploy/mlx | ~3 GB | 16 GB | GPU/MPS required, 8 GB+ VRAM |

MinerU's recommended managed tier by hardware: macOS on Apple Silicon → `standard` (the default install already has PyTorch); macOS on Intel → `basic`; NVIDIA with 8 GB+ VRAM → `standard` with `mineru[full]`; NVIDIA with 4 GB+ VRAM → `basic` with `mineru[torch]`; Linux/Windows with a modern iGPU → `standard`; no accelerator → `basic`. There are only two managed tiers, `basic` and `standard`; a `standard` managed engine serves the `basic`, `standard` and `advanced` request tiers.

### Turning on the managed engine

```bash
mineru-kit models download --tier standard      # download the models first (basic or standard)
mineru-kit models verify --tier standard
mineru config set parse_server.local.managed_tier standard   # set the tier first
mineru config set parse_server.local.mode managed            # then turn on managed mode
mineru server status --json                                  # parse only once the engine is healthy
```

If you already run your own parse service, use `parse_server.local.mode self_hosted` with `parse_server.local.self_hosted_url`. Remote parsing (mineru.net) needs an explicit `--remote` and **uploads the file**; GuānLán's `convert`/`import`/Web parse never pass `--remote`.

### Startup wait and fallback

`mineru parse` needs a background server. When it is not running, the skill script runs `mineru server start`. After the server is up, the managed engine still has to load its models, and a parse submitted meanwhile gets `quality_tier_unavailable`. So the script waits for the engine before parsing:

- It polls `mineru server status --json` and reads `mode`, `healthy`, `starting`, `supported_tiers` and `error_code`/`error_msg` under `parse_server.local`.
- Engine healthy: logs `[mineru] engine ready: supported tiers=…` and parses at the requested tier.
- Local engine off (`mode=disabled`): no wait; the log says only flash can run.
- Timeout (180 s by default, skill script flag `--engine-wait`, `0` = don't wait) or a managed engine out of restart attempts: logs `[mineru] engine not ready (reason)`.
- With no tier given, an unavailable engine falls back to `flash` and logs `[mineru] fallback: retrying with --tier flash`; an explicit `basic`/`standard`/`advanced` never falls back and fails instead.
- If the server was already running but the engine is still loading, the script also waits once and retries.

### Checking the tier actually used

- After a successful parse the script logs `[mineru] tier=<tier>`; it shows in the `import` task log and in the Web parse progress.
- The landed source records `parsed_by: mineru/<tier>`, e.g. `mineru/standard` or `mineru/flash`. Sources landed by older versions say just `mineru`. To find flash-parsed sources: `grep -l "parsed_by: mineru/flash" raw/*.md`.
- MinerU's own view: `mineru server status` (human) or `mineru server status --json` (machine).

See: repo [`docs/P5.2-多格式摄入.md`](../../P5.2-多格式摄入.md), [`docs/P5.2.1-图片落盘.md`](../../P5.2.1-图片落盘.md), [CLI commands → ingest](03-cli-commands.md#guanlan-ingest-target).
