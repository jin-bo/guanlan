"""答案的 IM 形态：markdown 降级 / wikilink 处理 / 长答案切分（P4.21 §6.3/§6.5）。

**长答案契约 = 「不静默截断」，不是「绝不截断」**（决策P4.21-31）：原设计的三条要求
（≤N 片 / 每片 ≤ limit / 绝不截断）对超过 `max_parts × limit` 的答案**数学上不可同时满足**，
必须选边。超限时**末片是显式截断告示**（含剩余字数），故用户**永远看得到"这里被截断了"**。

**告示文案不能说「完整版：<URL>」**（决策P4.21-50）：IM 会话是 `persist=False`，被舍弃的那段
**没有存在任何地方**；`--web-base-url` 也只是站点入口、不是这一次回答的地址。照那种文案，
用户点过去只会看到一个空的新会话——**比不给链接更糟，因为它承诺了一个不存在的东西**。
"""

from __future__ import annotations

import re

# 围栏块识别：mermaid（P4.13）/ KaTeX（P4.14）/ flint（P4.20）在 IM 里都无渲染器，沿用那三个
# 半阶段一致的**降级契约：保留源码，绝不静默丢弃**（§6.5）。
# 公开名：飞书适配器的 `post` 分行（§8.4）用的是**同一条**判据，不再各写一份正则（否则两处漂移时
# "围栏块独占一片"与"围栏块独占一 row"会对不上）。
FENCE_LINE = re.compile(r"^\s{0,3}(?:```|~~~)")

TRUNCATION_HINT = "⚠️ 答案还有约 {dropped} 字未发送；请在 Web 端**重新提问**以获取完整回答。"
WEB_ENTRY_HINT = "Web 入口：{url}"


def to_im_markdown(answer: str, *, web_base_url: str | None) -> str:
    """把一份答案转成适合 IM 的 markdown。

    **实际上什么都不做**——这个函数存在的意义是让「不处理」成为一个**显式**的决定，
    而不是某处忘了写。两条被显式选择的"不做"：

    1. **围栏块原样保留**（§6.5）：mermaid / KaTeX / flint 在 IM 里都无渲染器，沿用那三个半阶段
       一致的降级契约——**保留源码，绝不静默丢弃**。
    2. **`[[wikilink]]` 保留原文**（决策P4.21-17，经决策P4.21-76 收紧为**无条件**）：
       原设计留了「显式给了 `--web-base-url` 就转真链接」这条支线，**实现期核实后撤回**——
       Web 宿主**根本没有按页面名打开某一页的路由**（SPA 只认 `?raw=<源文件名>` 与 `?c=<会话 id>`；
       `/api/page?path=` 要的是**相对路径**、且返回 JSON 不是可看的页面）。也就是说，
       任何由页面**名**拼出来的 URL 都是死链。
       **一个 404 比一段可读的 `[[甲实体]]` 更误导**——这正是决策P4.21-50「不承诺一个不存在的
       东西」的同一条判据，只不过那次是截断告示、这次是页内引用。
       `--web-base-url` 仍然有用：它是**站点入口**，出现在截断告示里（§6.3）。

    **不为 IM 通道改 prompt 让模型"答短点"**——那是把判断塞进宿主、破**零业务智能**
    （决策P4.21-16）；要短靠用户选择用 `/search`（决策P4.21-10）。
    """
    del web_base_url  # 仅用于截断告示的站点入口，见上；正文不做任何链接化
    return answer.strip()


def _split_paragraph(chunk: str, limit: int) -> list[str]:
    """把一段超长文本硬切成 ≤ limit 的片（优先在换行处断，其次硬切）。"""
    out: list[str] = []
    rest = chunk
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        piece = rest[:cut].rstrip()
        if piece:  # 断点前只有空白（如 "   \n" 开头）时 `rstrip()` 会得到空串——
            out.append(piece)  # 放进去就会变成一条**空消息**发给平台（多半直接被 API 拒）。
        rest = rest[cut:].lstrip("\n")
    if rest:
        out.append(rest)
    return out


def split_for(text: str, limit: int, *, max_parts: int) -> tuple[list[str], int]:
    """切分成 ≤ `max_parts` 片、每片 ≤ `limit` 字符。

    返回 `(片列表, 被舍弃的字符数)`。**舍弃 > 0 时调用方必须发显式截断告示**（§6.3）——
    告示**自己占一片**，所以留给正文的是 `max_parts - 1` 片。

    切分优先在**围栏边界**与空行处断开（避免把一个代码块劈成两半后前一半自己不成立）。
    """
    if limit <= 0 or max_parts <= 0:
        raise ValueError("limit 与 max_parts 都必须为正")
    text = text.strip()
    if not text:
        return [], 0
    if len(text) <= limit:
        return [text], 0

    # 先按行聚成不超过 limit 的块；围栏起止行强制成为块边界。
    blocks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    in_fence = False
    for line in text.split("\n"):
        is_fence = bool(FENCE_LINE.match(line))
        add = len(line) + 1
        if cur and not in_fence and (cur_len + add > limit or is_fence):
            blocks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += add
        if is_fence:
            in_fence = not in_fence
            if not in_fence:  # 围栏闭合 → 该块独立成片
                blocks.append("\n".join(cur))
                cur, cur_len = [], 0
    if cur:
        blocks.append("\n".join(cur))

    parts: list[str] = []
    for block in blocks:
        block = block.strip("\n")
        if not block:
            continue
        for piece in _split_paragraph(block, limit) if len(block) > limit else [block]:
            if parts and len(parts[-1]) + 1 + len(piece) <= limit:
                parts[-1] = f"{parts[-1]}\n{piece}"
            else:
                parts.append(piece)

    if len(parts) <= max_parts:
        return parts, 0
    # 告示**自己占一片**，故正文得 `max_parts - 1` 片——但 `max_parts == 1` 时那是 **0 片**，
    # `kept` 成了空列表：正文被整段吞掉，用户只收到一句"还有约 N 字未发送"，而**一个字的答案
    # 都没有**；edit 档更糟，占位消息「🔍 正在查阅知识库…」就此冻成终稿。`_clip` 的注释已经把
    # 这个陷阱写清楚了，但两个调用方仍直接把 `caps.max_parts` 递进来。**正文至少留一片**：
    # 宁可多发一条（一片正文 + 一条告示），也不能把"不静默截断"实现成"静默丢光"。
    body_parts = max(1, max_parts - 1)
    kept = parts[:body_parts]
    dropped = sum(len(p) for p in parts[body_parts:]) + (len(parts) - body_parts - 1)
    return kept, dropped


def truncation_notice(dropped: int, *, web_base_url: str | None) -> str:
    """截断告示（§6.3）。有 `--web-base-url` 时**只**附站点入口、**不**称其为本答案的地址。"""
    lines = [TRUNCATION_HINT.format(dropped=dropped)]
    if web_base_url:
        lines.append(WEB_ENTRY_HINT.format(url=web_base_url.rstrip("/")))
    return "\n".join(lines)
