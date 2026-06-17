# vendored 第三方前端资产

本目录存放**随包打入 wheel** 的第三方前端运行时（非 CDN、离线可用）。`packages = ["guanlan"]`
（`pyproject.toml`）令 hatchling 自动携带包内非 `.py` 资源，此目录与 `static/*` 同机制入 wheel——
**无需** `force-include`。

收录原则与 P4 决策P4-3（前端无 npm/构建/CDN/第三方运行时）的有界破例理由见
[`docs/P4.13-Web-mermaid渲染.md`](../../../../docs/P4.13-Web-mermaid渲染.md) §6 决策P4.13-1：
仅放行**内容渲染器**（把一种标记渲染成富呈现，与服务端已 vendored 的 `markdown` 同位阶），
不放行 UI 应用框架。

## mermaid.min.js

| 项 | 值 |
|----|----|
| 版本 | `11.15.0`（钉版；升级走显式 PR 复核，见决策P4.13-3） |
| 上游 | https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.min.js |
| 包 | [mermaid](https://www.npmjs.com/package/mermaid)（npm，MIT License） |
| 字节 | 3312967 |
| SHA256 | `70137e77bb273bb2ef972b86e8b0400cca8be53cb25bfc45911a186dc98665de` |
| 形态 | **自包含 UMD 单文件**——加载后 `globalThis.mermaid` 就绪；**零运行时动态 `import()`**（核心图型 flowchart/sequence/class/state/er 全内联），故离线自洽 |

**为何用 UMD 而非 ESM**：mermaid v11 的 `mermaid.esm.min.mjs` 是**代码分割**构建、运行时动态 import
子 chunk，单文件不离线自洽；UMD `mermaid.min.js` 是经 esbuild `--bundle` 的单文件、无动态 import，
适合离线 vendored。前端经注入 `<script>` 加载、读 `window.mermaid`（见 `static/mermaid_enhance.js`）。

### 校验

```bash
shasum -a 256 guanlan/web/static/vendor/mermaid.min.js
# 应得 70137e77bb273bb2ef972b86e8b0400cca8be53cb25bfc45911a186dc98665de
```

### 升级步骤

1. `curl -o guanlan/web/static/vendor/mermaid.min.js https://cdn.jsdelivr.net/npm/mermaid@<新版>/dist/mermaid.min.js`
2. 确认仍是自包含 UMD（`tail` 见 `globalThis["mermaid"]=…`）、无新增动态 `import(`（`grep '[^a-zA-Z]import(' …` 应空）。
3. 更新本表版本 + 字节 + SHA256；浏览器手测 §7 验收串（含注入测试 `securityLevel:'strict'` 仍消毒）。
4. 经显式 PR 复核（决策P4.13-3：mermaid 历史有 XSS CVE，升级须复核 strict 模式无回归）。
