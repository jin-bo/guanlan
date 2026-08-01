"""P4.19 外部 MCP 配置诊断测试（见 docs/P4.19-Web-MCP诊断.md §4）。

**连接检查是本相位的核心价值，故它进默认 pytest 面、不是 smoke**（决策P4.19-7）：被测上游用仓库
现成的 `guanlan mcp` 真起一个 stdio server（零新依赖、零网络）。脱敏漏出是**沉默的**——正例测不
出来，故两条反向用例（配置展示 / 连接错误文本）都必须在。

依赖门控：缺 `[web]` 整组 skip；连接检查另需 `[mcp]`（MCP **客户端**栈随该 extra 到位），缺时只
跳那几条，配置展示（纯读盘）照跑。
"""

import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from guanlan.web import mcpdiag  # noqa: E402
from guanlan.web.app import create_app  # noqa: E402

# 连接检查要 `from mcp import ClientSession`（经 agentao.mcp.client）——那是 `guanlan-wiki[mcp]`
# 带的 SDK，不在 `[web]` 里。缺它时端点回 501（另有用例覆盖），这里跳过需要真连接的用例。
needs_mcp_sdk = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None, reason="需要 guanlan-wiki[mcp]（MCP 客户端栈）"
)

# 中性占位（公开仓库）：不要出现任何真实 KB / 内网地址。
PROJECT_TOKEN = "PROJECT-URL-TOKEN-9c1f"
USER_TOKEN = "USER-HEADER-TOKEN-4b7e"
ENV_TOKEN = "USER-ENV-TOKEN-2a55"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """把 `~` 挪到 tmp：`user_root()` 走 `Path.home()`，绝不碰开发者真实的 ~/.agentao。"""
    fake = tmp_path / "home"
    (fake / ".agentao").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))  # Windows 侧同义
    return fake


def write_mcp(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}, ensure_ascii=False), encoding="utf-8")


def project_mcp(kb: Path, servers: dict) -> None:
    write_mcp(kb / ".agentao" / "mcp.json", servers)


def user_mcp(home: Path, servers: dict) -> None:
    write_mcp(home / ".agentao" / "mcp.json", servers)


def stdio_upstream(kb: Path) -> dict:
    """仓库现成的只读 MCP 服务端作被测上游（真 stdio 子进程，零新依赖）。"""
    return {"command": sys.executable, "args": ["-u", "-m", "guanlan.cli", "-C", str(kb), "mcp"]}


def child_alive(marker: str) -> bool:
    """子进程是否还在（按命令行里的唯一标记匹配）。无 pgrep 的平台上恒 False → 断言自动放行。

    **marker 绝不能以 `-` 开头**：BSD/macOS 的 pgrep 会把 `-C /tmp/x` 当成非法选项、以 rc=2 空输出
    退出，于是本函数恒 False、清理断言真空通过（漏报是沉默的）。故这里对 rc 严格分档——
    0=命中、1=无命中，其余一律当探测器坏了并抛出，不再伪装成"进程不在"。
    """
    if not shutil.which("pgrep"):
        return False
    proc = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"pgrep 探测失败（rc={proc.returncode}）：{proc.stderr.strip()}")
    return bool(proc.stdout.strip())


def test_child_alive_detector_actually_detects():
    """正例控制：先证明 `child_alive` 在本平台真能看见活进程。

    没有这条，`test_check_real_stdio_upstream` 的清理断言可能永远真空通过（评审实证：marker 以
    `-` 开头时 macOS pgrep 直接报非法选项）——正例测不出漏报，得专门立一条。
    """
    if not shutil.which("pgrep"):
        pytest.skip("本平台无 pgrep")
    marker = "guanlan-mcpdiag-probe-7f31"
    proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(30)  # {marker}"])
    try:
        deadline = time.time() + 5
        while not child_alive(marker) and time.time() < deadline:
            time.sleep(0.05)
        assert child_alive(marker), "pgrep 探测器在本平台看不见活进程 → 清理断言会真空通过"
    finally:
        proc.kill()
        proc.wait(10)


# ───────────────────────── GET /api/mcp（纯读盘） ─────────────────────────


