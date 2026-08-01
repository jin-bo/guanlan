# Web host

`guanlan web` is an **optional add-on layer after the MVP**: it puts the CLI commands in a browser. Without installing or starting it, everything still runs from the CLI; markdown remains the sole source of truth, and the Web is just another entry point for ingest/Q&A and a read-only browser for the wiki.

> Prerequisite: `pip install 'guanlan-wiki[web]'` (pulls in fastapi / uvicorn / markdown / python-multipart / anyio). Without it, `guanlan web` degrades gracefully with an install hint (exit code `1`).

## Start

```bash
guanlan -C my-wiki web                          # default 127.0.0.1:8765, opens a browser
guanlan -C my-wiki web --port 9000 --no-browser # different port / no browser
```

| Arg | Default | Meaning |
|---|---|---|
| `--port` | `8765` | Listen port (**127.0.0.1 only**) |
| `--no-browser` | — | Don't auto-open a browser on start |
| `--model` | — | Override the Agentao model (passed to write jobs and sessions) |
| `--reader` | off | Read-only multi-conversation deployment (see below) |
| `--agent-log` / `--no-agent-log` | on for non-reader / off for reader | Whether to write session agent logs to `<kb>/agentao.log` |
| `--max-conversations` | `100` | In-memory conversation hard cap (must be ≥ 1) |
| `--no-session-persist` | off (persists by default) | Don't persist read-only chat sessions to `<kb>/.agentao/sessions/`; off ⇒ memory-only (privacy/ephemeral) |
| `--mode` | `read-only` | Opening posture for new sessions; `workspace-write` lets the Agent write `wiki`/`workspace` from the start, switchable in-browser via `/mode` |
| `--confirm` | `ask` | Under workspace-write, whether ASK operations (operator/piped shell, confirmation-required tools) are **confirmed by a human**; `auto` keeps silent auto-approve (see "Tool confirmation") |
| `--confirm-timeout` | `120` | Seconds to wait for the user's confirm/answer; no answer ⇒ **deny by default** |

## What it does

- **Browse the wiki**, click `[[wikilink]]` to navigate
- Run **check·health·lint** reports, view the **graph**
- Pick a source from `raw/` and **trigger ingest** (single worker, serialized, polled)
- **Read-only multi-turn chat** with the agent (token-streamed)
- **Full-text search** (`/api/search`), debounced recall from the input box
- **Feed / upload / promote**: paste-to-save (`POST /api/raw`), upload files to a staging area, parse → human review → promote into a `raw/` source
- **Backfill** (`query --backfill`): persist Q&A into the wiki (gated)
- **Semantic audit** (`audit`): re-review drifted sources whose `raw/` changed but wiki wasn't re-synthesized (top-bar "Audit" button: preview drifted-source groups → review in one click → poll the structured receipt; gated)
- **Slash commands + read-only introspection**: `/status` `/context` `/skills` `/tools` `/mode`, stop button
- **Writable work-session** `/mode workspace-write`: the Agent may write `workspace/` (`raw/` stays hard read-only), with a three-layer write guard + single-writer + undo
- **External MCP config diagnostics** (top-bar "MCP" button): see which external MCP servers this KB actually gets injected with, and run one explicit connection check (read-only; executes no external tool)
- **Bilingual UI**: a top-right 中文 ⇄ English toggle (pure-frontend i18n; only translates interface chrome — wiki content / agent answers / report bodies stay in their source language)

## Rich rendering (in-browser, pure frontend)

A few kinds of markup in wiki pages and chat answers render into rich output in the browser — **markdown stays the sole source of truth**; rendering is an overlay enhancement, and the CLI / plain-text fallback still shows honest source:

- **mermaid diagrams**: ` ```mermaid ` fenced blocks → flow / sequence / class / state diagrams (P4.13)
- **Math**: `$…$` / `$$…$$` / `\(…\)` / `\[…\]` → KaTeX typesetting (P4.14)
- **Chemistry**: mhchem `\ce{}` / `\pu{}`, **must be inside math delimiters** (e.g. `$\ce{2H2 + O2 -> 2H2O}$`); a bare `\ce{}` stays literal (P4.14)
- **Code highlighting**: language-fenced blocks like ` ```python ` → syntax highlighting (highlight.js common, ~36 languages; uncovered languages stay plain text) (P4.14)

The renderers are all **vendored, bundled, non-CDN, offline-capable**, and **lazy-loaded** (pages without such content load nothing); any load/syntax failure **keeps the source** and never blanks the page. Security-wise: KaTeX `trust:false` (blocks `\href`/`\html*`), mermaid `securityLevel:'strict'`, highlight is fed escaped text — **products are not assumed unconditionally trusted**; trust boundaries are in the per-phase design docs. CLI / MCP text channels do not render (they return literal source).

> **Copy raw Markdown**: each answer bubble has a clipboard icon in its bottom-right corner; clicking it copies that turn's **markdown source** (not the rendered text — formulas / code / `[[links]]` paste back verbatim) to the clipboard, with a brief "Copied" confirmation.

## Tool confirmation / human-in-the-loop (writable sessions, P4.15)

