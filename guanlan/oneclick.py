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
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .check import run_check
from .convert import _default_origin, convert_to_markdown
from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .imageio import (
    ConvertError,
    _admit_image_ref,
    _images_dir,
    _stage_and_swap_images,
    collect_and_rewrite,
    collect_for_promotion,
    commit_md_with_images,
)
from .kblock import KBLockBusy, acquire, describe_holder, read_holder
from .normalize import normalize
from .pages import load_page
from .paths import require_kb_root
from .provenance import compute_raw_digest, parse_digest_value
from .rawio import atomic_write_text, raw_slug
from .sourcepage import locate_source_page
from .splitdoc import index_name, render_index, render_part, split_document

__all__ = [
    "ACTIVE_STATES",
    "OneClickError",
    "active_task",
    "cancel",
    "confirm",
    "import_entrypoint",
    "iter_tasks",
    "load_task",
    "prepare",
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


def _sha_file(path: Path) -> str | None:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError:
        return None


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
        else:
            continue
        save_task(root, task)
        TaskLog(root, task["task_id"], None)(f"恢复：{state} → {task['state']}")
        changed.append(task["task_id"])
    return changed


# ── 预检（§5） ───────────────────────────────────────────────────────────────


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _owned_by_other_tasks(root: Path, exclude: str) -> set[str]:
    """旧任务清单记录为已写入（`written`）的 raw/ 文件（md 与图片）。归属只认清单，不凭路径推断。"""
    owned: set[str] = set()
    for task in iter_tasks(root):
        if task["task_id"] == exclude:
            continue
        for it in task.get("items", []):
            if it.get("land") in ("written", "writing"):
                owned.add(it["raw"])
                owned.update((it.get("images") or {}).keys())
    return owned


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
            cur = _sha_file(root / rel)
            if cur is not None and cur == sha:
                ok.add(rel)
            elif cur is not None:
                diffs.append(f"{rel}（本任务写入后被改动）")
    return ok, diffs


def precheck(root: Path, task: dict) -> dict:
    """冲突预检（§5、决策P4.30-12）。`raw/<sample>.md` 与 `raw/<sample>/` 作为一个整体检查。

    返回 `{"status": "ok" | "overwritable" | "conflict", "details": [...]}`。
    """
    sample = task["sample"]
    raw = root / "raw"
    targets = {it["raw"] for it in task["items"]}
    split = task.get("split", False)
    existing: list[str] = []
    whole = raw / f"{sample}.md"
    if whole.is_file():
        existing.append(_rel(root, whole))
    folder = raw / sample
    if folder.is_dir():
        existing += [_rel(root, p) for p in sorted(folder.rglob("*")) if p.is_file()]
    if not split:  # 未拆分的图片落 raw/images/<sample>/，覆盖时会被整盘替换
        img = raw / "images" / sample
        if img.is_dir():
            existing += [_rel(root, p) for p in sorted(img.rglob("*")) if p.is_file()]

    details: list[str] = []
    self_ok, self_diffs = _self_owned(root, task)
    details += self_diffs

    # slug 冲突（沿 ingest._reject_source_slug_collision）：在预检阶段就查，不留到 ⑥ 才炸。
    target_slugs = {raw_slug(Path(t).stem): t for t in targets}
    if raw.is_dir():
        for p in raw.rglob("*.md"):
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
    if md_existing == targets and not unowned:
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
    details.append("首版不做整书替换：请先自行清理 raw/ 中的相关文件后再导入。")
    return {"status": "conflict", "details": details, "existing": existing}


def _precheck_signature(pc: dict) -> list:
    return [pc["status"], sorted(pc.get("existing", [])), pc.get("details", [])]


# ── ①–④ 准备 ────────────────────────────────────────────────────────────────


def _average_item_seconds(root: Path) -> float:
    samples: list[float] = []
    for task in iter_tasks(root):
        for it in task.get("items", []):
            if it.get("state") == "done" and isinstance(it.get("seconds"), (int, float)):
                samples.append(float(it["seconds"]))
    samples = samples[-_RECENT_SAMPLES:]
    return sum(samples) / len(samples) if samples else float(DEFAULT_ITEM_SECONDS)


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
    resolved = src.expanduser().resolve()
    try:
        return resolved.relative_to(_ws(root).resolve()).as_posix()
    except ValueError:
        return _default_origin(src, root)


def _check_images(root: Path, items: list[dict], split: bool, sample: str) -> list[str]:
    """图片断链检查（零 LLM）：拆分改写造成的断链 → 抛错；解析时就断的链 → 返回警告。"""
    ws = _ws(root).resolve()
    parsed = ws / "parsed"
    warnings: list[str] = []
    for it in items:
        md = ws / it["parsed"]
        text = md.read_text(encoding="utf-8")
        col = collect_for_promotion(text, source_md=md, root=ws, stem=Path(it["raw"]).stem, strict=False)
        for url in col.missing:
            if split and url.startswith("../") and _admit_image_ref(url[3:], parsed, ws) is not None:
                raise OneClickError(f"拆分改写图片引用出错：{it['parsed']} 中的 {url} 找不到（这是一键入库的缺陷，请报告）。")
            warnings.append(f"{Path(it['parsed']).name}：图片 {url} 找不到，原样保留")
        it["image_count"] = len(col.images)
    return warnings


def prepare(
    root: Path,
    src: Path,
    *,
    origin_kind: str,
    backend: str = "auto",
    emit: Emit | None = None,
    dry_run: bool = False,
) -> dict:
    """①–④（**调用方持锁**）：解析 → 整理 → 拆分 → 预检，停在 `awaiting_confirm`。返回任务清单。

    `dry_run=True`：照跑 ①–④、写 `parsed/`，但**不创建任务记录**（返回的 dict 只用于打印摘要）。
    """
    src = Path(src).expanduser()
    if not src.is_file():
        raise OneClickError(f"输入文件不存在：{src}")
    existing = active_task(root)
    if existing is not None:
        raise OneClickError(
            f"已有未完成的入库任务 {existing['task_id']}（{_STATE_LABEL.get(existing['state'], existing['state'])}）；"
            "请先继续（--resume）或放弃（--cancel）它。"
        )
    sample = raw_slug(src.stem) or "import"
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{sample}"
    task_id, n = base, 1
    while _task_file(root, task_id).exists():  # 同一秒内的第二个任务不能覆盖第一个的清单
        n += 1
        task_id = f"{base}-{n}"
    data = src.read_bytes()
    task: dict = {
        "task_id": task_id,
        "sample": sample,
        "upload": _origin_label(root, src),
        "upload_sha256": _sha_bytes(data),
        "origin": origin_kind,
        "state": "preparing",
        "created": _now(),
        "stages": {"parse": "pending", "normalize": "pending", "split": "pending", "confirm": "pending", "land": "pending"},
        "items": [],
        "dry_run": dry_run,
    }
    log = TaskLog(root, None if dry_run else task_id, emit)
    if not dry_run:
        save_task(root, task)
        task["log"] = _rel(root, _log_file(root, task_id))
    try:
        _prepare_steps(root, src, task, backend=backend, log=log)
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


def _prepare_steps(root: Path, src: Path, task: dict, *, backend: str, log: TaskLog) -> None:
    sample = task["sample"]
    ws = _ws(root)
    parsed = ws / "parsed"
    parsed.mkdir(parents=True, exist_ok=True)
    whole = parsed / f"{sample}.md"

    # ① 解析
    log(f"① 解析 {src.name} …")
    if src.suffix.lower() == ".md":
        text = src.read_text(encoding="utf-8", errors="replace")
        res = collect_and_rewrite(text, produced_md=src.resolve(), root=src.resolve().parent, stem=sample)
        backend_used = None
    else:
        try:
            res = convert_to_markdown(src, stem=sample, backend=backend, cwd=root, progress=lambda ln: log("  " + ln))
        except ConvertError as exc:
            raise OneClickError(f"解析失败：{exc}") from None
        backend_used = res.backend
    _write_parsed_images(whole, res.images)
    atomic_write_text(parsed / f"{sample}.origin.md", res.markdown)
    task["stages"]["parse"] = "done"
    log(f"  解析完成（{'backend=' + backend_used if backend_used else '已是 markdown'}，{len(res.images)} 张图片）")

    # ② 整理
    norm = normalize(
        res.markdown,
        title_hint=src.stem,
        origin=task["upload"],
        parsed_by=backend_used,
        source_sha256=task["upload_sha256"],
    )
    atomic_write_text(whole, norm.text)
    for op in norm.ops:
        log("  " + op.describe(), detail=True)
    s = norm.meta["normalized"]
    suspects = [o for o in norm.ops if o.kind == "watermark_suspect"]
    log(
        f"② 整理：删页眉页脚 {s['headers_removed']} 处、页码 {s['page_numbers_removed']} 处、"
        f"水印 {s['watermarks_removed']} 处，接句 {s['sentences_joined']} 处"
        + (f"；{len(suspects)} 条疑似水印已保留（见日志）" if suspects else "")
    )
    task["stages"]["normalize"] = "done"
    task["title"] = norm.title
    task["pages"] = norm.pages
    task["chars"] = norm.chars
    task["normalized"] = dict(s)

    # ③④ 拆分 + 目录索引
    parts = split_document(norm.body, sample=sample)
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

    task["warnings"] = _check_images(root, items, bool(parts), sample)
    for w in task["warnings"]:
        log("  ⚠ " + w, detail=True)
    if task["warnings"]:
        log(f"  ⚠ {len(task['warnings'])} 处图片引用找不到，原样保留（见日志）")
    task["prepared_sha256"] = {it["parsed"]: _sha_file(ws / it["parsed"]) for it in items}
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
    changed = [rel for rel, sha in task.get("prepared_sha256", {}).items() if _sha_file(ws / rel) != sha]
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
    return _execute(root, task, log=log, ingest=ingest, should_pause=should_pause)


def resume(
    root: Path,
    task_id: str,
    *,
    emit: Emit | None = None,
    ingest: IngestFn,
    should_pause: Callable[[], bool] = lambda: False,
) -> tuple[int, dict]:
    """继续（**调用方持锁**）：从清单读出断点，逐项判定跳过/重入/停下。"""
    task = load_task(root, task_id)
    log = TaskLog(root, task_id, emit)
    if task.get("state") != "paused":
        raise OneClickError(f"任务 {task_id} 当前状态为 {task.get('state')}，不能继续。")
    task["state"] = "running"
    task.pop("reason", None)
    save_task(root, task)
    log("▶ 继续入库")
    return _execute(root, task, log=log, ingest=ingest, should_pause=should_pause)


def _pause(root: Path, task: dict, log: TaskLog, reason: str) -> None:
    task["state"] = "paused"
    task["reason"] = reason
    save_task(root, task)
    log("⏸ " + reason)


# ── ⑤ 落 raw/ ───────────────────────────────────────────────────────────────


def _land(root: Path, task: dict, log: TaskLog) -> int:
    ws = _ws(root).resolve()
    if task["stages"].get("land") == "done":
        return EXIT_OK
    pc = precheck(root, task)
    if pc["status"] == "conflict" or (pc["status"] == "overwritable" and not task.get("overwrite")):
        task["state"] = "awaiting_confirm" if not any(it.get("land") != "pending" for it in task["items"]) else "paused"
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
            state = {rel: _sha_file(root / rel) for rel in files}
            if all(state[rel] == sha for rel, sha in files.items()):
                it["land"] = "written"
                save_task(root, task)
                log(f"  [{k}/{n}] {it['raw']} 已在盘上（上次写入后未及更新清单），补记为已写入", detail=True)
                continue
            if any(v is not None for v in state.values()):
                diffs = [rel for rel, sha in files.items() if state[rel] != sha]
                _pause(root, task, log, f"{it['raw']} 上次写到一半，盘上与记录不符：" + "、".join(diffs[:5]))
                return EXIT_USAGE
        md = ws / it["parsed"]
        text = md.read_text(encoding="utf-8")
        col = collect_for_promotion(text, source_md=md, root=ws, stem=target.stem, strict=False)
        img_dir = _images_dir(target)
        it["sha256"] = _sha_bytes(col.markdown.encode("utf-8"))
        it["images"] = {_rel(root, img_dir / img.name): img.sha256 for img in col.images}
        it["land"] = "writing"
        save_task(root, task)
        overwrite = bool(task.get("overwrite")) or target.exists() and _sha_file(target) == it["sha256"]
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


def _digest_matches(root: Path, raw_rel: str) -> bool:
    rel = raw_rel.removeprefix("raw/")
    loc = locate_source_page(root, rel)
    if loc.page is None:
        return False
    meta, _ = load_page(loc.page)
    parsed = parse_digest_value((meta or {}).get("raw_digest"))
    if parsed is None:
        return False
    try:
        return parsed == (raw_rel, compute_raw_digest(root / raw_rel))
    except OSError:
        return False


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


def _execute(root: Path, task: dict, *, log: TaskLog, ingest: IngestFn, should_pause: Callable[[], bool]) -> tuple[int, dict]:
    code = _land(root, task, log)
    if code != EXIT_OK:
        return code, task
    items = task["items"]
    n = len(items)
    if task.get("links_before") is None:
        task["links_before"] = _broken_links(root)
        save_task(root, task)
    started = time.monotonic()
    done_now: list[float] = []
    for k, it in enumerate(items, 1):
        if should_pause():
            _pause(root, task, log, f"已按要求暂停（{k - 1}/{n} 篇已处理）")
            task["paused_by_user"] = True
            save_task(root, task)
            return EXIT_OK, task
        raw_path = root / it["raw"]
        cur = _sha_file(raw_path)
        if cur != it.get("sha256"):
            _pause(root, task, log, f"{it['raw']} 与任务记录不一致（被改动或删除），请核对后再继续")
            return EXIT_USAGE, task
        if it.get("state") == "done" and _digest_matches(root, it["raw"]):
            log(f"[{k}/{n}] 跳过 {it['raw']}（本任务已入库且指纹一致）")
            continue
        it["state"] = "running"
        it["started"] = _now()
        it.pop("exit_code", None)
        save_task(root, task)
        log(f"[{k}/{n}] 入库 {it['raw']} …")
        before = _wiki_state(root)
        t0 = time.monotonic()
        try:
            rc = ingest(it["raw"], root)
        except KeyboardInterrupt:
            it["state"] = "pending"
            _pause(root, task, log, f"已立即中止；{it['raw']} 的 wiki 写入可能未经门禁收尾，请先运行 `guanlan check`")
            raise
        took = time.monotonic() - t0
        after = _wiki_state(root)
        created = sum(1 for p in after if p not in before)
        updated = sum(1 for p, v in after.items() if p in before and before[p] != v)
        it.update(finished=_now(), seconds=round(took, 1), exit_code=rc)
        if rc != EXIT_OK:
            it["state"] = "failed"
            _pause(root, task, log, f"[{k}/{n}] {it['raw']} 入库失败（退出码 {rc}），已暂停；处理后继续会从这一篇重试")
            return rc, task
        it["state"] = "done"
        save_task(root, task)
        done_now.append(took)
        log(f"[{k}/{n}] 入库 {it['raw']} … 完成（{_fmt_duration(took)}，wiki 新建 {created} 页、更新 {updated} 页）")
        left = sum(1 for x in items[k:] if x.get("state") != "done")
        if left:
            avg = sum(done_now) / len(done_now)
            log(f"入库 {k}/{n} · 已用 {_fmt_duration(time.monotonic() - started)} · 预计剩余约 {_fmt_duration(avg * left)}")
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


def summary_lines(task: dict, *, detail: bool = False) -> list[str]:
    items = task.get("items", [])
    est = task.get("estimate_seconds")
    label = "预演，未建任务" if task.get("dry_run") else _STATE_LABEL.get(task.get("state"), task.get("state"))
    lines = [
        f"任务 {task['task_id']}（{label}）",
        f"  文件：{task.get('upload')}",
        f"  分份：{len(items)} 份" + (f" · 预计耗时约 {_fmt_duration(est)}" if est else ""),
    ]
    done = sum(1 for it in items if it.get("state") == "done")
    if task.get("state") in ("running", "paused", "done"):
        lines.append(f"  进度：{done}/{len(items)} 篇已入库")
    if task.get("reason"):
        lines.append(f"  说明：{task['reason']}")
    pc = task.get("precheck") or {}
    if pc.get("status") == "overwritable":
        lines.append("  ⚠ 可覆盖：" + "；".join(pc.get("details", [])))
    elif pc.get("status") == "conflict":
        lines.append("  ✋ 冲突（不可覆盖，只能放弃）：")
        lines += [f"    - {d}" for d in pc.get("details", [])]
    if detail:
        s = task.get("normalized") or {}
        if s:
            lines.append(
                f"  整理：页眉页脚 {s.get('headers_removed', 0)}、页码 {s.get('page_numbers_removed', 0)}、"
                f"水印 {s.get('watermarks_removed', 0)}、接句 {s.get('sentences_joined', 0)}"
            )
        if task.get("warnings"):
            lines.append(f"  图片：{len(task['warnings'])} 处引用找不到，原样保留")
        for k, it in enumerate(items, 1):
            pages = f"第 {it['pages'][0]}–{it['pages'][1]} 页 · " if it.get("pages") else ""
            path = " / ".join(it.get("path") or []) or "—"
            lines.append(f"  {k:>3}. {it['raw']}  [{pages}{it.get('chars', 0)} 字]  {path}  ({it.get('state')})")
    return lines


# ── CLI：guanlan import ──────────────────────────────────────────────────────


class _PauseFlag:
    """CLI Ctrl+C：第一次 = 本篇入完后暂停；第二次 = 立即中止（抛 KeyboardInterrupt）。"""

    def __init__(self) -> None:
        self.event = threading.Event()
        self._old = None

    def __call__(self) -> bool:
        return self.event.is_set()

    def _handler(self, signum, frame) -> None:  # noqa: ARG002
        if not self.event.is_set():
            self.event.set()
            print("\n⏸ 将在当前这篇入库完成后暂停（再按一次 Ctrl+C 立即中止）。", file=sys.stderr, flush=True)
            return
        signal.signal(signal.SIGINT, signal.default_int_handler)
        raise KeyboardInterrupt

    def __enter__(self) -> _PauseFlag:
        if threading.current_thread() is threading.main_thread():
            self._old = signal.signal(signal.SIGINT, self._handler)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._old is not None:
            signal.signal(signal.SIGINT, self._old)


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


def _stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _receipt(task: dict, json_output: bool) -> None:
    if json_output:
        keep = ("task_id", "state", "reason", "upload", "sample", "split", "precheck", "warnings", "estimate_seconds", "links_before", "links_after")
        out = {k: task.get(k) for k in keep if k in task}
        out["items"] = [
            {k: it.get(k) for k in ("raw", "parsed", "chars", "pages", "path", "land", "state", "exit_code", "seconds")}
            for it in task.get("items", [])
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("\n".join(summary_lines(task)))


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
) -> int:
    """`guanlan import` 的单一落地。`ingest` / `interactive` 供测试注入。"""
    modes = sum(bool(x) for x in (src, do_resume, status, do_cancel))
    if modes != 1:
        _stderr("用法：guanlan import <file> | --resume | --status | --cancel（四选一）。")
        return EXIT_USAGE
    try:
        root = require_kb_root(root_dir, writable=not status)
    except GuanlanError as exc:
        _stderr(str(exc))
        return exc.exit_code
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
            return _cli_resume(root, json_output, run_ingest_fn)
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
        )
    except KBLockBusy as exc:
        _stderr(str(exc))
        return exc.exit_code
    except GuanlanError as exc:
        _stderr(str(exc))
        return exc.exit_code
    except KeyboardInterrupt:
        _stderr("已中止。")
        return EXIT_USAGE


