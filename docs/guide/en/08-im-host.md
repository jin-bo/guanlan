# IM host (WeChat / Feishu)

> **Status: implemented** (P4.21; design and decisions in [`docs/P4.21-IM宿主.md`](../../P4.21-IM宿主.md)).
> WeChat QR login has been verified against the live service (2026-08-16) and the QR code is **drawn
> directly in your terminal**; the Feishu side still needs verification in each deployment.

`guanlan im` is an **optional post-MVP add-on layer**: it moves wiki Q&A into the IM you already live in.
Don't install it, don't start it — everything still works through the CLI and the Web host. Markdown
remains the single source of truth; IM is just **one more read-only entrance**.

## Three things to be clear about first

1. **Read-only, with no writable branch.** Not "read-only by default, switchable" — `ingest` / `heal` /
   upload / `/goal` are all absent, and there is no `--mode` flag at all. The reason goes beyond protecting
   the knowledge base: wiki content is **shared** across every authorized user, so the moment writing is
   allowed, whatever A ingests is instantly visible to B.
2. **No listening port whatsoever.** Both platforms use **outbound** connections (WeChat long-polling,
   Feishu WebSocket). That is a stronger posture than the Web host (which binds `127.0.0.1`): nothing is
   bound at all, not even loopback. No public IP, no reverse proxy, works fine behind NAT.
3. **Everyone is denied by default.** An empty allowlist **refuses to start** — there is no "just run it and
   answer whoever shows up" mode. This is the opposite of most IM bots' default, and it is deliberate: a wiki
   is a shared knowledge asset, and one misconfiguration means the whole base is readable by everyone.

## Which platform

| | Personal WeChat | Feishu / Lark |
|---|---|---|
| Per-message limit | **2000 chars** | 8000 chars |
| Waiting experience | Shows "typing…", sends the whole answer at once | Sends a placeholder first, then **edits it in place** into the answer (close to Web streaming) |
| Group chat | ❌ **Group messages never arrive** | ✅ |
| Identity strength | Weak (iLink bot identity, not bound to a real-world identity) | Strong (enterprise member directory) |
| Onboarding | Scan a QR code | Create an app in the open platform console |
| Operational weak spot | The login session expires and needs a **manual re-scan** | None |

**Bottom line**: just you, or a handful of people, wanting to ask questions from your phone → WeChat.
Shared across a team or department, used in group chats → Feishu.

> **On WeChat, `/search` should be your main tool.** 2000 chars cannot hold a full answer with citations, so
> it gets chopped into several messages fired at your phone; search results, by contrast, are naturally
> short, come back in milliseconds, and cost no tokens. Save LLM Q&A for when you genuinely need synthesis.

## Prerequisites

```bash
pip install 'guanlan-wiki[im-weixin]'   # WeChat only
pip install 'guanlan-wiki[im-feishu]'   # Feishu only
pip install 'guanlan-wiki[im]'          # both
```

**Splitting the extras per platform is deliberate** — someone who only uses WeChat should not be forced to
install the Feishu SDK. Without the matching extra the command degrades gracefully with an install hint
(exit code `1`).

You also need **a machine that stays on**: both transports require the process to remain online, and a
laptop lid closing drops the connection. A small home server / NAS / always-on workstation all work.

---

## Route A: personal WeChat

### Three things it cannot do (read this before configuring anything)

1. **Group messages never arrive.** Scanning gives you an **iLink bot identity** (`xxx@im.bot`), not your own
   WeChat account. Tencent generally does not deliver ordinary WeChat group events to such identities,
   @-mentions included. So the WeChat route is **direct messages only**.
2. **Identities cannot be looked up.** A `wxid_…` is stable, but it cannot be enumerated and cannot be traced
   back to a person. The allowlist can only be built by hand via `im-identify`. That is a one-off cost among a
   few people, and unworkable for a 200-person organization.
3. **The login session expires.** When it does, the host logs an explicit ERROR and you have to re-run the
   QR login.

### Steps

```bash
# ① QR login (credentials land in ~/.guanlan/im/weixin/account.json, mode 0600)
# The QR code is drawn **directly in the terminal** — scan the screen with your phone,
# no need to open any link.
guanlan im-login --platform weixin

# ② Open a 5-minute window to collect the full IDs of the people you want to authorize —
#    have each of them send one message
guanlan im-identify --platform weixin

# ③ Start the host
guanlan -C ~/my-wiki im --platform weixin \
    --allow-user wxid_8f3k2m9qp0zx \
    --allow-user wxid_2b7d4e1a9c0f
```

