#!/usr/bin/env python3
"""离线估算 Agentao 0.5.14 入库日志中各类文本的历史携带成本。

用法：python scripts/analyze_ingest_history.py <agentao.log> [...]
只读日志，只输出计数。字符×后续请求数不是 token、费用或可节省时延。
适用于单次、无压缩、无自愈的完整日志；不重建 wire，也不改变任何上下文。
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re


STAMP = r"\d{2}:\d{2}:\d{2} "


def analyze(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    request = 0
    response = False
    role = ""
    tool = ""
    events = []
    calls = {}
    users = []
    system = []
    prompt_tokens = 0
    completion_tokens = 0
    reasoning_chars = 0
    compacted = False
    request_start = None
    last_response = None
    request_seconds = 0
    between_seconds = 0
    responses = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if not re.match(STAMP, line):
            i += 1
            continue
        if re.search(r"\[req_\d+\] LLM REQUEST$", line):
            clock = list(map(int, line[:8].split(":")))
            now = clock[0] * 3600 + clock[1] * 60 + clock[2]
            if last_response is not None:
                between_seconds += (now - last_response) % 86400
            request_start = now
            request += 1
            response = False
            role = tool = ""
        if re.search(r"\[req_\d+\] LLM RESPONSE$", line):
            clock = list(map(int, line[:8].split(":")))
            now = clock[0] * 3600 + clock[1] * 60 + clock[2]
            if request_start is None:
                raise ValueError("响应缺少对应请求")
            request_seconds += (now - request_start) % 86400
            last_response = now
            request_start = None
            responses += 1
            response = True
        if re.search(r"(microcompact|compression complete|context compressed)", line, re.I):
            compacted = True
        m = re.search(r"LLM iteration (\d+)/", line)
        if m and int(m[1]) == 1 and request:
            raise ValueError("含多个运行/自愈轮，不能按单个连续历史估算")
        m = re.search(r"Messages \((\d+) total, logging (\d+) new\)", line)
        if m and int(m[2]) == 0:
            compacted = True
        m = re.search(r"system prompt (?:initial|unchanged|changed): (\d+) chars", line)
        if m:
            system.append(int(m[1]))
        m = re.search(r"  Message \d+ \[(\w+)\]:$", line)
        if m:
            role = m[1]
        m = re.search(r"    Tool: (\w+) \(call_id=(\S+)\)$", line)
        if m:
            tool = m[2]
        m = re.search(r"    Content \((\d+) chars\):$", line)
        if m and not response:
            size = int(m[1])
            if role == "tool":
                events.append((request, tool, size))
            elif role == "user":
                users.append((request, size))
        m = re.search(r"  Prompt Tokens: (\d+)", line)
        if m:
            prompt_tokens += int(m[1])
        m = re.search(r"  Completion Tokens: (\d+)", line)
        if m:
            completion_tokens += int(m[1])
        m = re.search(r"Reasoning Content \((\d+) chars\):$", line)
        if m and response:
            reasoning_chars += int(m[1])
        m = re.match(STAMP + r"  Tool: (\w+)$", line)
        if m and response and i + 2 < len(lines) and lines[i + 2].endswith("Arguments:"):
            call_id = lines[i + 1].split("ID:", 1)[1].strip()
            j = i + 3
            while j < len(lines) and lines[j] != "}":
                j += 1
            if j == len(lines):
                raise ValueError("工具参数日志不完整")
            args = json.loads("\n".join(lines[i + 3:j + 1]))
            calls[call_id] = (request, m[1], args)
            i = j
        i += 1
    if not request or compacted or responses != request:
        raise ValueError("没有请求或存在压缩迹象，拒绝连续历史估算")
    if len(system) != request:
        raise ValueError("缺少逐请求 system 长度，日志格式不适用")

    weighted = Counter()
    reads = Counter()
    old_text_weighted = 0
    for req, size in users:
        if req != 1:
            # 0.5.14 的 skills/todos/recall 尾部是临时追加，不进入持久历史。
            weighted["dynamic_tail"] += size
    # 首请求有任务和 active-skill；后者亦为临时消息。
    first_users = [size for req, size in users if req == 1]
    if len(first_users) != 2 or len(users) != request + 1:
        raise ValueError("预期首请求两个 user、后续各一个临时 skill；日志格式不适用")
    weighted["task"] = first_users[0] * request
    weighted["dynamic_tail"] += first_users[1]
    weighted["system"] = sum(system)
    for req, call_id, size in events:
        if call_id not in calls:
            raise ValueError("工具结果缺少对应响应参数")
        _, name, args = calls[call_id]
        weight = request - req + 1
        category = "other_tool_results"
        if name == "read_file":
            category = "read_results"
            file_path = args.get("file_path", "")
            # 仅导出目录类别，不输出资料名/原文。
            group = "other"
            if "/raw/" in file_path or file_path.startswith("raw/"):
                group = "raw"
            elif file_path.endswith("/wiki/index.md"):
                group = "index"
            elif file_path.endswith("/wiki/overview.md"):
                group = "overview"
            elif "/wiki/" in file_path:
                group = "wiki_content"
            elif file_path.endswith("SCHEMA.md") or "/references/" in file_path:
                group = "rules"
            reads[group] += size * weight
        weighted[category] += size * weight
    for req, name, args in calls.values():
        size = len(json.dumps(args, ensure_ascii=False, separators=(",", ":")))
        weighted["write_arguments" if name in ("write_file", "replace") else "other_arguments"] += size * (request - req)
        if name == "replace":
            old_text_weighted += len(args.get("old_text", "")) * (request - req)
    # 不计 assistant 普通文本、reasoning、工具 schema、协议字段和 tokenizer 差异。
    total = sum(weighted.values())
    return {
        "requests": request,
        "request_to_response_s": request_seconds,
        "between_requests_s": between_seconds,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "generated_reasoning_chars": reasoning_chars,
        "estimated_char_requests": dict(weighted),
        "estimated_share": {k: round(v / total, 4) for k, v in weighted.items()},
        "read_char_requests": dict(reads),
        "old_text_char_requests": old_text_weighted,
        "calls": dict(Counter(name for _, name, _ in calls.values())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path, nargs="+")
    args = parser.parse_args()
    for i, path in enumerate(args.logs, 1):
        try:
            print(json.dumps({"sample": i, **analyze(path)}, ensure_ascii=False))
        except (ValueError, OSError) as exc:
            # 不回显异常原文；JSON 解码异常/路径可能含资料信息。
            print(json.dumps({"sample": i, "error": type(exc).__name__}))
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
