"""确定性 frontmatter 引号修复（消除最常见的写门禁自愈轮）。**零 LLM。**

写门禁每发现一条本次新引入的阻断性违规，就把它回喂同一 Agent 自愈，**每轮 = 一整个
额外的 Agentao 子进程**（冷启动 + LLM 推理，见 `gate.run_guarded_write_result`）。触发自愈
的违规里绝大多数是 `frontmatter.unparsable`——字符串值（尤其 `title`）把引号写坏（双引号
套双引号 / 字符串没用单引号）导致整块 YAML 解析失败。这一类**确定性可修**：坏的只是引号
转义，意图基本可恢复（剥一层外引号 → 改用单引号重包、内部 `'` 翻倍）。

本模块在 enforce 后、自愈循环前插一道确定性修复，**用一次廉价的 Python 重判换掉一整轮
LLM 自愈**。三道安全闸把风险压到「最差等于现状」：

1. **parse-verified**：修完整块必须能用**严格档**重新解析成 mapping，否则整页放弃、原样
   回落现有 LLM 自愈（修不动的页行为和今天逐字节一致，零回归）。
2. **只碰 `frontmatter.unparsable` 页**：作用面取自门禁的违规集（已是「本次新引入的阻断性」
   违规）——baseline 里早就坏的页不动。缺键/坏类型/缺块不在此修（非引号问题）。
3. **正文与 `---` 分隔行逐字节不变**：只重写解析失败的标量值行。

这是宿主侧专项确定性 frontmatter 操作（仿 `provenance.py` 的 `raw_digest` 盖章），便于单测。
复用 `pages.py` 的 `split_frontmatter` / `parse_frontmatter` 严格档归口，不另写 YAML 解析。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .check import Violation
from .pages import parse_frontmatter, split_frontmatter
from .rawio import atomic_write_bytes  # 字节级原子覆盖写：保 CRLF 保真 + 避免半写坏页

__all__ = ["repair_page_frontmatter", "repair_unparsable_pages"]

# 顶层 `key: value` 映射条目行：key 在第 0 列（非缩进续行）、非 `#` 注释、非 `-` 列表项、
# key 内不含 `:`（约定里键都是 plain 标量）；冒号后须至少一个空白 + 非空值。其余行（缩进/
# 列表/注释/空行/纯分隔）一律不匹配 → 原样保留。行尾在调用处单独剥离，这里只匹配内容部分。
_ENTRY_RE = re.compile(r"^([^\s#:-][^:]*):([ \t]+)(\S.*?)[ \t]*$")


def _value_parses(value: str) -> bool:
    """该标量值单独是否已是合法 YAML（合法标量/行内集合 → 不动）。`yaml.YAMLError` 即不合法。

    **必须保留**：先放过已合法的值，才能保证不去重写本就正确的引号行（如另一行坏掉导致整块
    unparsable 时，合法的 `title: "正常"` 不被无谓改成 `title: '正常'`），守住「合法行逐字节不变」。
    """
    try:
        yaml.safe_load(value)
    except yaml.YAMLError:
        return False
    return True


def _requote_block(block: str) -> str | None:
    """按行把解析失败的顶层标量值改用单引号重包。无任何行被改 → 返回 None。

    保留每行原始行尾（`splitlines(keepends=True)`，CRLF 亦原样）；已合法的值、缩进/列表/注释行
    一律不碰。**只修真正的引号转义 bug**：值须有**成对外引号**（首尾同为 `"` 或 `'`）——剥一层外引号
    后用单引号重包（内部 `'` 翻倍）。首字符是引号但首尾不成对（`"a` 未闭合、`"a"b" # 注释` 带尾注）
    不是干净引号 bug，强行重包会把尾注/残文吞进值或产生退化恢复，**一律留给 LLM 自愈**。
    """
    changed = False
    out: list[str] = []
    for raw_line in block.splitlines(keepends=True):
        stripped = raw_line.rstrip("\r\n")
        eol = raw_line[len(stripped):]
        m = _ENTRY_RE.match(stripped)
        if m is None:
            out.append(raw_line)
            continue
        key, sep, value = m.group(1), m.group(2), m.group(3)
        if _value_parses(value):
            out.append(raw_line)
            continue
        if not (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"):
            out.append(raw_line)  # 无成对外引号 → 非干净引号 bug，留给自愈
            continue
        recovered = value[1:-1]  # 剥成对外引号（已确认成对）
        new_value = "'" + recovered.replace("'", "''") + "'"
        out.append(f"{key}:{sep}{new_value}{eol}")
        changed = True
    return "".join(out) if changed else None


def repair_page_frontmatter(path: Path, wiki: Path) -> bytes | None:
    """尝试修一张 frontmatter 引号写坏的页面：写了返回**原字节**（供门禁回滚），否则返回 None。

    本函数只负责确定性的「把成对外引号的失败标量值单引号重包、整块复验为 mapping 后落盘」这一步——
    **不判定该页是否真的全清**。「修完是否真省下一轮自愈」由调用方门禁用**真 `enforce_write_result`
    重判 + 回滚未清页**裁定（见 `gate.run_guarded_write_result`）：验收判据**就是门禁本身**，故决不会
    漏掉门禁里任何一道校验——`run_check`（frontmatter/断链/sources/alias）**以及** page_guard 专属的
    `_check_source_regression`（`sources.dropped` 源不回退，`run_check` 看不见）都在门禁重判里兜住。
    `wiki` 仅用于 `wiki.parent`（库根）锚定与符号链接越界判定。

    - 仅当严格档解析失败为 `frontmatter.unparsable` 才尝试（缺键/坏类型/缺块等非引号问题不在此修）。
    - **拒符号链接 / 越界路径**：`write_bytes` 跟随符号链接，而 `iter_pages` 经 `is_file()` 会把指向库外的
      `wiki/` 符号链接页纳入校验——若不挡，宿主代码会改动 KB 之外的文件。故页本身是符号链接、或解析后逃出
      `wiki/`（父目录符号链接）一律跳过、返回 None。
    - **逐字节 I/O**：`read_bytes` + `atomic_write_bytes` 避开 `read_text` 换行翻译（否则 CRLF 被静默改成
      LF、连带改动正文与 `---` 分隔行）；只替换 frontmatter 块段，原 `---` 行与 body 逐字节不变。写经
      `atomic_write_bytes`（同目录 tmp + `os.replace`）：崩在写一半也不留半写坏页（页已过符号链接闸，
      故 rename 换名与旧 `write_bytes` 落点一致）。
    """
    # 安全闸：符号链接页 / 解析后逃出 wiki/ 的路径不修（修复是可选优化，越界即跳过、最差=现状）。
    if path.is_symlink():
        return None
    try:
        if not path.resolve().is_relative_to(wiki.resolve()):
            return None
    except OSError:
        return None
    original = path.read_bytes()
    text = original.decode("utf-8")
    block, _body = split_frontmatter(text)
    if block is None:
        return None
    _meta, fatal = parse_frontmatter(block)
    if fatal is None or fatal.kind != "frontmatter.unparsable":
        return None
    new_block = _requote_block(block)
    if new_block is None:
        return None
    new_meta, new_fatal = parse_frontmatter(new_block)
    if new_fatal is not None or not isinstance(new_meta, dict):
        return None  # 仍解析不通 / 非映射 → 放弃，交 LLM 自愈
    # 仅替换 block 段：保留原首/尾 `---` 行与 body 逐字节（split_frontmatter 已确认两者存在）。
    lines = text.splitlines(keepends=True)
    close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    new_text = lines[0] + new_block + lines[close] + "".join(lines[close + 1:])
    atomic_write_bytes(path, new_text.encode("utf-8"))  # 字节保真 + 原子换名，不留半写坏页
    return original


def repair_unparsable_pages(root: Path, violations: list[Violation]) -> dict[str, bytes]:
    """对违规集里 `frontmatter.unparsable` 的页逐页确定性修引号、落盘，返回 `{相对库根 posix: 原字节}`。

    `violations` 取自门禁结论（已是本次新引入的阻断性违规集）。返回的原字节供门禁**重判后回滚仍阻断
    的修复页**——「最差 = 现状」由门禁回滚兜底，本函数不自行判定全清（见 `repair_page_frontmatter`）。
    单页 IO / 解码失败吞掉、交后续自愈，不连累其余页。键去重排序，确定可重放。
    """
    wiki = Path(root) / "wiki"
    out: dict[str, bytes] = {}
    for rel in sorted({v.page for v in violations if v.kind == "frontmatter.unparsable"}):
        try:
            original = repair_page_frontmatter(Path(root) / rel, wiki)
        except (OSError, UnicodeDecodeError):  # 非 UTF-8 页解码失败也吞掉（UnicodeDecodeError 非 OSError）
            original = None
        if original is not None:
            out[rel] = original
    return out