Paste `im-identify`'s output straight into `--allow-user`. The CLI's own output is Chinese
(`用户ID` = user ID, `会话ID` = chat ID, `聊天类型` = chat type):

```
[im-identify] 监听中，300s 后自动退出（不会回复任何消息，Ctrl-C 提前结束）

[1] 平台=weixin  租户=wx_acct_a1b2
    用户ID=wxid_8f3k2m9qp0zx        ← goes into --allow-user
    会话ID=（单聊，无需 --allow-chat）
    聊天类型=dm
```

> **While `im-identify` is running the bot never replies to anything** — the sender only sees "no one
> answered". That is intentional: replying "you are not authorized" tells an unauthorized stranger that a
> knowledge-base bot lives here.

---

## Route B: Feishu / Lark

### Server-side configuration (open platform console)

**① Create the app**

| Item | Value |
|---|---|
| App type | **Custom app for your enterprise** |
| Console | Feishu `open.feishu.cn` / Lark `open.larksuite.com` |
| Capability to enable | **Bot** |
| What to copy | **App ID** and **App Secret** from the "Credentials & Basic Info" page |

**② Permissions — GuānLán needs exactly two**

| Scope | Purpose |
|---|---|
| `im:message` | Receive messages; edit an already-sent message (used to rewrite the answer in place) |
| `im:message:send_as_bot` | Send messages as the bot |

Every other scope that IM-bot tutorials commonly ask for is **not needed**: no media handling, so no
`im:resource`; no chat lookups, so no `im:chat*`; no emoji reactions, so no `im:message.reactions*`;
exact matching on the `open_id` carried in the event itself, so no `contact:user.id:readonly`; the bot
identity probe uses `/open-apis/bot/v3/info`, which needs no extra permission, so **no
`admin:app.info:readonly` either** (admin-class scopes usually need a higher tier of approval in most
enterprises — skip what you can).

> When you tick the events, the console tells you which scope each one requires (Feishu subdivides message
> reception into finer permissions such as DM vs. group @; `im:message` is the parent that covers them) —
> **trust the console's prompt**.

**③ Events and callbacks**

1. Choose **long connection (WebSocket)** as the delivery mode; do **not** configure a webhook URL.
   (GuānLán only implements WS mode: a webhook needs a publicly reachable inbound endpoint, which
   contradicts the "no listening port" red line.)
2. Subscribe to exactly one event: **`im.message.receive_v1`** (receive message).

**④ Publish and wait for approval ← the biggest trap**

Go to "Version Management & Release", **create a version and publish it**; a custom enterprise app usually
requires **admin approval**.

**Permissions do not take effect until the release is approved**, and the symptom is: the connection comes
up, the logs look perfectly normal, and **no message ever arrives**. This is the single most common
"looks configured but does nothing" case in Feishu onboarding — check the version status first.

**⑤ Group chat needs one more step**: **add the bot to the target group**. If it isn't in the group, it
receives no events at all.

### Steps

```bash
export GUANLAN_IM_FEISHU_APP_ID=cli_xxxxxxxxxxxx
export GUANLAN_IM_FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
export GUANLAN_IM_FEISHU_DOMAIN=feishu        # use lark for Lark (international); default is feishu

guanlan im-identify --platform feishu         # have someone DM the bot, or @ it in a group
guanlan -C ~/my-wiki im --platform feishu \
    --allow-chat oc_5d2f8a1c9e \
    --allow-user ou_9c4e1a7b2d8f5g3h
```

For a group message, `im-identify` prints both the user ID and the chat ID:

```
[2] 平台=feishu  租户=abcdef123456
    用户ID=ou_9c4e1a7b2d8f5g3h        ← goes into --allow-user
    会话ID=oc_5d2f8a1c9e            ← goes into --allow-chat (group chats only)
    聊天类型=group   @了机器人=是
```

`bot_open_id` **does not need to be configured** — it is probed automatically at startup.

---

## What a successful start looks like

