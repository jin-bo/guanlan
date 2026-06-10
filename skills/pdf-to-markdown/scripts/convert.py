#!/usr/bin/env python3
"""Convert a document (PDF, DOCX, PPTX, XLSX, EPUB, HTML, image) to Markdown.

Tiered backend strategy (best quality first, degrade gracefully):

  1. MinerU      (`mineru`)        — primary. Highest-fidelity layout/table/formula
                                      extraction. Used whenever it is on PATH.
  2. marker-pdf  (`marker_single`) — first fallback. Optional Google Gemini LLM
                                      enhancement when GEMINI_API_KEY is available.
  3. pure-python (`pypdf`)         — last resort. Plain text-layer extraction for
                                      PDFs only; no OCR, no layout reconstruction.

With `--backend auto` (the default) the script tries each tier in order, moving to
the next one when a backend is missing OR fails, and prints which backend produced
the result. Force a single backend with `--backend mineru|marker|python`.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Extensions the document backends (MinerU / marker) can convert.
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".epub",
    ".html", ".htm",
    # images
    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif",
}

# The pure-python last-resort tier only understands a text-layer PDF.
PYTHON_FALLBACK_EXTENSIONS = {".pdf"}

DEFAULT_GEMINI_MODEL = "gemini-flash-latest"


class BackendUnavailable(Exception):
    """The backend tool/library is not installed — try the next tier."""


class BackendFailed(Exception):
    """The backend ran but did not produce usable output — try the next tier."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# Flags whose following token is a secret and must not be logged.
_SECRET_FLAGS = {"--gemini_api_key"}


def redact_cmd(cmd: list[str]) -> str:
    """Render a command for logging, masking the value after any secret flag."""
    parts: list[str] = []
    mask_next = False
    for c in cmd:
        if mask_next:
            parts.append("***")
            mask_next = False
        else:
            parts.append(c)
            mask_next = c in _SECRET_FLAGS
    return " ".join(parts)


def run_backend(cmd: list[str]) -> int:
    """Run a backend tool, routing its stdout to stderr so our own stdout stays
    reserved for the single output-path line the caller parses."""
    return subprocess.run(cmd, stdout=sys.stderr).returncode


