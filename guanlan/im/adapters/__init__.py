"""适配器注册表（P4.21 §3.1）。**新增平台只碰这里**：核心零改（决策P4.21-3）。

每个工厂**在被调用时**才 import 自己的 SDK，故缺 `[im-feishu]` 不妨碍跑微信（反之亦然）；
缺对应 extra 时抛 `GuanlanError(EXIT_USAGE)` 优雅引导安装、**不吐 traceback**（§9.2）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...errors import EXIT_USAGE, GuanlanError
from ..contract import IMAdapter

# `group_wanted` = "这次部署要不要收群消息"，`actions_wanted` = "这次部署要不要可点面"，
# 二者都由核心从**部署意图**推出后传进来。它们是**平台无关的**，不是平台分支——决策P4.21-3
# 守住的是「核心不写 `if platform ==`」，不是「适配器不许收参数」。飞书用前者决定 bot 身份探测
# 失败是否致命（§8.2）、用后者决定要不要注册卡片回调（`im-identify` 传 False，因为那个模式的
# 安全性整个建立在「绝不回复任何消息」上，而卡片回调是要同步回一个 toast 的）；微信两者都忽略。
AdapterFactory = Callable[..., IMAdapter]


def _weixin(
    state: Path, *, group_wanted: bool = False, actions_wanted: bool = True
) -> IMAdapter:
    try:
        import httpx  # noqa: F401
    except ImportError:
        raise GuanlanError(
            "`guanlan im --platform weixin` 需要可选依赖："
            "请先 `pip install 'guanlan-wiki[im-weixin]'`。",
            exit_code=EXIT_USAGE,
        ) from None
    from .weixin import WeixinAdapter

    # iLink 通常不投递群事件、也没有任何可点面，故两个部署意图对它都无意义。
    return WeixinAdapter(state)


def _feishu(
    state: Path, *, group_wanted: bool = False, actions_wanted: bool = True
) -> IMAdapter:
    try:
        import lark_oapi  # noqa: F401
    except ImportError:
        raise GuanlanError(
            "`guanlan im --platform feishu` 需要可选依赖："
            "请先 `pip install 'guanlan-wiki[im-feishu]'`（需要 `lark-oapi>=1.6.8`）。",
            exit_code=EXIT_USAGE,
        ) from None
    from .feishu import FeishuAdapter

    return FeishuAdapter(state, group_wanted=group_wanted, actions_wanted=actions_wanted)


async def _weixin_login(state: Path) -> int:
    from .weixin import run_qrcode_login

    return await run_qrcode_login(state)


ADAPTERS: dict[str, AdapterFactory] = {
    "weixin": _weixin,
    "feishu": _feishu,
}

# 需要（且支持）交互式登录流程的平台。**用注册表而不是 `if platform == "weixin"`**：
# 决策P4.21-3 的判据是"核心零平台分支"，一个 `!=` 同样是分支。飞书凭据走环境变量，
# 故它根本不在这张表里——`run_login` 查不到即拒绝，理由从数据里读出来，不写死在代码里。
LOGIN_FLOWS: dict[str, Callable[[Path], object]] = {"weixin": _weixin_login}

__all__ = ["ADAPTERS", "LOGIN_FLOWS", "AdapterFactory"]
