"""银行模块测试：存款校验链 / 加息事件 / 取款锁定 / 结息计算 / 信息数据组装"""

import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _install_astrbot_runtime_stub():
    """在 conftest 的 astrbot stub 之上补全 event / message_components / util"""
    if "astrbot.api.event" in sys.modules:
        return
    event_mod = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:  # noqa: D401 - 测试桩
        pass

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain or [])

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain

    comp_mod = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text: str = ""):
            self.text = text

    class At:
        def __init__(self, qq=None, name=None):
            self.qq = qq
            self.name = name

    class Image:
        def __init__(self, url: str = ""):
            self.url = url

        @classmethod
        def fromURL(cls, url):
            return cls(url)

    comp_mod.Plain = Plain
    comp_mod.At = At
    comp_mod.Image = Image

    util_mod = types.ModuleType("astrbot.api.util")

    class SessionController:
        def keep(self, timeout=30, reset_timeout=True):
            pass

        def stop(self, error=None):
            pass

    def session_waiter(timeout=30, record_history_chains=False):
        def deco(fn):
            return fn

        return deco

    util_mod.SessionController = SessionController
    util_mod.SessionWaiter = object
    util_mod.session_waiter = session_waiter

    api = sys.modules["astrbot.api"]
    api.event = event_mod
    api.message_components = comp_mod
    api.util = util_mod
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = comp_mod
    sys.modules["astrbot.api.util"] = util_mod


_install_astrbot_runtime_stub()

from core import MahiroBank, SignUser, UserConsole  # noqa: E402
from core.database import db, now_str  # noqa: E402
from modules.bank import BankModule  # noqa: E402
from modules.bank import render as bank_render  # noqa: E402
import modules.bank.logic as bank_logic  # noqa: E402

DEFAULT_CONFIG = {
    "bank_sign_max_deposit": 100,
    "bank_max_daily_deposit_count": 3,
    "bank_rate_min": 0.0005,
    "bank_rate_max": 0.001,
    "bank_impression_event": 25,
    "bank_impression_event_prop": 0.3,
    "bank_impression_event_rate_min": 0.00001,
    "bank_impression_event_rate_max": 0.0003,
    "render_enabled": False,
}


class FakeEvent:
    """最简事件桩：记录 send 的消息链"""

    def __init__(self, user_id: str = "u1"):
        self._user_id = user_id
        self.sent: list = []

    def get_sender_id(self):
        return self._user_id

    def get_sender_name(self):
        return "测试员"

    def get_platform_name(self):
        return "test"

    async def send(self, chain):
        self.sent.append(chain)


def _last_text(event: FakeEvent) -> str:
    """提取最后一条消息链中的纯文本"""
    assert event.sent, "没有发送任何消息"
    return "".join(
        getattr(seg, "text", "") for seg in event.sent[-1].chain
    )


@pytest.fixture
def module():
    return BankModule(plugin=types.SimpleNamespace(), config=dict(DEFAULT_CONFIG))


async def _set_impression(user_id: str, impression: float):
    await SignUser.get_user(user_id)
    await db.execute(
        "UPDATE sign_users SET impression = ? WHERE user_id = ?", (impression, user_id)
    )


# ---------------- 存款校验链 ----------------


@pytest.mark.asyncio
async def test_deposit_check_amount_must_positive(econ_db, module):
    assert await module._deposit_check("u1", 0) == "存款数量必须大于 0 啊笨蛋！"
    assert await module._deposit_check("u1", -5) == "存款数量必须大于 0 啊笨蛋！"


@pytest.mark.asyncio
async def test_deposit_check_gold_insufficient(econ_db, module):
    # 新用户初始 100 金币，好感度 10 → 上限 1000，先触发金币校验
    await _set_impression("u1", 10)
    result = await module._deposit_check("u1", 200)
    assert result == "金币数量不足，当前你的金币为：100."


@pytest.mark.asyncio
async def test_deposit_check_limit(econ_db, module):
    # 好感度 0 → 上限保底 100
    await UserConsole.add_gold("u1", 100000, "test")
    result = await module._deposit_check("u1", 101)
    assert result == "存款超过上限，存款上限为：100，当前你的还可以存款金额：100。"
    # 好感度 1.5 × 100 = 150 → 上限 150
    await _set_impression("u1", 1.5)
    assert await module._deposit_check("u1", 150) is None
    result = await module._deposit_check("u1", 151)
    assert result == "存款超过上限，存款上限为：150，当前你的还可以存款金额：150。"


@pytest.mark.asyncio
async def test_deposit_check_daily_count(econ_db, module, monkeypatch):
    await UserConsole.add_gold("u1", 100000, "test")
    await _set_impression("u1", 100)  # 上限 10000
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: 0.001)
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.99)  # 不触发加息
    for _ in range(3):
        assert await module._deposit_check("u1", 100) is None
        await module._do_deposit("u1", 100)
    assert (
        await module._deposit_check("u1", 100)
        == "存款次数超过上限，每日存款次数上限为：3。"
    )


