# guanlan/mcp — read-only MCP server (P4.10 / P4.17 / P4.18 / P4.24)

Loaded only when working under `guanlan/mcp/`. Design and decisions: `docs/P4.10-MCP宿主.md`, `docs/P4.17-MCP远程传输.md`, `docs/P4.18-MCP2.0迁移.md`, `docs/P4.24-MCP技能发布.md`.

- `guanlan mcp` is a read-only MCP **server** over stdio (default) or Streamable HTTP (`--transport http`, P4.17). It is distinct from DESIGN's reverse-direction "Tool 注入", where Agentao is the MCP *client* (that direction's Web diagnostics are P4.19, `guanlan/web/mcpdiag.py`).
- **SDK v2 only** (P4.18): the extra requires `mcp>=2,<3` (`MCPServer`, protocol `2026-07-28`). An env still holding `mcp` 1.x fails the import and the CLI says so — v1.x is upstream-maintenance-only. Old clients keep working: v2 still serves the handshake-era revisions on both transports.
- **Published skill location** (P4.24): `guanlan mcp` also publishes one static read-only skill, `guanlan-query`, through the `io.modelcontextprotocol/skills` extension. Its file lives in `guanlan/mcp/published_skills/`, deliberately *not* repo-root `skills/`, which is an Agentao discovery path. `read_page` takes an optional `name` so clients can follow `[[links]]`.