Both routes are the same: once connected, the terminal prints a banner and then waits. **No banner means it
did not connect** — see [Troubleshooting](#troubleshooting).

```
[guanlan im] 已连上 feishu，等待消息中（Ctrl-C 停服）
  知识库  /Users/me/my-wiki（只读）
  白名单  用户 3 人 · 群 1 个
  外部 MCP 已启用（单次请求上限 120s）
```

The whitelist line is there so you can check **that your configuration actually loaded** — especially when
the list comes from an environment variable via `--allow-user-env`: a wrong count means the variable was not
read. It prints **counts only, never IDs**: the host is a long-running process whose output is often
redirected into a log file, and IDs do not belong there. Use `guanlan im-identify` to see full IDs.

---

## Who may ask: the authorization truth table

| Scenario | Passes when |
|---|---|
| DM | `--allow-all-users` **OR** sender ∈ `--allow-user` |
| Group | chat ∈ `--allow-chat` **AND** (`--allow-all-users` **OR** sender ∈ `--allow-user`) **AND** the bot was @-mentioned |

**For groups it is AND, not OR, and not "check the chat only".** Allowlisting the group without checking the
person outsources authorization to the group owner — they add one person and the entire base is open to that
new member. So in the group case you must supply **both** `--allow-chat` and `--allow-user`.

**`--allow-all-users` opens up "people", never "chats".** It means "I don't want to list the people in these
chats one by one" — it does **not** mean "the bot may be in any chat". So even with it on, **a chat that is
not in `--allow-chat` is still ignored entirely**. No single flag opens all chats at once, and that is
deliberate: which room the bot is placed in is the operator's decision, and an "allow all users" switch
should not punch through it.

ID matching is **exact and case-sensitive** — paste `im-identify`'s output verbatim, don't retype it.

---

## The three commands are mutually exclusive: adding a person means stopping the host

`im`, `im-identify` and `im-login` **cannot run at the same time** — they contend for the same platform
credential (WeChat allows only one long-polling instance per token; two connections under the same Feishu
`app_id` make messages arrive at random). Losing the race is a startup refusal that tells you which process
holds it (pid + subcommand name).

**Full flow for adding one person to the allowlist**:

```bash
# ① Stop the host (Ctrl-C) — required first, or ② cannot take the credential
# ② Open the collection window
guanlan im-identify --platform feishu --seconds 300
# ③ Append the new ID to the startup flags
# ④ Start the host again
guanlan -C ~/my-wiki im --platform feishu --allow-chat oc_… --allow-user ou_alice --allow-user ou_newcomer
```

**"Adding a person requires stopping" is the unavoidable price of "never reply to an unauthorized sender"**:
either you stay open, collecting IDs while replying to strangers, or you close down to collect. GuānLán
picks the latter. To keep changes in one place, use `--allow-user-env` to hold the list in an environment
variable — but **a restart is still required** (there is no allowlist hot-reload).

---

## Using it inside IM

Three slash commands, **all zero-LLM: no conversation is created, no tokens are spent**:

| Command | Effect |
|---|---|
| `/search <keywords>` | Retrieval; returns titles + snippets + page paths in milliseconds |
| `/new` | Clear the current conversation context and start over |
| `/help` | Usage |

Anything without a slash is **LLM Q&A** (multi-turn, remembers the context). **In a group you must @ the
bot**; `@bot /search entity-a` is recognized too.

**Answer shape**:

- Long answers are **sent as several consecutive messages** (with a small gap between parts to avoid rate
  limiting). When the cap is exceeded (5 parts on WeChat, 3 on Feishu) **the last part is an explicit
  truncation notice** telling you how many characters remain — nothing is ever silently truncated; you always
  see it. The notice asks you to **re-ask on the Web host**: IM conversations are not persisted, so the
  truncated part exists nowhere, and there is no "full version" link to give you (one would not open anyway).
- ` ``` ` fenced blocks (code / mermaid / flint charts) **keep their source verbatim**: there is no renderer
  in IM, but nothing is silently dropped — you can view the rendered form on the Web host.
- `[[wikilink]]` is **always kept as-is**; no link is fabricated, because it carries readable reference
  semantics. **Even with `--web-base-url` it is not turned into a link**: the Web host has no "open the page
  with this name" route, so any URL assembled from a page name 404s, and **a dead link misleads more than a
  readable `[[entity-a]]` does**. `--web-base-url` exists to attach a **site entrance** to the truncation
  notice.
- **Attachments are not processed**: sending an image or file gets a fixed hint asking you to paste the key
  points as text.
- When **the model produced no content at all** (an empty tool round, say), the host says so explicitly
  instead of leaving the "🔍 searching the knowledge base…" placeholder sitting there forever.

**Restarting the process discards all conversation context** (conversations are never written to disk — the
price of "zero bytes written to the knowledge base"). A conversation idle for 30 minutes is also reclaimed,
and the next message will **usually** be prefixed with "the context expired, a new conversation has started".
"Usually" because that notice relies on a bounded record (100 entries by default) — if you have been quiet
for a long time and your entry has been pushed out by newer ones, it simply starts fresh with no notice.
Either way, **the context has already been reset**.

**Shutdown**: after `Ctrl-C` the host **does not exit immediately** — it first asks the in-flight Q&A to
stop, then **waits until that round genuinely finishes** before closing the connection (telling you every 30
seconds who it is still waiting for). This is deliberate: the LLM round runs on a background thread, and
force-cancelling it would only make the process *look* cleanly exited while the thread keeps running. A round
aborted this way **does not send the user an "error" message** — a shutdown is not a failure, so the message
simply stops there.

**In rare cases the first `Ctrl-C` waits a long time**: a model round has no hard upper bound on total
duration (as long as the server keeps sending data the connection never times out), and the abort can only
take effect when it emits its next chunk. If you can't wait, **press `Ctrl-C` again**.

**A second `Ctrl-C` forces an immediate exit** (exit code `5`): **no cleanup at all, the logs may be
incomplete, and even the "background threads may still be running" line is only best-effort — if it cannot be
written, the process exits anyway.** Everything yields to "it exits no matter what". It exists because if a
model request truly hangs, an ordinary exit would itself **hang on exiting** (background threads are joined
at process exit).

---

## Flags

```bash
guanlan -C <base> im --platform {weixin,feishu} [options]
```

| Flag | Default | Meaning |
|---|---|---|
| `--platform` | **required** | `weixin` / `feishu`; no default (the two differ too much in capability) |
| `--allow-user` | — | An authorized user; **repeatable** |
| `--allow-user-env` | — | Read a comma-separated list from an environment variable; unioned with the flag above |
| `--allow-chat` | — | Group allowlist, **repeatable**; omitting it means group messages are always ignored. **Giving only this and no user → startup refusal** (not one message could pass the gates) |
| `--allow-all-users` | off | Bypasses **only** the user list, **not** `--allow-chat`; the startup log warns loudly |
| `--web-base-url` | — | The Web **site entrance**, attached to truncation notices. Does **not** turn `[[links]]` into URLs (the correct address cannot be assembled — see above) |
| `--model` | — | Override the model used for Q&A |
| `--max-conversations` | `100` | Hard cap on in-memory conversations |
| `--idle-ttl` | `1800` | Seconds before an idle conversation is reclaimed; must be a **finite positive number** (`0` would make every round count as expired) |
| `--mcp-request-timeout` | `120` | Upper bound, in seconds, on **waiting for one `tools/call` response** from an external MCP tool. **It is not a bound on one tool execution**, and it does not even cover a full request: the step that **sends** the request is outside this timer; the handshake and other phases have their own timeouts that this value does not govern; and for SSE/stdio, connection setup and disconnect cleanup have **no timeout protection at all**. This is a **host-level ceiling**: a longer value configured in `mcp.json` is clamped down to it (with a log warning). Raise it here if you need longer. Must be positive |
| `--no-mcp` | off | Do not load the external MCP servers configured for this base at all (answer from the knowledge base alone) |

```bash
guanlan im-login    --platform weixin          # QR login; does not accept -C
guanlan im-identify --platform {weixin,feishu} [--seconds 300]   # collect IDs; does not accept -C
```

**Environment variables**: `GUANLAN_IM_FEISHU_APP_ID` / `_APP_SECRET` / `_DOMAIN` (`feishu` | `lark`,
default `feishu`). WeChat credentials are written to disk by `im-login` and never come from environment
variables. Credentials are **never** accepted as plaintext on the command line and never appear in logs.

**These variables can live in `.env`** (the same file, and the same rules, as your model API key):

```dotenv
# .env — searched for from the current directory upwards; the same file agentao reads model keys from
GUANLAN_IM_FEISHU_APP_ID=cli_xxxxxxxxxxxx
GUANLAN_IM_FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
GUANLAN_IM_FEISHU_DOMAIN=feishu
GUANLAN_IM_USERS=ou_9c4e1a7b2d8f5g3h,ou_2b7d4e1a9c0f     # for use with --allow-user-env
```

Three things to remember:

1. **Real environment variables win**: an `export`ed value **overrides** `.env` (no-override semantics). If
   you changed an `export` and it didn't take effect, the cause is almost certainly the opposite of what you
   suspect — a stale value in `.env` can never quietly shadow the one you just set.
2. **The lookup walks up from the current working directory**, rather than being "the `.env` in the knowledge
   base root". `im-identify` does not accept `-C` and follows the same rule.
3. **Never commit `.env`** — this repository's `.gitignore` already covers `.env` / `.env.*`; check for
   yourself when you put one in a different repository.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Refuses to start, says the allowlist is empty | Run `guanlan im-identify` first to get IDs, then fill in `--allow-user` |
| Refuses to start, says the credential is in use | The three commands are mutually exclusive. The error carries the pid and subcommand name — stop it, or wait for `im-identify` to time out |
| Refuses to start, says `--allow-chat` will have no effect | WeChat never receives group messages. Drop the flag, or switch to Feishu |
| Refuses to start, says "only `--allow-chat` was given" | Group access is chat **AND** person **AND** @-mention. Add `--allow-user`, or use `--allow-all-users` to mean "anyone in these chats may ask" |
| Refuses to start, says the credential lock "cannot be read" | Rare: another process is creating the lock, or the lock file is corrupt. After confirming no other `guanlan im*` is running, delete `~/.guanlan/im/<platform>/credential.lock` by hand |
| Feishu: everyone is ignored, suddenly, mid-run | The WS thread died. The host logs an ERROR and exits with code `5` (it does not pretend to be alive) — check whether the credential was revoked or the network dropped |
| Replies with "the conversation limit is full" | `--max-conversations` (100 by default) conversations are **simultaneously active**. Wait for idle ones to be reclaimed, or raise the value |
| One round never answers and `Ctrl-C` won't stop it | Most likely stuck in an external MCP tool. Tighten the per-request bound with `--mcp-request-timeout`, or use `--no-mcp` to confirm that is the cause. Note that connection setup and disconnect cleanup for SSE/stdio have no timeout protection, so tightening this value does not guarantee it will ever return — if you can't wait, press `Ctrl-C` again |
| Everyone is ignored and the log says `入站流已中断` (inbound stream interrupted) | The connection or login is no longer valid; the process exits with code `5`. WeChat: re-run `im-login`. Feishu: check app status and network |
| **Feishu: startup reports "WS 首次连接失败" / "在 30 秒内没有连上"** | The long connection never came up, and the host **refuses to pretend it started**. Three common causes: no network route, wrong `app_id`/`app_secret`, or an empty local TLS trust store (python.org Python on macOS needs `Install Certificates.command` run once, otherwise you get `CERTIFICATE_VERIFY_FAILED`). The `[Lark] ... connect failed` lines just above carry the server's own words |
| **Feishu: connected but no messages ever arrive** | **Almost certainly the app version is unpublished or not yet approved** — permissions do not take effect before that. Next, check that `im.message.receive_v1` is subscribed |
| Feishu: DMs work, @-mentions in a group do nothing | Two independent causes, check separately: ① the bot **was never added to the group**; ② `lark-oapi` is older than `1.6.8` (older versions never receive group @ events; the host says so explicitly at startup) |
| Feishu: cannot connect to Lark international | `GUANLAN_IM_FEISHU_DOMAIN=lark` |
| WeChat: the log reports an invalid session | Re-run `guanlan im-login --platform weixin` and scan again |
| WeChat: an occasional reply fails to send | Normal; the host retries automatically. Only investigate if it keeps failing |
| `--allow-all-users` is on but the group is still ignored | Working as intended: that flag does not open the chat allowlist. Add the chat ID to `--allow-chat` |
| An unauthorized person sends a message and the bot does nothing at all | **By design**, not a fault |
| The answer is split across several messages | It exceeded the per-message limit. WeChat's 2000 chars is easy to hit — use `/search`, or read it on the Web host |

---

## Explicitly out of scope

Writing to the knowledge base (in any form), media send/receive, proactive push or scheduled broadcasts,
`/goal` long-running task resumption, user management and multi-tenancy, and serving two platforms from one
process (run two processes if you need both).

Feishu's webhook mode and WeCom's callback mode are also out of scope — they require a publicly reachable
inbound endpoint, which conflicts with "no listening port".

---

← [MCP host](06-mcp-host.md) ｜ [Convert](07-convert.md) ｜ [Back to index](../README.md)
