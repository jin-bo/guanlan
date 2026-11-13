"""一键入库（P4.30，见 docs/P4.30-一键入库.md）：解析 → 整理 → 拆分 → 确认 → 落 `raw/` → 依次入库。

与传输无关的内核，Web（`web/oneclick_routes.py`）与 CLI（`guanlan import`）是两层薄壳，共用同一份
任务清单 `workspace/tasks/<task_id>.json` 与日志 `workspace/tasks/<task_id>.log`。

- **锁**：本模块的内核函数**一律不取库级锁**（决策P4.30-15「只由最外层入口取锁」）；调用方须在
  准备阶段（`prepare`）与执行阶段（`confirm` / `resume`）持有 `kblock` 锁。`recover_stale` 也只能在
  持锁时调用——拿得到锁才说明原执行者已经退出。
- **①–④ 零 LLM、只写 `workspace/parsed/`**；确认后 ⑤ 逐篇原子落 `raw/`（`writing` → `written`），
  ⑥ 串行 `run_ingest`，任意一篇非 0 即暂停（决策P4.30-7），暂停只在篇与篇之间生效（决策P4.30-5）。
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import re
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

from .check import run_check
from .convert import _default_origin, convert_to_markdown
from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .formats import BATCH_IMPORT_EXTS
from .sourcerec import read_source_record, redact_url
from .imageio import (
    ConvertError,
    ConvertResult,
    _MD_IMAGE,
    _URL_SCHEME,
    _admit_image_ref,
    _images_dir,
    _stage_and_swap_images,
    collect_and_rewrite,
    collect_for_promotion,
    commit_md_with_images,
)
from .kblock import KBLockBusy, acquire, describe_holder, inside_write_run, read_holder
from .normalize import normalize
from .pages import load_page, split_frontmatter
from .paths import require_kb_root
from .provenance import parse_digest_value
from .rawio import (
    NAME_STEM_MAX_BYTES,
    atomic_write_text,
    cap_utf8,
    check_text_admission,
    raw_slug,
    sha256_file,
)
from .sourcepage import locate_source_page
from .stopscope import StopRequested, stop_scope
from .stopscope import check as stop_check
from .splitdoc import index_name, render_index, render_part, split_document
from .tocnorm import TocReviewer, toc_title_lines

__all__ = [
    "ACTIVE_STATES",
    "OneClickError",
    "StopRequested",
    "active_task",
    "cancel",
    "confirm",
    "import_entrypoint",
    "iter_tasks",
    "load_task",
    "doc_entries",
    "prepare",
    "prepare_batch",
    "progress_info",
    "recover_stale",
    "resume",
    "summary_lines",
]

ACTIVE_STATES = ("preparing", "awaiting_confirm", "running", "paused")
DEFAULT_ITEM_SECONDS = 600  # 没有本库实测记录时，按每篇 10 分钟估
_RECENT_SAMPLES = 20  # 预计耗时取最近若干篇的均值

Emit = Callable[[str], None]
IngestFn = Callable[[str, Path], int]


class OneClickError(GuanlanError):
    """一键入库的用法/状态错误（退出码 EXIT_USAGE）。"""


# ── 路径与清单 ───────────────────────────────────────────────────────────────


def _ws(root: Path) -> Path:
    return root / "workspace"


def tasks_dir(root: Path) -> Path:
    return _ws(root) / "tasks"


def _task_file(root: Path, task_id: str) -> Path:
    return tasks_dir(root) / f"{task_id}.json"


def _log_file(root: Path, task_id: str) -> Path:
    return tasks_dir(root) / f"{task_id}.log"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_task(root: Path, task: dict) -> None:
    """原子重写任务清单（临时文件 + rename，`atomic_write_text`）。"""
    task["updated"] = _now()
    tasks_dir(root).mkdir(parents=True, exist_ok=True)
    atomic_write_text(_task_file(root, task["task_id"]), json.dumps(task, ensure_ascii=False, indent=2) + "\n")


def load_task(root: Path, task_id: str) -> dict:
    path = _task_file(root, task_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OneClickError(f"找不到入库任务：{task_id}") from None
    except (OSError, ValueError) as exc:
        raise OneClickError(f"入库任务清单损坏：{path.name}（{exc}）") from None
    if not isinstance(data, dict) or data.get("task_id") != task_id:
        raise OneClickError(f"入库任务清单损坏：{path.name}")
    return data


def iter_tasks(root: Path) -> list[dict]:
    """所有可读的任务清单，按创建时间排序；坏文件跳过。"""
    out: list[dict] = []
    d = tasks_dir(root)
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("task_id") == path.stem:
            out.append(data)
    out.sort(key=lambda t: (t.get("created", ""), t["task_id"]))
    return out


def active_task(root: Path) -> dict | None:
    """本库唯一的未完成任务（preparing / awaiting_confirm / running / paused）。"""
    for task in reversed(iter_tasks(root)):
        if task.get("state") in ACTIVE_STATES:
            return task
    return None


class TaskLog:
    """进展日志：追加写 `<task_id>.log`，同时（非 detail 行）经 `emit` 推给调用方。"""

    def __init__(self, root: Path, task_id: str | None, emit: Emit | None) -> None:
        self.path = _log_file(root, task_id) if task_id else None
        self.emit = emit

    def __call__(self, line: str, *, detail: bool = False) -> None:
        if self.path is not None:
            with contextlib.suppress(OSError):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{_now()}] {line}\n")
        if not detail and self.emit is not None:
            with contextlib.suppress(Exception):
                self.emit(line)


def _recover_if_owner(root: Path, lock) -> None:
    """持**真锁**才恢复：嵌套放行的锁（祖先写运行持锁，`lock.nested`）说明原执行者还活着，不能改清单。"""
    if lock is not None and not lock.nested:
        recover_stale(root)


def recover_stale(root: Path) -> list[str]:
    """**持锁时调用**：原执行者已退出 → preparing 改 failed、running 改 paused。返回被改动的任务号。"""
    changed: list[str] = []
    for task in iter_tasks(root):
        state = task.get("state")
        if state == "preparing":
            task["state"] = "failed"
            task["reason"] = "准备阶段中断（原执行者已退出）；准备是零 LLM 的，可重新导入。"
        elif state == "running":
            task["state"] = "paused"
            task["reason"] = "执行中断（原执行者已退出）；继续后从未完成的那一篇重试。"
            for it in task.get("items", []):  # 中断在途的那一篇不能继续显示「入库中」
                if it.get("state") == "running":
                    it["state"] = "pending"
        else:
            continue
        save_task(root, task)
        TaskLog(root, task["task_id"], None)(f"恢复：{state} → {task['state']}")
        changed.append(task["task_id"])
    return changed


# ── 预检（§5） ───────────────────────────────────────────────────────────────


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _owned_by_other_tasks(root: Path, exclude: str) -> dict[str, set[str | None]]:
    """旧任务清单记录为已写入的 raw/ 文件（md 与图片）→ 当时写入的哈希。归属只认清单，不凭路径推断。"""
    owned: dict[str, set[str | None]] = {}
    for task in iter_tasks(root):
        if task["task_id"] == exclude:
            continue
        for it in task.get("items", []):
            if it.get("land") in ("written", "writing"):
                owned.setdefault(it["raw"], set()).add(it.get("sha256"))
                for rel, sha in (it.get("images") or {}).items():
                    owned.setdefault(rel, set()).add(sha)
    return owned


def _modified_since_written(root: Path, files: list[str], owned: dict[str, set[str | None]]) -> list[str]:
    """旧任务写下、但盘上内容已与当时记录不同的文件（`raw/` 本应不可变：哈希变了说明有人手工改过）。"""
    out: list[str] = []
    for rel in files:
        shas = owned.get(rel)
        if shas and None not in shas and sha256_file(root / rel) not in shas:
            out.append(rel)
    return out


def _self_owned(root: Path, task: dict) -> tuple[set[str], list[str]]:
    """本任务已写且盘上哈希一致的文件（续跑时不算冲突）；哈希不一致的列为差异。

    `writing` 的项（上次写到一半）整体放行给 ⑤ 自己处理：一致补记、不存在重写、其余停下报差异。
    """
    ok: set[str] = set()
    diffs: list[str] = []
    for it in task.get("items", []):
        if it.get("land") == "writing":
            ok.add(it["raw"])
            ok.update((it.get("images") or {}).keys())
            continue
        if it.get("land") != "written":
            continue
        files = {it["raw"]: it.get("sha256")}
        files.update(it.get("images") or {})
        for rel, sha in files.items():
            cur = sha256_file(root / rel)
            if cur is not None and cur == sha:
                ok.add(rel)
            elif cur is not None:
                diffs.append(f"{rel}（本任务写入后被改动）")
    return ok, diffs


def precheck(root: Path, task: dict) -> dict:
    """冲突预检的唯一入口：准备结束、确认、续跑、⑤ 落盘前都调它。

    单份任务 → `_precheck_doc`，结果与 P4.30 相同。批量任务（P4.32 §5）→ 按 `items[*].doc` 筛出各份篇目、
    **每次都重新**逐份检查（不复用准备时的结果），再加批内 slug 检查并汇总：任一份冲突或批内冲突 → `conflict`；
    否则任一份可覆盖 → `overwritable`；否则 `ok`。逐份结果放在 `docs`。
    """
    if task.get("kind") != "batch":
        return _precheck_doc(root, task)
    items = task.get("items", [])
    docs_pc: list[dict] = []
    details: list[str] = []
    existing: list[str] = []
    for i, d in enumerate(task.get("docs", [])):
        view = {
            "task_id": task["task_id"],
            "sample": d["sample"],
            "split": d.get("split", False),
            "items": [it for it in items if it.get("doc") == i],
        }
        pc = _precheck_doc(root, view)
        docs_pc.append(pc)
        existing += pc.get("existing", [])
        name = Path(d["upload"]).name
        details += [f"{name}：{x}" for x in pc.get("details", [])]
    clashes = _batch_slug_clashes(items)
    details += clashes
    statuses = {pc["status"] for pc in docs_pc}
    status = "conflict" if clashes or "conflict" in statuses else "overwritable" if "overwritable" in statuses else "ok"
    return {"status": status, "details": details, "existing": existing, "docs": docs_pc}


def _batch_slug_clashes(items: list[dict]) -> list[str]:
    """批内 slug 冲突：逐份预检只看盘上 `raw/`，看不到同批其它份**将要写**的文件（沿 `ingest._reject_source_slug_collision`）。"""
    seen: dict[str, str] = {}
    out: list[str] = []
    for it in items:
        slug = raw_slug(Path(it["raw"]).stem)
        first = seen.setdefault(slug, it["raw"])
        if first != it["raw"]:
            out.append(f"批内：{first} 与 {it['raw']} 的 source 页 slug 相同（`{slug}`）")
    return out


def _precheck_doc(root: Path, task: dict) -> dict:
    """单份的冲突预检（§5、决策P4.30-12）。`raw/<sample>.md` 与 `raw/<sample>/` 作为一个整体检查。

    输入是单份形状的字典（`task_id`、`sample`、`split`、这一份的 `items`）。
    返回 `{"status": "ok" | "overwritable" | "conflict", "details": [...], "existing": [...]}`。
    """
    sample = task["sample"]
    raw = root / "raw"
    targets = {it["raw"] for it in task["items"]}
    split = task.get("split", False)
    existing: list[str] = []
    whole = raw / f"{sample}.md"
    if whole.is_file():
        existing.append(_rel(root, whole))
    def _visible_files(d: Path) -> list[str]:
        # 隐藏文件（macOS Finder 的 `.DS_Store` 等）不是库内容、不属于任何任务：算进来会让覆盖 / 续跑永远判「冲突」。
        return [
            _rel(root, p)
            for p in sorted(d.rglob("*"))
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(d).parts)
        ]

    folder = raw / sample
    if folder.is_dir():
        existing += _visible_files(folder)
    if not split:  # 未拆分的图片落 raw/images/<sample>/，覆盖时会被整盘替换
        img = raw / "images" / sample
        if img.is_dir():
            existing += _visible_files(img)

    details: list[str] = []
    self_ok, self_diffs = _self_owned(root, task)
    details += self_diffs

    # slug 冲突（沿 ingest._reject_source_slug_collision）：在预检阶段就查，不留到 ⑥ 才炸。
    target_slugs = {raw_slug(Path(t).stem): t for t in targets}
    if raw.is_dir():
        for p in sorted(raw.rglob("*")):  # 排序：明细进 `_precheck_signature`，次序须稳定；后缀不分大小写：与 ingest 的 slug 冲突检查同口径（`X.MD` 同样建 source 页）
            if p.suffix.lower() != ".md":
                continue
            rel = _rel(root, p)
            if rel in targets or not p.is_file():
                continue
            slug = raw_slug(p.stem)
            if slug in target_slugs:
                details.append(f"{rel} 与 {target_slugs[slug]} 的 source 页 slug 相同（`{slug}`）")

    remaining = [e for e in existing if e not in self_ok]
    if details:
        return {"status": "conflict", "details": details, "existing": existing}
    if not remaining:
        return {"status": "ok", "details": [], "existing": existing}
    owned = _owned_by_other_tasks(root, task["task_id"])
    md_existing = {e for e in existing if e.endswith(".md") and "/images/" not in e}
    unowned = [e for e in remaining if e not in owned]
    modified = _modified_since_written(root, [e for e in remaining if e in owned], owned)
    if md_existing == targets and not unowned and not modified:
        return {
            "status": "overwritable",
            "details": [f"{len(remaining)} 个文件已由旧的一键入库任务写入，目标集合与本次相同，可覆盖"],
            "existing": existing,
        }
    if md_existing - targets:
        details.append("盘上多出：" + "、".join(sorted(md_existing - targets)[:10]))
    if targets - md_existing and md_existing:
        details.append("本次新增：" + "、".join(sorted(targets - md_existing)[:10]))
    if unowned:
        details.append("不属于任何一键入库任务：" + "、".join(unowned[:10]) + ("…" if len(unowned) > 10 else ""))
    if modified:  # 覆盖会悄悄冲掉手工改动：判冲突，交给用户处理
        details.append("写入后被改动过：" + "、".join(modified[:10]) + ("…" if len(modified) > 10 else ""))
    details.append("首版不做整书替换：请先自行清理 raw/ 中的相关文件后再导入。")
    return {"status": "conflict", "details": details, "existing": existing}


def _precheck_signature(pc: dict) -> list:
    # 批量：逐份状态按序纳入（明细已带份名并入 details），任一份变化都使旧确认失效
    return [pc["status"], sorted(pc.get("existing", [])), pc.get("details", []), [d.get("status") for d in pc.get("docs", [])]]


# ── ①–④ 准备 ────────────────────────────────────────────────────────────────


def _average_item_seconds(root: Path) -> float:
    samples: list[float] = []
    for task in iter_tasks(root):
        for it in task.get("items", []):
            if it.get("state") == "done" and isinstance(it.get("seconds"), (int, float)):
                samples.append(float(it["seconds"]))
    samples = samples[-_RECENT_SAMPLES:]
    return sum(samples) / len(samples) if samples else float(DEFAULT_ITEM_SECONDS)


def _rate(items) -> float | None:
    """每字耗时（秒/字）：取有实测耗时和字数的已入库各篇。分份大小差很多，按字数加权比按篇平均准。"""
    sec = chars = 0.0
    for it in items:
        if it.get("state") == "done" and isinstance(it.get("seconds"), (int, float)) and (it.get("chars") or 0) > 0:
            sec += float(it["seconds"])
            chars += it["chars"]
    return sec / chars if chars > 0 and sec > 0 else None


def _history_rate(root: Path, skip: str | None) -> float | None:
    recent: list[dict] = []
    for task in iter_tasks(root):
        if task.get("task_id") != skip:
            recent += [it for it in task.get("items", []) if it.get("state") == "done"]
    return _rate(recent[-_RECENT_SAMPLES:])


def _elapsed_since(stamp: str | None) -> float:
    if not stamp:
        return 0.0
    try:
        t0 = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return 0.0
    return max(0.0, time.time() - t0)


def progress_info(root: Path, task: dict) -> dict:
    """入库进度（CLI 与 Web 共用）：共几篇、完成几篇、正在入哪一篇及已用多久、累计耗时、预计剩余。

    预计剩余 = 每字耗时 × 未入库各篇字数，减去当前这篇已用的时间（当前篇超出自身估计时按 0 计，不倒扣别的篇）。
    每字耗时先取本任务已入完的各篇；还没有就取本库最近 `_RECENT_SAMPLES` 篇；仍没有就按每篇 `DEFAULT_ITEM_SECONDS`。
    累计耗时只算真正在入库的时间（各篇实测 + 当前篇已用），不含暂停和排队。
    """
    items = task.get("items", [])
    total = len(items)
    done = sum(1 for it in items if it.get("state") == "done")
    cur_idx = next((i for i, it in enumerate(items) if it.get("state") == "running"), None)
    current = None
    cur_elapsed = 0.0
    if cur_idx is not None and task.get("state") == "running":
        it = items[cur_idx]
        cur_elapsed = _elapsed_since(it.get("started"))
        current = {"index": cur_idx + 1, "raw": it.get("raw"), "chars": it.get("chars"), "elapsed_seconds": round(cur_elapsed)}
        if task.get("kind") == "batch" and isinstance(it.get("doc"), int):
            current["doc"] = it["doc"] + 1  # 第几份原件（P4.32），与 `docs_total` 配对显示
    left = [it for it in items if it.get("state") != "done"]
    rate = _rate(items)
    basis = "task"
    if rate is None:
        rate = _history_rate(root, task.get("task_id"))
        basis = "history"
    eta = 0.0
    for i, it in enumerate(items):
        if it.get("state") == "done":
            continue
        est = rate * (it.get("chars") or 0) if rate is not None else float(DEFAULT_ITEM_SECONDS)
        eta += max(0.0, est - cur_elapsed) if i == cur_idx else est
    if rate is None:
        basis = "default"
    spent = sum(float(it["seconds"]) for it in items if it.get("state") == "done" and isinstance(it.get("seconds"), (int, float)))
    out = {
        "total": total,
        "done": done,
        "remaining": len(left),
        "current": current,
        "elapsed_seconds": round(spent + cur_elapsed),
        "eta_seconds": round(eta) if left else 0,
        "eta_basis": basis,
    }
    if task.get("kind") == "batch":
        out["docs_total"] = len(task.get("docs", []))
    return out


def _write_parsed_images(target: Path, images: tuple) -> None:
    """解析图落 `parsed/images/<sample>/`（整盘替换）；无图时清掉旧目录。"""
    real = _images_dir(target)
    if not images and not real.exists():
        return
    bak = _stage_and_swap_images(target, images)
    if bak is not None:
        shutil.rmtree(bak, ignore_errors=True)
    if not images:
        with contextlib.suppress(OSError):
            real.rmdir()


def _clear_part_dir(folder: Path) -> None:
    """清掉上次拆分留下的分卷（只删本目录下的 .md；目录空了再删）。parsed/ 是暂存区。"""
    if not folder.is_dir():
        return
    for p in folder.glob("*.md"):
        with contextlib.suppress(OSError):
            p.unlink()
    with contextlib.suppress(OSError):
        folder.rmdir()


def _origin_label(root: Path, src: Path) -> str:
    """`origin` 沿决策P5.2-11（与 `guanlan convert` 同口径）：库内 → 相对库根，库外 → 绝对路径。"""
    return _default_origin(src.expanduser(), root)


def _check_images(root: Path, items: list[dict], split: bool, sample: str) -> list[str]:
    """图片断链检查（零 LLM）：拆分改写造成的断链 → 抛错；解析时就断的链 → 返回警告。"""
    ws = _ws(root).resolve()
    parsed = ws / "parsed"
    warnings: list[str] = []
    for it in items:
        md = ws / it["parsed"]
        text = md.read_text(encoding="utf-8")
        try:  # 与投喂 / convert 同一道文本闸（空 / 超限 / NUL / 控制字符）：准备阶段就挡下，不让它进 raw/
            check_text_admission(text)
        except ValueError as exc:
            raise OneClickError(f"{it['parsed']}：{exc}") from None
        try:
            col = collect_for_promotion(text, source_md=md, root=parsed, stem=Path(it["raw"]).stem, strict=False)
        except ConvertError as exc:  # 单份图片超容量：准备阶段就挡下，别留到 ⑤ 才炸
            raise OneClickError(f"{it['parsed']}：{exc}") from None
        for url in col.missing:
            # 分卷在 parsed/<sample>/ 下：同一个引用若按整本所在的 parsed/ 解析得到、按分卷位置却找不到，
            # 说明拆分漏改写了它（`../` 前缀没加上）。加过前缀的引用与整本指向同一个文件，不会落进这里。
            if split and _admit_image_ref(url, parsed, parsed) is not None:
                raise OneClickError(f"拆分改写图片引用出错：{it['parsed']} 中的 {url} 找不到（这是一键入库的缺陷，请报告）。")
            warnings.append(f"{Path(it['parsed']).name}：图片 {url} 找不到，原样保留")
        it["image_count"] = len(col.images)
        it["prepared_images"] = {img.name: img.sha256 for img in col.images}  # ⑤ 落盘前复检（图同 md 一样须是确认时的内容）
    return warnings


# 网页空壳判断（P4.31 §4.6）：靠 JS 渲染的页面，转出的 Markdown 只剩「请开启 JavaScript」「加载中…」和几个导航词，
# 进了 raw/ 会让 ingest 对着空壳写 source 页。只数正文字数，不维护特征词表（词表要追着各站改、且漏得无声）。
# 阈值与正文提取的回退阈值（htmlprep.MIN_EXTRACT_CHARS）同为 200：提取不到 200 字已退回整页，整页仍不足即判空壳。
MIN_HTML_BODY_CHARS = 200
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"<?(?:https?|ftp)://[^\s>)]+>?")
_LETTER = re.compile(r"[^\W_]")  # 字母、数字、汉字等：一个算一个
# pandoc 转 gfm 时合并单元格的表格、`<br>`、`<sup>` 等以原始 HTML 留下：标签名与属性不是正文，不计
_HTML_MARKUP = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)


def body_chars(markdown: str) -> int:
    """Markdown 正文「字数」：字母、数字、汉字一个算一个；标点、空白、图片、网址、标记符号不计。

    与正文提取数「可见非空白字符」同一口径（不按词折算：曾按「英文一词折 2 字」，82 词的正常英文通知只得 162、被误判空壳）。
    """
    _, text = split_frontmatter(markdown)
    # 图片语法与 ⑤ 落盘、`_md_images_to_links` 同一个正则（`imageio._MD_IMAGE`）
    text = _URL.sub(" ", _MD_LINK.sub(r" \1 ", _MD_IMAGE.sub(" ", text)))
    return len(_LETTER.findall(_HTML_MARKUP.sub(" ", text)))


# 链接文字里除了图还可以有别的字（`[![logo](a.png) 文档](guide.md)`）：前后文字（不含方括号）一并收进来，
# 否则只认「整段链接文字就是一张图」时，这种写法会被改成嵌套链接、外层相对地址也补全不了
_MD_LINKED_IMAGE = re.compile(
    r"\[(?P<lead>[^\[\]]*?)" + _MD_IMAGE.pattern + r"(?P<trail>[^\[\]]*)\]\((?P<href>[^)\s]+)(?P<hpost>[^)]*)\)"
)
_MD_REF_IMAGE = re.compile(r"!\[([^\]]*)\]\[([^\]]*)\]")  # 引用式图片 `![说明][ref]`
_MD_LINKED_REF_IMAGE = re.compile(r"\[([^\[\]]*?)!\[([^\]]*)\]\[[^\]]*\]([^\[\]]*)\](?=[\[(])")
_MD_PLAIN_LINK = re.compile(r"(?<!!)\[(?P<text>[^\]]*)\]\((?P<pre>\s*)(?P<url>[^)\s]+)(?P<post>[^)]*)\)")
# 引用式链接 / 图片的定义行 `[ref]: 地址`（README 徽章常把地址写在这里）
# `[^1]: 文字` 是脚注定义、不是地址：排除 `[^`，否则脚注首词会被当成相对地址补全
_MD_REF_DEF = re.compile(r"^(?P<lead> {0,3}\[(?!\^)[^\]]+\]:[ \t]*)(?P<url><[^>\n]*>|[^\s<>]+)", re.MULTILINE)
# 引用式图片的简写 `![说明]`（后面不跟 `[` / `(`）；前面带 `[` 的是外层链接里的图
_MD_SHORTCUT_IMAGE = re.compile(r"(?P<lb>\[\s*)?!\[(?P<alt>[^\]]*)\](?![\[(])")
# 引用定义的标签（CommonMark：不分大小写、空白折叠后比较）
_MD_REF_LABEL = re.compile(r"^ {0,3}\[(?!\^)([^\]]+)\]:", re.MULTILINE)


def _ref_label(label: str) -> str:
    return " ".join(label.split()).casefold()


# 代码块（``` / ~~~ 围栏）与行内代码：里面的 `[x](y)` / `[ref]: y` 是示例文字，不补全地址
_MD_CODE = re.compile(
    # 收尾围栏可以比开头长（CommonMark）：`(?P=fence)` 后再收同一种字符，否则 ```` 配 ````` 会一路吞到文末
    r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n.*?(?:^[ ]{0,3}(?P=fence)(?:(?<=`)`*|(?<=~)~*)[ \t]*$|\Z)"
    r"|(?P<ticks>`+)[^\n]*?(?P=ticks)",
    re.MULTILINE | re.DOTALL,
)


def _angle_dest(url: str, post: str) -> tuple[str, str] | None:
    """`<…>` 括起的链接目标（CommonMark 允许其中有空格，图片正则的地址部分只截到第一个空格）：返回 (目标, 其后剩余)；
    不是这种写法 → None。不认的话 `![a](<img/x y.png>)` 会被补全成 `…/img/x`，进了 raw/ 就是错地址。"""
    if not url.startswith("<"):
        return None
    full = url + post
    end = full.find(">")
    if end < 0:
        return None
    return full[1:end], full[end + 1 :]


def _md_dest(url: str) -> str:
    """写回链接目标：含空白的放进 `<…>`（否则 Markdown 在空格处就断了）。"""
    return f"<{url}>" if re.search(r"\s", url) else url


def _img_label(alt: str) -> str:
    alt = " ".join(alt.split())
    return "图：" + alt if alt else "图"


def _md_images_to_links(text: str, base_url: str) -> str:
    """网址导入的 Markdown：`![说明](相对或远程地址)` → `[图：说明](按原网址补全的地址)`；`data:` 图片原样。

    用 ⑤ 落盘收集图片的同一个正则（`imageio._MD_IMAGE`）：两边认的图片语法必须一致——这里漏掉的
    （单引号标题、跨行的说明…）到 ⑤ 会被当成 `parsed/` 里的本地图去找，可能把无关的图搬进 raw/。
    """

    def label(m: re.Match) -> str:
        return _img_label(m.group("head")[2:-2])

    def linked(m: re.Match) -> str:
        # 外层带链接的图（README 徽章 `[![CI](badge.svg)](actions)`）：只留外层链接、说明作文字，
        # 不然会改出 `[[图：CI](…)](…)` 这种嵌套链接（与网页预处理 `in_link` 同口径）
        angle = _angle_dest(m.group("href"), m.group("hpost"))
        href = angle[0] if angle else m.group("href").strip("<>")
        # 锚点与带协议的地址原样（与下面 `rel_link` 同口径）：`#top` 不能补成原网址的 `…#top`
        if not (href.startswith("#") or _URL_SCHEME.match(href)):
            href = urljoin(base_url, href)
        if m.group("url").strip("<>").startswith("data:"):
            # 内嵌图原样留着，外层链接的相对地址照样补全（`_MD_PLAIN_LINK` 认不出外层：链接文字里有 `]`）
            return f"[{m.group(0)[1 : m.end('trail') - m.start()]}]({_md_dest(href)})"
        text = f"{m.group('lead').strip()} {label(m)} {m.group('trail').strip()}".strip()
        return f"[{text}]({_md_dest(href)})"

    def sub(m: re.Match) -> str:
        angle = _angle_dest(m.group("url"), m.group("post"))
        url = angle[0] if angle else m.group("url").strip("<>")
        if url.startswith("data:"):
            return m.group(0)
        return f"[{label(m)}]({_md_dest(urljoin(base_url, url))})"

    text = _MD_LINKED_IMAGE.sub(linked, text)
    text = _MD_IMAGE.sub(sub, text)

    # 以上行内图片改写不避开代码：⑤ 落盘收集图片（`_MD_IMAGE`）也不分代码与正文，两边必须一致（否则代码里的
    # `![x](a.png)` 到 ⑤ 会去 parsed/ 找同名图）。以下的引用式 / 简写图片、普通链接与引用定义 ⑤ 都不认：
    # 代码里的是示例文字（Rust 的 `vec![1, 2]` 会被认成简写图片），先换成占位符（私用区字符）再改写、改完换回
    codes: list[str] = []
    # 占位符用原文里没有的字符：原文自带 `\ue000…` 时（图标字体字形）换回会错位，序号越界还会抛 IndexError
    mark = "\ue000"
    while mark in text:
        mark = chr(ord(mark) + 1)

    def stash(m: re.Match) -> str:
        codes.append(m.group(0))
        return f"{mark}{len(codes) - 1}{mark}"

    text = _MD_CODE.sub(stash, text)

    # 引用式图片 `![说明][ref]` / `![说明][]`（README 徽章常见）：同样改成链接，否则预览仍会去第三方取图。
    # 外层已是链接的（`[![CI][badge]][ci]`）只留说明文字，不改出嵌套链接（同 `linked`）
    text = _MD_LINKED_REF_IMAGE.sub(
        lambda m: "[" + f"{m.group(1).strip()} {_img_label(m.group(2))} {m.group(3).strip()}".strip() + "]", text
    )
    text = _MD_REF_IMAGE.sub(lambda m: f"[{_img_label(m.group(1))}][{m.group(2) or m.group(1)}]", text)
    # 简写 `![说明]` 同理；外层是链接（`[![CI]](…)`）时只留说明文字
    # 只改有定义的：没有 `[说明]: 地址` 时 `![说明]` 只是正文（`What a result![1]`），改了就是篡改原文
    defined = {_ref_label(m.group(1)) for m in _MD_REF_LABEL.finditer(text)}

    def shortcut(m: re.Match) -> str:
        if _ref_label(m.group("alt")) not in defined:
            return m.group(0)
        if m.group("lb"):
            return f"{m.group('lb')}{_img_label(m.group('alt'))}"
        return f"[{_img_label(m.group('alt'))}][{m.group('alt')}]"

    text = _MD_SHORTCUT_IMAGE.sub(shortcut, text)

    # 普通链接的相对地址同样按原网址补全（与网页预处理补全 `<a href>` 同口径）：留着相对地址，进了 raw/ 就指向库里不存在的文件
    def rel_link(m: re.Match) -> str:
        url = m.group("url")
        angle = _angle_dest(url, m.group("post"))
        if angle is not None:  # `[a](<相对 地址.md>)`：括号里的才是目标
            dest, rest = angle
            if not dest or dest.startswith("#") or _URL_SCHEME.match(dest):
                return m.group(0)
            return f"[{m.group('text')}]({m.group('pre')}<{urljoin(base_url, dest)}>{rest})"
        if url.startswith(("#", "<")) or _URL_SCHEME.match(url):
            return m.group(0)
        return f"[{m.group('text')}]({m.group('pre')}{urljoin(base_url, url)}{m.group('post')})"

    # 引用式定义 `[ref]: 相对地址` 同样补全：上面把 `![说明][ref]` 改成了 `[图：说明][ref]`，地址若留相对就指向库里不存在的文件
    def ref_def(m: re.Match) -> str:
        url = m.group("url")
        angle = url.startswith("<")
        dest = url[1:-1] if angle else url
        if not dest or dest.startswith("#") or _URL_SCHEME.match(dest):
            return m.group(0)
        resolved = urljoin(base_url, dest)
        return f"{m.group('lead')}{f'<{resolved}>' if angle else resolved}"

    text = _MD_REF_DEF.sub(ref_def, _MD_PLAIN_LINK.sub(rel_link, text))
    if not codes:
        return text
    return re.sub(re.escape(mark) + r"(\d+)" + re.escape(mark), lambda m: codes[int(m.group(1))], text)


def prepare(
    root: Path,
    src: Path,
    *,
    origin_kind: str,
    backend: str = "auto",
    emit: Emit | None = None,
    dry_run: bool = False,
    toc_reviewer: TocReviewer | None = None,
    stop: threading.Event | None = None,
    html_extract: str = "auto",
) -> dict:
    """①–④（**调用方持锁**）：解析 → 整理 → 拆分 → 预检，停在 `awaiting_confirm`。返回任务清单。

    `dry_run=True`：照跑 ①–④、写 `parsed/`，但**不创建任务记录**（返回的 dict 只用于打印摘要）。
    `html_extract="off"`：HTML 保留整页、不提取正文（P4.31「整页一键入库」，决策P4.31-13）。
    原件旁有有效的网址出处记录（P4.31 §2.5）时，`origin` 记最终网址，另记 `origin_url` / `fetched`。
    `toc_reviewer`：整理时送审目录（#175，见 tocreview.py）；None = 只用规则解析。
    `stop`：Web 的「停止」标志（#173）。置位后转换 / 审校子进程整组结束、步与步之间也检查，抛 `StopRequested`。
    停止（含 CLI 的 Ctrl+C）不是失败：任务记 `cancelled`，可立即重新导入；`parsed/` 残留由下次准备覆盖。
    准备阶段只停不续——重新导入与「从解析这一步续跑」几乎等价（MinerU 有解析缓存，原件仍在暂存区）。
    """
    if html_extract not in ("auto", "off"):  # 参数错先报：不为它整读一遍可达 256 MiB 的原件
        raise OneClickError(f"html_extract 只能是 auto 或 off：{html_extract}")
    if stop is not None and stop.is_set():  # 排队期间就点了停止：任务还没建，什么都不留
        raise StopRequested
    src = Path(src).expanduser()
    if not src.is_file():
        raise OneClickError(f"输入文件不存在：{src}")
    _reject_if_active(root)
    sample = doc_sample(src.stem)
    task_id = _new_task_id(root, sample)
    upload_sha = sha256_file(src)
    if upload_sha is None:
        raise OneClickError(f"输入文件读不出：{src}")
    task: dict = {
        "task_id": task_id,
        "sample": sample,
        "upload": _origin_label(root, src),
        "upload_sha256": upload_sha,
        "origin": origin_kind,
        "state": "preparing",
        "created": _now(),
        "stages": {"parse": "pending", "normalize": "pending", "split": "pending", "confirm": "pending", "land": "pending"},
        "items": [],
        "dry_run": dry_run,
    }
    source = _attach_source(task, src, dry_run=dry_run)
    if html_extract != "auto":
        task["html_extract"] = html_extract
    log = TaskLog(root, None if dry_run else task_id, emit)
    if not dry_run:
        save_task(root, task)
        task["log"] = _rel(root, _log_file(root, task_id))
    return _guarded_prepare(
        root,
        task,
        log,
        dry_run=dry_run,
        stop=stop,
        body=lambda: _prepare_steps(root, src, task, backend=backend, log=log, toc_reviewer=toc_reviewer, source=source),
    )


def doc_sample(stem: str) -> str:
    """原件 stem → 样本名 `<sample>`。准备（`prepare` / `prepare_batch`）与目录扫描的撞车检查共用，
    保证扫描查的就是准备实际要写的路径（决策P4.32-8）。

    `<sample>` 会拼进任务清单 / 日志 / `parsed/<sample>.origin.md` 等文件名：按 UTF-8 字节截短（Linux 文件名按字节计 255）。
    """
    sample = cap_utf8(raw_slug(stem), NAME_STEM_MAX_BYTES) or "import"
    if sample.lower() == "images":  # raw/images/ 是所有未拆文档共用的图片根，不能当本任务的分卷目录
        sample = f"{sample}-doc"
    return sample


def _reject_if_active(root: Path) -> None:
    existing = active_task(root)
    if existing is not None:
        raise OneClickError(
            f"已有未完成的入库任务 {existing['task_id']}（{_STATE_LABEL.get(existing['state'], existing['state'])}）；"
            "请先继续（--resume）或放弃（--cancel）它。"
        )


def _new_task_id(root: Path, name: str) -> str:
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
    task_id, n = base, 1
    while _task_file(root, task_id).exists():  # 同一秒内的第二个任务不能覆盖第一个的清单
        n += 1
        task_id = f"{base}-{n}"
    return task_id


def _attach_source(task: dict, src: Path, *, dry_run: bool) -> dict | None:
    """读网址导入的出处记录（P4.31 §2.5）；哈希对不上（被同名上传覆盖）即作废。须先算好 `task["upload_sha256"]`。

    有效时把去掉凭据参数的网址（签名链接）记进 `task["source_url"]`；完整记录只作返回值传给 `_prepare_steps`、不进清单。
    """
    source = read_source_record(src, sha256=task["upload_sha256"], discard_stale=not dry_run)
    if source is not None:
        task["source_url"] = redact_url(source.get("url") or source["final_url"])
    return source


def _guarded_prepare(
    root: Path, task: dict, log: TaskLog, *, dry_run: bool, stop: threading.Event | None, body: Callable[[], None]
) -> dict:
    """跑准备主体 `body`；停止记 `cancelled`、出错记 `failed`（`dry_run` 不落清单），成功停在 `awaiting_confirm`。"""
    try:
        with stop_scope(stop):
            body()
    except (StopRequested, KeyboardInterrupt):
        # 用户要求停止（Web「停止」/ CLI Ctrl+C）：不是失败，记已放弃，调用方随即释放写锁
        if not dry_run:
            task["state"] = "cancelled"
            task["reason"] = "准备阶段已按要求停止；可重新导入。"
            save_task(root, task)
        log("■ 准备阶段已按要求停止")
        raise
    except BaseException as exc:
        if not dry_run:
            task["state"] = "failed"
            task["reason"] = str(exc) if isinstance(exc, Exception) else "准备阶段被中断"
            save_task(root, task)
            log(f"✗ 准备失败：{task['reason']}")
        raise
    task["state"] = "awaiting_confirm"
    if not dry_run:
        save_task(root, task)
    return task


# ── P4.32 目录批量入库：扫描 → 逐份准备并归并 ─────────────────────────────────

BATCH_MAX_FILES = 50  # 准备阶段全程持锁：封顶最坏情况，超出请分批（§10 Q1）


def _doc_footprint(sample: str) -> list[tuple[str, bool]]:
    """一份原件准备与落盘会写的路径（相对库根，`_prepare_steps` 与 ⑤ 的现状）→ (路径, 是否目录)。

    分卷文件名由拆分决定、扫描时还不知道，但都落在 `parsed/<s>/`、`raw/<s>/` 之下，由目录项覆盖。
    """
    return [
        (f"workspace/parsed/{sample}.md", False),
        (f"workspace/parsed/{sample}.origin.md", False),
        (f"workspace/parsed/{sample}.toc.json", False),
        (f"workspace/parsed/{sample}", True),
        (f"workspace/parsed/images/{sample}", True),
        (f"raw/{sample}.md", False),
        (f"raw/{sample}", True),
        (f"raw/images/{sample}", True),
    ]


def _footprint_clash(a: str, b: str) -> str | None:
    """两个样本名的准备产物是否相撞：任一路径相等，或一方落在另一方的目录之下。返回撞上的路径。

    统一 `casefold()`：macOS 默认文件系统不分大小写（区分大小写的文件系统上会多拒一些，宁可多拒）。
    """
    for p, p_dir in _doc_footprint(a):
        pk = p.casefold()
        for q, q_dir in _doc_footprint(b):
            qk = q.casefold()
            if pk == qk or (p_dir and qk.startswith(pk + "/")):
                return p
            if q_dir and pk.startswith(qk + "/"):
                return q
    return None


def scan_dir(folder: Path) -> tuple[list[Path], list[str]]:
    """目录批量入库的扫描（零 LLM、只读目录项、不读内容，§2）。返回 (要导入的原件, 跳过说明)。

    只收第一层常规文件、扩展名在 `BATCH_IMPORT_EXTS` 内、非空；按文件名码位排序。隐藏文件（含网址导入的
    出处记录 `.<名>.source.json`、`.DS_Store`）静默跳过；子目录、符号链接、不支持的格式、空文件列进跳过说明。
    0 份、超过 `BATCH_MAX_FILES`、准备产物路径相撞 → `OneClickError`。
    """
    files: list[Path] = []
    skipped: list[str] = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name):
        name = p.name
        if name.startswith("."):
            continue
        try:
            if p.is_symlink():
                skipped.append(f"{name}（符号链接，不跟随）")
            elif p.is_dir():
                skipped.append(f"{name}/（子目录，不递归）")
            elif not p.is_file():
                skipped.append(f"{name}（不是普通文件）")
            elif p.suffix.lower() not in BATCH_IMPORT_EXTS:
                skipped.append(f"{name}（不支持的格式）")
            elif p.stat().st_size == 0:
                skipped.append(f"{name}（空文件）")
            else:
                files.append(p)
        except OSError as exc:
            skipped.append(f"{name}（读不出：{exc.strerror or exc}）")
    if not files:
        exts = " ".join(sorted(BATCH_IMPORT_EXTS))
        raise OneClickError(f"目录里没有可导入的文件：{folder}（只收第一层的 {exts}）")
    if len(files) > BATCH_MAX_FILES:
        raise OneClickError(
            f"目录里有 {len(files)} 份可导入的文件，超过单批上限 {BATCH_MAX_FILES} 份；"
            "准备阶段会一直占着库的写锁，请分成几个目录分批导入。"
        )
    samples = [doc_sample(p.stem) for p in files]
    clashes: list[str] = []
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            hit = _footprint_clash(samples[i], samples[j])
            if hit is not None:
                clashes.append(f"{files[i].name} 与 {files[j].name}：都会写 {hit}")
    if clashes:
        raise OneClickError(
            "以下原件准备时会写到同一位置，请改名或挪走后再导入：\n  - " + "\n  - ".join(clashes)
        )
    return files, skipped


# 逐份准备后归并进 `docs[i]` 的字段（准备期字段；逐份预检不存，统一由 `precheck` 现算）
_DOC_KEYS = ("upload_sha256", "split", "source_url", "title", "pages", "chars", "normalized", "warnings")
_DOC_STAGES = ("parse", "normalize", "split")


def prepare_batch(
    root: Path,
    folder: Path,
    *,
    origin_kind: str,
    backend: str = "auto",
    emit: Emit | None = None,
    dry_run: bool = False,
    toc_reviewer: TocReviewer | None = None,
    stop: threading.Event | None = None,
) -> dict:
    """P4.32：一个目录 = 一个批量任务（**调用方持锁**）。扫描一次 → 逐份 ①–④ 并归并 → 整批预检 → `awaiting_confirm`。

    任何一份准备失败 → 整批 `failed`，一篇都不落 `raw/`（决策P4.32-2）；停止 → 整批 `cancelled`。
    `dry_run=True`：照跑、写 `parsed/`，但不创建任务记录（同 `prepare`）。
    """
    if stop is not None and stop.is_set():
        raise StopRequested
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise OneClickError(f"输入目录不存在：{folder}")
    _reject_if_active(root)
    files, skipped = scan_dir(folder)
    name = doc_sample(folder.resolve().name)
    task_id = _new_task_id(root, f"{name}-批量")
    task: dict = {
        "task_id": task_id,
        "kind": "batch",
        "sample": name,  # 只用于任务号和日志；不对应任何 parsed/ 或 raw/ 路径
        "upload": _origin_label(root, folder),
        "origin": origin_kind,
        "state": "preparing",
        "created": _now(),
        "stages": {"confirm": "pending", "land": "pending"},
        # 扫描结果在建任务时就固定（含每份的样本名）：`--status` 与 Web 看得到要准备哪些原件，之后不再重扫
        "docs": [
            {"upload": _origin_label(root, p), "sample": doc_sample(p.stem), "stages": dict.fromkeys(_DOC_STAGES, "pending")}
            for p in files
        ],
        "skipped": skipped,
        "items": [],
        "prepared_sha256": {},
        "dry_run": dry_run,
    }
    log = TaskLog(root, None if dry_run else task_id, emit)
    if not dry_run:
        save_task(root, task)
        task["log"] = _rel(root, _log_file(root, task_id))
    log(
        f"批量入库：{len(files)} 份原件" + (f"，跳过 {len(skipped)} 项" if skipped else "")
        + "；准备期间本库的其它写操作会被拒绝"
    )

    def body() -> None:
        for i, src in enumerate(files):
            _prepare_doc(root, task, i, src, backend=backend, log=log, toc_reviewer=toc_reviewer, dry_run=dry_run)
        task["precheck"] = precheck(root, task)
        task["estimate_seconds"] = round(_average_item_seconds(root) * len(task["items"]))
        for d in _batch_slug_clashes(task["items"]):  # 逐份的明细已由各份准备记进日志，这里只补批内的
            log("  ✋ " + d)

    return _guarded_prepare(root, task, log, dry_run=dry_run, stop=stop, body=body)


def _prepare_doc(
    root: Path,
    task: dict,
    i: int,
    src: Path,
    *,
    backend: str,
    log: TaskLog,
    toc_reviewer: TocReviewer | None,
    dry_run: bool,
) -> None:
    """批量的第 i 份：建一个与单份任务同形的普通字典交给 `_prepare_steps`（它原地改字典），再归并进批量清单（§3.2）。"""
    entry = task["docs"][i]
    n = len(task["docs"])
    log(f"── [{i + 1}/{n}] {src.name}")
    try:
        sha = sha256_file(src)
        if sha is None:
            raise OneClickError("读不出（已被删除或无权限）")
        doc: dict = {
            "task_id": task["task_id"],
            "sample": entry["sample"],
            "upload": entry["upload"],
            "upload_sha256": sha,
            "origin": task["origin"],
            "stages": {"parse": "pending", "normalize": "pending", "split": "pending", "confirm": "pending", "land": "pending"},
            "items": [],
        }
        # 网址下载来的原件：沿用单份的出处记录读取，否则出处退回本地路径、Markdown 相对图片被当成本地附件（复审）
        source = _attach_source(doc, src, dry_run=dry_run)
        _prepare_steps(root, src, doc, backend=backend, log=log, toc_reviewer=toc_reviewer, source=source)
    except (StopRequested, KeyboardInterrupt):
        raise
    except Exception as exc:  # 一份失败即整批失败（决策P4.32-2）：原因指明是哪一份
        raise OneClickError(f"第 {i + 1}/{n} 份 {src.name} 准备失败：{exc}") from exc
    for k in _DOC_KEYS:
        if k in doc:
            entry[k] = doc[k]
    entry["stages"] = {k: doc["stages"][k] for k in _DOC_STAGES}
    for it in doc["items"]:
        it["doc"] = i
    task["items"] += doc["items"]
    task["prepared_sha256"].update(doc["prepared_sha256"])
    if not dry_run:
        save_task(root, task)


def doc_entries(task: dict) -> list[dict]:
    """只读：任务里的各份原件。批量任务即 `docs`；单份任务从顶层字段拼出一项（摘要、Web、`upload_in_use` 用，不写回）。"""
    if task.get("kind") == "batch":
        return list(task.get("docs", []))
    return [{k: task.get(k) for k in ("upload", "upload_sha256", "sample", "split", "source_url")}]


def _prepare_steps(
    root: Path,
    src: Path,
    task: dict,
    *,
    backend: str,
    log: TaskLog,
    toc_reviewer: TocReviewer | None = None,
    source: dict | None = None,
) -> None:
    sample = task["sample"]
    ws = _ws(root)
    parsed = ws / "parsed"
    parsed.mkdir(parents=True, exist_ok=True)
    whole = parsed / f"{sample}.md"
    source = source or {}  # 网址导入的出处记录（P4.31 §2.5）；没有时为空
    # 去掉凭据参数的网址（签名链接）：作相对引用的基址、写进 frontmatter，都只用这两个
    final_url, first_url = redact_url(source.get("final_url") or ""), redact_url(source.get("url") or "")

    # ① 解析
    log(f"① 解析 {src.name} …")
    if src.suffix.lower() == ".md":
        text = src.read_text(encoding="utf-8", errors="replace")
        if source:
            # 网址下载来的 Markdown：相对图片引用指的是原站上的文件，不是暂存区里碰巧同名的本地图片——
            # 不按 uploads/ 收集（否则会把无关的本地上传图搬进 raw/）。与网页同口径（P4.31 §4.4）：图片按原网址补全后
            # 改成普通链接，不留会让预览去第三方取图的图片语法；⑤ 落盘时也就不会去 parsed/ 里找同名图
            res = ConvertResult(markdown=_md_images_to_links(text, final_url))
        else:
            try:  # 图片超容量同样是 ConvertError：与转换分支同口径归一为用法错误，不让 CLI 抛 traceback
                res = collect_and_rewrite(text, produced_md=src.resolve(), root=src.resolve().parent, stem=sample)
            except ConvertError as exc:
                raise OneClickError(f"解析失败：{exc}") from None
        backend_used = None
    else:
        # HTML 选项（P4.31 §4）：字符集、基址来自出处记录，「整页」来自用户；只对 HTML 生效由 convert_to_markdown 判断。
        # 基址给去掉凭据参数的网址：相对引用解析出的地址不带基址的查询串，签名也不进子进程命令行（`ps` 看得见）。
        try:
            res = convert_to_markdown(
                src,
                stem=sample,
                backend=backend,
                cwd=root,
                progress=lambda ln: log("  " + ln),
                html_charset=source.get("charset"),
                html_base_url=final_url or None,
                html_extract=task.get("html_extract", "auto"),
            )
        except ConvertError as exc:
            raise OneClickError(f"解析失败：{exc}") from None
        backend_used = res.backend
    # 403 后经 jina 代取的网页按 .md 落盘（决策P4.31-14），本质仍是网页，同样可能只是个 JS 壳
    if src.suffix.lower() in (".html", ".htm") or source.get("fetched_via") == "jina":
        n = body_chars(res.markdown)
        if n < MIN_HTML_BODY_CHARS:
            raise OneClickError(
                f"网页正文只有约 {n} 字（不足 {MIN_HTML_BODY_CHARS} 字），像是要在浏览器里执行脚本才显示内容的空壳页，未入库。"
                "可以在浏览器里打开后打印成 PDF 再上传。"
            )
    _write_parsed_images(whole, res.images)
    atomic_write_text(parsed / f"{sample}.origin.md", res.markdown)
    task["stages"]["parse"] = "done"
    log(f"  解析完成（{'backend=' + backend_used if backend_used else '已是 markdown'}，{len(res.images)} 张图片）")
    stop_check()

    # ② 整理
    if toc_reviewer is not None and toc_title_lines(res.markdown.split("\n")):
        log("② 整理：发现目录，交模型审校目录结构（只读，约一两分钟；目录区过长时跳过）…")
    norm = normalize(
        res.markdown,
        title_hint=src.stem,
        # 网址导入：出处是网址而不是暂存区路径（P4.31 §5）；抓取日期是出处的一部分（网页会变）
        origin=final_url or task["upload"],
        provenance=(
            {
                "origin_url": first_url if first_url != final_url else None,
                "fetched": (source.get("fetched_at") or "")[:10] or None,
                # 403 后经 agentao 后备取得（决策P4.31-14）：浏览器渲染或第三方代取，内容未必与直接下载一致，记下来
                "fetched_via": source.get("fetched_via") if source.get("fetched_via") in ("playwright", "jina") else None,
            }
            if source
            else None
        ),
        parsed_by=backend_used,
        source_sha256=task["upload_sha256"],
        toc_reviewer=toc_reviewer,
    )
    atomic_write_text(whole, norm.text)
    toc_file = parsed / f"{sample}.toc.json"
    if norm.toc:  # 采用的目录条目落盘，供核对「按目录改了哪些层级」
        summary = norm.meta["normalized"]
        atomic_write_text(
            toc_file,
            json.dumps(
                {k: summary.get(k) for k in ("toc_source", "toc_review", "toc_matched", "toc_applied")}
                | {"entries": norm.toc},
                ensure_ascii=False,
                indent=1,
            )
            + "\n",
        )
    elif toc_file.exists():
        toc_file.unlink()
    for op in norm.ops:
        log("  " + op.describe(), detail=True)
    s = norm.meta["normalized"]
    suspects = [o for o in norm.ops if o.kind == "watermark_suspect"]
    log(
        f"② 整理：删页眉页脚 {s['headers_removed']} 处、页码 {s['page_numbers_removed']} 处、"
        f"水印 {s['watermarks_removed']} 处，接句 {s['sentences_joined']} 处"
        + (f"；{len(suspects)} 条疑似水印已保留（见日志）" if suspects else "")
        + (
            f"；按目录定标题层级（目录 {s['toc_entries']} 条，命中 {s['toc_matched']} 条"
            + ("，经模型审校" if s.get("toc_source") == "llm" else "")
            + "）"
            if s.get("toc_applied")
            else (f"；目录 {s['toc_entries']} 条仅命中 {s['toc_matched']} 条，未按目录改层级" if s.get("toc_entries") else "")
        )
    )
    task["stages"]["normalize"] = "done"
    task["title"] = norm.title
    task["pages"] = norm.pages
    task["chars"] = norm.chars
    task["normalized"] = dict(s)
    stop_check()

    # ③④ 拆分 + 目录索引
    # 已按目录规范化（#175）→ 拆分直接信任标题层级（目录优先）
    parts = split_document(norm.body, sample=sample, trust_levels=bool(s.get("toc_applied")))
    folder = parsed / sample
    _clear_part_dir(folder)
    items: list[dict] = []
    if parts:
        folder.mkdir(parents=True, exist_ok=True)
        for p in parts:
            atomic_write_text(folder / f"{p.stem}.md", render_part(p, sample=sample, doc_meta=norm.meta))
            items.append(
                {
                    "parsed": f"parsed/{sample}/{p.stem}.md",
                    "raw": f"raw/{sample}/{p.stem}.md",
                    "path": p.path,
                    "pages": list(p.pages) if p.pages else None,
                    "chars": p.chars,
                    "method": p.method,
                }
            )
        atomic_write_text(folder / index_name(sample), render_index(parts, sample=sample, doc_meta=norm.meta))
        log(f"③ 拆分：{len(parts)} 份（{min(p.chars for p in parts)}–{max(p.chars for p in parts)} 字），目录索引见 parsed/{sample}/{index_name(sample)}")
        for it in items:
            log(f"  {Path(it['raw']).name}  {it['chars']} 字  {' / '.join(it['path'])}", detail=True)
    else:
        items.append(
            {
                "parsed": f"parsed/{sample}.md",
                "raw": f"raw/{sample}.md",
                "path": [],
                "pages": [1, norm.pages] if norm.pages else None,
                "chars": norm.chars,
                "method": "whole",
            }
        )
        log(f"③ 不拆分（{norm.chars} 字）")
    for it in items:
        it.update(land="pending", state="pending")
    task["items"] = items
    task["split"] = bool(parts)
    task["stages"]["split"] = "done"
    stop_check()

    task["warnings"] = _check_images(root, items, bool(parts), sample)
    for w in task["warnings"]:
        log("  ⚠ " + w, detail=True)
    if task["warnings"]:
        log(f"  ⚠ {len(task['warnings'])} 处图片引用找不到，原样保留（见日志）")
    task["prepared_sha256"] = {it["parsed"]: sha256_file(ws / it["parsed"]) for it in items}
    task["precheck"] = precheck(root, task)
    task["estimate_seconds"] = round(_average_item_seconds(root) * len(items))
    if task["precheck"]["status"] != "ok":
        for d in task["precheck"]["details"]:
            log("  ✋ " + d)


# ── 确认 / 放弃 ──────────────────────────────────────────────────────────────


def cancel(root: Path, task_id: str) -> dict:
    """放弃（awaiting_confirm / paused）：任务记 cancelled，`parsed/` 产物保留。"""
    task = load_task(root, task_id)
    if task.get("state") not in ("awaiting_confirm", "paused"):
        raise OneClickError(f"任务 {task_id} 当前状态为 {task.get('state')}，不能放弃。")
    task["state"] = "cancelled"
    save_task(root, task)
    TaskLog(root, task_id, None)("已放弃")
    return task


def confirm(
    root: Path,
    task_id: str,
    *,
    overwrite: bool = False,
    emit: Emit | None = None,
    ingest: IngestFn,
    should_pause: Callable[[], bool] = lambda: False,
) -> tuple[int, dict]:
    """确认开始（**调用方持锁**）：复检 ①–④ 产物哈希与冲突预检 → ⑤ 落 raw/ → ⑥ 依次入库。"""
    task = load_task(root, task_id)
    log = TaskLog(root, task_id, emit)
    if task.get("state") != "awaiting_confirm":
        raise OneClickError(f"任务 {task_id} 当前状态为 {task.get('state')}，不在等待确认。")
    ws = _ws(root)
    changed = [rel for rel, sha in task.get("prepared_sha256", {}).items() if sha256_file(ws / rel) != sha]
    if changed:
        task["state"] = "failed"
        task["reason"] = "确认前已解析的文件被改动：" + "、".join(changed[:5]) + "。请重新导入。"
        save_task(root, task)
        log("✗ " + task["reason"])
        raise OneClickError(task["reason"])
    pc = precheck(root, task)
    if _precheck_signature(pc) != _precheck_signature(task.get("precheck", pc)):
        task["precheck"] = pc
        save_task(root, task)
        log("✋ raw/ 的情况在确认前发生了变化，请查看新的预检结果后重新确认。")
        raise OneClickError("raw/ 的情况在确认前发生了变化，旧的确认已失效；请重新确认。")
    if pc["status"] == "conflict":
        raise OneClickError("存在不可覆盖的冲突，只能放弃：\n  " + "\n  ".join(pc["details"]))
    if pc["status"] == "overwritable" and not overwrite:
        raise OneClickError("raw/ 中已有本次的目标文件；确认覆盖才能继续（--overwrite / 勾选「覆盖」）。")
    task["overwrite"] = pc["status"] == "overwritable"
    task["stages"]["confirm"] = "done"
    task["state"] = "running"
    task["confirmed"] = _now()
    save_task(root, task)
    log(f"✓ 已确认，开始入库（{len(task['items'])} 份）")
    # 刚复检过、其间 raw/ 未动：把结果交给 ⑤，不再整扫 raw/、重哈希本任务已写文件一遍
    return _execute(root, task, log=log, ingest=ingest, should_pause=should_pause, pc=pc)


def resume(
    root: Path,
    task_id: str,
    *,
    emit: Emit | None = None,
    ingest: IngestFn,
    should_pause: Callable[[], bool] = lambda: False,
    overwrite: bool = False,
) -> tuple[int, dict]:
    """继续（**调用方持锁**）：从清单读出断点，逐项判定跳过/重入/停下。

    `overwrite=True`：暂停后复检发现其余目标变成「可覆盖」（只可能是旧一键入库任务写下的同一组文件）时，
    继续可以补给覆盖确认；它只放宽 overwritable，「冲突」照样拒绝。
    """
    task = load_task(root, task_id)
    log = TaskLog(root, task_id, emit)
    if task.get("state") != "paused":
        raise OneClickError(f"任务 {task_id} 当前状态为 {task.get('state')}，不能继续。")
    pc = None
    if overwrite and not task.get("overwrite"):
        pc = precheck(root, task)
        if pc["status"] == "overwritable":  # 只在此刻确实「可覆盖」时生效，不留宽于需要的授权
            task["overwrite"] = True
            log("已确认覆盖 raw/ 中本任务的旧文件")
        else:
            log("当前没有需要覆盖的旧文件，忽略 --overwrite")
    task["state"] = "running"
    task.pop("reason", None)
    task.pop("paused_by_user", None)  # 继续后不再是「用户暂停」状态，别让旧标志留在清单里
    save_task(root, task)
    log("▶ 继续入库")
    # 刚复检过（持锁、其间 raw/ 未动）就交给 ⑤，不再整扫 raw/、重哈希一遍
    return _execute(root, task, log=log, ingest=ingest, should_pause=should_pause, pc=pc)


def _pause(root: Path, task: dict, log: TaskLog, reason: str) -> None:
    task["state"] = "paused"
    task["reason"] = reason
    save_task(root, task)
    log("⏸ " + reason)


# ── ⑤ 落 raw/ ───────────────────────────────────────────────────────────────


def _land(root: Path, task: dict, log: TaskLog, pc: dict | None = None) -> int:
    ws = _ws(root).resolve()
    if task["stages"].get("land") == "done":
        return EXIT_OK
    if pc is None:
        pc = precheck(root, task)
    if pc["status"] == "conflict" or (pc["status"] == "overwritable" and not task.get("overwrite")):
        task["state"] = "awaiting_confirm" if not any(it.get("land") != "pending" for it in task["items"]) else "paused"
        if task["state"] == "awaiting_confirm":  # 退回等待确认：上一次确认作废，别留「已确认」的痕迹
            task["stages"]["confirm"] = "pending"
            task.pop("confirmed", None)
        task["precheck"] = pc
        task["reason"] = "写入前复检发现 raw/ 有变化：" + "；".join(pc["details"])
        save_task(root, task)
        log("✋ " + task["reason"])
        return EXIT_USAGE
    n = len(task["items"])
    for k, it in enumerate(task["items"], 1):
        if it.get("land") == "written":
            continue
        target = root / it["raw"]
        if it.get("land") == "writing":
            files = {it["raw"]: it.get("sha256")}
            files.update(it.get("images") or {})
            state = {rel: sha256_file(root / rel) for rel in files}
            if all(state[rel] == sha for rel, sha in files.items()):
                it["land"] = "written"
                save_task(root, task)
                log(f"  [{k}/{n}] {it['raw']} 已在盘上（上次写入后未及更新清单），补记为已写入", detail=True)
                continue
            # md 是末步提交：md 不在盘上 = 上次停在「图已换、md 未写」之间，照常重写即可（图会整盘再换）；
            # md 已在盘上却与记录不符 → 写入后被改动，停下报差异。
            if state[it["raw"]] is not None:
                diffs = [rel for rel, sha in files.items() if state[rel] != sha]
                _pause(root, task, log, f"{it['raw']} 上次写到一半，盘上与记录不符：" + "、".join(diffs[:5]))
                return EXIT_USAGE
        md = ws / it["parsed"]
        expected = (task.get("prepared_sha256") or {}).get(it["parsed"])
        if expected is not None and sha256_file(md) != expected:  # 暂停期间 parsed/ 被改：不能落未经确认的内容
            _pause(root, task, log, f"{it['parsed']} 在确认后被改动或删除，与确认时的内容不符；请放弃后重新导入")
            return EXIT_USAGE
        text = md.read_text(encoding="utf-8")
        col = collect_for_promotion(text, source_md=md, root=ws / "parsed", stem=target.stem, strict=False)  # 准入边界 = parsed/
        expected_imgs = it.get("prepared_images")
        if expected_imgs is not None and {img.name: img.sha256 for img in col.images} != expected_imgs:
            _pause(root, task, log, f"{it['parsed']} 引用的图片在确认后被改动或删除；请放弃后重新导入")
            return EXIT_USAGE
        img_dir = _images_dir(target)
        it["sha256"] = _sha_bytes(col.markdown.encode("utf-8"))
        it["images"] = {_rel(root, img_dir / img.name): img.sha256 for img in col.images}
        it["land"] = "writing"
        save_task(root, task)
        overwrite = bool(task.get("overwrite")) or target.exists() and sha256_file(target) == it["sha256"]
        # `atomic_write_raw` 同名冲突时把提示 print 到 stdout：`import --json` 的 stdout 只能有一份 JSON，
        # 故改道 stderr（Web worker 下 stderr 同样被重定向进 job.output，提示不丢）。
        with contextlib.redirect_stdout(sys.stderr):
            code = commit_md_with_images(target, col.markdown, col.images, overwrite=overwrite)
        if code != EXIT_OK:
            _pause(root, task, log, f"写入 {it['raw']} 失败（退出码 {code}）")
            return code
        it["land"] = "written"
        save_task(root, task)
        log(f"  ⑤ [{k}/{n}] 已写 {it['raw']}" + (f"（{len(col.images)} 张图片）" if col.images else ""), detail=True)
    task["stages"]["land"] = "done"
    save_task(root, task)
    log(f"⑤ 已落 raw/：{n} 份")
    return EXIT_OK


# ── ⑥ 依次入库 ──────────────────────────────────────────────────────────────


def _wiki_state(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    wiki = root / "wiki"
    if wiki.is_dir():
        for p in wiki.rglob("*.md"):
            with contextlib.suppress(OSError):
                st = p.stat()
                out[p.relative_to(wiki).as_posix()] = (st.st_mtime_ns, st.st_size)
    return out


def _digest_matches(root: Path, raw_rel: str, raw_sha: str) -> bool:
    """source 页的 `raw_digest` 是否指向本篇的现字节。`raw_sha` 由调用方刚算过（与
    `compute_raw_digest` 同口径：整文件 sha256 十六进制），不再整读一遍 raw 文件。"""
    rel = raw_rel.removeprefix("raw/")
    loc = locate_source_page(root, rel)
    if loc.page is None:
        return False
    meta, _ = load_page(loc.page)
    return parse_digest_value((meta or {}).get("raw_digest")) == (raw_rel, raw_sha)


def _broken_links(root: Path) -> int | None:
    try:
        return sum(1 for v in run_check(root / "wiki").violations if v.kind == "wikilink.broken")
    except Exception:  # 统计是附加信息，失败不影响入库
        return None


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m} 分 {s} 秒" if s else f"{m} 分钟"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"


def _execute(
    root: Path,
    task: dict,
    *,
    log: TaskLog,
    ingest: IngestFn,
    should_pause: Callable[[], bool],
    pc: dict | None = None,
) -> tuple[int, dict]:
    try:
        code = _land(root, task, log, pc)
    except KeyboardInterrupt:  # 第二次 Ctrl+C / SIGTERM：同样不留 running（`writing` 项续跑时由 ⑤ 自行补记或重写）
        _pause(root, task, log, "落 raw/ 时被中止，已暂停；继续会从未写完的那一份重试")
        raise
    except Exception as exc:  # 与 ⑥ 同口径：不留 running（否则放弃被拒、只能等下次取锁恢复）
        _pause(root, task, log, f"落 raw/ 异常（{type(exc).__name__}: {exc}），已暂停；处理后继续会从未写完的那一份重试")
        raise
    if code != EXIT_OK:
        return code, task
    try:
        return _ingest_items(root, task, log=log, ingest=ingest, should_pause=should_pause)
    except BaseException as exc:
        # 逐篇 ingest 之外的地方被中断（第二次 Ctrl+C / SIGTERM 落在统计、落清单等处）或异常：同样不留 running，
        # 否则 Web 一直显示「入库中」、只能等下次取锁恢复。ingest 本身的中断/异常已就地暂停，这里不重复。
        if task.get("state") == "running":
            for x in task["items"]:
                if x.get("state") == "running":
                    x["state"] = "pending"
            with contextlib.suppress(Exception):
                _pause(root, task, log, f"入库被中断（{type(exc).__name__}），已暂停；继续会从未完成的那一篇重试")
        raise


def _ingest_items(
    root: Path, task: dict, *, log: TaskLog, ingest: IngestFn, should_pause: Callable[[], bool]
) -> tuple[int, dict]:
    """⑥ 依次入库（⑤ 已落盘之后）。"""
    items = task["items"]
    n = len(items)
    if task.get("links_before") is None:
        task["links_before"] = _broken_links(root)
        save_task(root, task)
    started = time.monotonic()
    wiki_now: dict[str, tuple[int, int]] | None = None  # 上一篇入库后的 wiki/ 状态（持锁、篇间无人写）
    for k, it in enumerate(items, 1):
        raw_path = root / it["raw"]
        cur = sha256_file(raw_path)
        if cur != it.get("sha256"):
            _pause(root, task, log, f"{it['raw']} 与任务记录不一致（被改动或删除），请核对后再继续")
            return EXIT_USAGE, task
        if it.get("state") == "done" and _digest_matches(root, it["raw"], cur):
            log(f"[{k}/{n}] 跳过 {it['raw']}（本任务已入库且指纹一致）")
            continue
        if should_pause():  # 放在跳过判定之后：剩下的都已入库时，暂停请求不必把任务停在「已暂停」
            task["paused_by_user"] = True  # 先置位，`_pause` 一次落盘
            _pause(root, task, log, f"已按要求暂停（{k - 1}/{n} 篇已处理）")
            return EXIT_OK, task
        it["state"] = "running"
        it["started"] = _now()
        it.pop("exit_code", None)
        save_task(root, task)
        pi = progress_info(root, task)
        eta = f"，预计剩余约 {_fmt_duration(pi['eta_seconds'])}" if pi["eta_seconds"] else ""
        where = f"第 {it['doc'] + 1}/{len(task['docs'])} 份原件；" if task.get("kind") == "batch" and isinstance(it.get("doc"), int) else ""
        log(f"[{k}/{n}] 入库 {it['raw']}（{where}{it.get('chars', 0)} 字；已完成 {pi['done']} 篇、剩 {pi['remaining']} 篇{eta}）…")
        before = wiki_now if wiki_now is not None else _wiki_state(root)
        t0 = time.monotonic()
        try:
            rc = ingest(it["raw"], root)
        except KeyboardInterrupt:
            it["state"] = "pending"
            _pause(root, task, log, f"已立即中止；{it['raw']} 的 wiki 写入可能未经门禁收尾，请先运行 `guanlan check`")
            raise
        except Exception as exc:  # 不留 running：否则 Web 卡在「入库中」、放弃也被拒，只能等重启恢复
            it["state"] = "failed"
            _pause(root, task, log, f"[{k}/{n}] {it['raw']} 入库异常（{type(exc).__name__}: {exc}），已暂停；处理后继续会从这一篇重试")
            raise
        took = time.monotonic() - t0
        it.update(finished=_now(), seconds=round(took, 1), exit_code=rc)
        if rc != EXIT_OK:
            it["state"] = "failed"
            _pause(root, task, log, f"[{k}/{n}] {it['raw']} 入库失败（退出码 {rc}），已暂停；处理后继续会从这一篇重试")
            return rc, task
        it["state"] = "done"
        save_task(root, task)  # 先记完成再做统计：统计期间被中断不至于把已入库的这篇打回重跑
        after = wiki_now = _wiki_state(root)
        created = sum(1 for p in after if p not in before)
        updated = sum(1 for p, v in after.items() if p in before and before[p] != v)
        log(f"[{k}/{n}] 入库 {it['raw']} … 完成（{_fmt_duration(took)}，wiki 新建 {created} 页、更新 {updated} 页）")
        pi = progress_info(root, task)
        if pi["remaining"]:
            log(
                f"进度 {pi['done']}/{n} 篇 · 本次已用 {_fmt_duration(time.monotonic() - started)}"
                f" · 预计剩余约 {_fmt_duration(pi['eta_seconds'])}（{pi['remaining']} 篇）"
            )
    task["state"] = "done"
    task["finished"] = _now()
    task.pop("reason", None)
    task["links_after"] = _broken_links(root)
    save_task(root, task)
    log(f"✓ 一键入库完成：{n} 份")
    lb, la = task.get("links_before"), task.get("links_after")
    if lb is not None and la is not None:
        log(f"断链：入库前 {lb} 处 → 入库后 {la} 处" + ("；可运行 `guanlan heal --dry-run` 查看可补的页面" if la else ""))
    return EXIT_OK, task


# ── 摘要（CLI 确认 / --status / Web 共用） ───────────────────────────────────

_STATE_LABEL = {
    "preparing": "准备中",
    "awaiting_confirm": "等待确认",
    "running": "入库中",
    "paused": "已暂停",
    "done": "已完成",
    "failed": "失败",
    "cancelled": "已放弃",
}


def summary_lines(task: dict, *, detail: bool = False, root: Path | None = None) -> list[str]:
    items = task.get("items", [])
    est = task.get("estimate_seconds")
    label = "预演，未建任务" if task.get("dry_run") else _STATE_LABEL.get(task.get("state"), task.get("state"))
    lines = [f"任务 {task['task_id']}（{label}）"]
    if task.get("kind") == "batch":
        lines += _batch_doc_lines(task)
        lines.append(f"  合计：{len(items)} 篇" + (f" · 预计耗时约 {_fmt_duration(est)}" if est else ""))
    else:
        lines += [
            f"  文件：{task.get('upload')}",
            f"  分份：{len(items)} 份" + (f" · 预计耗时约 {_fmt_duration(est)}" if est else ""),
        ]
    done = sum(1 for it in items if it.get("state") == "done")
    if task.get("state") in ("running", "paused", "done"):
        lines.append(f"  进度：{done}/{len(items)} 篇已入库")
        if root is not None and task.get("state") in ("running", "paused"):
            pi = progress_info(root, task)
            cur = pi["current"]
            if cur:
                where = f"（第 {cur['doc']}/{pi['docs_total']} 份原件）" if cur.get("doc") else ""
                lines.append(f"  当前：第 {cur['index']} 篇 {cur['raw']}{where}（已用 {_fmt_duration(cur['elapsed_seconds'])}）")
            lines.append(
                f"  耗时：已用 {_fmt_duration(pi['elapsed_seconds'])} · 预计剩余约 {_fmt_duration(pi['eta_seconds'])}"
                f"（{pi['remaining']} 篇）"
            )
    if task.get("reason"):
        lines.append(f"  说明：{task['reason']}")
    pc = task.get("precheck") or {}
    if pc.get("status") == "overwritable":
        lines.append("  ⚠ 可覆盖：" + "；".join(pc.get("details", [])))
    elif pc.get("status") == "conflict":
        lines.append("  ✋ 冲突（不可覆盖，只能放弃）：")
        lines += [f"    - {d}" for d in pc.get("details", [])]
    if detail:
        s = task.get("normalized") or _normalized_total(task)
        if s:
            lines.append(
                f"  整理：页眉页脚 {s.get('headers_removed', 0)}、页码 {s.get('page_numbers_removed', 0)}、"
                f"水印 {s.get('watermarks_removed', 0)}、接句 {s.get('sentences_joined', 0)}"
            )
        warnings = task.get("warnings") or [w for d in task.get("docs", []) for w in d.get("warnings") or []]
        if warnings:
            lines.append(f"  图片：{len(warnings)} 处引用找不到，原样保留")
        for k, it in enumerate(items, 1):
            pages = f"第 {it['pages'][0]}–{it['pages'][1]} 页 · " if it.get("pages") else ""
            path = " / ".join(it.get("path") or []) or "—"
            lines.append(f"  {k:>3}. {it['raw']}  [{pages}{it.get('chars', 0)} 字]  {path}  ({it.get('state')})")
    return lines


def _batch_doc_lines(task: dict) -> list[str]:
    """批量任务的摘要：目录、逐份原件（拆分结果、逐份预检标记）、跳过项（P4.32 §6）。"""
    docs = task.get("docs", [])
    items = task.get("items", [])
    skipped = task.get("skipped") or []
    n = len(docs)
    lines = [f"  目录：{task.get('upload')}（{n} 份原件" + (f"，跳过 {len(skipped)} 项" if skipped else "") + "）"]
    docs_pc = (task.get("precheck") or {}).get("docs") or []
    for i, d in enumerate(docs):
        its = [it for it in items if it.get("doc") == i]
        if not its:
            desc = "未准备"
        elif d.get("split"):
            cs = [it.get("chars") or 0 for it in its]
            desc = f"拆 {len(its)} 份（{min(cs)}–{max(cs)} 字），raw/{d['sample']}/"
        else:
            desc = f"不拆（{its[0].get('chars') or 0} 字），{its[0]['raw']}"
        st = docs_pc[i].get("status") if i < len(docs_pc) else None
        mark = "   ⚠ 可覆盖" if st == "overwritable" else "   ✋ 冲突" if st == "conflict" else ""
        lines.append(f"  [{i + 1}/{n}] {Path(d['upload']).name} → {desc}{mark}")
    if skipped:
        lines.append("  已跳过：" + "、".join(skipped))
    return lines


def _normalized_total(task: dict) -> dict:
    """批量：各份整理统计的合计（只合计摘要里显示的几项）。"""
    keys = ("headers_removed", "page_numbers_removed", "watermarks_removed", "sentences_joined")
    total = dict.fromkeys(keys, 0)
    found = False
    for d in task.get("docs", []):
        s = d.get("normalized") or {}
        if s:
            found = True
            for k in keys:
                total[k] += s.get(k) or 0
    return total if found else {}


# ── CLI：guanlan import ──────────────────────────────────────────────────────


_ABORT_SIGNALS = tuple(getattr(signal, n) for n in ("SIGHUP", "SIGTERM") if hasattr(signal, n))


class _PauseFlag:
    """CLI Ctrl+C：第一次 = 本篇入完后暂停；第二次 = 立即中止（抛 KeyboardInterrupt）。

    ingest 子进程在新会话里（`start_new_session`），关终端的 SIGHUP、外部 SIGTERM 都到不了它；
    这两个信号在父进程里一律按「立即中止」处理，`runtime._run_in_new_session` 随之整组杀掉子进程——否则父进程一死，
    agentao 会在没有门禁收尾、库级锁已释放的情况下继续写 wiki/。
    """

    def __init__(self) -> None:
        self.event = threading.Event()
        self._old = None
        self._old_abort: dict[int, object] = {}

    def __call__(self) -> bool:
        return self.event.is_set()

    def _handler(self, signum, frame) -> None:  # noqa: ARG002
        if not self.event.is_set():
            self.event.set()
            print("\n⏸ 将在当前这篇入库完成后暂停（再按一次 Ctrl+C 立即中止）。", file=sys.stderr, flush=True)
            return
        signal.signal(signal.SIGINT, signal.default_int_handler)
        raise KeyboardInterrupt

    @staticmethod
    def _abort(signum, frame) -> None:  # noqa: ARG004
        raise KeyboardInterrupt

    def __enter__(self) -> _PauseFlag:
        if threading.current_thread() is threading.main_thread():
            self._old = signal.signal(signal.SIGINT, self._handler)
            for sig in _ABORT_SIGNALS:
                # 继承来的「忽略」（如 `nohup guanlan import` 对 SIGHUP）照旧忽略：不能把关终端变成立即中止。
                if signal.getsignal(sig) is signal.SIG_IGN:
                    continue
                self._old_abort[sig] = signal.signal(sig, self._abort)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._old is not None:
            signal.signal(signal.SIGINT, self._old)
        for sig, old in self._old_abort.items():
            signal.signal(sig, old)
        self._old_abort.clear()


def _default_ingest(model: str | None, thinking: str | None) -> IngestFn:
    from .ingest import run_ingest
    from .runtime import _subprocess_runner

    runner = functools.partial(_subprocess_runner, new_session=True)

    def run(raw_rel: str, root: Path) -> int:
        # ingest 的人读回执（Agent 摘要、门禁结论）打在 stdout；import 的 stdout 只留最终回执（--json 时为 JSON），
        # 故逐篇 ingest 的输出一律转到 stderr，与进展日志同处。
        with contextlib.redirect_stdout(sys.stderr):
            return run_ingest(raw_rel, root=root, model=model, thinking=thinking, runner=runner)

    return run


def _default_toc_reviewer(root: Path, model: str | None, thinking: str | None) -> TocReviewer:
    from .runtime import _subprocess_runner
    from .tocreview import make_toc_reviewer

    # 与逐篇 ingest 同：子进程放进新会话，第一次 Ctrl+C 不连带打断它
    return make_toc_reviewer(
        root, model=model, thinking=thinking, runner=functools.partial(_subprocess_runner, new_session=True)
    )


# `--json` 契约：每条出口在 stdout 恰好给一份 JSON。回执路径置 `emitted`；其余（用法错误、找不到任务等）
# 由 `import_entrypoint` 末尾补一份 `{"ok": false, ...}`，错误文本取最后一条 stderr 消息。
_JSON_OUT = {"emitted": False, "last_error": None}


def _stderr(line: str) -> None:
    _JSON_OUT["last_error"] = line
    print(line, file=sys.stderr, flush=True)


def _progress(line: str) -> None:
    """进度行（`TaskLog` 的 emit）：前加 `[HH:MM:SS]`，与 Web 浮层同口径；错误与摘要仍走 `_stderr`、不加时间。
    `last_error` 记原文，`--json` 回执里的错误不带时间前缀。"""
    _JSON_OUT["last_error"] = line
    print(f"[{time.strftime('%H:%M:%S')}] {line}", file=sys.stderr, flush=True)


def _receipt(task: dict, json_output: bool, *, root: Path | None = None) -> None:
    if json_output:
        keep = (
            "task_id", "kind", "state", "reason", "upload", "sample", "split", "docs", "skipped",
            "precheck", "warnings", "estimate_seconds", "links_before", "links_after",
        )
        out = {k: task.get(k) for k in keep if k in task}
        out["items"] = [
            {k: it.get(k) for k in ("doc", "raw", "parsed", "chars", "pages", "path", "land", "state", "exit_code", "seconds") if k != "doc" or "doc" in it}
            for it in task.get("items", [])
        ]
        if root is not None and task.get("state") in ("running", "paused"):
            out["progress"] = progress_info(root, task)  # --status --json：给脚本看的进度
        print(json.dumps(out, ensure_ascii=False, indent=2))
        _JSON_OUT["emitted"] = True
    else:
        print("\n".join(summary_lines(task, root=root)))


def import_entrypoint(
    root_dir: str | Path,
    *,
    src: str | None,
    dry_run: bool = False,
    yes: bool = False,
    overwrite: bool = False,
    do_resume: bool = False,
    status: bool = False,
    do_cancel: bool = False,
    json_output: bool = False,
    model: str | None = None,
    thinking: str | None = None,
    backend: str = "auto",
    ingest: IngestFn | None = None,
    interactive: bool | None = None,
    toc_review: bool = True,
    toc_reviewer: TocReviewer | None = None,
) -> int:
    """`guanlan import` 的单一落地。`ingest` / `interactive` / `toc_reviewer` 供测试注入。

    `toc_review=False`（`--no-toc-review`）：目录只用规则解析，不调模型。
    """
    _JSON_OUT.update(emitted=False, last_error=None)
    try:
        code = _import_main(
            root_dir,
            src=src,
            dry_run=dry_run,
            yes=yes,
            overwrite=overwrite,
            do_resume=do_resume,
            status=status,
            do_cancel=do_cancel,
            json_output=json_output,
            model=model,
            thinking=thinking,
            backend=backend,
            ingest=ingest,
            interactive=interactive,
            toc_review=toc_review,
            toc_reviewer=toc_reviewer,
        )
    except Exception as exc:  # 意外异常（任务已就地暂停）：--json 照样给一份 JSON，再原样上抛
        if json_output and not _JSON_OUT["emitted"]:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "exit_code": None, "error": err}, ensure_ascii=False))
        raise
    if json_output and not _JSON_OUT["emitted"]:
        print(json.dumps({"ok": False, "exit_code": code, "error": _JSON_OUT["last_error"]}, ensure_ascii=False))
    return code


def _import_main(
    root_dir: str | Path,
    *,
    src: str | None,
    dry_run: bool,
    yes: bool,
    overwrite: bool,
    do_resume: bool,
    status: bool,
    do_cancel: bool,
    json_output: bool,
    model: str | None,
    thinking: str | None,
    backend: str,
    ingest: IngestFn | None,
    interactive: bool | None,
    toc_review: bool = True,
    toc_reviewer: TocReviewer | None = None,
) -> int:
    modes = sum(bool(x) for x in (src, do_resume, status, do_cancel))
    if modes != 1:
        _stderr("用法：guanlan import <文件或目录> | --resume | --status | --cancel（四选一）。")
        return EXIT_USAGE
    if dry_run and not src:  # --dry-run 只对 `import <file>` 有意义：别让 `--resume --dry-run` 悄悄真跑入库 / 真放弃
        _stderr("--dry-run 只能与 `import <文件或目录>` 同用（--resume / --status / --cancel 不支持预演）。")
        return EXIT_USAGE
    try:
        root = require_kb_root(root_dir, writable=not status)
    except GuanlanError as exc:
        _stderr(str(exc))
        return exc.exit_code
    if not status and inside_write_run(root):
        # Agent 在 ingest / 可写 turn 里照技能说明跑 `guanlan import`：嵌套放行会让它在外层写门禁与快照里
        # 连续入库多篇。一键入库只由用户直接发起。
        _stderr("一键入库不能在另一个写运行（ingest / 可写对话 / 一键入库作业）里嵌套执行；请由用户在外层结束后直接运行。")
        return EXIT_USAGE
    if interactive is None:
        interactive = sys.stdin.isatty()
    if src and not dry_run and not yes and not interactive:
        _stderr("非交互终端须加 --yes 才能开始入库（或用 --dry-run 只看确认摘要）。")
        return EXIT_USAGE
    run_ingest_fn = ingest or _default_ingest(model, thinking)
    try:
        if status:
            return _cli_status(root, json_output)
        if do_cancel:
            return _cli_cancel(root, json_output)
        if do_resume:
            return _cli_resume(
                root, json_output, run_ingest_fn, overwrite=overwrite, yes=yes, interactive=interactive
            )
        return _cli_import(
            root,
            Path(src),  # type: ignore[arg-type]
            dry_run=dry_run,
            yes=yes,
            overwrite=overwrite,
            json_output=json_output,
            backend=backend,
            ingest=run_ingest_fn,
            interactive=interactive,
            # 目录审校默认开启（#175），--dry-run 也审：预演的拆分结果须与正式运行一致；--no-toc-review 关掉
            toc_reviewer=(toc_reviewer or _default_toc_reviewer(root, model, thinking)) if toc_review else None,
        )
    except GuanlanError as exc:  # 含 KBLockBusy
        _stderr(str(exc))
        return exc.exit_code
    except (ConvertError, OSError) as exc:  # 图片超容量 / 落盘 IO 失败：任务已记失败或暂停，不抛 traceback
        _stderr(f"一键入库中止：{exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        _stderr("已中止。")
        return EXIT_USAGE


def _cli_status(root: Path, json_output: bool) -> int:
    busy = False
    task = active_task(root)
    # 只有可能需要恢复（preparing / running）时才去取写锁：库级锁不排队，--status 这种只读查询白占一下锁，
    # 恰好此刻起步的 Web 作业 / CLI 写命令就会被误报「库被占用」。
    if task is not None and task.get("state") in ("preparing", "running"):
        try:
            lock = acquire(root, "cli: import --status")
        except KBLockBusy:
            lock, busy = None, True
        except OSError:  # 只读库等：建不出锁文件，不是「有人在写」
            lock = None
        try:
            _recover_if_owner(root, lock)
            task = active_task(root)
        finally:
            if lock is not None:
                lock.release()
    if busy:
        _stderr(f"库正在被写（{describe_holder(read_holder(root))}）；以下为清单原样，未做恢复。")
    if task is None:
        if json_output:
            print("null")
            _JSON_OUT["emitted"] = True
        else:
            print("没有未完成的一键入库任务。")
        return EXIT_OK
    if json_output:
        _receipt(task, True, root=root)
    else:
        print("\n".join(summary_lines(task, detail=True, root=root)))
        print(f"  日志：{task.get('log')}")
    return EXIT_OK


def _cli_cancel(root: Path, json_output: bool) -> int:
    with acquire(root, "cli: import --cancel") as lock:
        _recover_if_owner(root, lock)
        task = active_task(root)
        if task is None:
            _stderr("没有未完成的一键入库任务。")
            return EXIT_USAGE
        task = cancel(root, task["task_id"])
    _receipt(task, json_output)
    return EXIT_OK


def _cancel_locked(root: Path, task_id: str) -> dict:
    """放弃本次刚准备好的任务。锁恰好被别的写者拿着 → 不抛：任务留在等待确认，提示稍后 --cancel。"""
    try:
        with acquire(root, "cli: import --cancel"):
            return cancel(root, task_id)
    except KBLockBusy as exc:
        _stderr(f"{exc}\n任务 {task_id} 仍在等待确认；等它结束后运行 `guanlan import --cancel` 放弃。")
        return load_task(root, task_id)
    except OneClickError:  # 提示期间任务已在别处（Web）确认 / 跑完：不能放弃，如实报告它现在的状态
        task = load_task(root, task_id)
        _stderr(f"任务 {task_id} 已在别处推进（{_STATE_LABEL.get(task['state'], task['state'])}），未放弃。")
        return task


def _ask_start() -> bool:
    try:
        print("开始入库？[y/N] ", end="", file=sys.stderr, flush=True)
        answer = input().strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")


def _run_with_pause(fn: Callable[..., tuple[int, dict]], *args, **kwargs) -> tuple[int, dict]:
    with _PauseFlag() as flag:
        return fn(*args, should_pause=flag, **kwargs)


def _cli_resume(
    root: Path,
    json_output: bool,
    ingest: IngestFn,
    *,
    overwrite: bool = False,
    yes: bool = False,
    interactive: bool = False,
) -> int:
    with acquire(root, "cli: import --resume") as lock:
        _recover_if_owner(root, lock)
        task = active_task(root)
        if task is None:
            _stderr("没有未完成的一键入库任务。")
            return EXIT_USAGE
        state = task["state"]
        if state == "paused":
            code, task = _run_with_pause(resume, root, task["task_id"], overwrite=overwrite, emit=_progress, ingest=ingest)
            _receipt(task, json_output)
            return code
        if state != "awaiting_confirm":
            _stderr("\n".join(summary_lines(task)))
            _stderr("该任务不是「等待确认」或「已暂停」状态，无法继续。")
            return EXIT_USAGE
    # 等待确认：与 `import <file>` 的确认同一口径（问答期间不持锁；确认本身在锁内复检）
    pc = precheck(root, task)  # 现查（只读、不落清单）：存下的预检可能已过时；确认时仍在锁内复检
    # 摘要与回执（--json）都报现查结果：别让摘要/stdout 仍是过时的预检、结论却说冲突。只改内存。
    task = {**task, "precheck": pc}
    _stderr("\n".join(summary_lines(task, detail=False)))  # 冲突/可覆盖的明细已在摘要里
    # 与 `import <file>` 同口径：明知会被 confirm 拒的情形先说清，别等用户答了「y」再拒。
    if pc.get("status") == "conflict":
        _stderr("存在不可覆盖的冲突，只能放弃（--cancel）。")
        if json_output:
            _receipt(task, True)
        return EXIT_USAGE
    if pc.get("status") == "overwritable" and not overwrite:
        _stderr("raw/ 中已有本次的目标文件；如确认覆盖，请加 --overwrite 再 --resume。")
        if json_output:
            _receipt(task, True)
        return EXIT_USAGE
    if not yes and not interactive:
        _stderr("非交互终端须加 --yes 才能确认开始入库。")
        return EXIT_USAGE
    try:
        start = yes or _ask_start()
    except KeyboardInterrupt:  # 在确认提示处 Ctrl+C = 回答「否」（同 `_cli_import`）
        start = False
    if not start:
        _stderr("未开始；任务仍在等待确认（可再 --resume，或 --cancel 放弃）。")
        if json_output:  # --json 契约：正常结局给回执，不落到兜底的 {"ok": false, "exit_code": 0}
            _receipt(task, True)
        return EXIT_OK
    with acquire(root, "cli: import --resume"):
        # 用户确认的是上面现查的预检：落进清单作为 confirm 的比对基线，否则存下的旧预检（如准备时的冲突、
        # 用户已清理）会让 confirm 判「raw/ 有变化」而拒绝这次确认。confirm 仍在锁内再复检一次。
        cur = load_task(root, task["task_id"])
        if cur.get("state") == "awaiting_confirm" and cur.get("precheck") != pc:
            cur["precheck"] = pc
            save_task(root, cur)
        code, task = _run_with_pause(confirm, root, task["task_id"], overwrite=overwrite, emit=_progress, ingest=ingest)
    _receipt(task, json_output)
    return code


def _cli_import(
    root: Path,
    src: Path,
    *,
    dry_run: bool,
    yes: bool,
    overwrite: bool,
    json_output: bool,
    backend: str,
    ingest: IngestFn,
    interactive: bool,
    toc_reviewer: TocReviewer | None = None,
) -> int:
    origin = "cli: import --dry-run" if dry_run else "cli: import"
    # 目录 = 一个批量任务（P4.32）；其后的确认、落盘、入库与单份同一条路
    prep = prepare_batch if src.expanduser().is_dir() else prepare
    try:
        with acquire(root, origin) as lock:
            _recover_if_owner(root, lock)
            task = prep(
                root, src, origin_kind="cli", backend=backend, emit=_progress, dry_run=dry_run, toc_reviewer=toc_reviewer
            )
    except KeyboardInterrupt:
        # 准备阶段第一次 Ctrl+C 就立即停止（#173）：转换子进程与本进程同组、一并收到 SIGINT；目录审校在新会话里，
        # 由 `_run_in_new_session` 整组结束。prepare 已把任务记为已放弃，锁随 with 释放。
        _stderr("已停止准备" + ("。" if dry_run else "；任务已放弃，可重新导入。"))
        return EXIT_USAGE
    # 进入 awaiting_confirm：释放锁（等待确认期间不妨碍其它写操作）
    if dry_run:
        if json_output:
            _receipt(task, True)
        else:
            print("\n".join(summary_lines(task, detail=True)))
            print("（--dry-run：未创建任务记录，未写 raw/；中间产物在 workspace/parsed/）")
        return EXIT_OK
    _stderr("\n".join(summary_lines(task, detail=False)))
    pc = task["precheck"]
    if pc["status"] == "conflict":
        task = _cancel_locked(root, task["task_id"])
        _stderr("存在不可覆盖的冲突，任务已放弃。")
        if json_output:  # --json 的 stdout 契约：任何结局都给一份 JSON 回执，不留空 stdout
            _receipt(task, True)
        return EXIT_USAGE
    if pc["status"] == "overwritable" and not overwrite:
        task = _cancel_locked(root, task["task_id"])
        _stderr("raw/ 中已有本次的目标文件；如确认覆盖，请加 --overwrite 重新导入。任务已放弃。")
        if json_output:
            _receipt(task, True)
        return EXIT_USAGE
    try:
        start = yes or _ask_start()
    except KeyboardInterrupt:  # 在确认提示处 Ctrl+C = 回答「否」：不留一个挡住下次导入的等待确认任务
        start = False
    if not start:
        task = _cancel_locked(root, task["task_id"])
        _stderr("已放弃；parsed/ 中的产物保留。")
        if json_output:
            _receipt(task, True)
        return EXIT_OK
    with acquire(root, "cli: import"):
        code, task = _run_with_pause(confirm, root, task["task_id"], overwrite=overwrite, emit=_progress, ingest=ingest)
    _receipt(task, json_output)
    return code