# ---------------- 加息事件 ----------------


@pytest.mark.asyncio
async def test_random_event_trigger(econ_db, module, monkeypatch):
    # 好感度不足 25 → 不触发
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.0)
    assert module._random_event(10) is None
    # 好感度达标但概率未命中 → 不触发
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.99)
    assert module._random_event(30) is None
    # 达标且命中 → 返回区间内加成
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.1)
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: 0.0002)
    assert module._random_event(30) == 0.0002


@pytest.mark.asyncio
async def test_deposit_with_impression_event(econ_db, module, monkeypatch):
    await UserConsole.add_gold("u1", 100000, "test")
    await _set_impression("u1", 30)
    # 第一次 uniform 是基础利率，第二次是加息事件加成
    rates = iter([0.001, 0.0002])
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: next(rates))
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.0)  # 必中加息
    event = FakeEvent("u1")
    await module.handle_deposit(event, 100)
    text = _last_text(event)
    assert "存款成功！" in text
    assert "小真寻偷偷将小时利率给你增加了" in text
    assert "当前小时利率为: 0.12%" in text  # (0.001 + 0.0002) * 100
    bank_user = await MahiroBank.get_user("u1")
    assert bank_user.amount == 100
    assert bank_user.rate == pytest.approx(0.0012)
    assert await UserConsole.get_gold("u1") == 100000 + 100 - 100


@pytest.mark.asyncio
async def test_deposit_reply_without_event(econ_db, module, monkeypatch):
    await UserConsole.add_gold("u1", 100000, "test")
    await _set_impression("u1", 10)  # 低于加息门槛
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: 0.001)
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.0)
    event = FakeEvent("u1")
    await module.handle_deposit(event, 100)
    text = _last_text(event)
    assert "存款成功！" in text
    assert "小真寻偷偷" not in text
    assert "预计总收益为:" in text


# ---------------- 取款 ----------------


@pytest.mark.asyncio
async def test_withdraw_locked(econ_db, module, monkeypatch):
    """当日存款处于锁定期，不可取"""
    await UserConsole.add_gold("u1", 100000, "test")
    await _set_impression("u1", 10)
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: 0.001)
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.99)
    await module._do_deposit("u1", 100)
    event = FakeEvent("u1")
    await module.handle_withdraw(event, 50)
    assert _last_text(event) == "取款金额不足，当前你的存款为：100（100已被锁定）！"
    # 金币未被退回
    assert await UserConsole.get_gold("u1") == 100000 + 100 - 100


@pytest.mark.asyncio
async def test_withdraw_success(econ_db, module, monkeypatch):
    await UserConsole.add_gold("u1", 100000, "test")
    await _set_impression("u1", 10)
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: 0.001)
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.99)
    await module._do_deposit("u1", 100)
    # 模拟结息完成 → 存款解锁
    await db.execute("UPDATE mahiro_bank_log SET is_completed = 1")
    event = FakeEvent("u1")
    await module.handle_withdraw(event, 60)
    text = _last_text(event)
    assert "取款成功！" in text
    assert "当前存款金额为: 40" in text
    bank_user = await MahiroBank.get_user("u1")
    assert bank_user.amount == 40


@pytest.mark.asyncio
async def test_withdraw_insufficient(econ_db, module, monkeypatch):
    await UserConsole.add_gold("u1", 100000, "test")
    await _set_impression("u1", 10)
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: 0.001)
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.99)
    await module._do_deposit("u1", 100)
    await db.execute("UPDATE mahiro_bank_log SET is_completed = 1")
    event = FakeEvent("u1")
    await module.handle_withdraw(event, 150)
    assert _last_text(event) == "取款金额不足，当前你的存款为：100（0已被锁定）！"


# ---------------- 每日结息 ----------------


async def _insert_deposit_log(user_id, amount, rate, effective_hour, is_completed=0):
    await db.execute(
        "INSERT INTO mahiro_bank_log (user_id, amount, rate, handle_type,"
        " is_completed, effective_hour, update_time, create_time)"
        " VALUES (?, ?, ?, 'DEPOSIT', ?, ?, ?, ?)",
        (user_id, amount, rate, is_completed, effective_hour, now_str(), now_str()),
    )


