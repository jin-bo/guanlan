#!/usr/bin/env python3
"""Convert a document (PDF, DOCX, PPTX, XLSX, EPUB, HTML, image) to Markdown.

Tiered backend strategy (best quality first, degrade gracefully):

  1. MinerU      (`mineru`)        — primary. Highest-fidelity layout/table/formula
                                      extraction. Used whenever it is on PATH.
                                      MinerU 4.x (`mineru parse`) is preferred; the
                                      3.x CLI (`mineru -p`) is still supported.
  2. marker-pdf  (`marker_single`) — first fallback. Optional Google Gemini LLM
                                      enhancement when GEMINI_API_KEY is available.
  3. pure-python (`pypdf`)         — last resort. Plain text-layer extraction for
                                      PDFs only; no OCR, no layout reconstruction.

With `--backend auto` (the default) the script tries each tier in order, moving to
the next one when a backend is missing OR fails, and prints which backend produced
the result. Force a single backend with `--backend mineru|marker|python`.

HTML (.html/.htm) is preprocessed once before any backend (see htmlprep.py): decoded
to UTF-8, main content extracted (trafilatura, optional), remote images turned into
links. pandoc is tried first for HTML, as for .docx.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

# Extensions the document backends (MinerU / marker) can convert.
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".epub",
    ".html", ".htm",
    # MinerU 4.x only (marker/python tiers will report them unsupported)
    ".doc", ".ppt", ".xls", ".rtf", ".odt", ".ods", ".odp", ".ofd", ".csv", ".tsv",
    ".mhtml", ".mht",
    # images
    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif",
}

# marker-pdf handles the original document set, not the MinerU 4.x-only formats.
MARKER_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".epub", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif",
}

# pandoc reads the document structure directly (Word heading styles, tables, footnotes),
# so for .docx it beats the layout-based engines and needs no models.
PANDOC_EXTENSIONS = {".docx", ".html", ".htm"}

# HTML goes through the shared preprocessing (htmlprep.py) before any backend.
HTML_EXTENSIONS = {".html", ".htm"}

# Image formats a browser can show; pandoc may extract others (EMF/WMF/WDP) from Office files.
WEB_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}

# The pure-python last-resort tier only understands a text-layer PDF.
PYTHON_FALLBACK_EXTENSIONS = {".pdf"}

DEFAULT_GEMINI_MODEL = "gemini-flash-latest"


class BackendUnavailable(Exception):
    """The backend tool/library is not installed — try the next tier."""


class BackendFailed(Exception):
    """The backend ran but did not produce usable output — try the next tier."""


# Garbled text layer (P4.30.1 §3). Some publishers embed fonts with a private encoding:
# Latin letters and digits in the text layer read `犐犆犛` for "ICS", `犌犅／犜` for "GB/T",
# or come out as bare glyph names such as `/G21`. Text-layer extraction copies that
# verbatim, so every Latin word (codes, English terms, units) is unreadable.
# The shifted glyphs land in U+7280-72FF, which also holds everyday characters (犯 状 独 狗);
# those are all in GB2312, the shifted ones never are -- so only count the non-GB2312 ones.
# Traditional Chinese also uses non-GB2312 characters from this block (狀 犢 犧 狹), one at a time;
# shifted glyphs spell whole words (犐犆犛, 犌犅), so only count runs of two or more.
_ODD_BLOCK = re.compile(r"[\u7280-\u72ff]{2,}")
_GLYPH_NAME = re.compile(r"/G[0-9A-F]{2}\b")
GARBLED_GLYPH_NAMES = 20
GARBLED_SHIFTED_CHARS = 20


def _rare(ch: str) -> bool:
    try:
        ch.encode("gb2312")
    except UnicodeEncodeError:
        return True
    return False


def garbled_text_layer(markdown: str) -> str | None:
    """Reason string if the Markdown looks like a garbled text layer, else None."""
    glyphs = len(_GLYPH_NAME.findall(markdown))
    if glyphs >= GARBLED_GLYPH_NAMES:
        return f"{glyphs} raw glyph names (/Gxx) in the text layer"
    shifted = 0
    for run in _ODD_BLOCK.findall(markdown):
        rare = [ch for ch in run if _rare(ch)]
        if len(rare) >= 2:
            shifted += len(rare)
    if shifted >= GARBLED_SHIFTED_CHARS:
        return f"{shifted} characters from a private font encoding (U+7280-72FF outside GB2312)"
    return None


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
    # Explicit UTF-8: on Windows open() defaults to the ANSI code page (cp1252 / cp936),
    # and one non-ASCII comment would crash the script before any backend runs.
    with open(path, encoding="utf-8-sig", errors="replace") as f:
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
# MinerU 4.x document types that accept a quality tier (basic/standard/advanced);
# every other format (Office, HTML, EPUB, ...) is always parsed with "flash".
MINERU_TIERED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
                            ".tif", ".tiff"}

# Error codes meaning "no local engine for a quality tier" — retry with flash.
_MINERU_NO_QUALITY_ENGINE = {"quality_tier_unavailable", "no_engine", "engine_unavailable"}
# MinerU 4.2 added token auth between the CLI and its doclib server. A server started by
# 4.0.x before the upgrade is refused with one of these codes until it is restarted.
_MINERU_STALE_SERVER = {"doclib_upgrade_required", "invalid_doclib_token"}


def mineru_major_version() -> int | None:
    """Return the installed MinerU major version, or None if it can't be read."""
    try:
        out = subprocess.run(["mineru", "--version"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.\d+", out)
    return int(m.group(1)) if m else None


# How often to poll `mineru server status --json` while the local engine starts.
ENGINE_POLL_INTERVAL = 3.0


def mineru_server_status() -> dict:
    """Return `mineru server status --json` as a dict, or {} if it can't be read.

    A server that is still booting answers {"status": "starting", ...} instead of the
    full status object; `local_engine_state` treats both shapes.
    """
    try:
        proc = subprocess.run(["mineru", "server", "status", "--json"],
                              capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    decoder = json.JSONDecoder()
    i = proc.stdout.find("{")
    while i != -1:
        try:
            obj, _ = decoder.raw_decode(proc.stdout, i)
        except json.JSONDecodeError:
            i = proc.stdout.find("{", i + 1)
            continue
        return obj if isinstance(obj, dict) else {}
    return {}


def local_engine_state(status: dict) -> tuple[str, list[str], str]:
    """Classify the local parse engine from a server status object.

    Returns (state, supported_tiers, reason). state is one of:
      ready    — the local engine is healthy and lists at least one tier;
      disabled — parse_server.local.mode is disabled: only flash runs locally,
                 so waiting cannot help;
      failed   — a managed engine used up its restart attempts;
      starting — anything else (server or engine still booting): worth waiting.
    """
    if status.get("status") == "starting":
        return "starting", [], "mineru server is still starting"
    if not status.get("running"):
        return "starting", [], "mineru server is not running yet"
    local = (status.get("parse_server") or {}).get("local") or {}
    mode = local.get("mode") or "disabled"
    tiers = [str(t) for t in local.get("supported_tiers") or []]
    if mode == "disabled":
        return "disabled", tiers, "local parse engine disabled (parse_server.local.mode=disabled)"
    if local.get("healthy") and tiers:
        return "ready", tiers, ""
    error = local.get("error_code")
    reason = f"{error}: {local.get('error_msg')}" if error else (
        "local parse engine is starting" if local.get("starting") else
        "local parse engine is not healthy yet")
    max_restarts = local.get("max_restart_attempts") or 0
    if (error and not local.get("starting") and mode == "managed" and max_restarts
            and (local.get("restart_count") or 0) >= max_restarts):
        return "failed", tiers, reason
    return "starting", tiers, reason


def wait_for_engine(timeout: float, *, sleep=None, clock=None) -> tuple[str, list[str], str]:
    """Poll the server status until the local engine is ready, disabled or failed, or
    `timeout` seconds pass. Returns the last `local_engine_state`."""
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + timeout
    while True:
        state, tiers, reason = local_engine_state(mineru_server_status())
        if state != "starting" or clock() >= deadline:
            return state, tiers, reason
        sleep(ENGINE_POLL_INTERVAL)


def run_mineru(input_path: Path, output_dir: Path, args, env_vars: dict[str, str]) -> Path:
    if shutil.which("mineru") is None:
        raise BackendUnavailable("'mineru' not found on PATH (uv tool install -U mineru)")

    major = mineru_major_version()
    if major is not None and major >= 4:
        return run_mineru_v4(input_path, output_dir, args)
    log(f"[mineru] legacy CLI detected (major={major}); upgrade with: uv tool install -U mineru")
    return run_mineru_legacy(input_path, output_dir, args)


def mineru_v4_pages(args) -> str:
    """Translate our 0-based inclusive --start/--end to MinerU 4 page syntax
    (1-based inclusive, 'r1' = last page). Always explicit: MinerU 4 parses only
    the first 10 pages when --pages is omitted."""
    if args.start is None and args.end is None:
        return "all"
    start = (args.start or 0) + 1
    end = str(args.end + 1) if args.end is not None else "r1"
    return f"{start}-{end}"


def parse_json_stdout(text: str) -> dict:
    """Pick the result object out of `mineru --json` stdout (notices may precede it)."""
    decoder = json.JSONDecoder()
    found: dict = {}
    i = text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and ("parse" in obj or "error" in obj):
            found = obj
        i = text.find("{", end)
    return found


def _normalize_error(payload: dict) -> dict:
    """Make `payload["error"]` a dict so the error branches can call `.get` on it.

    MinerU reports `{"error": {"code", "message"}}`; anything else (a bare string, a list)
    is kept as the diagnostic message, so a failed parse still falls through to the next
    backend instead of raising AttributeError."""
    err = payload.get("error")
    if err is not None and not isinstance(err, dict):
        payload["error"] = {"code": None, "message": str(err)}
    return payload


def run_mineru_v4(input_path: Path, output_dir: Path, args) -> Path:
    """MinerU 4.x: `mineru parse` via the local document-library service."""
    for flag, value in (("--method", args.method), ("--lang", args.lang),
                        ("--mineru-backend", args.mineru_backend)):
        if value:
            log(f"[mineru] {flag} is a MinerU 3.x option; ignored by MinerU 4")

    out_md = output_dir / input_path.stem / f"{input_path.stem}.md"
    tiered = input_path.suffix.lower() in MINERU_TIERED_EXTENSIONS

    def build(tier: str | None) -> list[str]:
        cmd = ["mineru", "parse", str(input_path), "-o", str(out_md),
               "--wait", str(args.wait), "--json"]
        if input_path.suffix.lower() == ".pdf":
            cmd += ["--pages", mineru_v4_pages(args)]
        if tier and tiered:
            cmd += ["--tier", tier]
        if args.force:
            cmd.append("--force")
        if args.remote:
            cmd.append("--remote")
        return cmd

    def attempt(tier: str | None) -> tuple[int, dict]:
        cmd = build(tier)
        log(f"[mineru] {redact_cmd(cmd)}")
        # MinerU 4 always writes UTF-8 to its std streams, whatever the locale; decoding
        # with the Windows code page would garble Chinese output paths in the JSON.
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, encoding="utf-8", errors="replace")
        return proc.returncode, _normalize_error(parse_json_stdout(proc.stdout))

    if args.remote:
        log("[mineru] --remote: the document is uploaded to the mineru.net parse service")

    # The local quality engine (basic/standard/advanced) starts after the doclib server
    # and can take minutes to load its models. Parsing before it is healthy makes MinerU
    # answer quality_tier_unavailable, so wait for it once instead of degrading to flash.
    wants_quality = tiered and not args.remote and args.tier != "flash"
    waited = False

    def wait_engine() -> str:
        nonlocal waited
        waited = True
        if args.engine_wait <= 0:
            return "skipped"
        log(f"[mineru] waiting up to {args.engine_wait}s for the local parse engine")
        state, tiers, reason = wait_for_engine(args.engine_wait)
        if state == "ready":
            log(f"[mineru] engine ready: supported tiers={','.join(tiers)}")
        elif state == "disabled":
            log(f"[mineru] {reason}; only the flash tier runs locally")
        else:
            log(f"[mineru] engine not ready ({reason})")
        return state

    rc, payload = attempt(args.tier)
    err = payload.get("error") or {}
    if rc != 0 and err.get("code") == "server_not_running":
        # `mineru parse` talks to a background doclib server; start it once.
        log("[mineru] starting local server (stop later with: mineru server stop)")
        if run_backend(["mineru", "server", "start"]) != 0:
            raise BackendFailed("could not start the local mineru server")
        if wants_quality:
            wait_engine()
        rc, payload = attempt(args.tier)
        err = payload.get("error") or {}
    if rc != 0 and err.get("code") in _MINERU_NO_QUALITY_ENGINE and wants_quality and not waited:
        # The server was already up but its engine may still be loading.
        log(f"[mineru] {err.get('message')}")
        if wait_engine() == "ready":
            rc, payload = attempt(args.tier)
            err = payload.get("error") or {}
    if rc != 0 and err.get("code") in _MINERU_NO_QUALITY_ENGINE and args.tier is None and tiered:
        log(f"[mineru] {err.get('message')}")
        log("[mineru] fallback: retrying with --tier flash (text-only). For layout/OCR "
            "quality, enable a local engine: mineru config set parse_server.local.mode managed")
        rc, payload = attempt("flash")
        err = payload.get("error") or {}

    if rc != 0:
        status = (payload.get("parse") or {}).get("status")
        if status in ("pending", "parsing"):
            raise BackendFailed(
                f"mineru still {status} after {args.wait}s; re-run the same command to "
                "collect the cached result, or raise --wait")
        if err.get("code") in _MINERU_STALE_SERVER:
            # Restarting a server that may be parsing for someone else is the user's call.
            log("[mineru] the running mineru server predates this CLI; restart it: "
                "mineru server stop && mineru server start")
        detail = ": ".join(str(x) for x in (err.get("code"), err.get("message")) if x) or f"exit {rc}"
        raise BackendFailed(f"mineru parse failed ({detail})")

    def produced() -> Path:
        written = (payload.get("output") or {}).get("path")
        out = Path(written) if written else out_md
        if not out.is_file() or out.stat().st_size == 0:
            raise BackendFailed("mineru produced no markdown output")
        return out

    out = produced()
    # MinerU 4.0.12 (fixed upstream in 4.2; kept for 4.0.x, never fires on 4.2+): the first
    # parse of a document it has not seen before covers only page 1
    # for Office files (it learns the page count during that parse), although the page
    # markers already say "page 1 of N". A second call is served from the cache and covers
    # every page. PDFs get an explicit --pages, so only check the other formats.
    if input_path.suffix.lower() != ".pdf":
        seen, total = page_coverage(out.read_text(encoding="utf-8", errors="replace"))
        if total and seen < total:
            log(f"[mineru] output covers {seen} of {total} pages; asking again for the full result")
            rc, payload = attempt((payload.get("parse") or {}).get("tier") or args.tier)
            if rc != 0:
                raise BackendFailed(f"mineru parse failed on retry (exit {rc})")
            out = produced()
            seen, total = page_coverage(out.read_text(encoding="utf-8", errors="replace"))
            if total and seen < total:
                raise BackendFailed(f"mineru output still covers only {seen} of {total} pages")

    parse = payload.get("parse") or {}
    tier = parse.get("tier")
    if tier:
        log(f"[mineru] tier={tier}")
    # Which document this output belongs to: image locators are checked against it before export.
    if parse.get("sha256"):
        MINERU_DOCS[str(out)] = {"sha256": parse["sha256"], "tier": tier}
    return out


_PAGE_MARKER = re.compile(r"<!-- page (\d+) of (\d+) -->")


def page_coverage(markdown: str) -> tuple[int, int]:
    """(highest page seen, total pages) from MinerU 4 page markers; (0, 0) if there are none."""
    marks = [(int(a), int(b)) for a, b in _PAGE_MARKER.findall(markdown)]
    if not marks:
        return 0, 0
    return max(a for a, _ in marks), max(b for _, b in marks)


# --------------------------------------------------------------------------- #
# MinerU 4 image export (P5.2.2)
# --------------------------------------------------------------------------- #
# MinerU 4 keeps figures and charts in its document library and writes library locators
# into the Markdown, `![Image block](doc:060dfdd/tier:standard/page:8/block:3)`, instead of
# image files next to it. A Markdown viewer cannot open them, and callers that collect
# relative image references (guanlan) see an external link. `mineru read <locator> -f image`
# exports one block as PNG, but only while the registered source file still exists -- that
# is, during this run, before a caller deletes its staged copy.
# Output path -> {"sha256", "tier"} of the parse that produced it (set by run_mineru_v4).
MINERU_DOCS: dict[str, dict] = {}
DOC_IMAGE_REF = re.compile(
    r"(?P<head>!\[[^\]]*\]\()\s*(?P<loc>doc:(?P<id>[0-9a-f]+)/tier:(?P<tier>[a-z]+)"
    r"/page:(?P<page>\d+)/block:(?P<block>\d+))\s*\)"
)
IMAGE_EXPORT_TIMEOUT = 60  # seconds for one `mineru read`; the first timeout stops the export
# The same three caps guanlan applies when it collects images (guanlan/imageio.py, P5.2.1).
# Staying under them here means a figure-heavy document still converts: the blocks over a cap
# keep their doc: links instead of making guanlan reject the whole result.
MAX_IMAGE_COUNT = 500
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES_TOTAL_BYTES = 200 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_LOCAL_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")
_URL_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*:")


class _ExportTimeout(Exception):
    """`mineru read` did not answer in time: the server is likely stuck, stop asking."""


def _read_block_image(locator: str, target: Path) -> str | None:
    """Export one block to `target`. None on success, else a short reason.
    Raises _ExportTimeout when `mineru read` times out."""
    target.unlink(missing_ok=True)  # only a file this call writes counts as success
    cmd = ["mineru", "read", locator, "-f", "image", "-o", str(target), "--json"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              encoding="utf-8", errors="replace", timeout=IMAGE_EXPORT_TIMEOUT)
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        raise _ExportTimeout(f"timed out after {IMAGE_EXPORT_TIMEOUT}s") from None
    except OSError as e:
        return str(e)
    payload = parse_json_stdout(proc.stdout)
    if proc.returncode != 0:
        err = payload.get("error")
        code = err.get("code") if isinstance(err, dict) else None
        return code or f"exit {proc.returncode}"
    try:
        with target.open("rb") as fh:
            ok = fh.read(len(_PNG_MAGIC)) == _PNG_MAGIC
    except OSError:
        ok = False
    if not ok:
        target.unlink(missing_ok=True)
        return "no PNG written"
    return None


def _local_images(text: str, base: Path) -> tuple[int, int]:
    """(count, bytes) of the distinct local image files the Markdown already references:
    they take their share of the same budget when guanlan collects them."""
    seen: set[Path] = set()
    total = 0
    for url in _LOCAL_IMAGE_REF.findall(text):
        if _URL_SCHEME.match(url) or url.startswith(("/", "~")):
            continue
        try:
            # guanlan percent-decodes a reference before it resolves it (imageio._admit_image_ref):
            # count the same file, or `a%20b.png` slips past this budget and guanlan rejects the lot.
            path = (base / urllib.parse.unquote(url)).resolve()
            if path in seen or not path.is_file():
                continue
            size = path.stat().st_size
        except (OSError, ValueError):
            continue
        seen.add(path)
        total += size
    return len(seen), total


def export_doc_images(md_path: Path) -> tuple[int, int]:
    """Export the MinerU 4 image blocks referenced in `md_path` to `images/` beside it and
    rewrite those references to the exported files. Returns (exported, referenced) counts of
    distinct locators. A block that is not exported keeps its `doc:` reference.

    Blocks are taken in order of first appearance. Not exported: blocks of another document,
    blocks `mineru read` fails on, blocks over the count/size caps, and every block after the
    first timeout. Bytes outside the rewritten references stay as they were (line endings,
    stray invalid UTF-8)."""
    # newline="" + surrogateescape: read and write back byte for byte, except the references.
    with md_path.open(encoding="utf-8", errors="surrogateescape", newline="") as fh:
        text = fh.read()
    locators: dict[str, re.Match] = {}
    for m in DOC_IMAGE_REF.finditer(text):
        locators.setdefault(m.group("loc"), m)
    if not locators:
        return 0, 0
    doc = MINERU_DOCS.get(str(md_path)) or {}
    sha, tier = doc.get("sha256") or "", doc.get("tier")
    images = md_path.parent / "images"
    count, total = _local_images(text, md_path.parent)
    log(f"[mineru] exporting {len(locators)} image block(s)")
    names: dict[str, str] = {}  # locator -> file name under images/
    used: set[str] = set()
    kept = {"foreign": 0, "failed": 0, "over cap": 0, "after timeout": 0}
    stop: str | None = None  # why the remaining blocks are not tried
    for loc, m in locators.items():
        # Only blocks of the document parsed in this run: the library holds other files too.
        if not sha or not sha.startswith(m.group("id")) or (tier and m.group("tier") != tier):
            log(f"[mineru] image {loc}: not from this document; kept as a doc: link")
            kept["foreign"] += 1
            continue
        if stop:
            kept["over cap" if stop.startswith("cap") else "after timeout"] += 1
            continue
        if count >= MAX_IMAGE_COUNT:
            stop = f"cap: more than {MAX_IMAGE_COUNT} images"
            kept["over cap"] += 1
            continue
        base = f"p{m.group('page')}-b{m.group('block')}"
        name, k = f"{base}.png", 2
        while name in used:  # different locators, same page/block: never share a file
            name, k = f"{base}-{k}.png", k + 1
        images.mkdir(exist_ok=True)
        target = images / name
        try:
            reason = _read_block_image(loc, target)
        except _ExportTimeout as e:
            log(f"[mineru] image {loc}: {e}; stopping the image export")
            stop = f"timeout: {e}"
            kept["after timeout"] += 1
            continue
        if reason:
            log(f"[mineru] image {loc}: {reason}; kept as a doc: link")
            kept["failed"] += 1
            continue
        size = target.stat().st_size
        if size > MAX_IMAGE_BYTES:
            target.unlink(missing_ok=True)
            log(f"[mineru] image {loc}: {size} bytes, over the {MAX_IMAGE_BYTES}-byte cap; kept as a doc: link")
            kept["over cap"] += 1
            continue
        if total + size > MAX_IMAGES_TOTAL_BYTES:
            target.unlink(missing_ok=True)
            stop = f"cap: more than {MAX_IMAGES_TOTAL_BYTES} bytes of images"
            kept["over cap"] += 1
            continue
        count += 1
        total += size
        used.add(name)
        names[loc] = name

    def repl(m: re.Match) -> str:
        name = names.get(m.group("loc"))
        return f"{m.group('head')}images/{name})" if name else m.group(0)

    if names:
        # Write a sibling temp file and swap it in: a failed write (disk full) must leave the
        # original output intact, because the caller still hands that file back as a success.
        fd, tmp_name = tempfile.mkstemp(prefix=".imgexport-", suffix=".md", dir=md_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                fh.write(DOC_IMAGE_REF.sub(repl, text))
            os.chmod(tmp_name, md_path.stat().st_mode & 0o7777)  # mkstemp makes it 0600
            os.replace(tmp_name, md_path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    if images.is_dir() and not any(images.iterdir()):
        images.rmdir()
    exported = len(names)
    summary = f"[mineru] exported {exported} of {len(locators)} image block(s)"
    rest = sum(kept.values())
    if rest:
        why = ", ".join(f"{n} {k}" for k, n in kept.items() if n)
        summary += f"; {rest} kept as doc: links ({why})"
    if stop:
        summary += f"; stopped early ({stop.split(': ', 1)[1]})"
    log(summary)
    return exported, len(locators)


def run_mineru_legacy(input_path: Path, output_dir: Path, args) -> Path:
    """MinerU <= 3.x: `mineru -p <in> -o <dir>`."""
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
# pandoc — structured formats (.docx)
# --------------------------------------------------------------------------- #
# Lua filter: drop image attributes and unwrap figures so the gfm writer emits
# ![alt](src) instead of <img style=...> / <figure> (guanlan only collects markdown image
# references), and replace images a browser cannot show (EMF/WMF/WDP) with their alt text.
PANDOC_IMAGE_FILTER = """
local web = {%s}
function Image(el)
  -- data: images (kept by htmlprep) carry their type in the MIME, not in a file extension;
  -- --extract-media writes them out only after this filter runs.
  local ext = (el.src:match("^data:image/(%%w+)") or el.src:match("%%.([%%w]+)$") or ""):lower()
  if not web[ext] then
    return el.caption
  end
  el.attr = pandoc.Attr()
  el.title = ""
  return el
end
-- pandoc 3 wraps a captioned standalone image in a Figure, which gfm writes as
-- <figure><img>; unwrap it to the image paragraph (the caption is the alt text).
function Figure(el)
  return el.content
end
""" % ", ".join(f'["{e[1:]}"]=true' for e in sorted(WEB_IMAGE_EXTENSIONS))


def run_pandoc(input_path: Path, output_dir: Path, args, env_vars: dict[str, str]) -> Path:
    if input_path.suffix.lower() not in PANDOC_EXTENSIONS:
        raise BackendUnavailable(
            f"pandoc tier only handles {', '.join(sorted(PANDOC_EXTENSIONS))}, not '{input_path.suffix}'")
    if shutil.which("pandoc") is None:
        raise BackendUnavailable("'pandoc' not found on PATH (https://pandoc.org/installing.html)")
    if args.start is not None or args.end is not None:
        log("[pandoc] --start/--end ignored: a .docx / .html has no fixed pages")

    out_subdir = output_dir / input_path.stem
    out_subdir.mkdir(parents=True, exist_ok=True)
    out_md = out_subdir / f"{input_path.stem}.md"
    lua = out_subdir / ".guanlan-images.lua"
    lua.write_text(PANDOC_IMAGE_FILTER, encoding="utf-8")
    # gfm keeps tables with merged cells as HTML tables instead of dropping the merge;
    # --extract-media with a relative path (run from out_subdir) gives image links
    # relative to the markdown file, as the other backends do.
    cmd = ["pandoc", str(input_path), "-t", "gfm", "--wrap=none",
           "--extract-media=media", "--lua-filter", lua.name, "-o", out_md.name]
    if input_path.suffix.lower() in HTML_EXTENSIONS:
        # Without native divs/spans, <div class=…>/<span style=…> wrappers stop leaking into the
        # Markdown, while tables with merged cells still come out as HTML tables (P4.31 §4.3).
        cmd[2:2] = ["-f", "html-native_divs-native_spans"]
        resource_dir = getattr(args, "html_resource_dir", None)
        # pandoc splits --resource-path on the search-path separator (":" on POSIX, ";" on Windows) and has
        # no escape: a folder name containing it would make pandoc look for (and --extract-media copy)
        # relative images somewhere else. Skip it then — the page's relative images are simply not found.
        if resource_dir and os.pathsep not in str(resource_dir):
            cmd.append(f"--resource-path={resource_dir}")
    log(f"[pandoc] {redact_cmd(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=out_subdir, stdout=sys.stderr, stderr=subprocess.PIPE,
                              encoding="utf-8", errors="replace")
    finally:
        lua.unlink(missing_ok=True)
    if proc.stderr.strip():
        log(proc.stderr.rstrip())
    if proc.returncode != 0:
        raise BackendFailed(f"pandoc exited {proc.returncode}")
    if not out_md.is_file() or not out_md.read_text(encoding="utf-8", errors="replace").strip():
        raise BackendFailed("pandoc produced no markdown output")
    return out_md


# --------------------------------------------------------------------------- #
# Tier 2 — marker-pdf
# --------------------------------------------------------------------------- #
def run_marker(input_path: Path, output_dir: Path, args, env_vars: dict[str, str]) -> Path:
    if shutil.which("marker_single") is None:
        raise BackendUnavailable("'marker_single' not found on PATH (pip install marker-pdf)")
    if input_path.suffix.lower() not in MARKER_EXTENSIONS:
        raise BackendUnavailable(f"marker does not support '{input_path.suffix}'")

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
    "pandoc": run_pandoc,
    "mineru": run_mineru,
    "marker": run_marker,
    "python": run_python,
}
AUTO_ORDER = ["pandoc", "mineru", "marker", "python"]


def backend_order(backend: str, suffix: str) -> list[str]:
    """Backends to try. In auto mode pandoc is tried only for the formats it reads
    structurally (elsewhere it would only add an "unavailable" line to every log)."""
    if backend != "auto":
        return [backend]
    suffix = suffix.lower()
    return [n for n in AUTO_ORDER if n != "pandoc" or suffix in PANDOC_EXTENSIONS]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a document to Markdown via a tiered backend strategy "
        "(pandoc for .docx/.html -> MinerU -> marker-pdf -> pure-python).",
    )
    parser.add_argument(
        "input",
        help="Path to the input document (PDF, DOCX, PPTX, XLSX, EPUB, HTML, or image)",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", *BACKENDS],
        default="auto",
        help="Backend to use. 'auto' (default) tries pandoc (.docx/.html only) -> mineru -> "
        "marker -> python, falling back when one is missing or fails.",
    )
    # Page range (backend-agnostic, 0-based inclusive).
    parser.add_argument("--start", type=int, default=None, help="First page (0-based)")
    parser.add_argument("--end", type=int, default=None, help="Last page (0-based, inclusive)")
    # MinerU 4.x
    parser.add_argument("--tier", choices=["flash", "basic", "standard", "advanced"],
                        default=None,
                        help="MinerU 4 parse tier (default: best available local engine, "
                        "falling back to flash)")
    parser.add_argument("--wait", type=int, default=1800,
                        help="MinerU 4: max seconds to wait for a parse (default: 1800)")
    parser.add_argument("--engine-wait", type=int, default=180,
                        help="MinerU 4: max seconds to wait for the local parse engine to "
                        "become healthy before parsing at a quality tier (default: 180; "
                        "0 = don't wait)")
    parser.add_argument("--force", action="store_true",
                        help="MinerU 4: ignore the parse cache and re-parse")
    parser.add_argument("--remote", action="store_true",
                        help="MinerU 4: use the mineru.net remote service (uploads the file)")
    # MinerU 3.x (legacy CLI)
    parser.add_argument("--method", choices=["auto", "txt", "ocr"], default=None,
                        help="MinerU 3.x parsing method (default: auto)")
    parser.add_argument("--lang", default=None, help="MinerU 3.x OCR language hint, e.g. 'ch', 'en'")
    parser.add_argument("--mineru-backend", default=None,
                        help="MinerU 3.x -b backend, e.g. 'pipeline', 'hybrid-engine'")
    # marker-specific
    parser.add_argument("--page-range", help='marker page range, e.g. "0,5-10,20"')
    parser.add_argument("--disable-ocr", action="store_true",
                        help="marker: use text layer only, skip OCR")
    parser.add_argument("--disable-images", action="store_true",
                        help="marker: do not extract images")
    parser.add_argument("--model", default=None,
                        help=f"marker Gemini model (default: {DEFAULT_GEMINI_MODEL})")
    # HTML preprocessing (htmlprep.py)
    parser.add_argument("--html-charset", default=None,
                        help="HTML: charset declared by the HTTP Content-Type header (wins over <meta>)")
    parser.add_argument("--html-base-url", default=None,
                        help="HTML: page URL used to resolve relative image/link URLs")
    parser.add_argument("--html-extract", choices=["auto", "off"], default="auto",
                        help="HTML: extract the main content with trafilatura when installed "
                        "(auto, default) or keep the whole page (off)")
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
    order = backend_order(args.backend, input_path.suffix)

    prep_dir: Path | None = None
    source_path = input_path
    try:
        if input_path.suffix.lower() in HTML_EXTENSIONS:
            # Same directory as this script. Add it explicitly: with PYTHONSAFEPATH / `python -P`
            # (passed through from the caller's environment) the script folder is not on sys.path.
            script_dir = str(Path(__file__).resolve().parent)
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            from htmlprep import prepare_html

            text, report = prepare_html(input_path.read_bytes(), charset=args.html_charset,
                                        base_url=args.html_base_url, extract=args.html_extract)
            log(f"[html] charset={report['charset']} ({report['charset_source']})")
            log(f"[html] extract={report['extract']}")
            # Never overwrite the user's file: the prepared copy lives in its own folder and keeps
            # the stem, so every backend names its output exactly as before. Inside the try, so a
            # failed write (disk full) does not leave the hidden folder behind.
            # A unique folder (not `.<stem>.htmlprep`): two runs on the same file cannot delete each
            # other's input, and a long stem cannot push the folder name past 255 bytes.
            prep_dir = Path(tempfile.mkdtemp(prefix=".htmlprep-", dir=output_dir))
            source_path = prep_dir / f"{input_path.stem}.html"
            # pandoc looks up relative <img src> in its working directory: point it at the folder of the
            # input file, where htmlprep allows plain relative paths (and nothing outside it). When guanlan
            # calls this skill, that folder is its staging temp dir holding only the page itself, so
            # relative images are reported missing there (P4.31 §4.4) — this matters for direct skill use.
            args.html_resource_dir = str(input_path.parent)
            source_path.write_text(text, encoding="utf-8")
        _run_backends(order, source_path, output_dir, args, env_vars)
    finally:
        if prep_dir is not None:
            shutil.rmtree(prep_dir, ignore_errors=True)


def _run_backends(order, input_path, output_dir, args, env_vars) -> None:
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
        if input_path.suffix.lower() == ".pdf":
            reason = garbled_text_layer(out.read_text(encoding="utf-8", errors="replace"))
            if reason:
                # Not a usable result: try the next tier instead of handing back gibberish.
                # Only an OCR pass reads these files correctly (MinerU with OCR, marker).
                log(f"[{name}] failed: garbled text layer ({reason}); the PDF needs OCR")
                errors.append(f"{name}: failed (garbled text layer: {reason})")
                out.unlink(missing_ok=True)  # never leave the gibberish where a caller could pick it up
                continue
        if name == "mineru":
            # After the garbled check (a rejected output exports nothing) and while the source
            # file still exists. Images are an extra: a failure here never fails the conversion.
            try:
                export_doc_images(out)
            except Exception as e:  # noqa: BLE001 -- any export error, not only OSError
                log(f"[mineru] image export skipped: {e}")
        log(f"[done] backend={name}")
        print(out)
        return

    log("Error: all backends exhausted:")
    for e in errors:
        log(f"  - {e}")
    sys.exit(1)


if __name__ == "__main__":
    main()