def test_config_lists_both_scopes(kb, home):
    """用户级 server 必须出现且标 `scope=user`——漏掉它等于漏掉最常见的一半（§2.1）。"""
    project_mcp(kb, {"local-tool": {"command": "/opt/bin/some-server", "args": ["--x"]}})
    user_mcp(home, {"upstream": {"url": "https://api.example.com/mcp", "trust": True}})
    with TestClient(create_app(kb)) as c:
        data = c.get("/api/mcp").json()
    rows = {s["name"]: s for s in data["servers"]}
    assert rows["upstream"]["scope"] == "user"
    assert rows["upstream"]["transport"] == "http" and rows["upstream"]["trusted"] is True
    assert rows["local-tool"]["scope"] == "project"
    assert rows["local-tool"]["transport"] == "stdio" and rows["local-tool"]["trusted"] is False
    # stdio 只回 command 的 basename（路径含本机目录结构，不外泄），且绝不回 args。
    assert rows["local-tool"]["endpoint"] == "some-server"
    assert "--x" not in json.dumps(data)
    assert data["config_errors"] == []


def test_scope_collision_resolves_to_user(kb, home):
    """同名冲突 → `scope=user`（决策P4.19-9）：上游把 project 条目整条忽略，标 user 才是实情。"""
    project_mcp(kb, {"shared": {"command": "project-side"}})
    user_mcp(home, {"shared": {"url": "https://user.example.com/mcp"}})
    with TestClient(create_app(kb)) as c:
        data = c.get("/api/mcp").json()
    assert [s["name"] for s in data["servers"]] == ["shared"]
    row = data["servers"][0]
    assert row["scope"] == "user"
    # 生效的确实是 user 那条（project 的 command 根本没进来）。
    assert row["transport"] == "http" and row["endpoint"] == "https://user.example.com/mcp"


def test_broken_json_is_reported_not_swallowed(kb, home):
    """坏 JSON → `json_unparsable`。上游 `_load_json_file` 静默返回 `{}`，不自己读原文就只会
    显示"没有配置"而不是"配置写坏了"——这正是本面板要回答的问题。"""
    (kb / ".agentao").mkdir(parents=True, exist_ok=True)
    (kb / ".agentao" / "mcp.json").write_text('{"mcpServers": {oops}', encoding="utf-8")
    user_mcp(home, {"upstream": {"url": "https://api.example.com/mcp"}})
    with TestClient(create_app(kb)) as c:
        data = c.get("/api/mcp").json()
    assert data["config_errors"] == [
        {"path": ".agentao/mcp.json", "name": None, "kind": "json_unparsable"}
    ]
    assert [s["name"] for s in data["servers"]] == ["upstream"]  # 另一份仍如常生效


def test_unresolvable_transport_is_reported(kb, home):
    """坏 `type` → `transport_unresolvable` + 该行 transport 落 `unknown`（与 agentao 展示口径一致）。"""
    project_mcp(kb, {"typo": {"type": "streamable", "url": "https://api.example.com/mcp"}})
    with TestClient(create_app(kb)) as c:
        data = c.get("/api/mcp").json()
    assert data["servers"][0]["transport"] == "unknown"
    assert data["config_errors"] == [
        {"path": ".agentao/mcp.json", "name": "typo", "kind": "transport_unresolvable"}
    ]


def test_empty_config_is_empty_not_error(kb, home):
    with TestClient(create_app(kb)) as c:
        assert c.get("/api/mcp").json() == {"servers": [], "config_errors": []}


def test_config_response_carries_no_secret(kb, home):
    """脱敏反向用例①：URL query / headers / env 的值一律不得出现在响应体里（§3）。"""
    project_mcp(
        kb,
        {
            "remote": {
                "url": f"https://user:{PROJECT_TOKEN}@api.example.com/mcp?token={PROJECT_TOKEN}",
                "headers": {"Authorization": f"Bearer {USER_TOKEN}"},
            },
            "local": {"command": "/opt/bin/tool", "env": {"API_KEY": ENV_TOKEN}},
        },
    )
    with TestClient(create_app(kb)) as c:
        resp = c.get("/api/mcp")
    body = resp.text
    for secret in (PROJECT_TOKEN, USER_TOKEN, ENV_TOKEN):
        assert secret not in body, f"{secret} 漏出"
    rows = {s["name"]: s for s in resp.json()["servers"]}
    # userinfo 与整个 query 串都去掉，只留 scheme://host/path。
    assert rows["remote"]["endpoint"] == "https://api.example.com/mcp"


