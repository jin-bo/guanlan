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

The core is the CLI; the Web and MCP hosts are **optional add-on layers** — all CLI commands work without them.

### Web host

```bash
pip install 'guanlan-wiki[web]'
```

Pulls in `fastapi` / `uvicorn` / `markdown` / `python-multipart` / `anyio`. Enables `guanlan web` — see [Web host](05-web-host.md).

### MCP host

```bash
pip install 'guanlan-wiki[mcp]'
```

Pulls in the official `mcp` SDK (`mcp>=1.27,<2`) and `anyio`. Enables `guanlan mcp` — see [MCP host](06-mcp-host.md).

> Both extras **degrade gracefully**: without the dependency, `guanlan web` / `guanlan mcp` print a clear `pip install 'guanlan-wiki[...]'` hint instead of crashing.

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
