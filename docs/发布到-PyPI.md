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

```bash
# 1) 确认 pyproject.toml version 已经 bump（如 0.1.0 -> 0.1.1）
# 2) 打 tag 并推送（tag 名要 v 开头，与 release.yml 的 on.push.tags 匹配）
git tag v0.1.0
git push origin v0.1.0
```

推送后去仓库 **Actions** 看 `Release (PyPI)` 跑完，然后验证：

```bash
pip install guanlan-wiki
guanlan --version
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