def test_config_shape_invalid_is_reported(kb, home):
    """条目形状不合法 → `config_shape_invalid`，**不能**渲染成「未配置任何 server」。

    上游 `_expand_config_env` 对一条字符串条目直接抛 ValueError、整份配置炸掉；此前 `read_config`
    把异常吞成空 `merged` 且不记 errors，于是坏配置与真空配置的响应一字不差——正是本模块要消灭的沉默。
    """
    (kb / ".agentao").mkdir(parents=True, exist_ok=True)
    (kb / ".agentao" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"local": "some-server"}}), encoding="utf-8"
    )
    with TestClient(create_app(kb)) as c:
        data = c.get("/api/mcp").json()
    assert data["servers"] == []
    assert data["config_errors"] == [
        {"path": ".agentao/mcp.json", "name": "local", "kind": "config_shape_invalid"}
    ]


def test_non_dict_mcp_servers_is_reported(kb, home):
    """`mcpServers` 写成数组（别的工具接受的形态）→ 上游默默换成 `{}`，这里必须报出来。"""
    (kb / ".agentao").mkdir(parents=True, exist_ok=True)
    (kb / ".agentao" / "mcp.json").write_text(
        json.dumps({"mcpServers": [{"name": "x", "command": "/opt/x"}]}), encoding="utf-8"
    )
    with TestClient(create_app(kb)) as c:
        data = c.get("/api/mcp").json()
    assert data["config_errors"] == [
        {"path": ".agentao/mcp.json", "name": None, "kind": "config_shape_invalid"}
    ]


def test_non_string_env_value_is_reported(kb, home):
    """`env` 值写成数字 → 上游 `expand_env_vars` 抛 TypeError、整份配置炸掉，须点名到条目。"""
    project_mcp(kb, {"local": {"command": "/opt/bin/x", "env": {"PORT": 8080}}})
    with TestClient(create_app(kb)) as c:
        data = c.get("/api/mcp").json()
    assert data["servers"] == []
    assert data["config_errors"] == [
        {"path": ".agentao/mcp.json", "name": "local", "kind": "config_shape_invalid"}
    ]


def test_missing_transport_keys_is_reported(kb, home):
    """`command` 拼成 `comand`：上游 `resolve_transport` **不抛**、返回 "unknown"（config.py:217）。

    只在表里留一行孤零零的 unknown 等于没诊断——上游 `connect()` 随后会把它变成
    "No transport configured"，它确实是个错，必须点名。
    """
    project_mcp(kb, {"tool": {"comand": "/opt/bin/x"}})
    with TestClient(create_app(kb)) as c:
        data = c.get("/api/mcp").json()
    assert data["servers"][0]["transport"] == "unknown"
    assert data["config_errors"] == [
        {"path": ".agentao/mcp.json", "name": "tool", "kind": "transport_unresolvable"}
    ]


# ─────────────── 脱敏单元用例（变异测试可杀，防沉默漏报） ───────────────
#
# 端到端用例只能覆盖 agentao 今天恰好会拼进错误串的那几种形态，故 headers/env 分支曾被整段删掉而
# 16 条用例全绿（评审实证）。这一组直接打 `redact` / `endpoint_of`，每条对应一个过滤分支。


def test_redact_erases_header_and_env_values():
    """headers / env 的值——挡住 `Authorization` / `API_KEY` 进入 error 的唯一一道——必须可测。"""
    config = {"headers": {"Authorization": f"Bearer {USER_TOKEN}"}, "env": {"API_KEY": ENV_TOKEN}}
    out = mcpdiag.redact(f"upstream said: {USER_TOKEN} / {ENV_TOKEN} rejected", config)
    assert USER_TOKEN not in out and ENV_TOKEN not in out


def test_redact_erases_bare_query_token():
    """裸 token（server 的 401 正文常只引它，不带 `k=`、不在 URL 里）也须擦掉。"""
    config = {"url": f"https://api.example.com/mcp?token={PROJECT_TOKEN}"}
    out = mcpdiag.redact(f"401 Unauthorized: key {PROJECT_TOKEN} is not valid", config)
    assert PROJECT_TOKEN not in out