# --------------------------------------------------------------------------- #
# .env helpers (no third-party dependency)
# --------------------------------------------------------------------------- #
def find_dotenv() -> Path | None:
    """Search for .env starting from cwd, walking up to filesystem root."""
    current = Path.cwd()
    while True:
        candidate = current / ".env"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file and return key/value pairs."""
    env: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            env[key] = value
    return env


def find_markdown(root: Path, stem: str) -> Path | None:
    """Locate a produced .md under ``root``.

    Backends nest output differently (MinerU: <stem>/<method>/<stem>.md, where
    <method> is auto/txt/ocr/vlm depending on backend; marker: <stem>/<stem>.md),
    so glob recursively and prefer the file named ``<stem>.md``.
    """
    if not root.is_dir():
        return None
    candidates = sorted(root.rglob("*.md"))
    if not candidates:
        return None
    for c in candidates:
        if c.stem == stem:
            return c
    return candidates[0]


# --------------------------------------------------------------------------- #
# Tier 1 — MinerU
# --------------------------------------------------------------------------- #
def run_mineru(input_path: Path, output_dir: Path, args, env_vars: dict[str, str]) -> Path:
    if shutil.which("mineru") is None:
        raise BackendUnavailable("'mineru' not found on PATH (pip install -U 'mineru[core]')")

    cmd = ["mineru", "-p", str(input_path), "-o", str(output_dir)]
    if args.method:
        cmd += ["-m", args.method]
    if args.lang:
        cmd += ["-l", args.lang]
    if args.mineru_backend:
        cmd += ["-b", args.mineru_backend]
    if args.start is not None:
        cmd += ["-s", str(args.start)]
    if args.end is not None:
        cmd += ["-e", str(args.end)]

    log(f"[mineru] {redact_cmd(cmd)}")
    if run_backend(cmd) != 0:
        raise BackendFailed("mineru exited non-zero")

    out = find_markdown(output_dir / input_path.stem, input_path.stem)
    if out is None:
        raise BackendFailed("mineru produced no markdown output")
    return out


# --------------------------------------------------------------------------- #
# Tier 2 — marker-pdf
# --------------------------------------------------------------------------- #
def run_marker(input_path: Path, output_dir: Path, args, env_vars: dict[str, str]) -> Path:
    if shutil.which("marker_single") is None:
        raise BackendUnavailable("'marker_single' not found on PATH (pip install marker-pdf)")

    cmd = [
        "marker_single",
        str(input_path),
        "--output_dir", str(output_dir),
        "--output_format", "markdown",
        "--disable_tqdm",
    ]

    # LLM enhancement is opt-out: only enable it when a Gemini key is available,
    # so the fallback still works in a key-less environment.
    gemini_api_key = env_vars.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if gemini_api_key:
        gemini_model = (
            args.model
            or env_vars.get("GEMINI_MODEL")
            or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        )
        cmd += [
            "--use_llm",
            "--llm_service", "marker.services.gemini.GoogleGeminiService",
            "--gemini_api_key", gemini_api_key,
            "--gemini_model_name", gemini_model,
        ]
        log(f"[marker] LLM enhancement on (model: {gemini_model})")
    else:
        log("[marker] no GEMINI_API_KEY — running without LLM enhancement")

    # marker page range syntax: "0,5-10,20". Its parser splits each part on "-"
    # and calls int() on both ends, so an open-ended "N-" would crash — only emit
    # a closed range, and warn when --start is given without --end.
    if args.page_range:
        cmd += ["--page_range", args.page_range]
    elif args.end is not None:
        start = args.start if args.start is not None else 0
        cmd += ["--page_range", f"{start}-{args.end}"]
    elif args.start is not None:
        log("[marker] --start without --end is not expressible as a marker page "
            "range; converting all pages")
    if args.disable_ocr:
        cmd.append("--disable_ocr")
    if args.disable_images:
        cmd.append("--disable_image_extraction")

    log(f"[marker] {redact_cmd(cmd)}")
    if run_backend(cmd) != 0:
        raise BackendFailed("marker_single exited non-zero")

    out = find_markdown(output_dir / input_path.stem, input_path.stem)
    if out is None:
        raise BackendFailed("marker_single produced no markdown output")
    return out


# --------------------------------------------------------------------------- #
# Tier 3 — pure-python (pypdf)
# --------------------------------------------------------------------------- #
def run_python(input_path: Path, output_dir: Path, args, env_vars: dict[str, str]) -> Path:
    if input_path.suffix.lower() not in PYTHON_FALLBACK_EXTENSIONS:
        raise BackendUnavailable(
            f"pure-python fallback only supports PDF, not '{input_path.suffix}'"
        )
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise BackendUnavailable("pypdf not installed (pip install pypdf)") from e

    log("[python] extracting text layer with pypdf (no OCR / no layout)")
    reader = PdfReader(str(input_path))
    pages = reader.pages
    last = len(pages) - 1
    start = args.start if args.start is not None else 0
    end = args.end if args.end is not None else last
    if start < 0 or start > last:
        raise BackendFailed(f"--start {start} out of range (document has {len(pages)} pages)")
    if end < start:
        raise BackendFailed(f"--end {end} is before --start {start}")
    end = min(end, last)

    parts: list[str] = []
    any_text = False
    for i in range(start, end + 1):
        text = (pages[i].extract_text() or "").strip()
        if text:
            any_text = True
            parts.append(f"<!-- page {i} -->\n\n{text}")
        else:
            parts.append(f"<!-- page {i} (no text) -->")

    if not any_text:
        # Every page empty => almost certainly a scanned PDF with no text layer.
        raise BackendFailed("no extractable text (scanned PDF? needs OCR via mineru/marker)")

    body = "\n\n".join(parts).strip()

    out_subdir = output_dir / input_path.stem
    out_subdir.mkdir(parents=True, exist_ok=True)
    out_md = out_subdir / f"{input_path.stem}.md"
    out_md.write_text(f"# {input_path.stem}\n\n{body}\n", encoding="utf-8")
    return out_md


# --------------------------------------------------------------------------- #
BACKENDS = {
    "mineru": run_mineru,
    "marker": run_marker,
    "python": run_python,
}
AUTO_ORDER = ["mineru", "marker", "python"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a document to Markdown via a tiered backend strategy "
        "(MinerU -> marker-pdf -> pure-python).",
    )
    parser.add_argument(
        "input",
        help="Path to the input document (PDF, DOCX, PPTX, XLSX, EPUB, HTML, or image)",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", *BACKENDS],
        default="auto",
        help="Backend to use. 'auto' (default) tries mineru -> marker -> python, "
        "falling back when one is missing or fails.",
    )
    # Page range (backend-agnostic, 0-based inclusive).
    parser.add_argument("--start", type=int, default=None, help="First page (0-based)")
    parser.add_argument("--end", type=int, default=None, help="Last page (0-based, inclusive)")
    # MinerU-specific
    parser.add_argument("--method", choices=["auto", "txt", "ocr"], default=None,
                        help="MinerU parsing method (default: auto)")
    parser.add_argument("--lang", default=None, help="MinerU OCR language hint, e.g. 'ch', 'en'")
    parser.add_argument("--mineru-backend", default=None,
                        help="MinerU -b backend, e.g. 'pipeline', 'vlm-auto-engine'")
    # marker-specific
    parser.add_argument("--page-range", help='marker page range, e.g. "0,5-10,20"')
    parser.add_argument("--disable-ocr", action="store_true",
                        help="marker: use text layer only, skip OCR")
    parser.add_argument("--disable-images", action="store_true",
                        help="marker: do not extract images")
    parser.add_argument("--model", default=None,
                        help=f"marker Gemini model (default: {DEFAULT_GEMINI_MODEL})")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        log(f"Error: file not found: {input_path}")
        sys.exit(1)
    if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        log(f"Error: unsupported file type '{input_path.suffix}': {input_path}\n"
            f"  Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        sys.exit(1)

    env_vars: dict[str, str] = {}
    dotenv_path = find_dotenv()
    if dotenv_path:
        env_vars = load_dotenv(dotenv_path)

    output_dir = input_path.parent
    order = AUTO_ORDER if args.backend == "auto" else [args.backend]

    errors: list[str] = []
    for name in order:
        try:
            out = BACKENDS[name](input_path, output_dir, args, env_vars)
        except BackendUnavailable as e:
            log(f"[{name}] unavailable: {e}")
            errors.append(f"{name}: unavailable ({e})")
            continue
        except BackendFailed as e:
            log(f"[{name}] failed: {e}")
            errors.append(f"{name}: failed ({e})")
            continue
        log(f"[done] backend={name}")
        print(out)
        return

    log("Error: all backends exhausted:")
    for e in errors:
        log(f"  - {e}")
    sys.exit(1)


if __name__ == "__main__":
    main()
