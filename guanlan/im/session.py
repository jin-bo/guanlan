"""会话键 ↔ `Conversation` 映射与四态状态机（P4.21 §4.5，决策P4.21-40/46/47/51/64/69/71）。

**TTL 归 `SessionRegistry` 独占，不归 store**——三条已核实的源码事实决定了这一点：

1. `ConversationStore.get()` **命中即刷新** `conv.last_active`，且源码注释写明这是**有意的**
   （堵「get 拿到 conv → begin_turn 前被并发 reclaim 误逐」的竞态）。所以 registry 只要先
   `get`，闲置两小时的会话反而被**复活**。
2. `_reclaim_idle_locked` **只在 `create`/`restore` 里调**，`get` 路径永不触发回收。
3. store **根本没有「因容量逐出」这条路径**——`create()` 顶满时是**抛 `RuntimeError`**，
   它只在此之前顺手回收**超 TTL** 的会话。

三条一叠加，「registry 判 UX 过期 + store 的 2×TTL 兜内存」这套双 TTL 方案自相矛盾：
`conv.last_active` 恒不早于 registry 的 `last_seen`，故 store 判得动的会话 registry 早已判过期；
更要命的是**容量并没有人腾**——100 个处在「registry 已过期、未到 store 的 2×ttl」窗口里的会话，
只在**各自的 key 再次说话时**才被清理，而第 101 个用户根本没有 key 在表里，他撞上的是
`RuntimeError`。**多设一层 TTL 兜底反而制造了一个谁都不负责的空档。**

故：`store.idle_ttl=None`，TTL 与腾容量都归本模块。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Set as AbstractSet
from enum import Enum

import anyio.to_thread

from . import defaults

_logger = logging.getLogger("guanlan.im")

# 值归 `defaults.py`（零依赖叶子，cli 建 parser 时要读它；本模块 import anyio，取不起）。
# 这里保留同名转出，既有 `from .session import DEFAULT_IDLE_TTL` 的调用点无需改。
DEFAULT_IDLE_TTL = defaults.DEFAULT_IDLE_TTL


class AcquireStatus(str, Enum):
    """`acquire()` 的四态（评审 #12/#18）——**不是一个 bool**，各态的用户可见措辞不同。"""

    FIRST = "first"  # 该会话键第一次出现 → **不提示**（第一次说话被告知"上下文已过期"很荒谬）
    EXISTING = "existing"  # 沿用现有会话
    EXPIRED = "expired"  # 超过 registry 自持的 TTL → 已重置，提示「上下文已过期」
    LOST = "lost"  # store 里的对象**意外**不见了 → 已重置 + WARNING（不变量被破坏，不应发生）