def test_redact_hides_stdio_command_path_but_keeps_basename():
    """stdio 的绝对路径含用户名/本机目录结构：配置表早就只回 basename，error 此前却原样漏出。"""
    config = {"command": "/Users/someone/private/bin/no-such-tool"}
    out = mcpdiag.redact(
        "[Errno 2] No such file or directory: '/Users/someone/private/bin/no-such-tool'", config
    )
    assert "/Users/someone" not in out
    assert "no-such-tool" in out  # 只去目录、不毁可诊断性


def test_redact_keeps_short_words_readable():
    """短凭据不入表：否则 `user` 这种词会把面板唯一要展示的那段文本打成马赛克。"""
    config = {"url": "https://user:root1@host/mcp"}
    out = mcpdiag.redact("could not find the user manual for root cause", config)
    assert out == "could not find the user manual for root cause"


def test_endpoint_masks_path_embedded_token():
    """路径里的凭据（Zapier/Composio/Smithery 的标准形态）不得原样回显。"""
    url = "https://mcp.example.com/api/mcp/s/SECRET-PATH-TOKEN-abc123/mcp"
    assert "SECRET-PATH-TOKEN-abc123" not in mcpdiag.endpoint_of({"url": url})
    assert mcpdiag.endpoint_of({"url": url}) == "https://mcp.example.com/api/mcp/s/***/mcp"


# ─────────────────── POST /api/mcp/check（用户显式触发） ───────────────────


@needs_mcp_sdk
def test_check_real_stdio_upstream(kb, home):
    """真 stdio 端到端：连上、枚举到工具、**结束后子进程已被清理**。

    不需要先给 `guanlan mcp` 补 `readOnlyHint`——诊断不执行工具，annotations 缺失只如实显示为空。
    """
    project_mcp(kb, {"self": stdio_upstream(kb)})
    marker = str(kb)  # 不能用 `-C <kb>`：以 `-` 开头会被 BSD pgrep 当非法选项（见 child_alive）
    with TestClient(create_app(kb)) as c:
        resp = c.post("/api/mcp/check", json={"name": "self"})
    assert resp.status_code == 200
    (result,) = resp.json()["results"]
    assert result["status"] == "connected" and result["error"] is None
    assert result["transport"] == "stdio"
    names = {tool["name"] for tool in result["tools"]}
    assert {"search", "read_page"} <= names, names
    assert all("annotations" in tool for tool in result["tools"])
    # 拉起的是真子进程：断开后必须收干净，否则每点一次检查就留一个守护进程。
    deadline = time.time() + 10
    while child_alive(marker) and time.time() < deadline:
        time.sleep(0.1)
    assert not child_alive(marker), "stdio 子进程未被清理"


@needs_mcp_sdk
def test_check_reports_error_without_failing_request(kb, home):
    """连不上是**结果**不是故障：请求仍 200，该行 status=error 且 error 非空。"""
    project_mcp(kb, {"broken": {"command": "guanlan-no-such-command-42", "timeout": {"startup": 5}}})
    with TestClient(create_app(kb)) as c:
        resp = c.post("/api/mcp/check", json={})
    assert resp.status_code == 200
    (result,) = resp.json()["results"]
    assert result["status"] == "error" and result["error"]
    assert result["tools"] == []


@pytest.fixture
def html_server():
    """只回 HTML 的环回服务：喂给 MCP 客户端会触发 `NonMcpEndpointError`——那条错误消息里**必带
    完整 URL**（`mcp/client.py:381-388`），是验证脱敏的唯一可靠靶子（不出网、端口由内核选）。"""

    class _HtmlHandler(BaseHTTPRequestHandler):
        def _reply(self, body=b""):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_HEAD(self):  # noqa: N802 — BaseHTTPRequestHandler 的钩子名
            self._reply()

        def do_GET(self):  # noqa: N802
            self._reply(b"<html>not an MCP endpoint</html>")

        def log_message(self, *args):  # 静音：默认往 stderr 打访问日志
            pass

    httpd = HTTPServer(("127.0.0.1", 0), _HtmlHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()


def token_url_server(port: int) -> dict:
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/mcp?token={PROJECT_TOKEN}",
        "headers": {"Authorization": f"Bearer {USER_TOKEN}"},
        "timeout": {"startup": 5},
    }


