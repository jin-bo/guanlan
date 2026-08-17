"""出站投递编排：一轮问答的生命周期、能力档位分派、单飞、停机（P4.21 §4.2/§4.4/§4.6/§6）。

IM 的「秒回期待」 vs LLM 的分钟级时延，是本宿主相对 Web/MCP 的**唯一新问题**：IM 里发出去
30 秒没动静，用户会**再发一遍**（新 msg_id，去重救不了）。故按能力位分四档（v1 用前两档）：

| 条件 | 策略 | v1 覆盖 |
|---|---|---|
| `supports_edit` | 发一条「🔍 正在查阅…」→ **原地改写**（节流）→ 末次 `finalize` | 飞书 |
| `supports_typing and not supports_edit` | typing on → 跑 → off → 分片发答案 | 个人微信 |
| 都无，但 `supports_late_push` | 占位消息 + 追答 | 企业微信单聊（未实现） |
| `not supports_late_push` | 群内只开零 LLM 命令 | 企业微信群（未实现） |
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import anyio.to_thread
from agentao.cancellation import AgentCancelledError

from ..search import CorpusCache, search_result_dict
from .contract import Action, IMAdapter, OutboundRef
from .intake import Ready
from .pageview import filter_resolvable, open_page, resolve_many, validate_page_arg
from .reply import (
    extract_wikilinks,
    page_truncation_notice,
    split_for,
    to_im_markdown,
    truncation_notice,
)
from .session import AcquireStatus, SessionRegistry

_logger = logging.getLogger("guanlan.im")

# edit 档的节流参数——**归核心持有，不下放适配器**（§6.2）。两者都满足才发一次中间 edit。
EDIT_INTERVAL_S = 2.0
EDIT_MIN_CHARS = 200

PLACEHOLDER = "🔍 正在查阅知识库…"
BUSY_HINT = "上一个问题还在处理，请稍候。"
ERROR_HINT = "处理出错了，请稍后再试。"
FULL_HINT = "当前会话数已满（上限 {cap}），请稍后再试。"
ATTACHMENT_HINT = "暂不处理图片/语音/文件等附件，请用文字提问。"
EMPTY_HINT = "（本次没有得到任何回答内容，请换个说法再试。）"
NEW_HINT = "（已开始新的对话）"
HELP_TEXT = (
    "观澜知识库机器人：\n"
    "· 直接提问 → 走知识库问答（较慢，会分片回复）\n"
    "· /search 关键词 → 零 LLM 全库检索（快，推荐）\n"
    "· /page 页面名 → 零 LLM 按名开页（答案里的 [[引用]] 就是页面名）\n"
    "· /new → 清空上下文，开始新对话\n"
    "· /help → 本帮助"
)
PAGE_USAGE = "用法：`/page 页面名`（页面名就是答案里 [[ ]] 中间那几个字）"
PAGE_MISS = "没有找到名为「{name}」的页面。"
PAGE_MISS_SUGGEST = "没有找到名为「{name}」的页面。你也许想找："
# 可点引用卡片的引导语与超限告示（P4.22 §6.1）。**超限不静默**（继承决策P4.21-31）：
# 正文里的 `[[…]]` 原文本来就还在，故告示指回手打 `/page` 是**真的能用**的出路。
ACTIONS_TITLE = "本答案引用的页面："
PAGE_ACTIONS_TITLE = "本页引用的页面："  # `/page` 输出的引导语（说"本答案"就成了假话）
ACTIONS_OVERFLOW = "另有 {extra} 个引用未列出，可直接发送 `/page <名字>` 打开。"
# 提示语按状态分（§4.5）：`FIRST` **不提示**——第一次说话被告知"上下文已过期"很荒谬。
STATUS_NOTICE = {
    AcquireStatus.EXPIRED: "（上下文已过期，已开始新的对话）",
    AcquireStatus.LOST: "（会话已重置，已开始新的对话）",
}
SEARCH_LIMIT = 8
# `/page` 未命中时的降级召回条数（决策P4.22-11）。**top-3 而不是 `/search` 的 8**：
# 这是**降级提示**、不是检索结果，长了会喧宾夺主。未命中的现实来源是**手打错别字**
# （按钮点出来的名字出按钮前已经解析过一遍），而 BM25 + CJK 2-gram 正擅长救错别字。
PAGE_MISS_LIMIT = 3


@dataclass
class _EditState:
    """edit 档 writer task 的共享状态。**只在事件循环线程读写**（见下方 emit 线程边界说明）。"""

    latest: str = ""
    final: bool = False
    abort: bool = False
    # ★ 终稿那次 `edit` **确认写回**了吗（P4.22 决策P4.22-20）。writer 整段 `suppress(Exception)`
    # （这本身是对的——一条消息发不出去不该杀死主循环），于是"正文全挂"是可达状态；没有这个
    # 标志，`_offer_actions` 就会给一段**用户根本没读到的文字**配上一排按钮。
    final_ok: bool = False
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def touch(self) -> None:
        self.wake.set()


@dataclass
class Activity:
    """一轮在飞问答的登记表（决策P4.21-52）。

    **`task` 入口即登记，不等 `acquire`**：`conv` 只能在 `registry.acquire()` **之后**才填得上，
    而 `acquire` 里有一段卸线程的慢构造——停机若正好落在这段窗口，`active()` 是**空的**，
    `stop_all()` 便以为没事可等，`adapter.close()` 之后那个 task 才拿到会话、继续往已关闭的连接上发。
    **登记必须早于第一个 `await`**，所以只能由 `submit()` 在建 task 的同一步做掉。
    """

    session_key: str
    task: asyncio.Task
    conv: object | None = None  # acquire 返回后才填
    writer: asyncio.Task | None = None  # edit 档才有（§6.2）
    writer_state: _EditState | None = None


class Delivery:
    """投递编排 + 单飞 + 停机面。"""

    def __init__(
        self,
        adapter: IMAdapter,
        registry: SessionRegistry,
        cache: CorpusCache,
        kb: Path,
        *,
        web_base_url: str | None = None,
        max_conversations: int = 100,
    ) -> None:
        self._adapter = adapter
        self._registry = registry
        self._cache = cache
        self._kb = kb
        self._web_base_url = web_base_url
        self._max_conversations = max_conversations
        self._acts: dict[asyncio.Task, Activity] = {}
        self._inflight: set[str] = set()
        self._notices: set[asyncio.Task] = set()
        self._closing = False

    # ── 公开面 ──────────────────────────────────────────────────────────────
    def busy(self, session_key: str) -> bool:
        """单飞集合查询。同时是 `SessionRegistry` 的在飞守卫（§4.5）。"""
        return session_key in self._inflight

    def active(self) -> list[Activity]:
        """在飞轮快照（供停机、测试与日志枚举）。"""
        return list(self._acts.values())

    def submit(self, ready: Ready) -> None:
        """建 task **并同步登记**（决策P4.21-52）。**同步方法**：intake 永不阻塞在应答上。"""
        if self._closing:
            return
        if ready.session_key in self._inflight:
            # 单飞（决策P4.21-13）：**必须给显式反馈**而非静默阻塞。沿用 P4.19「单飞 + 并发拒绝、
            # 不排队」的姿态。
            self._notice(ready.chat_id, BUSY_HINT)
            return
        self._inflight.add(ready.session_key)
        task = asyncio.create_task(self._run(ready))
        # ★ 与建 task **同一步**登记（中间无 await）：task 体要等到本函数返回、事件循环拿回控制权
        # 才起跑，故"登记早于该 task 的第一个 await"在此闭合。
        self._acts[task] = Activity(session_key=ready.session_key, task=task)

    async def handle(self, ready: Ready) -> None:
        """直调入口，**仅供测试**；生产路径一律走 `submit()`（它才带登记）。"""
        self._inflight.add(ready.session_key)
        task = asyncio.current_task()
        assert task is not None
        self._acts[task] = Activity(session_key=ready.session_key, task=task)
        await self._run(ready)

    async def stop_all(self, *, warn_interval: float = 30.0) -> None:
        """封装 §4.4 的步骤③④⑤。**无返回值、不设超时**：它返回即代表可以安全 close adapter。

        强退不走这里——那条路在信号处理器里 `os._exit`（§4.4）。
        """
        # ③ 顺序不可反：**先置闸再拍快照**，才轮不到"闸后新登记"的漏网。
        self._closing = True
        for act in list(self._acts.values()):
            conv = act.conv
            if conv is not None:
                # **绝不 `task.cancel()`**：`arun` 收到 `CancelledError` 时是置令牌后**立刻
                # re-raise**、**不等线程**——那不是"没效果"，是**把"还没停"伪装成"已经停了"**。
                # 只有 `request_stop()` 走令牌路径，`await arun` 经 future 返回时线程确已结束。
                with contextlib.suppress(Exception):
                    conv.request_stop()
        # ④ 停 writer：否则它可能在关机后继续编辑占位消息。
        for act in list(self._acts.values()):
            st = act.writer_state
            if st is not None:
                st.abort = True
                st.touch()
        # ⑤ **无限期**等全部 task（含 `conv` 仍为 None 的）；每 warn_interval 记一条 WARNING。
        # **30 秒是 WARNING 而不是超时**（决策P4.21-48）：超时后 `task.cancel()` 恰好重演本设计
        # 要修的那个错。故超时只用来**告诉用户还在等谁**。
        await self._await_all(warn_interval)

    async def _await_all(self, warn_interval: float) -> None:
        pending: set[asyncio.Task] = {a.task for a in self._acts.values()} | set(self._notices)
        pending = {t for t in pending if not t.done()}
        while pending:
            _done, pending = await asyncio.wait(pending, timeout=warn_interval)
            if pending:
                keys = sorted(
                    self._acts[t].session_key for t in pending if t in self._acts
                )
                _logger.warning(
                    "停机仍在等 %d 轮收尾（不会强行取消，见 docs/P4.21 §4.4）：%s",
                    len(pending),
                    "、".join(keys) or "（通知任务）",
                )

    # ── 内部：一轮的骨架 ────────────────────────────────────────────────────
    def _notice(self, chat_id: str, text: str) -> None:
        """发一句不属于任何一轮的短提示（单飞拒绝等）。登记进 `_notices` 使停机等得到它。"""

        async def _go() -> None:
            await self._safe_send(chat_id, text)

        task = asyncio.create_task(_go())
        self._notices.add(task)
        task.add_done_callback(self._notices.discard)

    async def _safe_send(self, chat_id: str, text: str) -> bool:
        """出站失败只记日志、绝不冒泡——一条消息发不出去不该杀死主循环（§4.6）。

        **返回值是"确认送达了吗"**（P4.22 决策P4.22-20），不是装饰：`_offer_actions` 只吃
        确认送达的片，否则"正文全挂、卡片发成功"会让用户看到一排指向他没读到的文字的按钮。
        绝大多数调用方忽略它，这没问题——但**别把它改回 `None`**，那样测试也不会变红。
        """
        try:
            await self._adapter.send(chat_id, text)
        except Exception:  # noqa: BLE001 — 出站失败只记日志
            _logger.warning("向 %s 发送消息失败", chat_id, exc_info=True)
            return False
        return True

    async def _run(self, ready: Ready) -> None:
        task = asyncio.current_task()
        assert task is not None
        act = self._acts[task]
        try:
            await self._dispatch(act, ready)
        except asyncio.CancelledError:
            raise
        except AgentCancelledError:
            # 兜底（正常路径已在 `_answer` 内接住）：**主动停机不是处理失败**，一个字也不发。
            _logger.info("会话 %s 的一轮因停机/用户停止而中止", ready.session_key)
        except Exception:  # noqa: BLE001 — 单条消息的处理异常绝不能杀死主循环（§4.6）
            _logger.error("处理会话 %s 的消息时出错", ready.session_key, exc_info=True)
            # **不回传异常文本**（可能含路径/凭据片段）。
            if not self._closing:
                await self._safe_send(ready.chat_id, ERROR_HINT)
        finally:
            self._inflight.discard(ready.session_key)
            self._acts.pop(task, None)

    async def _dispatch(self, act: Activity, ready: Ready) -> None:
        if self._closing:  # 第一道停机闸
            return
        if ready.command == "attachment":
            # **附件提示归核心**（决策P4.21-36）：适配器只做事实映射，故这条可用 FakeAdapter
            # 决定性地测到。零 LLM。
            await self._safe_send(ready.chat_id, ATTACHMENT_HINT)
            return
        if ready.command == "help":
            await self._safe_send(ready.chat_id, HELP_TEXT)
            return
        if ready.command == "new":
            # 不留墓碑：主动重开不该在下一条消息里被告知"上下文已过期"（§4.5）。
            await self._registry.drop(ready.session_key)
            await self._safe_send(ready.chat_id, NEW_HINT)
            return
        if ready.command == "search":
            await self._search(ready)
            return
        if ready.command == "page":
            await self._page(ready)
            return
        await self._answer(act, ready)

    async def _search(self, ready: Ready) -> None:
        """`/search`：零 LLM、毫秒级、不烧 token——2000 字上限下它才是主力（决策P4.21-10）。"""
        query = ready.argument.strip()
        if not query:
            await self._safe_send(ready.chat_id, "用法：`/search 关键词`")
            return
        # ★ 卸线程（§4.3）：同步磁盘 + CPU；冷启动可全库扫描，后台预热未完成时仍会阻塞。
        result = await anyio.to_thread.run_sync(
            lambda: self._cache.search(self._kb / "wiki", query, limit=SEARCH_LIMIT)
        )
        payload = search_result_dict(result)
        hits = payload["results"]
        if not hits:
            await self._safe_send(ready.chat_id, f"没有找到与「{query}」相关的页面。")
            return
        lines = [f"「{query}」的检索结果（共扫 {payload['pages_searched']} 页）："]
        for i, hit in enumerate(hits, 1):
            lines.append(f"{i}. {hit['title']}　{hit['page']}")
            snippet = (hit.get("snippet") or "").strip().replace("\n", " ")
            if snippet:
                lines.append(f"   {snippet}")
        await self._send_parts("\n".join(lines), ready.chat_id)

    async def _page(self, ready: Ready) -> None:
        """`/page 名字`：零 LLM 按名开页（P4.22 §4）。**本相位的地基，与卡片正交。**

        参数走**核心唯一的**那份校验器（决策P4.22-18）——手打与点按钮在这里已经分不出来了，
        这正是"点击 = 一条合成的入站消息"想要的效果。
        """
        name = validate_page_arg(ready.argument)
        if name is None:
            await self._safe_send(ready.chat_id, PAGE_USAGE)
            return
        # ★ 卸线程（§4.3）：`link_resolution_index` 要遍历 wiki/ 读 frontmatter，是同步磁盘 + CPU。
        # v1 **不做缓存**：`/page` 是低频交互命令，而缓存要处理失效；`CorpusCache` 那套是为高频
        # `/search` 建的，别照抄。**但同一次调用里只建一次表**——页内引用也在这一跳里算完
        # （`PageView.links`），故下面出按钮时不必再建一次（决策P4.22-25）。
        view = await anyio.to_thread.run_sync(partial(open_page, self._kb, name))
        if view is None:
            await self._send_parts(
                await self._page_miss(name), ready.chat_id, truncation=page_truncation_notice
            )
            return
        delivered = await self._send_parts(
            view.text, ready.chat_id, truncation=page_truncation_notice
        )
        await self._offer_actions(
            ready.chat_id, delivered, title=PAGE_ACTIONS_TITLE, resolvable=view.links
        )

    async def _page_miss(self, name: str) -> str:
        """未命中 → 降级为一次 top-3 检索，**不空手而归**（决策P4.22-11）。"""
        result = await anyio.to_thread.run_sync(
            lambda: self._cache.search(self._kb / "wiki", name, limit=PAGE_MISS_LIMIT)
        )
        hits = search_result_dict(result)["results"]
        if not hits:
            return PAGE_MISS.format(name=name)
        lines = [PAGE_MISS_SUGGEST.format(name=name)]
        for i, hit in enumerate(hits, 1):
            lines.append(f"{i}. {hit['title']}　{hit['page']}")
        return "\n".join(lines)

    async def _offer_actions(
        self,
        chat_id: str,
        delivered: list[str],
        *,
        title: str = ACTIONS_TITLE,
        resolvable: Mapping[str, str] | None = None,
    ) -> None:
        """★ 可点引用：把**已送达正文**里的 wikilink 变成一排按钮（P4.22 §6.1）。

        输入是 `delivered`（**实际成功送达的片**），不是完整正文——两条理由，分量不同：

        - **安全**（决策P4.22-13）：`split_for` 超限时会**丢掉后缀**（"不静默截断"只保证有告示，
          不保证内容都发出去）。从完整正文抽，一个**只出现在被丢弃后缀里**的页面名会被印在
          群内可见的卡片上——那是"只有授权者问得到的内容"变成群内公开展示。
          **这条对 `/page` 的输出同样成立**：一页长到被截断时，只在被丢弃尾巴里的引用不该上卡片。
        - **一致性**（决策P4.22-20）：正文发送失败时不发按钮。收件人本来就该看到这些名字，
          故这条**不是**越权泄露；做它是因为成本近零。

        `resolvable` 给出**已经算好的「解析键 → 拥有页」表**时就不再建解析表（`/page` 走这条，
        决策P4.22-25）；缺省 `None` 是答案那条路——它没有现成的表，得自己解析一次。
        两条路都**按拥有页去重**（见 `pageview._dedupe_by_owner`）：别名与 fold variant 的名字不同、
        页却是同一张，只按名字去重会出两个点开同一页的按钮。

        `caps.supports_actions=False` 的平台（个人微信）在这里直接返回，`send_actions` 一次都
        不会被调到——与 `typing()` 的处置完全对称。
        """
        caps = self._adapter.caps
        if not caps.supports_actions or caps.max_actions <= 0 or not delivered:
            return
        # 名字要先过**核心那份唯一的**参数校验器：按钮点下去合成的正是 `/page <名字>`，一个
        # `validate_page_arg` 会拒的名字（超 `MAX_PAGE_ARG`、带控制字符）出成按钮，点了只会回一句
        # "用法：…"——正是决策P4.21-76 判据里"承诺一个不存在的东西"。构造端与执行端量同一把尺。
        names = [n for n in extract_wikilinks("\n".join(delivered)) if validate_page_arg(n)]
        if not names:
            return
        # **出按钮前先解析一次**（决策P4.22-3）：断链的引用出个按钮、点了回"没有找到"，正是
        # 决策P4.21-76 判据里"承诺一个不存在的东西"的翻版。
        if resolvable is None:
            hits = await anyio.to_thread.run_sync(partial(resolve_many, self._kb, names))
        else:
            hits = filter_resolvable(names, resolvable)  # 键集已备好 ⇒ 零盘 IO、不用卸线程
        if not hits:
            return
        shown, extra = hits[: caps.max_actions], len(hits) - caps.max_actions
        text = title
        if extra > 0:  # 超限**不静默**（决策P4.21-31）
            text = f"{text}\n{ACTIONS_OVERFLOW.format(extra=extra)}"
        actions = [Action(label=name, command=f"/page {name}") for name in shown]
        try:
            await self._adapter.send_actions(chat_id, text, actions)
        except Exception:  # noqa: BLE001 — 叠加层发不出去不该影响已经送达的正文
            _logger.warning("向 %s 发送可点引用失败", chat_id, exc_info=True)

    async def _answer(self, act: Activity, ready: Ready) -> None:
        try:
            conv, status = await self._registry.acquire(ready.session_key)
        except RuntimeError:
            # 容量真顶满（会话都在 TTL 内活跃）。**必须显式答复**：这是唯一一个"系统忙"而非
            # "你说错了"的拒绝，静默会被当成机器人挂了（§4.5）。
            _logger.warning("会话数已达上限 %d，拒绝 %s", self._max_conversations, ready.session_key)
            await self._safe_send(ready.chat_id, FULL_HINT.format(cap=self._max_conversations))
            return
        # ┌── 不可分割段：以下三行之间**不得有任何 await**（决策P4.21-52/58）──────────┐
        if self._closing:  # 第二道停机闸
            return  # 静默返回，一个字也不发
        act.conv = conv  # 登记，使 stop_all 看得见
        conv.begin_turn()  # ★ 必须在同一段内，理由见下
        # └──────────────────────────────────────────────────────────────────────┘
        #
        # 为什么 `begin_turn()` 必须挤进来：`request_stop()` 是**三态**的——有活跃令牌则打断；
        # 无令牌但 `_inflight > 0` 则记待停；**否则返回 `False` 什么都不做**。而
        # `act.conv = conv` 之后、`begin_turn()` 之前，恰恰是「令牌没装、`_inflight == 0`」：
        # 停机在这一刻调 `request_stop()` 会**空转**，然后这一轮照常起跑，再也没人叫得停它。
        caps = self._adapter.caps
        notice = STATUS_NOTICE.get(status)
        try:
            if caps.supports_edit:
                await self._answer_edit(act, ready, conv, notice)
            else:
                await self._answer_plain(act, ready, conv, notice)
        finally:
            conv.end_turn()

    # ── 档②：typing on → 跑 → off → 分片发（个人微信）─────────────────────
    async def _answer_plain(self, act: Activity, ready: Ready, conv, notice: str | None) -> None:
        caps = self._adapter.caps
        typing_on = False
        try:
            if caps.supports_typing:
                with contextlib.suppress(Exception):
                    await self._adapter.typing(ready.chat_id, True)
                    typing_on = True
            # 非 edit 档：`emit` **丢弃全部 token**（逐条推会刷屏并撞频控，§6.2）。
            answer = await conv.turn(ready.text, lambda _kind, _data: None)
        except AgentCancelledError:
            # **主动停机不是处理失败**（决策P4.21-54）：一个字也不发。按 v4 的兜底，用户在关机时
            # 会收到一句莫名其妙的「处理出错了」——更糟的是这会**训练用户去重试一个正在退出的进程**。
            _logger.info("会话 %s 的一轮因停机/用户停止而中止", ready.session_key)
            return
        finally:
            if typing_on:  # typing-off 必须在 finally，否则异常路径下「正在输入…」永不消失
                with contextlib.suppress(Exception):
                    await self._adapter.typing(ready.chat_id, False)
        delivered = await self._send_parts(self._compose(answer, notice), ready.chat_id)
        await self._offer_actions(ready.chat_id, delivered)  # ★ P4.22：叠加层，可整条不发

    # ── 档①：占位 → 原地改写（节流）→ finalize（飞书）────────────────────
    async def _answer_edit(self, act: Activity, ready: Ready, conv, notice: str | None) -> None:
        caps = self._adapter.caps
        ref: OutboundRef = await self._adapter.send(ready.chat_id, PLACEHOLDER)
        st = _EditState()
        act.writer_state = st
        writer = asyncio.create_task(self._writer_loop(ref, st))
        act.writer = writer

        def emit(kind: str, data: object) -> None:
            # **emit 的线程边界（决策P4.21-49）：`turn()` 内部已经桥好了，宿主侧不要再桥一次。**
            # `Conversation.turn` 把调用方传入的 `emit` 包成 `thread_safe_emit`，由它执行
            # `loop.call_soon_threadsafe(emit, ...)`——所以**我们传进去的这个 emit 已经在事件循环
            # 线程上执行**，直接改 `_latest` 即可，不加锁、不再 `call_soon_threadsafe`。
            # 再桥一次会把每次 token 推迟一个 loop tick，于是会出现「turn 已返回、writer 已
            # finalize、迟到的那一跳才改 latest」——终稿被改回中途快照，而且是**偶发**的。
            if kind == "token" and isinstance(data, str):
                st.latest += data
                st.touch()

        try:
            answer = await conv.turn(ready.text, emit)
        except AgentCancelledError:
            # 停 writer，**一个字也不发**（§4.6）。占位消息就停在最后一次中间 edit 的内容上，
            # 这比改成「处理出错了」诚实。
            await self._stop_writer(act, abort=True)
            _logger.info("会话 %s 的一轮因停机/用户停止而中止", ready.session_key)
            return
        except BaseException:
            # **顺序不能反**（§6.2）：先发提示再停 writer，writer 会把提示又改回旧的局部文本。
            await self._stop_writer(act, abort=True)
            raise
        parts, dropped = split_for(
            self._compose(answer, notice), caps.max_message_length, max_parts=caps.max_parts
        )
        # **edit 档的超长答案**：首片 `edit` 回占位消息，其余片用 `send` 追发——占位消息本身
        # 不可能承载多片内容（§6.3）。
        st.latest = parts[0] if parts else ""
        st.final = True
        st.touch()
        await self._stop_writer(act)
        # ★ 首片是**编辑**回占位消息的，故它是否送达要看 writer 的回执（`final_ok`），
        # 不能想当然（P4.22 决策P4.22-20）。
        delivered = [parts[0]] if parts and st.final_ok else []
        for part in parts[1:]:
            await asyncio.sleep(caps.chunk_delay_s)
            if await self._safe_send(ready.chat_id, part):
                delivered.append(part)
        if dropped > 0:
            await asyncio.sleep(caps.chunk_delay_s)
            await self._safe_send(
                ready.chat_id, truncation_notice(dropped, web_base_url=self._web_base_url)
            )
        await self._offer_actions(ready.chat_id, delivered)  # ★ P4.22：叠加层，可整条不发

    async def _writer_loop(self, ref: OutboundRef, st: _EditState) -> None:
        """**单 writer task**：取快照 → edit → 睡一会（§6.2）。

        这样天然满足三条：单写者（永不并发两个 edit）、严格有序（同一 task 串行）、
        final 必在所有中间 edit 之后（主协程 `await writer` 再返回）。**绝不**用
        「每次节流触发就 `create_task(edit)`」——那正是「中间 edit 后完成、把 final 覆盖回旧文本」
        的竞态。
        """
        sent = ""
        while True:
            # ★ `clear()` 必须在**读状态之前**（clear-then-check）：本轮的 `edit()` 是个 await，
            # 主协程正是在那段窗口里置 `final`/`abort` 并 `touch()`。把 `clear()` 放在读状态
            # **之后**会把那次唤醒直接抹掉，于是终稿/中止要白等满一个 `EDIT_INTERVAL_S`
            # ——用户看着占位消息多停 2 秒，停机也多拖 2 秒。丢唤醒是经典的 check-then-clear。
            st.wake.clear()
            if st.abort:
                return
            if st.final:
                text = self._clip(st.latest)
                if text:
                    with contextlib.suppress(Exception):
                        await self._adapter.edit(ref, text, finalize=True)
                        st.final_ok = True  # ★ 只有真写回了才置位（P4.22 决策P4.22-20）
                return
            text = self._clip(st.latest)
            if text and len(text) - len(sent) >= EDIT_MIN_CHARS:
                with contextlib.suppress(Exception):
                    await self._adapter.edit(ref, text)
                sent = text
            with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
                await asyncio.wait_for(st.wake.wait(), EDIT_INTERVAL_S)

    def _clip(self, text: str) -> str:
        """中间 edit 的文本也必须限长——超 `max_message_length` 的中途快照会被 API 拒（§6.2）。

        **不能用 `max_parts=1`**：`split_for` 的语义是"末片留给截断告示"，`max_parts=1` 时
        `kept = parts[:0]` ＝ **空列表**——于是答案一旦流过上限，`_clip` 就恒返回空串、中间 edit
        **静默停摆**，占位消息冻在上限那一刻。要的是"取首片"，故 `max_parts=2` 再取 `[0]`。
        """
        if not text:
            return ""
        limit = self._adapter.caps.max_message_length
        if len(text) <= limit:
            return text
        parts, _ = split_for(text, limit, max_parts=2)  # 至少留 1 片正文
        return parts[0] if parts else text[:limit]

    async def _stop_writer(self, act: Activity, *, abort: bool = False) -> None:
        """收干净 writer。`finally` 无条件置位 + `await`——**绝不允许 writer 活过 `handle()`**。"""
        st, writer = act.writer_state, act.writer
        if st is not None:
            if abort:
                st.abort = True
            st.touch()
        if writer is not None:
            with contextlib.suppress(Exception):
                await writer
        act.writer = None
        act.writer_state = None

    # ── 出站分片 ────────────────────────────────────────────────────────────
    def _compose(self, answer: str, notice: str | None) -> str:
        body = to_im_markdown(answer, web_base_url=self._web_base_url)
        if not body:
            # 空答案（模型一个字都没吐）**必须也有出站**：否则 typing 档是"打字指示亮了又灭、然后
            # 什么都没有"，edit 档更糟——占位消息「🔍 正在查阅知识库…」会**永远停在那里**当终稿
            # （`split_for("")` 返回空片列表 → writer 的 final 分支 `if text:` 直接跳过 edit）。
            body = EMPTY_HINT
        return f"{notice}\n{body}" if notice else body

    async def _send_parts(
        self,
        text: str,
        chat_id: str,
        *,
        truncation: Callable[[int], str] | None = None,
    ) -> list[str]:
        """分片发出，**返回确认送达的片**（P4.22 决策P4.22-20 的输入）。

        `truncation` 让调用方换掉截断告示的文案：`/page` 用 `page_truncation_notice`，
        它**不附 Web 入口**（决策P4.22-12）。缺省是答案那一份。
        """
        caps = self._adapter.caps
        parts, dropped = split_for(text, caps.max_message_length, max_parts=caps.max_parts)
        delivered: list[str] = []
        for i, part in enumerate(parts):
            if i:
                await asyncio.sleep(caps.chunk_delay_s)  # 防频控：微信是真的会丢消息
            if await self._safe_send(chat_id, part):
                delivered.append(part)
        if dropped > 0:
            # 告示**自己占一片**，所以用户**永远看得到「这里被截断了」**——这正是「不静默」的含义。
            await asyncio.sleep(caps.chunk_delay_s)
            notice = (
                truncation(dropped)
                if truncation is not None
                else truncation_notice(dropped, web_base_url=self._web_base_url)
            )
            await self._safe_send(chat_id, notice)
        return delivered
