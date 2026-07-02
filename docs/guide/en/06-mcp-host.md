# MCP host

`guanlan mcp` is an optional add-on layer: a **read-only MCP server** that exposes the wiki's search/read-page/graph/health/Q&A as tools to any MCP client (Claude Code / Codex / Cursor …). It is the P4 host layer's **second transport**, over two channels: **stdio** (default, launched by the caller as a subprocess) and **Streamable HTTP** (`--transport http`, cross-process / cross-machine).

> Prerequisite: `pip install 'guanlan-wiki[mcp]'` (pulls in the official `mcp` SDK + anyio; the uvicorn/starlette stack that HTTP needs ships with the SDK — no extra dependency). Without it, `guanlan mcp` degrades gracefully with an install hint (exit code `1`).

## Start (stdio, default)

You normally **don't run it by hand** — the calling Agent launches it as a subprocess. To verify manually:

```bash
guanlan -C my-wiki mcp                     # read-only MCP server over stdio (default)
guanlan -C my-wiki mcp --transport stdio   # equivalent explicit form
guanlan -C my-wiki mcp --model <id>        # override the model for the ask tool (ask only)
```

## Start (HTTP, cross-process / cross-machine)

`--transport http` starts the Streamable HTTP transport, binding `127.0.0.1:8766` by default, so MCP clients in **another process / on another machine** can connect:

```bash
# Same machine, cross-process (loopback, no token)
guanlan -C my-wiki mcp --transport http                 # binds 127.0.0.1:8766
guanlan -C my-wiki mcp --transport http --port 9000

# Cross-machine (non-loopback: token required + declare the external Host)
GUANLAN_MCP_TOKEN=<your-secret> \
guanlan -C my-wiki mcp --transport http \
  --host 0.0.0.0 --allowed-host kb.example.internal \
  --auth-token-env GUANLAN_MCP_TOKEN
```

| Arg | Default | Meaning |
|---|---|---|
| `--transport {stdio,http}` | `stdio` | Transport channel; omitting it means stdio, byte-for-byte the old behavior |
| `--host` | `127.0.0.1` | HTTP bind address; non-loopback **requires** `--auth-token-env` |
| `--port` | `8766` | HTTP port (offset from the Web host's 8765; must be 1–65535) |
| `--auth-token-env ENVVAR` | — | Read the bearer token from this env var (**never** CLI plaintext / on disk) |
| `--allowed-host HOST[:PORT]` | — | Extra allowed `Host` header (repeatable); a reverse-proxy's external domain must be added explicitly |
| `--allow-ask` | off | Over HTTP, explicitly expose the expensive `ask` tool (stdio always exposes it) |
| `--model` | — | Override the Agentao model for the `ask` tool (**`ask` only**) |

### HTTP safe defaults (the red lines to remember)

- **Binds only `127.0.0.1` by default**: same posture as the Web host — loopback is the trust boundary.
- **Non-loopback requires a token**: when `--host` is non-loopback (e.g. `0.0.0.0`), you **must** supply a bearer token via `--auth-token-env`, or it **refuses to start** — never expose an unauthenticated wiki on the network. The token is read only from the env var (blank / unset is rejected).
- **Wildcard binds must declare the external Host**: when binding `0.0.0.0`/`::`, you must name the domain / IP clients use via `--allowed-host`, otherwise DNS-rebinding protection rejects every remote request (this case refuses to start with a hint, rather than being silently unreachable).
- **`ask` is off the network by default**: over HTTP only the **six zero-LLM tools** are exposed; `ask` spawns a paid LLM subprocess (a cost / DoS surface), so it takes an explicit `--allow-ask`.
- **Stateless**: HTTP runs in stateless mode (no `Mcp-Session-Id`, no event replay).
- **TLS is external**: the server itself only speaks plaintext HTTP; for cross-machine encryption put a reverse proxy (caddy/nginx) or SSH tunnel in front to terminate TLS and forward to `127.0.0.1:8766`.

## Register in a client

**stdio** — register it as a stdio server:

```jsonc
{ "mcpServers": {
    "guanlan": { "command": "guanlan", "args": ["-C", "my-wiki", "mcp"] }
} }
```

**HTTP (same machine, no token):**

```jsonc
{ "mcpServers": {
    "guanlan-http": { "type": "streamable-http", "url": "http://127.0.0.1:8766/mcp" }
} }
```

**HTTP (cross-machine, via reverse proxy + token):**

```jsonc
{ "mcpServers": {
    "guanlan-remote": {
      "type": "streamable-http",
      "url": "https://kb.example.internal/mcp",
      "headers": { "Authorization": "Bearer ${GUANLAN_MCP_TOKEN}" }
} } }
```

On the server side, put caddy/nginx in front to terminate TLS and forward to `127.0.0.1:8766`. `--allowed-host kb.example.internal` **can't be omitted** — a reverse proxy typically passes through the original `Host: kb.example.internal`, and without it in the allowlist DNS-rebinding protection rejects the request.

## The read-only tools

| Tool | LLM? | Meaning |
|---|---|---|
| `search` | no | Full-page recall (reuses the `guanlan search` kernel) |
| `read_page` | no | Read a wiki page (with path-traversal protection) |
| `list_pages` | no | List content pages |
| `graph` | no | Graph (nodes/edges/communities/topology stats) |
| `health` | no | Health report |
| `lint` | no | Lint report |
| `ask` | **yes** | Ask the knowledge base (reuses the CLI-query read-only subprocess path) |

> **stdio exposes all seven**; **HTTP exposes only the six zero-LLM tools by default** — `ask` appears only with `--allow-ask`. The six zero-LLM tools reuse the same kernels as the Web read endpoints; `ask` runs the read-only subprocess (so the P4-8 embedding pitfalls don't apply).

## Design notes

- **Zero-write contract**: mirrors the `--reader` zero-byte KB-write posture — MCP **does not do convert** (writing `raw/` conflicts with the read-only posture).
- **No server-side session state**: MCP clients hold their own conversation, so the host needs no server-side session/conversation state (its biggest subtraction vs the Web host).
- **Opposite direction from DESIGN's "Tool injection"**: here GuānLán is the **MCP server** (exposing the wiki); DESIGN's tool injection is the reverse — **Agentao as MCP client** consuming external tools.
- **HTTP's network trust boundary ≠ the injection trust boundary**: `--auth-token-env`/`--allowed-host` govern "who may connect, on which address" — an orthogonal trust line from P4.11's prompt-injection defense; neither substitutes for the other.
- It is the **precursor to E2** ("remote / scoped MCP"); full OAuth / multi-tenant source-level scoping is left to E2.

See: repo [`docs/P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md), [`docs/P4.17-MCP远程传输.md`](../../P4.17-MCP远程传输.md).
