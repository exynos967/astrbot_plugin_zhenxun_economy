"""core 层单元测试"""

import pytest

from core import (
    GoldHandle,
    GoodsInfo,
    GoodsRegistry,
    InsufficientGold,
    MahiroBank,
    RedbagUser,
    RussianUser,
    SignUser,
    UserConsole,
    goods_register,
    get_level_and_next_impression,
)


@pytest.mark.asyncio
async def test_user_create_and_gold(econ_db):
    user = await UserConsole.get_user("10001", "qq")
    assert user.gold == 100  # 新用户 100 金币
    assert user.uid >= 1

    await UserConsole.add_gold("10001", 50, "sign_in")
    assert await UserConsole.get_gold("10001") == 150

    await UserConsole.reduce_gold("10001", 30, GoldHandle.PLUGIN, "test")
    assert await UserConsole.get_gold("10001") == 120

    with pytest.raises(InsufficientGold):
        await UserConsole.reduce_gold("10001", 99999, GoldHandle.PLUGIN, "test")

    # 流水日志
    rows = await econ_db.fetchall(
        "SELECT * FROM user_gold_log WHERE user_id = ?", ("10001",)
    )
    assert len(rows) == 2
    assert rows[0]["handle"] == "GET" and rows[1]["handle"] == "PLUGIN"


@pytest.mark.asyncio
async def test_props(econ_db):
    uuid = await GoodsInfo.add_goods("测试药水", 100, "测试用")
    assert await GoodsInfo.add_goods("测试药水", 100, "测试用") == uuid  # 幂等

    await UserConsole.add_props("10002", uuid, 3)
    user = await UserConsole.get_user("10002")
    assert user.props[uuid] == 3

    await UserConsole.use_props("10002", uuid, 3)  # 扣到 0 删除 key
    user = await UserConsole.get_user("10002")
    assert uuid not in user.props

    await UserConsole.add_props_by_name("10002", "测试药水", 1)
    await UserConsole.use_props_by_name("10002", "测试药水", 1)


@pytest.mark.asyncio
async def test_goods_register(econ_db):
    called = {}

    @goods_register(name="注册测试卡", price=10, des="测试", 注册测试卡_prob=0.5)
    async def use_func(user_id: str, prob: float):
        called["prob"] = prob
        return "ok"

    await GoodsRegistry.load_register()
    goods = await GoodsInfo.get_by_name("注册测试卡")
    entry = GoodsRegistry.get_entry(goods["uuid"])
    assert entry and entry.kwargs["prob"] == 0.5 and entry.max_num_limit == 1

    await entry.func("u1", **entry.kwargs)
    assert called["prob"] == 0.5


@pytest.mark.asyncio
async def test_sign(econ_db):
    assert not await SignUser.today_signed("20001")
    su = await SignUser.sign("20001", 0.5, platform="qq")
    assert su.sign_count == 1 and abs(su.impression - 0.5) < 1e-6
    assert await SignUser.today_signed("20001")

    await SignUser.set_probability("20001", add_probability=0.3)
    su = await SignUser.get_user("20001")
    assert su.add_probability == 0.3
    su = await SignUser.sign("20001", 0.4)  # 签到后清零
    assert su.add_probability == 0.0 and su.sign_count == 2
    assert abs(await SignUser.get_impression("20001") - 0.9) < 1e-6

    assert get_level_and_next_impression(0)[0] == 0
    assert get_level_and_next_impression(10)[0] == 1
    assert get_level_and_next_impression(400)[0] == 8


@pytest.mark.asyncio
async def test_bank(econ_db):
    acc = await MahiroBank.get_user("30001")
    assert acc.amount == 0 and acc.rate == 0.0005

    await MahiroBank.deposit("30001", 500, 0.001)
    acc = await MahiroBank.get_user("30001")
    assert acc.amount == 500 and acc.rate == 0.001  # 修复了原版 rate 不回写的 bug
    assert await MahiroBank.today_deposit_count("30001") == 1
    assert await MahiroBank.locked_amount("30001") == 500

    with pytest.raises(ValueError):
        await MahiroBank.withdraw("30001", 9999)
    await MahiroBank.withdraw("30001", 100)
    assert (await MahiroBank.get_user("30001")).amount == 400


@pytest.mark.asyncio
async def test_russian(econ_db):
    await RussianUser.add_count("40001", "500", "win")
    await RussianUser.add_count("40001", "500", "win")
    await RussianUser.add_count("40001", "500", "lose")
    rec = await RussianUser.get_user("40001", "500")
    assert rec.win_count == 2 and rec.fail_count == 1
    assert rec.losing_streak == 1 and rec.max_winning_streak == 2

    await RussianUser.add_money("40001", "500", "win", 300)
    assert (await RussianUser.get_user("40001", "500")).make_money == 300
    top = await RussianUser.rank("500", "win_count")
    assert top[0].win_count == 2


@pytest.mark.asyncio
async def test_redbag(econ_db):
    await RedbagUser.add_redbag_data("40002", "500", "send", 100)
    await RedbagUser.add_redbag_data("40003", "500", "get", 60)
    row = await econ_db.fetchone(
        "SELECT * FROM redbag_users WHERE user_id = ? AND group_id = ?", ("40002", "500")
    )
    assert row["send_redbag_count"] == 1 and row["spend_gold"] == 100
