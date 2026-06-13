"""暂存区确定性解析 + 图片断链检查/重整（P4.6.1，决策P4.6.1-1/2/5/6/7）。

三件事，都是**宿主零-LLM 写 `workspace/`**（区别于 P4.5 Agent 解析/修订）：

- **`parse_upload`（解析作业 thunk，决策P4.6.1-1/2/7）**：直调 P5.2 `convert_to_markdown` 内核
  （`progress=emit` 把 backend 分级回退日志实时推上 `job.output`）把 `workspace/uploads/<名>` 转成
  `workspace/parsed/<slug>.md` + 图片落 `workspace/parsed/images/<slug>/`、引用重写为 `images/<slug>/…`
  （与晋级后 raw/ **两处同形**）。**不落 `raw/`、不加 provenance**（晋级时才 apply_origin）。
- **`image_lint`（只读断链检查，决策P4.6.1-5）**：扫一个 parsed `.md` 的图片引用，标出（a）解析不到的
  **悬空**引用、（b）指向**非本文件 slug** image 目录的**错位**引用（拆分/合并后常见）。
- **`relocalize_commit`（重整作业 thunk，决策P4.6.1-5/6）**：把该文件引用到的图 **copy** 到「该文件名下
  image 目录」`parsed/images/<file_stem>/`、按首现序改名编号、重写引用，**原图仅在全局零引用时 GC**
  （**关键**：池里拆分后原件/草稿仍在，判删须全局扫 `parsed/*.md`，绝不在修一个文件时断掉别处引用，Q3）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

from ..convert import _BACKENDS, convert_to_markdown
from ..errors import EXIT_OK, EXIT_USAGE
from ..imageio import (
    IMAGE_EXTS,
    ConvertError,
    ConvertedImage,
    _admit_image_ref,
    _enforce_caps,
    _is_external_ref,
    commit_md_with_images,
)
from ..imageio import _MD_IMAGE as _MD_IMAGE
from ..rawio import raw_slug

__all__ = [
    "_BACKENDS",
    "parse_target",
    "parse_upload",
    "image_lint",
    "relocalize_commit",
]


def parse_target(root: Path, upload_path: Path) -> Path:
    """由上传文件推出解析落点 `workspace/parsed/<slug>.md`（slug 取上传 stem，强制 `.md`）。"""
    slug = raw_slug(Path(upload_path.name).stem) or "parsed"
    return (root / "workspace" / "parsed" / f"{slug}.md").resolve()


def parse_upload(
    upload_path: Path, target: Path, *, root: Path, backend: str, emit
) -> int:
    """解析作业 thunk（决策P4.6.1-1/2/7）：转换 `upload_path` → 落 `target`（parsed/）+ 随源图片。返回退出码。

    `emit(line)` 把转换内核的 backend 分级日志（stderr 行）实时推上 `job.output`，前端进度窗口可见
    （决策P4.6.1-10/11）。落盘「图先换、md 末步提交、失败回滚」（沿 P5.2.1-9）；parsed 是 scratch、
    同名直接整盘替换（overwrite=True，区别于 raw/ 默认不覆盖）。全 backend 失败 → ConvertError → 报错
    收场（决策P4.6.1-9，不回落 Agent 解析）。
    """
    emit(f"开始解析 {upload_path.name}（backend={backend}）……")
    try:
        result = convert_to_markdown(
            upload_path, stem=target.stem, backend=backend, cwd=root, progress=emit
        )
    except ConvertError as exc:
        print(f"解析失败：{exc}")  # 经 worker redirect → job.output（前端显错）
        print(
            "提示：可 `pip install -U 'mineru[core]'` / `pip install marker-pdf`（高保真）"
            "或 `pip install pypdf`（纯文本，仅 PDF）；或在外部转换后再上传。"
        )
        return EXIT_USAGE

    # 图 + md 原子提交（共用归口，决策P4.6.1-4）：staging-swap → md 末步提交 → 失败回滚。parsed scratch：
    # overwrite=True 整盘替换（区别于 raw/ 默认不覆盖）。OSError 上抛 → worker 500。
    code = commit_md_with_images(target, result.markdown, result.images, overwrite=True)
    if code != EXIT_OK:
        return code

    nimg = len(result.images)
    receipt = f"✓ 已解析 → workspace/parsed/{target.name}"
    if nimg:
        receipt += f"（{nimg} 张图片 → images/{target.stem}/）"
    print(receipt)
    if result.skipped:
        print(f"  注：另有 {result.skipped} 处图片引用未准入、原样保留（远程/越界/非图片）。")
    return EXIT_OK


def _image_basename_index(parsed_root: Path) -> dict[str, list[str]]:
    """basename（小写）→ [realpath] 索引，扫 `parsed/images/**` 下的图片文件（断链恢复用，决策P4.6.1-5）。

    拆分常见症状：子文件引用 `images/<错目录>/<对 basename>.jpg`——目录错、basename 对，物理图实在
    **被拆分原文件的图目录**里。此索引让重整能按 basename **唯一匹配**回原图、复制到子文件目录。
    转换图名是 `<slug>-N.ext`（slug 前缀），跨文档 basename 天然不撞，唯一匹配在实践中成立。

    **安全边界**（决策P4.6.1-5，对齐 `_admit_image_ref`）：跳过 symlink、且 realpath 须仍
    `relative_to(parsed_root)`——否则 `parsed/images/**` 里一个文件名碰巧匹配悬空 basename、却指向
    parsed 外的 symlink（或经 symlink 父目录逃逸）会被索引，再经 `_recover_by_basename` 复制进工作区，
    绕过 `_admit_image_ref` 的 symlink/越界闸。
    """
    index: dict[str, list[str]] = {}
    images_root = parsed_root / "images"
    root_real = Path(os.path.realpath(parsed_root))
    if images_root.is_dir():
        for p in images_root.rglob("*"):
            if p.is_symlink() or not p.is_file():  # 不跟随 symlink
                continue
            if p.suffix.lower() not in IMAGE_EXTS:
                continue
            real = Path(os.path.realpath(p))
            try:
                real.relative_to(root_real)  # 复检：realpath 须仍在 parsed/ 内
            except ValueError:
                continue
            index.setdefault(p.name.lower(), []).append(str(real))
    return index


def _recover_by_basename(url: str, bindex: dict[str, list[str]]) -> Path | None:
    """对解析不到的相对引用，按 basename 在 `parsed/images/**` **唯一**匹配恢复源图（拆分恢复，决策P4.6.1-5）。

    唯一命中 → 返回该 realpath（供重整 copy 到本文件目录）；零命中（真缺失）/多命中（歧义）→ None，
    **不猜**——多命中宁可如实判悬空，绝不复制错图。
    """
    base = unquote(url).rsplit("/", 1)[-1].lower()
    matches = bindex.get(base)
    if matches is not None and len(set(matches)) == 1:
        return Path(matches[0])
    return None


def image_lint(file_path: Path, parsed_root: Path) -> dict:
    """只读断链检查（决策P4.6.1-5）：扫 `file_path` 的图片引用，分类悬空 / 错位（含拆分可恢复）。

    - **悬空**（`dangling`）：相对引用解析不到、且 basename 在 `parsed/images/**` 内**无唯一物理源**
      可恢复（文件真缺失/歧义）——重整无能为力，如实上报让人补图或删引用；
    - **错位**（`misplaced`）：① 解析得到但**不在本文件名下** `images/<file_stem>/` 目录；**或**
      ② 解析不到但 basename 在 `parsed/images/**` 唯一匹配到物理源（拆分：目录错、basename 对）——
      两者重整都能把图 copy 到本文件目录、改名重写（决策P4.6.1-5/6）。

    `needs_relocalize` 由**错位**（含可恢复）驱动。外链（带 scheme/`//`/`/`/`~`）跳过、不算断链。
    """
    md_text = file_path.read_text(encoding="utf-8", errors="replace")
    own_dir = (file_path.parent / "images" / file_path.stem).resolve()
    bindex = _image_basename_index(parsed_root)
    dangling: list[str] = []
    misplaced: list[str] = []
    for m in _MD_IMAGE.finditer(md_text):
        url = m.group("url")
        if _is_external_ref(url):
            continue
        resolved = _admit_image_ref(url, file_path.parent, parsed_root)
        if resolved is None:
            # 解析不到：尝试按 basename 从源图目录唯一恢复（拆分常见）→ 可重整；否则真悬空。
            if _recover_by_basename(url, bindex) is not None:
                misplaced.append(url)
            else:
                dangling.append(url)
            continue
        try:
            resolved.relative_to(own_dir)
        except ValueError:
            misplaced.append(url)
    return {
        "dangling": dangling,
        "misplaced": misplaced,
        "needs_relocalize": bool(misplaced),
    }


def _global_referenced_images(parsed_root: Path) -> set[str]:
    """全局扫 `parsed/*.md` 收集**被引用**的图 realpath 集（决策P4.6.1-5 安全边界）。

    用于 re-localize 后的「全局零引用才 GC」：池里拆分后原件/其它草稿可能仍引同一张原图，判删绝不能
    只看当前文件，否则把图搬给一个文件会断掉别处引用。返回 `os.path.realpath` 字符串集（去 symlink）。
    """
    referenced: set[str] = set()
    for md in parsed_root.rglob("*.md"):
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for m in _MD_IMAGE.finditer(text):
            url = m.group("url")
            if _is_external_ref(url):
                continue
            resolved = _admit_image_ref(url, md.parent, parsed_root)
            if resolved is not None:
                referenced.add(str(resolved))
    return referenced


def relocalize_commit(file_path: Path, parsed_root: Path) -> int:
    """重整作业 thunk（决策P4.6.1-5/6）：把本文件引用图 copy 到 `images/<file_stem>/` + 改名编号 + 重写引用 + 全局 GC。

    写锁内执行（单写者）。落盘原子：图先 staging-swap 到 `parsed/images/<file_stem>/`（整盘替换该目录、
    旧内容存 `.bak`）→ md 末步 `atomic_write_raw` 提交 → 失败回滚（沿 P5.2.1-9）。**copy 既有文件、不 move**；
    提交成功后做**全局零引用 GC**：扫所有 `parsed/*.md`，删 `parsed/images/**` 里**全局无人引用**的图（含本次被
    搬走来源的原图，若已无任何草稿引用），并清空目录。悬空/外链引用原样留在 md（重整不臆造）。
    """
    md_text = file_path.read_text(encoding="utf-8", errors="replace")
    file_stem = file_path.stem
    md_parent = file_path.parent
    bindex = _image_basename_index(parsed_root)  # 断链恢复索引（拆分：目录错 basename 对）
    collected: dict[str, str] = {}  # src realpath -> 归一名
    order: list[tuple[Path, str]] = []  # (src realpath, 归一名)，按首现序
    totals = {"bytes": 0}  # 累计字节（容量闸用）

    def repl(m: re.Match[str]) -> str:
        url = m.group("url")
        if _is_external_ref(url):
            return m.group(0)
        resolved = _admit_image_ref(url, md_parent, parsed_root)
        if resolved is None:
            # 解析不到：按 basename 从源图目录唯一恢复（拆分恢复，决策P4.6.1-5）；真缺失/歧义 → 留原引用。
            resolved = _recover_by_basename(url, bindex)
            if resolved is None:
                return m.group(0)
        key = str(resolved)
        name = collected.get(key)
        if name is None:
            n = len(order) + 1
            # 与 convert/晋级同口径容量三闸（决策P5.2.1-11）：先 `stat` 校验单图/张数/累计三上限，
            # **再**（提交时）`read_bytes`——否则一张超大图会先整盘读入 Web 进程内存才被拒，
            # 单文件即可耗尽 `/api/workspace/relocalize` 内存。
            size = resolved.stat().st_size
            _enforce_caps(n, size, totals["bytes"])
            totals["bytes"] += size
            name = f"{file_stem}-{n}{resolved.suffix.lower()}"
            collected[key] = name
            order.append((resolved, name))
        new_url = f"images/{file_stem}/{name}"
        return f"{m.group('head')}{m.group('pre')}{new_url}{m.group('post')})"

    # 把引用到的图读进内存（copy 源，不 move）→ 共用落盘归口 staging-swap 整盘换 images/<file_stem>/ +
    # md 末步提交 + 失败回滚（决策P4.6.1-4/P5.2.1-9）。_images_dir(file_path)=images/<file_stem>/。
    # 容量超限 → ConvertError → 报错收场（同 parse_upload，不写盘）。
    try:
        new_md = _MD_IMAGE.sub(repl, md_text)
        images = tuple(
            ConvertedImage(name=name, data=src.read_bytes()) for src, name in order
        )
    except ConvertError as exc:
        print(f"重整失败：{exc}")  # 经 worker redirect → job.output（前端显错）
        return EXIT_USAGE

    code = commit_md_with_images(file_path, new_md, images, overwrite=True)
    if code != EXIT_OK:
        return code

    # 全局零引用 GC（决策P4.6.1-5）：扫所有 parsed/*.md（含刚提交的本文件），删 parsed/images/** 里
    # 任何全局无人引用的图——本次新 copy 的图被本文件引用故留存；被搬走来源的原图若已无草稿引用则回收。
    referenced = _global_referenced_images(parsed_root)
    images_root = parsed_root / "images"
    if images_root.is_dir():
        for img in images_root.rglob("*"):
            if img.is_file() and img.suffix.lower() in IMAGE_EXTS:
                if os.path.realpath(img) not in referenced:
                    img.unlink(missing_ok=True)
        # 清掉 GC 后变空的图子目录（自底向上）。
        for d in sorted(images_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    relocated = len(order)
    print(f"✓ 已重整 {file_path.name}：{relocated} 张图 → images/{file_stem}/")
    return EXIT_OK
