"""测试基础设施：stub astrbot.api（core 层只用到 logger 与 StarTools）"""

import sys
import tempfile
import types
from pathlib import Path

import pytest
import pytest_asyncio

PLUGIN_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))


def _install_astrbot_stub():
    if "astrbot" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api_star = types.ModuleType("astrbot.api.star")

    class _Logger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass

    api.logger = _Logger()

    class StarTools:
        @classmethod
        def get_data_dir(cls, plugin_name=None):
            p = Path(tempfile.mkdtemp(prefix="zxeco_test_"))
            p.mkdir(parents=True, exist_ok=True)
            return p

    api_star.StarTools = StarTools
    api.star = api_star

    # 事件与消息组件 stub（模块层 import 需要，测试不触发真实发送）
    api_event = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []

    api_event.AstrMessageEvent = AstrMessageEvent
    api_event.MessageChain = MessageChain
    api.event = api_event

    api_mc = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class At:
        def __init__(self, qq=None):
            self.qq = qq

    class Image:
        @classmethod
        def fromURL(cls, url):
            obj = cls()
            obj.url = url
            return obj

    class Node:
        def __init__(self, uin=None, name=None, content=None):
            self.uin = uin
            self.name = name
            self.content = content

    class Nodes:
        def __init__(self, nodes=None):
            self.nodes = nodes or []

    api_mc.Plain = Plain
    api_mc.At = At
    api_mc.Image = Image
    api_mc.Node = Node
    api_mc.Nodes = Nodes

    # Web API stub（console_api 导入需要；handler 内的 request 由测试自行 monkeypatch）
    api_web = types.ModuleType("astrbot.api.web")

    def json_response(data=None, status_code=200, headers=None):
        return {"_status": status_code, "body": {} if data is None else data}

    def error_response(message, status_code=400, data=None, headers=None):
        return {
            "_status": status_code,
            "body": {"status": "error", "message": message, "data": data},
        }

    api_web.json_response = json_response
    api_web.error_response = error_response
    api_web.request = None
    api.web = api_web
    sys.modules["astrbot.api.web"] = api_web

    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.star"] = api_star
    sys.modules["astrbot.api.event"] = api_event
    sys.modules["astrbot.api.message_components"] = api_mc


_install_astrbot_stub()


@pytest_asyncio.fixture
async def econ_db():
    from core.database import db

    await db.init()
    yield db
    await db.close()
