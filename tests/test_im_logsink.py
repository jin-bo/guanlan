"""P4.21.1 IM 日志出口测试（零网络、零 SDK，见 docs/P4.21.1-IM日志出口.md）。

本模块的全部价值是**让早就埋好的 DEBUG 记录看得见**，而它唯一的风险是**看见得太多**
（第三方 SDK 的 DEBUG 会把消息正文打出来）。故用例分两组：出口通不通，以及作用域关不关得住。
"""

import logging

import pytest

from guanlan.im.defaults import DEFAULT_LOG_LEVEL
from guanlan.im.logsink import LOG_LEVELS, LOGGER_NAME, install_log_sink


@pytest.fixture(autouse=True)
def _restore_guanlan_logger():
    """每例跑完把 `guanlan` logger 复原——否则装上的 handler 会跟着进程串到别的测试里。"""
    logger = logging.getLogger(LOGGER_NAME)
    before_handlers, before_level = list(logger.handlers), logger.level
    yield
    for h in list(logger.handlers):
        if h not in before_handlers:
            logger.removeHandler(h)
            h.close()
    logger.setLevel(before_level)


# ── 出口通不通 ──────────────────────────────────────────────────────────────────
def test_debug_records_become_visible(capsys):
    """`debug` 档下 `guanlan.im` 的 DEBUG 出得来——这正是 P4.22 §9 那几条真机未决项要的。"""
    install_log_sink("debug")
    logging.getLogger("guanlan.im").debug("卡片回调以 CARD 帧下发")
    assert "卡片回调以 CARD 帧下发" in capsys.readouterr().err


def test_default_level_hides_debug_but_keeps_warning(capsys):
    """默认档 = 装本模块之前就看得见的那一档：WARNING 出、DEBUG 不出。"""
    install_log_sink(DEFAULT_LOG_LEVEL)
    log = logging.getLogger("guanlan.im")
    log.debug("不该出现的 DEBUG")
    log.warning("该出现的 WARNING")
    err = capsys.readouterr().err
    assert "该出现的 WARNING" in err and "不该出现的 DEBUG" not in err


def test_info_level_shows_info_not_debug(capsys):
    install_log_sink("info")
    log = logging.getLogger("guanlan.im")
    log.info("INFO 行")
    log.debug("DEBUG 行")
    err = capsys.readouterr().err
    assert "INFO 行" in err and "DEBUG 行" not in err


def test_records_go_to_stderr_not_stdout(capsys):
    """日志一律走 stderr：stdout 留给启动横幅与将来可能的结构化输出，两者不许混流。"""
    install_log_sink("debug")
    logging.getLogger("guanlan.im").debug("到 stderr")
    out, err = capsys.readouterr()
    assert "到 stderr" in err and "到 stderr" not in out


def test_format_carries_time_level_and_logger(capsys):
    """真机排障要把"我点了按钮"那一秒与日志行对上，故时刻/级别/logger 名三者都要在。"""
    install_log_sink("debug")
    logging.getLogger("guanlan.im").debug("正文")
    line = capsys.readouterr().err.strip()
    assert "DEBUG" in line and "guanlan.im" in line and line[:2].isdigit()  # HH:MM:SS 开头


# ── 作用域关不关得住（**本模块的安全边界**）────────────────────────────────────────
def test_debug_level_never_opens_third_party_debug(capsys):
    """★ `--log-level debug` **在结构上**打不开第三方 DEBUG——handler 只挂 `guanlan` 树。

    这条是本模块的安全断言：第三方 SDK（`lark_oapi` / `httpx`）的 DEBUG 会把**消息正文**打出来，
    而观澜自己的日志姿态是「只打事件、ID 形状、计数」。用**作用域**挡而不是用黑名单挡——黑名单
    要逐个列 logger 名，漏一个就把用户私聊写进日志。
    """
    install_log_sink("debug")
    for third_party in ("httpx", "httpcore", "lark_oapi", "websockets", "openai", "agentao"):
        logging.getLogger(third_party).debug(f"{third_party} 的消息正文不该出现")
    assert capsys.readouterr().err == ""


def test_root_logger_is_not_touched():
    """不碰 root：第三方 WARNING+ 仍走 `logging.lastResort`，与装本模块之前逐字节相同。"""
    root_before = (list(logging.getLogger().handlers), logging.getLogger().level)
    install_log_sink("debug")
    assert (list(logging.getLogger().handlers), logging.getLogger().level) == root_before


def test_sibling_tree_unaffected(capsys):
    """只有 `guanlan` 这棵树被提级——同名前缀的别的树（`guanlanx`）不该被带上。"""
    install_log_sink("debug")
    logging.getLogger("guanlanx.thing").debug("不该出现")
    assert capsys.readouterr().err == ""


# ── 幂等与入参 ──────────────────────────────────────────────────────────────────
def test_install_is_idempotent_no_double_print(capsys):
    """重复安装不让同一条记录打两遍（测试会连装几次；将来别的宿主复用同理）。"""
    for _ in range(3):
        install_log_sink("debug")
    assert len(logging.getLogger(LOGGER_NAME).handlers) == 1
    logging.getLogger("guanlan.im").debug("只该出现一次")
    assert capsys.readouterr().err.count("只该出现一次") == 1


@pytest.mark.parametrize("level", LOG_LEVELS)
def test_all_advertised_levels_install(level):
    """CLI choices 里列出的每一档都真的装得上（别让帮助文案承诺一个装不上的值）。"""
    assert install_log_sink(level) in logging.getLogger(LOGGER_NAME).handlers


@pytest.mark.parametrize("bad", ["trace", "WARNING", "", "fatal"])
def test_unknown_level_raises(bad):
    """内核自挡未知级别（CLI 的 choices 是第一道门，供 web/测试直调时不静默吞）。"""
    with pytest.raises(ValueError, match="未知日志级别"):
        install_log_sink(bad)


def test_default_level_is_in_advertised_levels():
    """默认值必须是 CLI 能接受的值之一——否则 `--log-level` 的默认态自己就非法。"""
    assert DEFAULT_LOG_LEVEL in LOG_LEVELS
