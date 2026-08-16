# Installation

## Requirements

- **Python 3.10+**.
- Core commands (`init` / `check` / `health` / `lint` / `graph` / `reindex` / `search`) are **zero-LLM and run offline** — no model or network needed.
- `ingest` / `query` and Web chat require a **configured model** (via the Agentao runtime).
- `convert` is zero-LLM from GuānLán's side, but it **shells out** to the `pdf-to-markdown` skill's backends (MinerU / marker / pypdf); a real conversion needs at least one backend installed.

## Install the core package

```bash
pip install guanlan-wiki
```

> The PyPI name is `guanlan-wiki` (the bare `guanlan` is taken by an unrelated project); the **CLI and import name are still `guanlan`** after install.

Verify:

```bash
guanlan --version     # prints the current version (e.g. 0.1.9)
guanlan --help
```

## Optional hosts (add-on layers, install on demand)

The core is the CLI; the Web, MCP and IM hosts are all **optional add-on layers** — every CLI command works without them.

### Web host

```bash
pip install 'guanlan-wiki[web]'
```

Pulls in `fastapi` / `uvicorn` / `markdown` / `python-multipart` / `anyio`. Enables `guanlan web` — see [Web host](05-web-host.md).

### MCP host

```bash
pip install 'guanlan-wiki[mcp]'
```

Pulls in the official `mcp` SDK (`mcp>=2,<3`) and `anyio`. Enables `guanlan mcp` — see [MCP host](06-mcp-host.md).

> SDK **1.x is no longer supported** (P4.18 cut over to v2 / protocol `2026-07-28`). If your environment still pins 1.x, `guanlan mcp` says so explicitly. MCP clients pinned to older revisions are unaffected — a v2 server still serves the handshake-era revisions.

### IM host

```bash
pip install 'guanlan-wiki[im-weixin]'   # personal WeChat
pip install 'guanlan-wiki[im-feishu]'   # Feishu / Lark
pip install 'guanlan-wiki[im]'          # both
```

**Splitting this into two extras is deliberate**: someone who only uses WeChat should not have `lark-oapi` dragged in. The WeChat side pulls in `httpx` and `qrcode` (it draws the login QR code in your terminal); the Feishu side pulls in `lark-oapi>=1.6.8`. Enables `guanlan im` / `im-login` / `im-identify` — see [IM host](08-im-host.md).

> The Feishu floor of `1.6.8` is pinned hard: it is the first version whose long-connection client accepts `extra_ua_tags`, and **without that tag the server never pushes group @-mention events**. On an older version the adapter refuses to start and tells you to upgrade — better than connecting and silently receiving no group messages.

> Every extra **degrades gracefully**: without the dependency, `guanlan web` / `guanlan mcp` / `guanlan im` print a clear `pip install 'guanlan-wiki[...]'` hint instead of crashing. The IM probe checks **only the platform you asked for** — having `[im-weixin]` but not `lark-oapi` never blocks the WeChat route.

## Develop from source

The repo uses [`uv`](https://github.com/astral-sh/uv) for dependencies:

```bash
git clone <repo-url>
cd guanlan
uv run guanlan --help     # run the CLI in the project env
uv run pytest             # run tests
```

In dev mode the **repo root itself is a sample wiki**, and the maintenance engine `skills/guanlan-wiki/` is found via Agentao's repo-root skill discovery (`<working-dir>/skills/`) with **no install**. See "Two run modes" in the repo's [`CLAUDE.md`](../../../CLAUDE.md).

## Next

Once installed, see [Quickstart](02-quickstart.md) to build a knowledge base in a handful of commands.
