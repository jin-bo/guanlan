"""P4.7 界面双语词表静态守恒测试（见 docs/P4.7-中英双语.md §8）。

纯文本扫 `static/{i18n.js,index.html,app.js}`——**不依赖 web extra、不起服务、不触 LLM**，
故不 `importorskip`。专防三类漂移（`t()` 缺 key 静默回退字面量、运行时才露 key，静态扫描是唯一拦截）：
  ① zh/en 两套 key 必须一一对应；
  ② index.html 每个 data-i18n* 的值都在词表里；
  ③ app.js 每个 t("X") 字面量 key 都在词表里，且 t() 首参必为字符串字面量（决策P4.7-8，
     唯一白名单是 applyI18n 的四处 t(el.dataset.i18n*)）。
另查：同一 key 的 zh/en 占位 {n} 集一致；en 侧无空翻译。
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "guanlan" / "web" / "static"
I18N_JS = (STATIC / "i18n.js").read_text(encoding="utf-8")
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

_KEY_RE = re.compile(r'^\s*"([^"]+)"\s*:', re.M)
_ENTRY_RE = re.compile(r'^\s*"([^"]+)"\s*:\s*"(.*)",\s*$', re.M)
_PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")


def _block(name: str) -> str:
    """切出 I18N.<name> 这一段对象字面量的文本（zh 段到 en: 前；en 段到对象闭合 `};` 前）。"""
    start = I18N_JS.index(f"{name}: {{") + len(f"{name}: {{")
    rest = I18N_JS[start:]
    end = rest.index("\n  en: {") if name == "zh" else rest.index("\n  },")
    return rest[:end]


ZH_BLOCK = _block("zh")
EN_BLOCK = _block("en")
ZH_KEYS = set(_KEY_RE.findall(ZH_BLOCK))
EN_KEYS = set(_KEY_RE.findall(EN_BLOCK))
ZH_ENTRIES = dict(_ENTRY_RE.findall(ZH_BLOCK))
EN_ENTRIES = dict(_ENTRY_RE.findall(EN_BLOCK))


def test_catalog_nonempty():
    assert len(ZH_KEYS) > 100, "词表 key 太少，疑似解析失败"
    assert ZH_KEYS == set(ZH_ENTRIES), "zh 段有 key 无法解析出值（值含 ASCII 双引号？）"
    assert EN_KEYS == set(EN_ENTRIES), "en 段有 key 无法解析出值（值含 ASCII 双引号？）"


def test_zh_en_key_parity():
    """zh / en 两套 key 集完全相等（无单边缺漏）。"""
    missing_en = ZH_KEYS - EN_KEYS
    missing_zh = EN_KEYS - ZH_KEYS
    assert not missing_en, f"en 缺这些 key：{sorted(missing_en)}"
    assert not missing_zh, f"zh 缺这些 key：{sorted(missing_zh)}"


def test_placeholder_parity():
    """同一 key 的 zh/en 文案含相同的 {n} 占位集（防一边漏插值致参数丢失）。"""
    bad = {}
    for k in ZH_KEYS & EN_KEYS:
        z = set(_PLACEHOLDER_RE.findall(ZH_ENTRIES[k]))
        e = set(_PLACEHOLDER_RE.findall(EN_ENTRIES[k]))
        if z != e:
            bad[k] = (sorted(z), sorted(e))
    assert not bad, f"占位 {{n}} 不一致：{bad}"


def test_no_empty_translation():
    """en 侧无空串/纯空白值（缺译应回退而非显空）。"""
    empty = [k for k, v in EN_ENTRIES.items() if not v.strip()]
    assert not empty, f"en 空翻译：{empty}"


def test_html_keys_defined():
    """index.html 每个 data-i18n* 的值都在词表里有 key。"""
    used = set(re.findall(r'data-i18n(?:-title|-placeholder|-aria)?="([^"]+)"', INDEX_HTML))
    assert used, "index.html 未发现任何 data-i18n*（标注丢失？）"
    undefined = used - ZH_KEYS
    assert not undefined, f"index.html 引用了未定义的 key：{sorted(undefined)}"


def _strip_comments(src: str) -> str:
    """去块注释 + 行注释（app.js 内无 `//`/`/* */` 出现在字符串里，故安全）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line.split("//", 1)[0] for line in src.splitlines())


# applyI18n 的四处动态 key 由 HTML data-i18n* 喂入、取值域由 test_html_keys_defined 兜底，豁免字面量规则。
_DYNAMIC_WHITELIST_PREFIX = "t(el.dataset.i18n"


def test_js_t_calls_literal_and_defined():
    """app.js 每个 t(...)：① 首参必为字符串字面量（除白名单）；② 字面量 key 都在词表里。"""
    src = _strip_comments(APP_JS)
    quotes = {'"', "'"}
    literal_keys = set()
    dynamic = []
    for m in re.finditer(r"\bt\(", src):
        j = m.end()
        while j < len(src) and src[j] in " \t\n":
            j += 1
        if j >= len(src):
            continue
        if src[j] in quotes:  # 字面量首参：读到匹配引号
            q = src[j]
            k = src.index(q, j + 1)
            literal_keys.add(src[j + 1 : k])
        else:  # 动态首参：须命中白名单
            snippet = src[m.start() : m.start() + 40]
            if not snippet.startswith(_DYNAMIC_WHITELIST_PREFIX):
                dynamic.append(snippet)
    assert not dynamic, f"app.js 有非字面量 t() 首参（决策P4.7-8）：{dynamic}"
    assert literal_keys, "app.js 未发现任何 t(\"…\") 调用（扫描失效？）"
    undefined = literal_keys - ZH_KEYS
    assert not undefined, f"app.js 引用了未定义的 key：{sorted(undefined)}"


def test_rerender_dynamic_repaints_search_view():
    """语言切换重绘必须覆盖搜索态（P5.1，code-review 修复）：rerenderDynamic 须为 kind==\"search\"
    纯重绘——否则搜索结果头部/空态文案在切语言后停在旧语言（P4.7 不变量对新增动态面的盲区）。
    静态守卫：rerenderDynamic 函数体内须含对 search 视图的重绘分支（不依赖 DOM 执行）。"""
    src = _strip_comments(APP_JS)
    m = re.search(r"function rerenderDynamic\(\)\s*\{", src)
    assert m, "未找到 rerenderDynamic（扫描失效？）"
    # 取函数体到下一处顶层 `}\n`（足够覆盖该函数；只断言含 search 重绘语义）。
    body = src[m.end() : m.end() + 600]
    assert '"search"' in body and "paintSearch(" in body, (
        "rerenderDynamic 未覆盖 kind=='search' 的纯重绘——切语言后搜索态文案会停在旧语言"
    )