@needs_mcp_sdk
def test_check_error_text_is_redacted(kb, home, html_server):
    """脱敏反向用例②：**让带 token 的 URL 连接失败**，断言 token 不经 `error` 漏出。

    只测配置展示会漏掉这条路径——agentao 的 `NonMcpEndpointError` 把**完整 URL** 写进错误消息
    （`mcp/client.py:381-388`），query 里的 token 就跟着出去了。
    """
    project_mcp(kb, {"html-site": token_url_server(html_server)})
    with TestClient(create_app(kb)) as c:
        resp = c.post("/api/mcp/check", json={})
    (result,) = resp.json()["results"]
    assert result["status"] == "error" and result["error"], result
    assert PROJECT_TOKEN not in resp.text, f"token 经 error 漏出：{result['error']}"
    assert USER_TOKEN not in resp.text
    # 反证"错误串里本来就没有 URL"：host 仍在（脱敏只摘掉凭据、保住可诊断性），只有 token 没了。
    assert "127.0.0.1" in result["error"] and "?token" not in result["error"]


@needs_mcp_sdk
def test_check_error_hides_stdio_command_path(kb, home):
    """脱敏反向用例③：stdio 连不上时，错误串里不得带 command 的绝对路径（含用户名/本机目录）。"""
    project_mcp(
        kb, {"broken": {"command": "/Users/someone/private/bin/guanlan-no-such-cmd-42"}}
    )
    with TestClient(create_app(kb)) as c:
        resp = c.post("/api/mcp/check", json={})
    (result,) = resp.json()["results"]
    assert result["status"] == "error"
    assert "/Users/someone" not in resp.text, f"本机路径漏出：{result['error']}"
    assert "guanlan-no-such-cmd-42" in result["error"]  # 仍能看出是哪个命令没找到


@needs_mcp_sdk
def test_check_error_log_is_redacted(kb, home, caplog, html_server):
    """agentao 自己那条 ERROR 也得脱敏：响应体擦干净、终端里却留着 `?token=` 不叫脱敏。

    `McpClient.connect` 的失败分支 `logger.error("Failed to connect ... %s")` 会把**未脱敏的完整
    URL** 写进 stderr（实测），而这个 ERROR 现在是用户在浏览器里点一下就触发的。

    必须用 `html_server` 这个靶子：随便找个连不上的端口，上游的错误消息里**根本不含 URL**，
    断言就成了空转（本用例初稿正是这么写的，删掉 addFilter 也照样绿）。
    """
    project_mcp(kb, {"html-site": token_url_server(html_server)})
    with caplog.at_level("DEBUG", logger="agentao.mcp"), TestClient(create_app(kb)) as c:
        resp = c.post("/api/mcp/check", json={})
    assert resp.json()["results"][0]["status"] == "error"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Failed to connect" in logged, "上游没打那条 ERROR，用例失去意义"
    assert f"127.0.0.1:{html_server}" in logged, "日志里没有 URL，脱敏断言会空转"
    assert PROJECT_TOKEN not in logged, f"token 经日志漏出：{logged}"


@needs_mcp_sdk
def test_check_unknown_name_404(kb, home):
    project_mcp(kb, {"self": {"command": "whatever"}})
    with TestClient(create_app(kb)) as c:
        assert c.post("/api/mcp/check", json={"name": "ghost"}).status_code == 404


def test_check_with_unreadable_config_is_422(kb, home):
    """同一份坏配置：展示端如实标错（上一组用例），检查端**不能** 500。

    500 会让前端只显示一句「连接检查失败：HTTP 500」，一个字不提配置文件才是原因。
    """
    (kb / ".agentao").mkdir(parents=True, exist_ok=True)
    (kb / ".agentao" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"local": "some-server"}}), encoding="utf-8"
    )
    with TestClient(create_app(kb)) as c:
        resp = c.post("/api/mcp/check", json={})
        assert c.get("/api/mcp").status_code == 200  # 展示端仍可用
    assert resp.status_code == 422
    assert "mcp.json" in resp.json()["detail"]


def test_check_without_config_returns_empty(kb, home):
    """无配置 → 空结果，不 501（根本没走到 SDK）。"""
    with TestClient(create_app(kb)) as c:
        resp = c.post("/api/mcp/check", json={})
    assert resp.status_code == 200 and resp.json() == {"results": []}


