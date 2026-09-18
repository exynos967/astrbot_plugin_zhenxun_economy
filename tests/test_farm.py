"""农场子模块测试：金币兑换计算 + 数据库建表 + import 冒烟

农场代码依赖 astrbot.api 的 event/message_components/session_waiter 等符号，
conftest.py 只 stub 了 logger 与 StarTools，这里在 import modules.farm 之前
补充 stub（不修改共享的 conftest.py）。
"""

import math
import sys
import types
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent


def _install_farm_astrbot_stub():
    """补齐农场模块 import 所需的 astrbot 符号（在 conftest stub 基础上扩展）"""
    api = sys.modules["astrbot.api"]
    astrbot = sys.modules["astrbot"]

    # astrbot.api.event
    api_event = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:  # 仅占位，测试里用自定义 FakeEvent
        pass

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []

    class _Filter:
        @staticmethod
        def event_message_type(*a, **k):
            return lambda f: f

        @staticmethod
        def command(*a, **k):
            return lambda f: f

    api_event.AstrMessageEvent = AstrMessageEvent
    api_event.MessageChain = MessageChain
    api_event.filter = _Filter()

    # astrbot.api.message_components
    api_comp = types.ModuleType("astrbot.api.message_components")

    class _Base:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Plain(_Base):
        def __init__(self, text="", **kwargs):
            super().__init__(text=text, **kwargs)

    class At(_Base):
        pass

    class Reply(_Base):
        pass

    class Node(_Base):
        pass

    class Image(_Base):
        @classmethod
        def fromBase64(cls, data):
            return cls(base64=data)

        @classmethod
        def fromURL(cls, url):
            return cls(url=url)

    api_comp.Plain = Plain
    api_comp.At = At
    api_comp.Reply = Reply
    api_comp.Node = Node
    api_comp.Image = Image

    # astrbot.core.utils.session_waiter
    core = types.ModuleType("astrbot.core")
    core_utils = types.ModuleType("astrbot.core.utils")
    sw = types.ModuleType("astrbot.core.utils.session_waiter")

    class SessionController:
        def stop(self):
            pass

    def session_waiter(**kwargs):
        return lambda f: f

    sw.SessionController = SessionController
    sw.session_waiter = session_waiter

    api.event = api_event
    api.message_components = api_comp
    astrbot.core = core
    core.utils = core_utils
    sys.modules["astrbot.api.event"] = api_event
    sys.modules["astrbot.api.message_components"] = api_comp
    sys.modules["astrbot.core"] = core
    sys.modules["astrbot.core.utils"] = core_utils
    sys.modules["astrbot.core.utils.session_waiter"] = sw


_install_farm_astrbot_stub()

from core import UserConsole  # noqa: E402
from modules.farm import FarmModule  # noqa: E402
from modules.farm import cfg as farm_cfg  # noqa: E402
from modules.farm.database.database import g_pSqlManager  # noqa: E402
from modules.farm.dbService import g_pDBService  # noqa: E402


class FakeEvent:
    """最小可用的 AstrMessageEvent 替身：记录发送的消息链"""

    def __init__(self, uid: str = "10001", text: str = ""):
        self._uid = uid
        self.message_str = text
        self.is_at_or_wake_command = True
        self.sent = []
        self.stopped = False

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return "测试农夫"

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        comp = sys.modules["astrbot.api.message_components"].Plain
        return [comp(self.message_str)] if self.message_str else []

    def get_self_id(self):
        return "9999"

    def stop_event(self):
        self.stopped = True

    def chain_result(self, chain):
        return sys.modules["astrbot.api.event"].MessageChain(chain)

    def plain_result(self, text):
        comp = sys.modules["astrbot.api.message_components"].Plain
        return sys.modules["astrbot.api.event"].MessageChain([comp(text)])

    async def send(self, message):
        self.sent.append(message)


def _sent_texts(event: FakeEvent) -> str:
    """汇总 FakeEvent 已发送消息中的全部纯文本"""
    texts = []
    for msg in event.sent:
        for comp in getattr(msg, "chain", []):
            if hasattr(comp, "text"):
                texts.append(comp.text)
    return "\n".join(texts)


@pytest.fixture
def farm_db(tmp_path, monkeypatch):
    """把农场数据库重定向到临时目录（schema 与原版完全一致）"""
    db_dir = tmp_path / "farm_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(farm_cfg.g_pConfigManager, "sDBPath", db_dir)
    monkeypatch.setattr(
        farm_cfg.g_pConfigManager, "sDBFilePath", db_dir / "farm.db"
    )
    return db_dir / "farm.db"


@pytest.fixture
def farm_module():
    """兑换倍率 2、手续费率 0.2（对齐真寻默认值，便于口算校验）"""
    return FarmModule(None, {"farm_exchange_rate": 2, "farm_exchange_tax": 0.2})


# ---------- import 冒烟 ----------


def test_import_smoke():
    module = FarmModule(None, {})
    assert "购买农场币" in module.commands
    assert "开通农场" in module.commands
    assert "我的农场" in module.commands


# ---------- 数据库初始化建表 ----------