In a writable session (`workspace-write`), whenever the Agent wants to run an **operator/piped shell command** or a tool **marked "needs confirmation"** (what agentao decides is `ASK`), it is no longer silently approved — a confirmation request **pops in the browser**: the bubble **shows the full command verbatim** (displayed literally, never rendered or executed) and you click:

- **Allow** — approve this one tool only;
- **Auto-allow this session** — approve this one and stop asking for the rest of this session (reversible: click "Restore per-call confirm");
- **Deny** — this one doesn't run (the Agent gets a "tool denied" and continues this turn, rerouting on its own).

Not clicking ⇒ **deny by default** after `--confirm-timeout` (120s); hitting "Stop" or closing the tab (disconnect) also denies — so walking away never pins the write lock forever. The model can also **ask you a question** through the same channel (with options / free text); you fill in an answer that's sent back.

**Key boundary**: "Allow" **≠** "bypass the read-only wall". Confirmation only decides whether one ASK tool **runs**; it opens **no** new write path for the Agent — `raw/` and `AGENTAO.md` stay read-only via the deterministic write guards (layers ①②). Even after you allow a shell command, if it then tries to write `raw/` it is still refused. "Auto-allow this session" likewise only loosens "ask or not" — the **posture stays workspace-write and every write guard stays in place**; it is **not** CLI-style full-access.

For batch maintenance where you don't want per-call prompts: start with `--confirm auto` (equivalent to the old silent auto-approve), or click "Auto-allow this session" in the session.

## External MCP config diagnostics (top-bar "MCP" button, P4.19)

If you have MCP servers configured in `<kb>/.agentao/mcp.json` or `~/.agentao/mcp.json`, **those external tools are already being injected into every ingest / query / heal / audit / web chat** — that is agentao's default (with no `mcp_registry=` passed, it discovers from exactly those two files), and until now nothing in guanlan showed it. The "MCP" button adds that **visibility**:

- lists the external servers **this KB actually gets injected with**: name / scope (project ⇄ user) / transport / endpoint / whether `trust` is set;
- click "**Check connections**" to connect once, explicitly, and see which servers answer and what tools + annotations they report.

Three boundaries worth remembering:

- **When it applies**: the panel shows "the **parsed result of the config on disk right now**; a new web session — or the next CLI job — will use it. **An existing session only picks it up after you start a new one**." MCP is loaded once when a session is constructed, so editing `mcp.json` does **not** change the session you're currently in.
- **It is not a gate**: it reflects configuration and connectivity only; it does **not** certify that these servers are safe, read-only or trustworthy. This phase adds no admission / allow-list / policy and **changes no injection behavior**. The check itself only runs `initialize` + `tools/list` and **executes no tool**.
- **No automatic probing**: connecting really spawns stdio subprocesses / makes network requests, so it happens only when you click; only one check runs at a time (a concurrent click gets "a check is already running"). Tokens, `headers`, `env` and URL credentials are never exposed — in endpoints or in error text.

The connection check needs `pip install 'guanlan-wiki[mcp]'` (that extra carries the MCP client stack); without it **only the check button reports the missing dependency** — the config listing still works. Under `--reader` both endpoints are **not registered** and the button is hidden — not because of "KB writes", but because a connection check **has external side effects** (requests, subprocesses).

> Direction check: this diagnoses **other people's MCP servers being injected into guanlan**. The reverse — exposing guanlan's own wiki as a read-only MCP server to Claude Code / Codex / Cursor — is [`guanlan mcp`](06-mcp-host.md).

## Read/write split

- The only write job `ingest` (and heal/backfill/audit/raw-write) reuses the **P2 subprocess + single-writer gate** (one background worker, FIFO).
- All Q&A (one-shot + multi-turn) goes through a **read-only in-process embedded Agentao** (read-only by default, no gate, memory-only).

## Read-only multi-conversation deployment `--reader`

```bash
guanlan -C my-wiki web --reader
```

Opens the single-user host as a **read-only multi-user deployment**:

- **Registers no write routes** (raw/upload/ingest/heal/backfill/audit/workspace-delete/graph-rebuild/undo → 404/405)
- **Internally forces** `session_persist=False` + `mode=read-only` (zero-write for any caller)
- **Zero-byte KB writes by default** (persistence off, agent_log off)
- Conversation isolation rides the existing 122-bit capability UUID (`?c=<conversation_id>`): closing the enumeration endpoint makes others' ids undiscoverable (capability-URL model)
- Comes with reader-only idle reclaim (idle-TTL eviction of stale conversations); `--max-conversations` is tunable

## ⚠️ Security

**Single-user, local only.** Always `workers=1` + listens on `127.0.0.1` only. **Never expose the port to a network** — there is no account/auth; `--reader` isolation is only the capability-URL model (honest threat boundary in the design docs), not access control.

See: repo [`docs/P4-Web宿主.md`](../../P4-Web宿主.md) and the `P4.x` docs ([P4.1](../../P4.1-Web投喂.md) / [P4.5](../../P4.5-可写Web工作会话.md) / [P4.6](../../P4.6-Web上传与晋级.md) / [P4.9](../../P4.9-只读多会话.md) / [P4.13](../../P4.13-Web-mermaid渲染.md) / [P4.14](../../P4.14-Web数学化学代码渲染.md) / [P4.15](../../P4.15-Web工具确认.md) / [P4.19](../../P4.19-Web-MCP诊断.md), etc.).
