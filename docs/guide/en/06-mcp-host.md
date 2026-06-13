# MCP host

`guanlan mcp` is an optional add-on layer: a **read-only MCP server** (stdio) that exposes the wiki's search/read-page/graph/health/Q&A as tools to any MCP client (Claude Code / Codex / Cursor …). It is the P4 host layer's **second transport** (stdio, alongside the Web host).

> Prerequisite: `pip install 'guanlan-wiki[mcp]'` (pulls in the official `mcp` SDK + anyio). Without it, `guanlan mcp` degrades gracefully with an install hint (exit code `1`).

## Start

You normally **don't run it by hand** — the calling Agent launches it as a subprocess. To verify manually:

```bash
guanlan -C my-wiki mcp                 # read-only MCP server over stdio
guanlan -C my-wiki mcp --model <id>    # override the model for the ask tool (ask only)
```

| Arg | Meaning |
|---|---|
| `--model` | Override the Agentao model for the `ask` tool (**`ask` only**; the other six tools are zero-LLM) |

## Register in a client

Register it as a stdio server in your MCP client config, e.g.:

```jsonc
{
  "mcpServers": {
    "guanlan": { "command": "guanlan", "args": ["-C", "my-wiki", "mcp"] }
  }
}
```

## The seven read-only tools

| Tool | LLM? | Meaning |
|---|---|---|
| `search` | no | Full-page recall (reuses the `guanlan search` kernel) |
| `read_page` | no | Read a wiki page (with path-traversal protection) |
| `list_pages` | no | List content pages |
| `graph` | no | Graph (nodes/edges/communities/topology stats) |
| `health` | no | Health report |
| `lint` | no | Lint report |
| `ask` | **yes** | Ask the knowledge base (reuses the CLI-query read-only subprocess path) |

The six zero-LLM tools reuse the same kernels as the Web read endpoints; `ask` runs the read-only subprocess (so the P4-8 embedding pitfalls don't apply).

## Design notes

- **Zero-write contract**: mirrors the `--reader` zero-byte KB-write posture — MCP **does not do convert** (writing `raw/` conflicts with the read-only posture).
- **No server-side session state**: MCP clients hold their own conversation, so the host needs no server-side session/conversation state (its biggest subtraction vs the Web host).
- **Opposite direction from DESIGN's "Tool injection"**: here GuānLán is the **MCP server** (exposing the wiki); DESIGN's tool injection is the reverse — **Agentao as MCP client** consuming external tools.
- It is the **local read-only precursor to E2** ("remote / scoped MCP").

See: repo [`docs/P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md).