@pytest.mark.asyncio
async def test_farm_db_init(farm_db):
    try:
        assert await g_pSqlManager.init()
        await g_pDBService.init()

        cursor = await g_pSqlManager.m_pDB.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}

        # 与真寻 farm.db 一致的 8 张用户数据表（第 9 张 plant 在 resource/db/plant.db）
        expected = {
            "user",
            "userItem",
            "userSeed",
            "userSignLog",
            "userSignSummary",
            "userPlant",
            "userSteal",
            "userSoil",
        }
        assert expected <= tables
        assert farm_db.exists()
    finally:
        await g_pSqlManager.cleanup()
        await g_pDBService.cleanup()


# ---------- 金币兑换农场币 ----------


async def _run_buy_point(module, event, num: str):
    """驱动 buyPoint 异步生成器并把产出消息送进 FakeEvent"""
    agen = module.buyPoint(event, [num])
    async for piece in agen:
        await event.send(piece)


@pytest.mark.asyncio
async def test_buy_point_success(econ_db, farm_db, farm_module):
    """正常兑换：fee=floor(num*tax)，扣 num+fee 金币，到账 num*rate 农场币"""
    await g_pSqlManager.init()
    await g_pDBService.init()
    try:
        uid = "10001"
        # 开通农场（初始 500 农场币）并准备金币（新用户 100，再补 1000）
        await g_pDBService.user.initUserInfoByUid(uid=uid, name="测试农夫")
        await UserConsole.add_gold(uid, 1000, "test")

        event = FakeEvent(uid)
        # num=100, tax=0.2 -> fee=20, 扣 120 金币, 到账 100*2=200 农场币
        await _run_buy_point(farm_module, event, "100")

        assert await UserConsole.get_gold(uid) == 1100 - 120
        assert await g_pDBService.user.getUserPointByUid(uid) == 500 + 200

        text = _sent_texts(event)
        assert "充值200农场币成功" in text
        assert "手续费20金币" in text
        assert "当前农场币：700" in text
    finally:
        await g_pSqlManager.cleanup()
        await g_pDBService.cleanup()


@pytest.mark.asyncio
async def test_buy_point_insufficient_gold(econ_db, farm_db, farm_module):
    """余额不足：不扣金币、不加农场币"""
    await g_pSqlManager.init()
    await g_pDBService.init()
    try:
        uid = "10002"
        await g_pDBService.user.initUserInfoByUid(uid=uid, name="测试农夫")
        # 新用户仅 100 金币，买 100 农场币需 120 金币 -> 不足

        event = FakeEvent(uid)
        await _run_buy_point(farm_module, event, "100")

        assert await UserConsole.get_gold(uid) == 100  # 未扣费
        assert await g_pDBService.user.getUserPointByUid(uid) == 500  # 未到账
        assert "金币不足" in _sent_texts(event)
    finally:
        await g_pSqlManager.cleanup()
        await g_pDBService.cleanup()


@pytest.mark.asyncio
async def test_buy_point_zero_fee_and_invalid(econ_db, farm_db, farm_module):
    """小额兑换手续费向下取整为 0 时只扣本金；非法数量直接拒绝"""
    await g_pSqlManager.init()
    await g_pDBService.init()
    try:
        uid = "10003"
        await g_pDBService.user.initUserInfoByUid(uid=uid, name="测试农夫")
        # 新用户 100 金币

        event = FakeEvent(uid)
        # num=3, fee=floor(3*0.2)=0 -> 扣 3 金币, 到账 3*2=6 农场币
        await _run_buy_point(farm_module, event, "3")
        assert await UserConsole.get_gold(uid) == 97
        assert await g_pDBService.user.getUserPointByUid(uid) == 506
        assert "手续费0金币" in _sent_texts(event)

        # 非法数量
        event2 = FakeEvent(uid)
        await _run_buy_point(farm_module, event2, "abc")
        assert "不是正数" in _sent_texts(event2)
        assert await UserConsole.get_gold(uid) == 97

        # 未开通农场的用户不能兑换
        event3 = FakeEvent("10004")
        await _run_buy_point(farm_module, event3, "10")
        assert "尚未开通农场" in _sent_texts(event3)
    finally:
        await g_pSqlManager.cleanup()
        await g_pDBService.cleanup()


# ---------- dispatch 分发 ----------


@pytest.mark.asyncio
async def test_dispatch_hit_and_miss(farm_db, farm_module):
    """命中农场命令返回 True 且 stop_event；未命中返回 False"""
    await g_pSqlManager.init()
    await g_pDBService.init()
    try:
        # 未命中
        miss = FakeEvent("10001", "签到")
        assert await farm_module.dispatch(miss) is False
        assert not miss.stopped

        # 命中（未开通农场 -> 收到提示）
        hit = FakeEvent("10001", "我的农场币")
        assert await farm_module.dispatch(hit) is True
        assert hit.stopped
        assert "尚未开通农场" in _sent_texts(hit)
    finally:
        await g_pSqlManager.cleanup()
        await g_pDBService.cleanup()


def test_fee_formula_matches_zhenxun():
    """兑换公式与真寻版 buyPointByUid 对齐（纯计算校验）"""
    # 真寻：fee = floor(num * tax)，扣费 = num + fee，到账 = num * pro
    num, tax, pro = 123, 0.2, 2
    fee = math.floor(num * tax)
    assert fee == 24
    assert num + fee == 147
    assert int(num * pro) == 246