class SessionRegistry:
    """会话键 → `Conversation` 的映射 + TTL + 主动腾容量。

    `_map: dict[str, tuple[str | None, float]]`——`cid is None` 即**墓碑**：会话对象已被回收、
    但"这个键过期过"这件事留着（决策P4.21-51）。彻底删记录与"日后仍知道它过期过"**不可兼得**，
    显式选后者。

    `_creating: set[str]`——正在走「标墓碑 → 清理 → 建会话」的键，淘汰时**整集受保护**
    （决策P4.21-69）。只保护"当前这个键"挡不住 A、B 并发重建时互相淘汰对方的墓碑：
    **"保护当前操作"和"保护所有正在进行的操作"是两件事**——单进程单线程也一样，
    因为并发不是来自线程，是来自 `await` 处的交错。
    """

    def __init__(
        self,
        store,
        *,
        ttl: float = DEFAULT_IDLE_TTL,
        tombstone_limit: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._ttl = ttl
        self._tombstone_limit = tombstone_limit
        self._clock = clock
        self._map: dict[str, tuple[str | None, float]] = {}
        self._creating: set[str] = set()
        self._is_busy: Callable[[str], bool] = lambda _key: False

    def set_busy(self, pred: Callable[[str], bool]) -> None:
        """接 `Delivery.busy`（§4.5）。用 setter 而非构造参数——registry 要先于 `Delivery` 构造。

        **在飞守卫走 `Delivery.busy(session_key)`，不走 `conv.is_idle()`**：后者比较的是
        `store.get()` 刚刷新过的 `last_active`，在注入**冻结时钟**的测试里 `now - last_active > 0`
        恒为假、sweep 会一个都扫不掉——**一个只在真实时钟下才成立的守卫，等于没有测试**。
        而单飞集合本就是「这个会话键有没有在飞轮」的权威答案，且与时钟无关。
        """
        self._is_busy = pred

    # ── 只读自省（供日志 / 测试）────────────────────────────────────────────
    def live_keys(self) -> list[str]:
        return [k for k, (cid, _) in self._map.items() if cid is not None]

    def tombstone_keys(self) -> list[str]:
        return [k for k, (cid, _) in self._map.items() if cid is None]

    @property
    def creating(self) -> AbstractSet[str]:
        return self._creating

    async def acquire(self, key: str):
        """取（或重建）该会话键的 `Conversation`，返回 `(conv, AcquireStatus)`。"""
        now = self._clock()
        rec = self._map.get(key)
        if rec is not None:
            cid, last_seen = rec
            if cid is None:
                # ⓪ 墓碑：会话已被 sweep 回收，但"过期过"这件事留着。
                # `expired=True` 既把墓碑时间刷成 now，也让本键进 `_creating` 受保护。
                return await self._renew(key, expired=True), AcquireStatus.EXPIRED
            if now - last_seen <= self._ttl:
                # ① 未过期：get（此时刷新 last_active 是对的）。★ 卸线程，见模块 docstring 与 §4.3：
                # `get()` 与 `create()` 共用同一把 `threading.Lock`，而 `create()` 在慢 Agent 构造
                # 期间**全程持锁**——同步调它会把"老用户 get"连同整个事件循环一起卡住。
                conv = await anyio.to_thread.run_sync(self._store.get, cid)
                if conv is not None:
                    self._map[key] = (cid, now)
                    return conv, AcquireStatus.EXISTING
                # ② 不该发生：registry 独占生命周期后，`get()` 返回 None 只可能是有人绕过 registry
                # 删了会话——**不变量被破坏**。故记 WARNING；悬空映射先弹掉，无墓碑可留。
                _logger.warning(
                    "会话 %s 的 conversation %s 在 store 里意外消失（不变量被破坏），已重建", key, cid
                )
                self._map.pop(key, None)
                return await self._renew(key, expired=False), AcquireStatus.LOST
            return await self._renew(key, expired=True), AcquireStatus.EXPIRED
        return await self._renew(key, expired=False), AcquireStatus.FIRST

    async def _renew(self, key: str, *, expired: bool):
        """「标墓碑 → 清理 → 建会话」一条路径（决策P4.21-69）。

        进入前**同步**登记进 `_creating`（早于本路径上的**任何** `await`），`finally` 摘掉——
        成功、抛错、被取消三条路都要摘，**残留一个键就等于永久保护它**。
        """
        self._creating.add(key)
        try:
            if expired:
                # 墓碑的 `last_seen` 记为 **now**（决策P4.21-64）：用户**确实刚刚发过消息**，
                # 这是它的真实语义，不是权宜之计。沿用旧值的话，紧接着的 `_evict_tombstones` 里
                # 它往往正是最旧的那条 → 先被淘汰 → `create()` 再抛容量错 → 用户重试仍得 `FIRST`，
                # "上下文已过期"这句提示**恰好在最需要它的那次重试里消失**。
                await self.drop(key, tombstone=True, seen=self._clock())
            await self._sweep_expired()  # 腾容量：这才是真正腾容量的机制（决策P4.21-46）
            conv = await anyio.to_thread.run_sync(self._store.create)  # 顶满时抛 RuntimeError
            self._map[key] = (conv.id, self._clock())  # 覆盖墓碑（若有）
            return conv
        finally:
            self._creating.discard(key)

    async def drop(self, key: str, *, tombstone: bool = False, seen: float | None = None) -> None:
        """丢弃该键的会话对象。`tombstone=True` 只回收对象、保留 `(None, last_seen)` 标记。

        **async + 内部卸线程**（决策P4.21-44）：它最终调 `store.delete()` → `conv.close()`，
        后者被源码标注「慢（可能 MCP 断开等），锁外」；同步调用会让一次 `/new` 卡住整个事件循环。

        `/new` 用默认 `tombstone=False`——整条删掉，使下一条消息是 `FIRST` 而非 `EXPIRED`：
        主动重开不该被告知"上下文已过期"。
        """
        rec = self._map.get(key)
        if rec is None:
            return
        cid, last_seen = rec
        if tombstone:
            self._map[key] = (None, last_seen if seen is None else seen)
        else:
            self._map.pop(key, None)
        if cid is not None:
            await anyio.to_thread.run_sync(self._store.delete, cid)

    async def _sweep_expired(self) -> int:
        """建会话**前**扫掉自己记录里的过期会话（决策P4.21-46）。返回回收数。

        只在**新建**路径上跑（新 key 到达，罕见），表长 ≤ `max_conversations` + 墓碑界，
        一次 O(n) 可忽略。
        """
        now = self._clock()
        doomed = [
            k
            for k, (cid, seen) in self._map.items()
            if cid is not None and now - seen > self._ttl and not self._is_busy(k)
        ]
        for k in doomed:
            await self.drop(k, tombstone=True)  # 删会话对象，**留下墓碑**
        self._evict_tombstones(protect=self._creating)
        return len(doomed)

    def _evict_tombstones(self, *, protect: AbstractSet[str]) -> None:
        """墓碑数超界时按 `last_seen` **最旧优先**淘汰到界内；**永不淘汰活条目**。

        被淘汰的墓碑 = 那个键日后回来时得到 `FIRST` 而非 `EXPIRED`——**这是有意的取舍**：
        半天没说话的人，再被告知"上下文已过期"本就没有信息量。

        `protect` = 整个 `_creating` 集合（决策P4.21-69），不是"当前这一个键"。
        """
        tombs = [(k, seen) for k, (cid, seen) in self._map.items() if cid is None]
        excess = len(tombs) - self._tombstone_limit
        if excess <= 0:
            return
        for k, _ in sorted(tombs, key=lambda kv: kv[1]):
            if excess <= 0:
                break
            if k in protect:
                continue
            self._map.pop(k, None)
            excess -= 1