def _cli_status(root: Path, json_output: bool) -> int:
    try:
        lock = acquire(root, "cli: import --status")
    except KBLockBusy:
        lock = None
    except OSError:
        lock = None
    try:
        if lock is not None:
            recover_stale(root)
        task = active_task(root)
    finally:
        if lock is not None:
            lock.release()
    if lock is None:
        _stderr(f"库正在被写（{describe_holder(read_holder(root))}）；以下为清单原样，未做恢复。")
    if task is None:
        if json_output:
            print("null")
        else:
            print("没有未完成的一键入库任务。")
        return EXIT_OK
    if json_output:
        _receipt(task, True)
    else:
        print("\n".join(summary_lines(task, detail=True)))
        print(f"  日志：{task.get('log')}")
    return EXIT_OK


def _cli_cancel(root: Path, json_output: bool) -> int:
    with acquire(root, "cli: import --cancel"):
        recover_stale(root)
        task = active_task(root)
        if task is None:
            _stderr("没有未完成的一键入库任务。")
            return EXIT_USAGE
        task = cancel(root, task["task_id"])
    _receipt(task, json_output)
    return EXIT_OK


def _run_with_pause(fn: Callable[..., tuple[int, dict]], *args, **kwargs) -> tuple[int, dict]:
    with _PauseFlag() as flag:
        return fn(*args, should_pause=flag, **kwargs)


