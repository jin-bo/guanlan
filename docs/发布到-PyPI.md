# 发布到 PyPI

观澜的 PyPI **发布名是 `guanlan-wiki`**（裸名 `guanlan` 已被一个无关项目占用）。
导入名与 CLI 仍是 `guanlan`——只有 `pip install guanlan-wiki` 这一步名字不同。

发布走 **GitHub Actions Trusted Publishing（OIDC）**：仓库不存任何 API token，
推 `v*` tag 即由 `.github/workflows/release.yml` 自动构建并上传。

## 一次性配置（在 PyPI 网站做，仅首次）

1. 注册并登录 <https://pypi.org>（建议开两步验证）。
2. 进 **Your account → Publishing → Add a new pending publisher**，按下表填：

   | 字段 | 值 |
   |------|-----|
   | PyPI Project Name | `guanlan-wiki` |
   | Owner | `jin-bo` |
   | Repository name | `guanlan` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   （项目还不存在，所以用 *pending* publisher；首次发布成功后它自动转正。）
3. 可选但推荐：仓库 **Settings → Environments → New environment** 建一个名为
   `pypi` 的 environment，用于保护发布（可加 required reviewers）。workflow 里已
   声明 `environment: pypi`，名字必须与第 2 步一致。

## 每次发布

**版本号单一来源是 `guanlan/__init__.py:__version__`**——`pyproject.toml` 用 `dynamic = ["version"]`
经 `[tool.hatch.version]` 读它，**不要去 pyproject 里找版本号**（那里没有）。

发版走**两个 PR**（agent 不能自合并自己开的 PR，须人工合）：

1. **定版 PR**：`__version__` 去掉 `.dev0`（如 `0.1.18.dev0` → `0.1.18`）；`CHANGELOG.md` 的
   `## [未发布]` 改成 `## [X.Y.Z] - 日期`（**格式必须是 `## [X.Y.Z]`**——`release.yml` 靠
   `^## \[VERSION\]` 抽本版 notes，抽不到就只留一句占位链接）；`CLAUDE.md` status 行的
   `released through vX.Y.Z` 同步。
2. **回开发态 PR**（合并并打 tag 之后）：只改 `__version__` → 下一版 `.dev0` 一行，
   **CHANGELOG 不动**（`[未发布]` 段等下一个变更落地再加）。

```bash
# 定版 PR 合并后，tag 打在**合并后的 main 提交**上（不是 PR 分支上）
git checkout main && git pull
git tag -a v0.1.18 -m v0.1.18
git push origin v0.1.18
```

推送后去仓库 **Actions** 看 `Release (PyPI)` 跑完。该流水线产**两样**东西：PyPI 发布 **+ 自动建
GitHub Release**（notes 从 CHANGELOG 对应版本段抽，已存在则跳过）——**光推 tag 不会自己有 Release 页，
是流水线建的**。

验证：

```bash
# 刚发完立刻装可能拿到**上一版**（pip 缓存旧 wheel + PyPI /simple 索引比 JSON API 慢半拍），
# 故用 --no-cache-dir 且钉死版本号强取，并在隔离 venv 里做（别污染开发树）。
uv venv /tmp/verify-0.1.18 && /tmp/verify-0.1.18/bin/pip install --no-cache-dir 'guanlan-wiki==0.1.18'
/tmp/verify-0.1.18/bin/guanlan --version
```

## 可选：先发 TestPyPI 演练

想在正式发布前走一遍完整链路，可在 <https://test.pypi.org> 同样配一个 pending
publisher，并临时给 `pypa/gh-action-pypi-publish` 加 `repository-url:
https://test.pypi.org/legacy/`。注意 TestPyPI 上没有 `agentao`，从 TestPyPI 装时
要补 `--extra-index-url https://pypi.org/simple/` 才能解析依赖。

## 排查

- **`Trusted publishing exchange failure`**：pending publisher 的 owner/repo/
  workflow/environment 四项与实际不完全一致，逐字核对。
- **`File already exists`**：该版本号已发过，PyPI 不允许覆盖——bump version 重发。
- **依赖装不上**：`agentao` 在正式 PyPI，正常 `pip install guanlan-wiki` 不受影响。
