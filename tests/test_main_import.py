"""main.py 插件入口装配冒烟测试：扩展 astrbot stub 后导入 main 并实例化插件类"""

import sys
import types

import pytest


def _install_full_astrbot_stub():
    """在 conftest 的基础 stub 上补齐 main.py 需要的符号"""
    import astrbot.api as api

    # astrbot.api.AstrBotConfig
    class AstrBotConfig(dict):
        def save_config(self):
            pass

    api.AstrBotConfig = AstrBotConfig

    # astrbot.api.star: Context / Star
    import astrbot.api.star as api_star

    class Context:
        def __init__(self):
            self.cron_manager = types.SimpleNamespace(
                add_basic_job=None  # 占位，实例化后不会被调用
            )
            self.web_apis = []

        def get_registered_star(self, name):
            return None

        def register_web_api(self, route, handler, methods, desc):
            self.web_apis.append((route, methods))

    class Star:
        def __init__(self, context, config=None):
            self.context = context

        async def html_render(self, tmpl, data, return_url=True, options=None):
            return None

    api_star.Context = Context
    api_star.Star = Star
    api_star.register = lambda *a, **k: (lambda cls: cls)

    # astrbot.api.event.filter
    event_mod = sys.modules.get("astrbot.api.event")
    if event_mod is None:
        event_mod = types.ModuleType("astrbot.api.event")
        sys.modules["astrbot.api.event"] = event_mod

    class _Filter:
        @staticmethod
        def event_message_type(*a, **k):
            return lambda f: f

        @staticmethod
        def command(*a, **k):
            return lambda f: f

    event_mod.filter = _Filter

    # astrbot.core.star.filter.event_message_type.EventMessageType
    core = types.ModuleType("astrbot.core")
    core_star = types.ModuleType("astrbot.core.star")
    core_star_filter = types.ModuleType("astrbot.core.star.filter")
    emt_mod = types.ModuleType("astrbot.core.star.filter.event_message_type")

    class EventMessageType:
        ALL = 3
        GROUP_MESSAGE = 1
        PRIVATE_MESSAGE = 2

    emt_mod.EventMessageType = EventMessageType
    sys.modules["astrbot.core"] = core
    sys.modules["astrbot.core.star"] = core_star
    sys.modules["astrbot.core.star.filter"] = core_star_filter
    sys.modules["astrbot.core.star.filter.event_message_type"] = emt_mod

    # astrbot.core.utils.session_waiter（farm 模块需要）
    core_utils = types.ModuleType("astrbot.core.utils")
    sw_mod = types.ModuleType("astrbot.core.utils.session_waiter")

    class SessionController:
        def keep(self, timeout=30, reset_timeout=True):
            pass

        def stop(self, error=None):
            pass

    def session_waiter(timeout=30, record_history_chains=False):
        def deco(f):
            return f

        return deco

    sw_mod.SessionController = SessionController
    sw_mod.session_waiter = session_waiter
    sw_mod.SessionWaiter = object
    sys.modules["astrbot.core.utils"] = core_utils
    sys.modules["astrbot.core.utils.session_waiter"] = sw_mod


@pytest.mark.asyncio
async def test_main_assembly(econ_db):
    _install_full_astrbot_stub()
    # 与 AstrBot 加载方式一致：父目录上 sys.path，按包导入 dirname.main（命名空间包）
    import importlib
    from pathlib import Path

    parent = str(Path(__file__).parent.parent.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    main_mod = importlib.import_module("astrbot_plugin_zhenxun_economy.main")
    ZhenxunEconomyPlugin = main_mod.ZhenxunEconomyPlugin
    import astrbot.api as api
    import astrbot.api.star as api_star

    plugin = ZhenxunEconomyPlugin(api_star.Context(), api.AstrBotConfig())
    # 命令表注册成功且无编译错误
    assert len(plugin._handlers) > 20

    # 各命令正则抽样匹配验证（转义未损坏）
    import re

    cases = {
        "签到": True, "我的签到": True, "好感度排行": True, "好感度总排行 20": True,
        "商店": True, "购买道具 神秘药水 2": True, "使用道具 1": True,
        "金币总排行": True, "存款": True, "存款 500": True, "取款 100": True,
        "我的银行信息": True, "银行信息": True,
        "装弹": True, "装弹 3": True, "装弹 3 500": True, "俄罗斯轮盘 1 100": True,
        "接受对决": True, "开枪": True, "咔": True, "结算": True, "我的战绩": True,
        "轮盘胜场排行": True, "轮盘最高连败排行 20": True,
        "塞红包 100": True, "塞红包 100 5": True, "金币红包 50 3": True,
        "开": True, "抢": True, "退回红包": True, "经济帮助": True,
        # 不应匹配
        "签": False, "存款abc": False, "开机": False, "随便聊聊": False,
        "开农场": False,  # “开”只能精确匹配，农场命令走 farm.dispatch
    }
    for text, expected in cases.items():
        matched = any(p.match(text) for p, _h, _m in plugin._handlers)
        assert matched == expected, f"命令匹配错误: {text!r} 期望 {expected}"

    # 长命令优先：好感度总排行不能落到好感度排行
    for p, _h, _m in plugin._handlers:
        if m := p.match("好感度总排行 20"):
            assert "总排行" in p.pattern
            assert m.group(1) == "20"
            break