def _cli_resume(root: Path, json_output: bool, ingest: IngestFn) -> int:
    with acquire(root, "cli: import --resume"):
        recover_stale(root)
        task = active_task(root)
        if task is None:
            _stderr("没有未完成的一键入库任务。")
            return EXIT_USAGE
        if task["state"] != "paused":
            _stderr("\n".join(summary_lines(task)))
            _stderr("该任务不是「已暂停」状态，无法继续。")
            return EXIT_USAGE
        code, task = _run_with_pause(resume, root, task["task_id"], emit=_stderr, ingest=ingest)
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
) -> int:
    origin = "cli: import --dry-run" if dry_run else "cli: import"
    with acquire(root, origin):
        recover_stale(root)
        task = prepare(root, src, origin_kind="cli", backend=backend, emit=_stderr, dry_run=dry_run)
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
        cancel(root, task["task_id"])
        _stderr("存在不可覆盖的冲突，任务已放弃。")
        return EXIT_USAGE
    if pc["status"] == "overwritable" and not overwrite:
        cancel(root, task["task_id"])
        _stderr("raw/ 中已有本次的目标文件；如确认覆盖，请加 --overwrite 重新导入。任务已放弃。")
        return EXIT_USAGE
    if not yes:
        try:
            print("开始入库？[y/N] ", end="", file=sys.stderr, flush=True)
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            cancel(root, task["task_id"])
            _stderr("已放弃；parsed/ 中的产物保留。")
            return EXIT_OK
    with acquire(root, "cli: import"):
        code, task = _run_with_pause(confirm, root, task["task_id"], overwrite=overwrite, emit=_stderr, ingest=ingest)
    _receipt(task, json_output)
    return code
