"""一键入库认得的原件格式：扩展名 → 种类。网址导入（P4.31）与目录批量入库（P4.32）共用这一份（决策P4.32-7）。

只放数据，不 import 任何模块：`fetch` 依赖 httpx / agentao，目录扫描不该为一张表把它们拉进来。
"""

from __future__ import annotations

# 本期只认这些格式（决策P4.31-3）：扩展名（小写）→ 种类
EXT_KIND = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".markdown": "md",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".bmp": "bmp",
}

# 目录批量入库收的扩展名。`.markdown` 除外：网址导入把它改名成 `.md` 再落盘，一键入库内核只按 `.md` 认 Markdown，
# 本地的 `.markdown` 会被送去转换器。要收它须先在内核里当 Markdown 处理，不在这里放行。
BATCH_IMPORT_EXTS = frozenset(ext for ext in EXT_KIND if ext != ".markdown")
