# guanlan/im — IM host (P4.21)

Loaded only when working under `guanlan/im/`. Design and decisions: `docs/P4.21-IM宿主.md`, `docs/P4.22-IM可点引用.md`.

- **The core is platform-agnostic.** `guanlan/im/` dispatches only on `AdapterCaps`; anything platform-specific lives in `guanlan/im/adapters/<platform>.py`. Adding a platform = implement the `IMAdapter` Protocol + fill one `AdapterCaps` + one line in the adapter registry. `tests/test_im.py` asserts the core contains no `== "weixin"` / `== "feishu"` branch.
- **Extras are per platform on purpose** (`[im-weixin]`, `[im-feishu]`, `[im]` = both): one platform's user shouldn't pay for the other's SDK.
- **Feishu pins `lark-oapi>=1.6.8`**: the first version whose WS client accepts `extra_ua_tags`, **without which the server never pushes group-@ events**. The adapter probes the signature at startup and refuses to start rather than silently miss group messages. CI installs both im extras for exactly that regression.
- **Clickable citations reuse the text path** (P4.22): `/page <name>` is a zero-LLM read-only command on every platform; on Feishu a card-button tap is turned into a *synthesized inbound message*, so all five intake gates are reused and `server.py` / the Q&A path stay unchanged. The only source-aware rule is the read-only command allowlist (`intake.ACTION_COMMANDS`).