@pytest.mark.asyncio
async def test_settle_interest(econ_db, module):
    # 用户 A：账户 1000 @ 1%，当日有一笔未结算存款 100 @ 1% × 10 小时
    await UserConsole.get_user("u_a")
    await MahiroBank.get_user("u_a")
    await db.execute(
        "UPDATE mahiro_bank SET amount = 1000, rate = 0.01 WHERE user_id = 'u_a'"
    )
    await _insert_deposit_log("u_a", 100, 0.01, 10)
    # 用户 B：账户 1 @ 0.01%，未结算存款 1 @ 0.01% × 1 小时（利息 0 → 保底 1）
    await UserConsole.get_user("u_b")
    await MahiroBank.get_user("u_b")
    await db.execute(
        "UPDATE mahiro_bank SET amount = 1, rate = 0.0001 WHERE user_id = 'u_b'"
    )
    await _insert_deposit_log("u_b", 1, 0.0001, 1)

    await module.settle_interest()

    # A：存款利息 int(100*0.01*10)=10 + 存量利息 int(900*0.01)=9 → 共 19
    assert await UserConsole.get_gold("u_a") == 100 + 19
    # B：存款利息 int(1*0.0001*1)=0 → 保底 1；存量 0 不再计息
    assert await UserConsole.get_gold("u_b") == 100 + 1
    # 未结算存款全部标记完成
    pending = await db.fetchval(
        "SELECT COUNT(*) FROM mahiro_bank_log"
        " WHERE handle_type = 'DEPOSIT' AND is_completed = 0"
    )
    assert pending == 0
    # 利息日志：A 两条（10 + 9），B 一条（1）
    interest_logs = await db.fetchall(
        "SELECT user_id, amount FROM mahiro_bank_log WHERE handle_type = 'INTEREST'"
        " ORDER BY user_id, amount"
    )
    assert [(r["user_id"], r["amount"]) for r in interest_logs] == [
        ("u_a", 9),
        ("u_a", 10),
        ("u_b", 1),
    ]
    # 再次结息：存款利息不重复发放，仅剩存量利息 int(1000*0.01)=10
    await module.settle_interest()
    assert await UserConsole.get_gold("u_a") == 100 + 19 + 10
    # 用户 B 存量 int(1*0.0001)=0 → 不发放
    assert await UserConsole.get_gold("u_b") == 100 + 1


# ---------------- 信息数据组装 ----------------


@pytest.mark.asyncio
async def test_user_info_payload(econ_db, module, monkeypatch):
    await UserConsole.add_gold("u1", 100000, "test")
    await _set_impression("u1", 10)
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: 0.001)
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.99)
    await module._do_deposit("u1", 100)
    payload = await module._user_info_payload("u1", "测试员")
    assert payload["name"] == "测试员"
    assert payload["rank"] == 1
    assert payload["amount"] == 100
    assert payload["today_deposit_count"] == 1
    assert payload["today_deposit_amount"] == 100
    assert payload["cumulative_gain"] == 0
    assert len(payload["deposit_list"]) == 1
    dep = payload["deposit_list"][0]
    assert dep["amount"] == 100
    assert dep["rate"] == "0.10"
    # 纯文本降级包含关键字段
    text = bank_render.user_info_text(payload)
    assert "测试员" in text and "No.1" in text


@pytest.mark.asyncio
async def test_bank_info_payload(econ_db, module, monkeypatch):
    await UserConsole.add_gold("u1", 100000, "test")
    await _set_impression("u1", 10)
    monkeypatch.setattr(bank_logic.random, "uniform", lambda a, b: 0.001)
    monkeypatch.setattr(bank_logic.random, "random", lambda: 0.99)
    await module._do_deposit("u1", 100)
    payload = await module._bank_info_payload()
    assert payload["amount_sum"] == 100
    assert payload["user_count"] == 1
    assert payload["today_count"] == 1
    assert payload["active_user_count"] == 1
    assert len(payload["trend"]) == 7
    # 今天（趋势最后一天）存款 100，占比 100%
    assert payload["trend"][-1]["amount"] == 100
    assert payload["trend"][-1]["pct"] == 100
    # 原版 overview.html echarts 折线图字段（MM-DD 升序，今日收尾）
    assert len(payload["e_data"]) == len(payload["e_amount"]) == 7
    assert payload["e_data"][-1] == str(datetime.now().date())[5:]
    assert payload["e_amount"][-1] == 100
    text = bank_render.bank_info_text(payload)
    assert "总存款: 100" in text


# ---------------- cron 注册 ----------------


@pytest.mark.asyncio
async def test_register_cron(econ_db):
    calls = []

    class _Cron:
        async def add_basic_job(self, **kwargs):
            calls.append(kwargs)

    plugin = types.SimpleNamespace(context=types.SimpleNamespace(cron_manager=_Cron()))
    module = BankModule(plugin=plugin, config=dict(DEFAULT_CONFIG))
    await module.register_cron()
    assert len(calls) == 1
    assert calls[0]["name"] == "zhenxun_bank_interest"
    assert calls[0]["cron_expression"] == "0 0 * * *"
    assert calls[0]["timezone"] == "Asia/Shanghai"
    assert calls[0]["handler"] == module.settle_interest
