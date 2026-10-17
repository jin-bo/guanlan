#!/usr/bin/env python3
"""入库宿主开销基准：只使用临时合成库和 fake runner，不调用 LLM、不读取用户库。

从仓库根运行：.venv/bin/python scripts/bench_ingest.py
可选：--pages 100 1000 5000 --repeat 3 --raw-mib 0 --repairs 0 --profile
结果是热文件缓存下的宿主成本，不含 Agentao 启动、模型请求及真实语义写入。
"""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import io
import json
import platform
import pstats
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from guanlan import gate  # noqa: E402
from guanlan.ingest import run_ingest  # noqa: E402
from guanlan.runtime import AgentRunResult  # noqa: E402


def make_kb(root: Path, pages: int, raw_mib: int) -> None:
    for name in ("AGENTAO.md", "SCHEMA.md"):
        (root / name).write_text("# Benchmark\n", encoding="utf-8")
    (root / "raw").mkdir()
    for directory in ("sources", "concepts"):
        (root / "wiki" / directory).mkdir(parents=True)
    for name in ("index", "overview", "log"):
        (root / "wiki" / f"{name}.md").write_text("# Benchmark\n", encoding="utf-8")
    # 总 content 页数 = pages；source:concept 约 1:9，唯一 aliases + 跨页链接。
    sources = max(1, pages // 10)
    for i in range(sources):
        (root / "raw" / f"r{i}.md").write_text("原始资料。\n" * 256, encoding="utf-8")
    for i in range(pages):
        is_source = i < sources
        directory = "sources" if is_source else "concepts"
        stem = f"r{i}" if is_source else f"c{i}"
        source_list = "[]" if is_source else f"[r{i % sources}]"
        text = (
            f"---\ntitle: '页面{i}'\ntype: {'source' if is_source else 'concept'}\n"
            f"tags: [性能, 知识库]\nsources: {source_list}\n"
            f"aliases: ['别名{i}']\nlast_updated: 2026-10-17\n---\n"
            + ("知识说明与论断，引用 [[r0]]。\n" * 32)
        )
        (root / "wiki" / directory / f"{stem}.md").write_text(text, encoding="utf-8")
    if raw_mib:
        # 单个二进制附件，逐 MiB 写，避免基准生成阶段的大内存分配。
        with (root / "raw" / "attachment.bin").open("wb") as stream:
            chunk = b"x" * (1024 * 1024)
            for _ in range(raw_mib):
                stream.write(chunk)


def measure(root: Path, repairs: int) -> dict:
    phases: dict[str, dict] = {}

    def timed(name, fn):
        def wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                entry = phases.setdefault(name, {"calls": 0, "seconds": 0.0})
                entry["calls"] += 1
                entry["seconds"] += time.perf_counter() - start
        return wrapped

    calls = 0
    page = root / "wiki" / "sources" / "r0.md"

    def runner(prompt, **kwargs):
        nonlocal calls
        calls += 1
        # 人工注入类型违规，绕过确定性引号修复；最后一轮恢复合法页。
        if repairs:
            text = page.read_text(encoding="utf-8")
            if calls == 1:
                page.write_text(text.replace("type: source", "type: invalid"), encoding="utf-8")
            elif calls == repairs + 1:
                page.write_text(text.replace("type: invalid", "type: source"), encoding="utf-8")
        return AgentRunResult(ok=True, final_text="benchmark")

    with contextlib.ExitStack() as stack:
        for name in ("snapshot_raw", "snapshot_page_meta", "run_check"):
            stack.enter_context(patch.object(gate, name, timed(name, getattr(gate, name))))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        start = time.perf_counter()
        rc = run_ingest("raw/r0.md", root=root, runner=runner)
        elapsed = time.perf_counter() - start
    if rc != 0 or calls != repairs + 1:
        raise RuntimeError(f"基准未按预期通过：rc={rc}, runner_calls={calls}")
    return {"total_s": elapsed, "runner_calls": calls, "phases": phases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, nargs="+", default=[100, 1000, 5000])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--raw-mib", type=int, default=0)
    parser.add_argument("--repairs", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if min(args.pages) < 1 or args.repeat < 1 or args.raw_mib < 0:
        parser.error("pages/repeat 必须为正数，raw-mib 不得为负")
    print(json.dumps({"platform": platform.platform(), "python": platform.python_version(),
                      "pyyaml": yaml.__version__, "libyaml": yaml.__with_libyaml__}))
    for pages in args.pages:
        with tempfile.TemporaryDirectory(prefix="guanlan-bench-") as directory:
            root = Path(directory)
            make_kb(root, pages, args.raw_mib)
            measure(root, args.repairs)  # 显式预热；不把生成文件后的首轮当冷盘数据。
            samples = [measure(root, args.repairs) for _ in range(args.repeat)]
            phases = {
                name: {"calls": samples[0]["phases"][name]["calls"],
                       "median_s": round(statistics.median(s["phases"][name]["seconds"]
                                                           for s in samples), 6)}
                for name in samples[0]["phases"]
            }
            print(json.dumps({"pages": pages, "raw_attachment_mib": args.raw_mib,
                              "repairs": args.repairs, "repeat": args.repeat,
                              "median_total_s": round(statistics.median(s["total_s"] for s in samples), 6),
                              "runner_calls": samples[0]["runner_calls"], "phases": phases}))
            if args.profile:
                profiler = cProfile.Profile()
                profiler.runcall(measure, root, args.repairs)
                pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(18)


if __name__ == "__main__":
    main()
