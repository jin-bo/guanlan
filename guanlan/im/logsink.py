"""IM 宿主的日志出口（P4.21.1，见 docs/P4.21.1-IM日志出口.md）。**纯 stdlib、零依赖。**

`guanlan/im/` 一路埋了不少 `_logger.debug(...)`——丢弃未授权消息、丢弃越权合成消息、卡片回调
走的是哪种帧——但**宿主从不配置 `logging`**，于是 INFO/DEBUG 没有任何 handler 接
（`logging.lastResort` 只兜 WARNING+），这些记录默认一个字也不出现。想看它们，此前唯一的办法是
改源码插 `print` 再跑一次真机会话，而真机会话要人拿着手机点。

本模块补的就是那个出口：一个 stderr handler + 一个级别旋钮。**埋点早就写好了，缺的只是出口。**

## 作用域即安全边界（**关键**）

handler 挂在 **`guanlan` 这棵 logger 树**上、级别也只设在它上面，**绝不碰 root**。于是：

- `--log-level debug` **在结构上不可能**打开 `httpx` / `lark_oapi` 的 DEBUG——它们的记录属于
  别的 logger 树，永远到不了本 handler。**用作用域代替黑名单**：黑名单要逐个列 logger 名，漏一个
  就把用户的私聊内容写进日志文件或 journal；作用域是"默认关"，列漏了也不会泄。
- 第三方的 WARNING+ 仍照旧走 `logging.lastResort`，与加本模块之前**逐字节相同**。

姿态是**不打消息正文**：`guanlan/im/` 现有的日志只打事件、ID 形状、计数与结果，本模块不改变这一点
（它只决定这些记录看不看得见，不决定记什么）。

## 与硬退出路径的关系

决策P4.21-59 要求第二次中断信号那条路**完全绕开 logging**（`os.write(2, …)` 后 `os._exit(5)`）。
本模块只在启动时装一个 handler，**不触碰那条路**；硬退出仍不承诺刷完缓冲，文档口径不变。
"""

from __future__ import annotations

import logging
import sys

__all__ = ["LOG_LEVELS", "LOGGER_NAME", "install_log_sink"]

# 本 handler 只服务这棵树（见模块 docstring「作用域即安全边界」）。
LOGGER_NAME = "guanlan"
# 给 handler 起名，使**重复安装成为幂等操作**：先按名摘掉旧的再挂新的，杜绝同一条记录打两遍
# （测试里会连装几次；将来若有别的宿主复用本函数同理）。
_HANDLER_NAME = "guanlan-im-stderr"
# 只开放三档：再细的档（如 `critical`）对一个长驻宿主没有使用场景，多一档就多一种要解释的状态。
# **显式映射而非 `logging.getLevelNamesMapping()`**：后者是 3.11+ 才有的，而本包要 3.10（见
# `pyproject.toml` 的 `requires-python`）；也不用 `getattr(logging, …)`，那会让任意大写属性名
# （`logging.FATAL`、乃至非级别属性）成为合法输入。
_LEVELS = {"warning": logging.WARNING, "info": logging.INFO, "debug": logging.DEBUG}
LOG_LEVELS = tuple(_LEVELS)


def install_log_sink(level: str) -> logging.Handler:
    """给 `guanlan` logger 树装 stderr handler 并设级别；返回该 handler（供测试断言/清理）。

    **幂等**：按 `_HANDLER_NAME` 摘掉自己上一次装的，再挂新的——重复调用不会让记录打两遍。
    **级别设在 logger 上而非 handler 上**：`logging.getLogger("guanlan")` 默认 `NOTSET`、生效级别
    继承自 root（WARNING），只调 handler 级别的话 DEBUG 记录**根本到不了 handler**。
    """
    logger = logging.getLogger(LOGGER_NAME)
    for existing in list(logger.handlers):
        if getattr(existing, "name", None) == _HANDLER_NAME:
            logger.removeHandler(existing)
            existing.close()

    handler = logging.StreamHandler(sys.stderr)
    handler.name = _HANDLER_NAME
    # 带时刻：真机排障要把"我点了按钮"那一秒和日志行对上，没有时刻就只能数行数。
    handler.setFormatter(
        logging.Formatter(fmt="%(asctime)s %(levelname)-7s %(name)s  %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(handler)
    try:
        logger.setLevel(_LEVELS[level])
    except KeyError:  # CLI 的 choices 是第一道门；内核自挡，供 web/测试直调时不静默吞
        raise ValueError(f"未知日志级别 {level!r}，可选：{'/'.join(LOG_LEVELS)}") from None
    return handler