def test_check_requires_a_body(kb, home, monkeypatch):
    """请求体必填（最简 `{}`），无体 POST → 422（决策P4.19-13）。

    这条守的是 CSRF：无体 POST 与 `text/plain` 体都是浏览器**简单请求**、不触发 CORS 预检，若放行
    无体，任意网页就能驱使本机观澜去连一遍全部配置的 MCP server（起子进程 + 对外发请求）——响应读
    不到，副作用照发。`application/json` 则会预检、被同源策略拦下。
    """
    monkeypatch.setattr(mcpdiag, "check_servers", lambda root, *, name=None: [])
    with TestClient(create_app(kb)) as c:
        assert c.post("/api/mcp/check").status_code == 422  # 无体
        assert c.post("/api/mcp/check", content=b"{}",
                      headers={"Content-Type": "text/plain"}).status_code == 422  # 非 JSON 体
        assert c.post("/api/mcp/check", json={}).status_code == 200  # 正常前端路径


def test_check_is_single_flight(kb, home, monkeypatch):
    """并发第二个请求 → **409，不排队**（决策P4.19-10）：排队只会把连点变成 N 次真实连接。"""
    entered, release = threading.Event(), threading.Event()

    def _slow(root, *, name=None):
        entered.set()
        release.wait(10)
        return []

    monkeypatch.setattr(mcpdiag, "check_servers", _slow)
    with TestClient(create_app(kb)) as c:
        first = {}
        worker = threading.Thread(
            target=lambda: first.update(code=c.post("/api/mcp/check", json={}).status_code)
        )
        worker.start()
        try:
            assert entered.wait(10), "第一个检查没进到执行体"
            assert c.post("/api/mcp/check", json={}).status_code == 409
        finally:
            release.set()
            worker.join(10)
    assert first["code"] == 200
    # 闸必须被放开：否则一次检查之后端点永久 409。
    with TestClient(create_app(kb)) as c:
        assert c.post("/api/mcp/check", json={}).status_code == 200


def test_check_without_sdk_is_501(kb, home, monkeypatch):
    """缺 `[mcp]` extra → 501 + 安装指引（不是 500）；配置展示不受影响。"""
    project_mcp(kb, {"remote": {"url": "https://api.example.com/mcp"}})

    def _no_sdk(root, *, name=None):
        raise mcpdiag.McpSdkUnavailable("连接检查需要官方 MCP SDK：请先 pip install 'guanlan-wiki[mcp]'")

    monkeypatch.setattr(mcpdiag, "check_servers", _no_sdk)
    with TestClient(create_app(kb)) as c:
        resp = c.post("/api/mcp/check", json={})
        assert c.get("/api/mcp").status_code == 200
    assert resp.status_code == 501 and "guanlan-wiki[mcp]" in resp.json()["detail"]


# ───────────────────────────── reader 裁剪 ─────────────────────────────


def test_reader_drops_both_endpoints(kb, home):
    """reader 下两个端点**不注册**（决策P4.19-6）：理由不是"写 KB"，而是连接检查**有外部副作用**
    ——发网络请求、拉起本机 stdio 子进程。"""
    project_mcp(kb, {"remote": {"url": "https://api.example.com/mcp"}})
    with TestClient(create_app(kb, reader=True)) as c:
        assert c.get("/api/mcp").status_code == 404
        assert c.post("/api/mcp/check", json={}).status_code == 404
        assert c.get("/api/report/check").status_code == 200  # 只读诊断照旧可用（对照）


# ───────────────────────── 前端接线（静态） ─────────────────────────


def test_frontend_wires_panel_and_escapes():
    from guanlan.web.app import STATIC_DIR

    reports = (STATIC_DIR / "reports.js").read_text(encoding="utf-8")
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    chat = (STATIC_DIR / "chat.js").read_text(encoding="utf-8")
    assert 'id="mcp-btn"' in index
    assert "/api/mcp/check" in reports and "res.status === 409" in reports
    # 工具 description / 连接错误都是外部不可信文本：渲染前必须转义（决策P4.19-11）。
    assert "escapeHtml(tool.description" in reports and "escapeHtml(r.error)" in reports
    assert '"mcp-btn"' in chat  # reader 下隐藏（端点 404，不给死按钮）
    # 归属守卫：慢检查落地时若浮层已换主人，不得覆写别人的 #overlay-body。
    assert "overlayRepaint !== paintMcp" in reports
    # 防连点必须是渲染态（`btn.disabled = true` 会被随后的重画连节点一起丢掉）。
    assert "mcpChecking" in reports and 'mcpChecking ? " disabled" : ""' in reports
