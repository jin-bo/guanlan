"""Web 撤回源端点（P4.35，见 docs/P4.35-Web撤回源.md）。薄壳：单源计划在 `guanlan/remove.py`，整批在 `guanlan/remove_batch.py`。

- `GET /api/remove/preview`：零 LLM、不写盘。`?source=&scope=one|all` 或 `?task=<撤回任务 id>`（任务中心续跑整批）。
- `POST /api/remove`：进任务中心，**不拍前像**、运行中不可取消；作业取得写锁后重算计划、核对 `plan_digest`，
  不一致零写入。信封必须建成才入队（整批续跑依赖信封里的原件身份）。
- 待续任务（§3.5）：`scope=all`、`interrupted`/`failed`、带 `remove_writes_began`、当前盘面仍有成员、没有
  `superseded_by`。用 `source` 发起整批时遇到待续任务 → 409 并附任务 id，前端改走任务入口。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, Query
from pydantic import BaseModel

from ..errors import EXIT_OK, EXIT_USAGE
from ..remove import _resolve_slug, _resolve_target, execute_plan, format_plan, plan_digest, run_remove_result
from ..remove_batch import (
    BatchConflict,
    BatchError,
    GroupKey,
    ReusedIdentity,
    build_batch_plan,
    count_members,
    execute_batch,
    format_batch,
    group_key_for_raw,
)

PENDING_FAILED_STATES = ("interrupted", "failed")


class RemoveBody(BaseModel):
    """`POST /api/remove`。`source` 与 `from_task` 二选一；`from_task` 只接受整批撤回任务。"""

    source: str | None = None
    from_task: str | None = None
    scope: str = "one"
    plan_digest: str


@dataclass
class RemoveSpec:
    """提交一个撤回任务所需的一切（由 `_submit_task` 建信封、入队）。"""

    title: str
    params: dict
    extra: dict
    make_job: object  # Callable[[str], Callable[[Emit], int]]：拿到 task_id 后造作业
    retry_of: str | None = None


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _conflict(exc: BatchConflict) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": str(exc), "reason": exc.reason, "details": exc.details})


class RemoveService:
    def __init__(self, root: Path, tasks) -> None:
        self.root = Path(root)
        self.tasks = tasks

    # ── 待续任务 ───────────────────────────────────────────────────────────────

    def _key_of(self, env: dict) -> GroupKey | None:
        p = env.get("params") or {}
        if p.get("scope") != "all" or not isinstance(p.get("raw_dir"), str) or not isinstance(p.get("parent"), str):
            return None
        # 信封在库内 workspace/ 下、Agent 可写：raw_dir 只认 `raw/<单个目录名>`，不信任其中的 `..`、绝对路径等，
        # 否则改一行 JSON 就能让续跑枚举并移走 raw/ 之外（甚至库外）带 split 元数据的文件。
        parts = PurePosixPath(p["raw_dir"]).parts
        if len(parts) != 2 or parts[0] != "raw" or parts[1] in (".", "..") or parts[1].startswith(".") or "\\" in parts[1]:
            return None
        return GroupKey(raw_dir=p["raw_dir"], parent=p["parent"])

    def ineligible_reason(self, env: dict, *, remaining: int | None = None) -> str | None:
        """旧撤回任务能否续跑；能 → None，不能 → 原因（给人看）。"""
        key = self._key_of(env) if env.get("kind") == "remove" else None
        if key is None:
            return "这不是按原件撤回全部分卷的任务，请从摘要页重新发起。"
        if env.get("state") not in PENDING_FAILED_STATES:
            return f"该任务当前状态为 {env.get('state')}，不需要续跑。"
        if not env.get("remove_writes_began"):
            return "该任务没有进入执行（没有改动过库），请从摘要页重新发起。"
        if env.get("superseded_by"):
            return f"该任务已由后来的撤回任务 {env['superseded_by']} 完成。"
        if (remaining if remaining is not None else count_members(self.root, key)) == 0:
            return "本批已无剩余文件可撤回。"
        return None

    def pending_tasks(self, key: GroupKey, *, exclude: str | None = None) -> list[dict]:
        """同一原件的待续撤回任务，按 `remove_writes_began` 从晚到早。"""
        if self.tasks is None:
            return []
        remaining = count_members(self.root, key)
        out = [
            env
            for env in self.tasks.envelopes("remove")
            if env.get("id") != exclude and self._key_of(env) == key and self.ineligible_reason(env, remaining=remaining) is None
        ]
        out.sort(key=lambda e: (e.get("remove_writes_began") or "", e["id"]), reverse=True)
        return out

    def _single_pending(self, slug: str, *, exclude: str) -> list[str]:
        """同一个源、写了一半（带 `remove_writes_began`）且尚未被覆盖的旧单源撤回任务 id。"""
        if self.tasks is None:
            return []
        return [
            env["id"]
            for env in self.tasks.envelopes("remove")
            if env.get("id") != exclude
            and (env.get("params") or {}).get("scope") == "one"
            and (env.get("params") or {}).get("source") == slug
            and env.get("state") in PENDING_FAILED_STATES
            and env.get("remove_writes_began")
            and not env.get("superseded_by")
        ]

    def _task_entry(self, task_id: str) -> tuple[GroupKey, list[ReusedIdentity], dict]:
        env = None if self.tasks is None else self.tasks.envelope(task_id)
        if env is None:
            raise HTTPException(status_code=404, detail=f"未知任务：{task_id}")
        if env.get("kind") != "remove" or self._key_of(env) is None:
            raise HTTPException(status_code=400, detail="只有按原件撤回全部分卷的任务可以从任务中心续跑。")
        why = self.ineligible_reason(env)
        if why is not None:
            raise HTTPException(status_code=409, detail={"message": why, "reason": "not_resumable"})
        key = self._key_of(env)
        reuse = []
        for m in (env.get("group") or {}).get("members") or []:
            if isinstance(m, dict) and isinstance(m.get("raw"), str) and isinstance(m.get("sha256"), str):
                keys = tuple(k for k in m.get("identity_keys") or [] if isinstance(k, str))
                reuse.append(ReusedIdentity(raw=m["raw"], identity_keys=keys, sha256=m["sha256"]))
        return key, reuse, env

    # ── 预览 ───────────────────────────────────────────────────────────────────

    def _slug(self, source: str) -> str:
        # 按页路径给的源只认 wiki/sources/ 顶层：_resolve_slug 会剥掉目录成分，子目录页会被解析成顶层另一份同名源。
        # 先去掉空段与 `.`：`/wiki/sources/sub/x.md`、`./wiki/sources/sub/x.md` 不能绕过这道检查。
        parts = [p for p in source.strip().replace("\\", "/").split("/") if p not in ("", ".")]
        low = [x.lower() for x in parts]  # 大小写不敏感的盘上 Wiki/Sources 同样是子目录页
        # `..`、省掉 `wiki/` 前缀的 `sources/sub/x.md` 同样会被剥成顶层 slug：一并拒绝
        if ".." in parts or ("sources" in low and len(parts) - low.index("sources") > 2):
            raise HTTPException(status_code=400, detail="只能撤回 wiki/sources/ 顶层的摘要页对应的源。")
        try:
            return _resolve_slug(source)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    def _source_key(self, source: str) -> tuple[GroupKey, str]:
        slug = self._slug(source)
        try:
            raw_rel, _page = _resolve_target(self.root, slug)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if raw_rel is None:
            raise HTTPException(status_code=400, detail="这个源没有对应的原文，不能按原件撤回全部分卷。")
        try:
            return group_key_for_raw(self.root, raw_rel), slug
        except BatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    def preview(self, *, source: str | None, scope: str, task: str | None) -> dict:
        if (source is None) == (task is None):
            raise HTTPException(status_code=400, detail="source 与 task 须二选一。")
        if task is not None:
            key, reuse, env = self._task_entry(task)
            plan = self._batch(key, reuse)
            return {**plan.to_dict(), "from_task": task, "requested": None, "done_members": self._done(env, plan)}
        if scope == "one":
            slug = self._slug(source)
            try:
                plan = run_remove_result(self.root, slug)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            if not plan.ok:
                raise HTTPException(status_code=404, detail=f"找不到源 {slug!r}（可能已撤回）。")
            out = json.loads(format_plan(plan, executed=False, json_output=True))
            out.pop("executed", None)
            raw_rel = next((r for r in plan.relocate if r.startswith("raw/")), None)
            group_available = False
            if raw_rel is not None:
                try:
                    group_key_for_raw(self.root, raw_rel)
                    group_available = True
                except BatchError:
                    pass
            return {**out, "scope": "one", "group_available": group_available, "plan_digest": plan_digest(self.root, plan)}
        if scope != "all":
            raise HTTPException(status_code=400, detail="scope 只能是 one 或 all。")
        key, slug = self._source_key(source)
        pending = self.pending_tasks(key)
        if pending:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "这个原件有一次没完成的撤回，请从任务中心继续。",
                    "reason": "pending_task",
                    "task_id": pending[0]["id"],
                },
            )
        plan = self._batch(key, None)
        return {**plan.to_dict(), "requested": slug}

    def _batch(self, key: GroupKey, reuse):
        try:
            plan = build_batch_plan(self.root, key, reuse=reuse)
        except BatchConflict as exc:
            raise _conflict(exc) from None
        except BatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not plan.ok:
            raise HTTPException(status_code=409, detail={"message": "本批已无剩余文件可撤回。", "reason": "empty"})
        return plan

    @staticmethod
    def _done(env: dict, plan) -> int:
        saved = {m.get("raw") for m in (env.get("group") or {}).get("members") or [] if isinstance(m, dict)}
        now = {m.raw for m in plan.members}
        return len(saved - now)

    # ── 提交 ───────────────────────────────────────────────────────────────────

    def spec(self, body: RemoveBody) -> RemoveSpec:
        """校验请求、造任务规格。重算与核对放在作业里（取得写锁之后）。"""
        if (body.source is None) == (body.from_task is None):
            raise HTTPException(status_code=400, detail="source 与 from_task 须二选一。")
        digest = body.plan_digest
        if body.from_task is not None:
            if body.scope != "all":
                raise HTTPException(status_code=400, detail="from_task 只用于按原件撤回全部分卷。")
            key, reuse, _env = self._task_entry(body.from_task)
            return self._batch_spec(key, reuse, digest, entry={"from_task": body.from_task}, retry_of=body.from_task)
        if body.scope == "one":
            slug = self._slug(body.source)
            return RemoveSpec(
                title=f"撤回源：{slug}",
                params={"scope": "one", "source": slug, "plan_digest": digest},
                extra={"entry": {"source": slug}},
                make_job=lambda task_id: self._one_job(task_id, slug, digest),
            )
        if body.scope != "all":
            raise HTTPException(status_code=400, detail="scope 只能是 one 或 all。")
        key, slug = self._source_key(body.source)
        pending = self.pending_tasks(key)
        if pending:
            raise HTTPException(
                status_code=409,
                detail={"message": "这个原件有一次没完成的撤回，请从任务中心继续。", "reason": "pending_task", "task_id": pending[0]["id"]},
            )
        return self._batch_spec(key, None, digest, entry={"source": slug})

    def _batch_spec(self, key, reuse, digest, *, entry: dict, retry_of: str | None = None) -> RemoveSpec:
        plan = self._batch(key, reuse)  # 提交时的盘面：信封里存成员身份，供以后续跑复用
        group = {
            **key.to_dict(),
            "members": [{"raw": m.raw, "identity_keys": list(m.identity_keys), "sha256": m.sha256} for m in plan.members],
        }
        return RemoveSpec(
            title=f"撤回原件全部分卷：{key.raw_dir}",
            params={"scope": "all", **key.to_dict(), "plan_digest": digest},
            extra={"entry": entry, "group": group},
            make_job=lambda task_id: self._batch_job(task_id, key, reuse, digest),
            retry_of=retry_of,
        )

    # ── 作业（持写锁运行）─────────────────────────────────────────────────────────

    def _mark_began(self, task_id: str, supersedes: list[str]) -> None:
        # 信封不在或落盘失败时 set_fields 返回 False：此时「开始写」无处记录、中断后无从续跑，不写库。
        if (
            self.tasks is None
            or self.tasks.envelope(task_id) is None
            or not self.tasks.set_fields(task_id, remove_writes_began=_now(), supersedes=supersedes)
        ):
            raise OSError("任务记录写不进（workspace/ 不可写？）")

    def _one_job(self, task_id: str, slug: str, digest: str):
        def job(emit) -> int:
            try:
                plan = run_remove_result(self.root, slug)
            except ValueError as exc:
                print(f"✗ 没有执行任何改动：{exc}")
                return EXIT_USAGE
            if not plan.ok:
                print(f"✗ 没有执行任何改动：找不到源 {slug!r}（可能已撤回）。")
                return EXIT_USAGE
            if plan_digest(self.root, plan) != digest:
                print("✗ 预览之后库有变化，没有执行任何改动，请重新预览。")
                return EXIT_USAGE
            began = []

            def before() -> None:
                # 同一个源写了一半的旧单源撤回：本次成功即替它收尾，记进覆盖名单，免得它永远停在「需要处理」
                self._mark_began(task_id, self._single_pending(slug, exclude=task_id))
                began.append(True)

            try:
                trash = execute_plan(self.root, plan, before_write=before)
            except Exception as exc:  # 不只 OSError：开始写之后任何异常都要给「可能已部分执行、重新预览」的引导
                if began:
                    print(f"✗ 撤回时写盘失败，可能已部分执行；重新预览并提交一次即可完成剩余步骤：{exc}")
                else:
                    print(f"✗ 没有执行任何改动：{exc}")
                return EXIT_USAGE
            print(f"✓ 已移入 {trash.relative_to(self.root).as_posix()}/（含 manifest.json）。")
            print(format_plan(plan, executed=True, json_output=False))
            self.mark_superseded(task_id)
            return EXIT_OK

        return job

    def _batch_job(self, task_id: str, key: GroupKey, reuse, digest: str):
        def job(emit) -> int:
            try:
                plan = build_batch_plan(self.root, key, reuse=reuse)
            except (BatchConflict, BatchError) as exc:
                print(f"✗ 没有执行任何改动：{exc}")
                return EXIT_USAGE
            if not plan.ok:
                print("✗ 没有执行任何改动：本批已无剩余文件可撤回。")
                return EXIT_USAGE
            if plan.digest != digest:
                print("✗ 预览之后库有变化，没有执行任何改动，请重新预览。")
                return EXIT_USAGE
            supersedes: list[str] = []
            began = []

            def before() -> None:
                # 覆盖名单在移动文件之前定：这时旧任务的剩余成员还在盘上，「仍有待处理成员」才判得准（§3.5）。
                supersedes.extend(e["id"] for e in self.pending_tasks(key, exclude=task_id))
                self._mark_began(task_id, list(supersedes))
                began.append(True)

            try:
                trash = execute_batch(self.root, plan, before_write=before)
            except Exception as exc:  # 含 BatchError；开始写之后任何异常都要给续跑引导
                if began:
                    print(f"✗ 撤回时写盘失败，可能已部分执行；从任务中心重新预览并提交一次即可完成剩余步骤：{exc}")
                else:
                    print(f"✗ 没有执行任何改动：{exc}")
                return EXIT_USAGE
            print(format_batch(plan, executed=True, trash_dir=trash.relative_to(self.root).as_posix()))
            self.mark_superseded(task_id)
            return EXIT_OK

        return job

    def mark_superseded(self, task_id: str) -> None:
        """按本任务信封里的 `supersedes` 名单给旧记录写 `superseded_by`。失败只影响引导，记一行日志。"""
        env = None if self.tasks is None else self.tasks.envelope(task_id)
        for old in (env or {}).get("supersedes") or []:
            if not self.tasks.set_fields(old, superseded_by=task_id):
                print(f"（未能在旧任务 {old} 上记下「已由本任务完成」，任务中心可能还会提示它待续。）")


def register(app, *, root: Path, tasks, writer_only, reject_if_writable_active, submit_remove) -> RemoveService:
    """挂 `GET /api/remove/preview` 与 `POST /api/remove`。reader 下都不注册（决策P4.9-2）。"""
    import anyio

    service = RemoveService(root, tasks)

    @writer_only(app.get("/api/remove/preview"))
    async def remove_preview(
        source: str | None = Query(None), scope: str = Query("one"), task: str | None = Query(None)
    ) -> dict:
        try:
            return await anyio.to_thread.run_sync(lambda: service.preview(source=source, scope=scope, task=task))
        except OSError as exc:  # 算内容指纹要整读待移动文件：读不了（权限等）时报清楚，不抛 500 栈
            raise HTTPException(status_code=500, detail=f"读取库内文件失败，无法预览撤回：{exc}") from None

    @writer_only(app.post("/api/remove"))
    async def remove_submit(body: RemoveBody) -> dict:
        reject_if_writable_active()  # 层③：可写 turn 活跃 → 423
        spec = await anyio.to_thread.run_sync(service.spec, body)
        return await submit_remove(spec)

    return service
