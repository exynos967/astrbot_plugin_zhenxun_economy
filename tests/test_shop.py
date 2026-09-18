"""商店模块 logic 层单元测试（event 相关已抽离，直接测业务方法）"""

import json

import pytest
import pytest_asyncio

from core import (
    GoodsInfo,
    GoodsRegistry,
    NotMeetUseConditionsException,
    SignUser,
    UserConsole,
    goods_register,
    goods_register_before,
)
from modules.shop import default_goods  # noqa: F401  # import 即注册默认商品
from modules.shop.logic import ShopModule

USER = "90001"


@pytest_asyncio.fixture
async def shop(econ_db):
    """每个测试用例一个全新数据库 + 完成商品注册的 ShopModule"""
    await GoodsRegistry.load_register()
    return ShopModule(plugin=None, config={})


# ---------------- 购买流程 ----------------


@pytest.mark.asyncio
async def test_buy_by_name(econ_db, shop):
    result = await shop.buy_prop(USER, "好感度双倍加持卡Ⅰ", 2)
    assert result == "花费 60 金币购买 好感度双倍加持卡Ⅰ ×2 成功！"
    assert await UserConsole.get_gold(USER) == 40  # 100 - 30*2
    goods = await GoodsInfo.get_by_name("好感度双倍加持卡Ⅰ")
    user = await UserConsole.get_user(USER)
    assert user.props[goods["uuid"]] == 2
    # 道具流水
    rows = await econ_db.fetchall(
        "SELECT * FROM user_props_log WHERE user_id = ?", (USER,)
    )
    assert len(rows) == 1
    assert rows[0]["handle"] == "BUY" and rows[0]["num"] == 2
    assert rows[0]["gold"] == 60 and rows[0]["uuid"] == goods["uuid"]
    # 金币流水
    gold_rows = await econ_db.fetchall(
        "SELECT * FROM user_gold_log WHERE user_id = ?", (USER,)
    )
    assert len(gold_rows) == 1 and gold_rows[0]["handle"] == "BUY"
    assert gold_rows[0]["source"] == "shop"


@pytest.mark.asyncio
async def test_buy_by_index(econ_db, shop):
    """纯数字按在售列表序号(1起)解析：1 = 好感度双倍加持卡Ⅰ"""
    result = await shop.buy_prop(USER, "1")
    assert "好感度双倍加持卡Ⅰ" in result
    assert await UserConsole.get_gold(USER) == 70

    assert await shop.buy_prop(USER, "99") == "道具编号不存在..."
    assert await shop.buy_prop(USER, "不存在的道具") == "道具名称不存在..."


@pytest.mark.asyncio
async def test_buy_index_zero(econ_db, shop):
    """序号 0 越界（修复真寻 num 校验之外的序号边界）"""
    assert await shop.buy_prop(USER, "0") == "道具编号不存在..."


@pytest.mark.asyncio
async def test_buy_num_zero_and_negative(econ_db, shop):
    """num<=0 拒绝（修复真寻 num=0 未拦截的 bug）"""
    assert await shop.buy_prop(USER, "好感度双倍加持卡Ⅰ", 0) == "购买的数量要大于0!"
    assert await shop.buy_prop(USER, "好感度双倍加持卡Ⅰ", -2) == "购买的数量要大于0!"
    assert await UserConsole.get_gold(USER) == 100  # 金币未变动
    rows = await econ_db.fetchall(
        "SELECT * FROM user_props_log WHERE user_id = ?", (USER,)
    )
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_buy_insufficient_gold(econ_db, shop):
    result = await shop.buy_prop(USER, "神秘药水")
    assert result == "糟糕! 您的金币好像不太够哦..."
    assert await UserConsole.get_gold(USER) == 100


@pytest.mark.asyncio
async def test_buy_daily_limit(econ_db, shop):
    """每日限购：daily_limit=1 的商品第二次购买被拒"""

    @goods_register(name="限购测试卡", price=10, des="限购测试", daily_limit=1)
    async def _use(user_id: str):
        return None

    await GoodsRegistry.load_register()
    assert "成功" in await shop.buy_prop(USER, "限购测试卡")
    assert await shop.buy_prop(USER, "限购测试卡") == "今天的购买已达限制了喔!"


# ---------------- 使用流程 ----------------


@pytest.mark.asyncio
async def test_use_card_sets_probability(econ_db, shop):
    """加持卡：扣道具 + SignUser.set_probability 生效 + 默认成功文案"""
    await UserConsole.add_props_by_name(USER, "好感度双倍加持卡Ⅱ", 1)
    result = await shop.use_prop(USER, "好感度双倍加持卡Ⅱ")
    assert result == "使用道具 好感度双倍加持卡Ⅱ 1 次成功！"
    su = await SignUser.get_user(USER)
    assert abs(su.add_probability - 0.2) < 1e-6
    # 道具已扣除
    goods = await GoodsInfo.get_by_name("好感度双倍加持卡Ⅱ")
    user = await UserConsole.get_user(USER)
    assert goods["uuid"] not in user.props


@pytest.mark.asyncio
async def test_use_mysterious_potion(econ_db, shop):
    await UserConsole.add_props_by_name(USER, "神秘药水", 1)
    result = await shop.use_prop(USER, "神秘药水")
    assert result == "使用道具神秘药水成功！你滴金币+1000000！"
    assert await UserConsole.get_gold(USER) == 100 + 1000000


@pytest.mark.asyncio
async def test_use_by_index_and_clean_dirty(econ_db, shop):
    """纯数字按背包序号(1起)；先清理 count<=0 / 商品已删除的脏数据"""
    await UserConsole.add_props_by_name(USER, "好感度双倍加持卡Ⅰ", 1)
    # 注入脏数据：不存在的 uuid + count<=0
    goods = await GoodsInfo.get_by_name("好感度双倍加持卡Ⅰ")
    user = await UserConsole.get_user(USER)
    dirty = {"bogus-uuid": 5, goods["uuid"]: 1, "another-bad": 0}
    await econ_db.execute(
        "UPDATE user_console SET props = ? WHERE user_id = ?",
        (json.dumps(dirty), USER),
    )
    # 清理后背包只剩 1 个道具，序号 1 = 加持卡Ⅰ
    result = await shop.use_prop(USER, "1")
    assert result == "使用道具 好感度双倍加持卡Ⅰ 1 次成功！"
    user = await UserConsole.get_user(USER)
    assert "bogus-uuid" not in user.props and "another-bad" not in user.props
    # 序号越界
    assert await shop.use_prop(USER, "5") == "仓库中道具不存在..."


@pytest.mark.asyncio
async def test_use_before_block(econ_db, shop):
    """before_handle 抛 NotMeetUseConditionsException：回复 info 且不扣道具"""

    @goods_register(name="阻断测试卡", price=10, des="阻断测试")
    async def _use(user_id: str):
        return None

    @goods_register_before("阻断测试卡")
    async def _before(user_id: str):
        raise NotMeetUseConditionsException("条件不满足捏...")

    await GoodsRegistry.load_register()
    await UserConsole.add_props_by_name(USER, "阻断测试卡", 1)
    result = await shop.use_prop(USER, "阻断测试卡")
    assert result == "条件不满足捏..."
    goods = await GoodsInfo.get_by_name("阻断测试卡")
    user = await UserConsole.get_user(USER)
    assert user.props[goods["uuid"]] == 1  # 道具未扣


@pytest.mark.asyncio
async def test_use_max_num_limit(econ_db, shop):
    @goods_register(name="限量使用卡", price=10, des="限量测试", max_num_limit=2)
    async def _use(user_id: str, num: int):
        return None

    await GoodsRegistry.load_register()
    await UserConsole.add_props_by_name(USER, "限量使用卡", 5)
    assert (
        await shop.use_prop(USER, "限量使用卡", 3)
        == "限量使用卡 单次使用最大数量为2..."
    )
    # num=2 放行且按数量扣减
    assert await shop.use_prop(USER, "限量使用卡", 2) == "使用道具 限量使用卡 2 次成功！"
    user = await UserConsole.get_user(USER)
    goods = await GoodsInfo.get_by_name("限量使用卡")
    assert user.props[goods["uuid"]] == 3


@pytest.mark.asyncio
async def test_use_not_owned_and_passive(econ_db, shop):
    # 未持有道具
    assert (
        await shop.use_prop("90002", "好感度双倍加持卡Ⅰ")
        == "没有找到道具 好感度双倍加持卡Ⅰ 或道具数量不足..."
    )
    # 被动道具拒绝使用

    @goods_register(name="被动测试卡", price=10, des="被动测试", is_passive=True)
    async def _use(user_id: str):
        return None

    await GoodsRegistry.load_register()
    assert (
        await shop.use_prop(USER, "被动测试卡") == "被动测试卡 是被动道具, 无法使用..."
    )
    # 不存在的道具
    assert await shop.use_prop(USER, "不存在的东西") == "对应的道具不存在..."


# ---------------- 背包与排行 ----------------


@pytest.mark.asyncio
async def test_my_props_data(econ_db, shop):
    assert await shop.get_my_props_data(USER, "测试君") is None  # 空背包
    await UserConsole.add_props_by_name(USER, "好感度双倍加持卡Ⅰ", 3)
    data = await shop.get_my_props_data(USER, "测试君")
    assert data["user_name"] == "测试君"
    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["id"] == 1 and row["name"] == "好感度双倍加持卡Ⅰ" and row["count"] == 3
    assert row["icon"].startswith("data:image/png;base64,")  # 图标已内联


@pytest.mark.asyncio
async def test_query_gold_rank(econ_db, shop):
    await UserConsole.add_gold("u_a", 500, "test")
    await UserConsole.add_gold("u_b", 300, "test")
    await UserConsole.get_user("u_c")  # 100 金币
    rows, index = await shop.query_gold_rank("u_b", 10)
    assert [r[0] for r in rows] == ["u_a", "u_b", "u_c"]
    assert index == "2"
    # 群内过滤
    rows, index = await shop.query_gold_rank("u_c", 10, user_ids=["u_b", "u_c"])
    assert [r[0] for r in rows] == ["u_b", "u_c"]
    assert index == "2"
    # 未上榜
    _, index = await shop.query_gold_rank("u_x", 10, user_ids=["u_b"])
    assert index == "-1（未统计）"
    # num 截断
    rows, _ = await shop.query_gold_rank("u_a", 1)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_shop_data(econ_db, shop):
    data = await shop.get_shop_data()
    assert data["bot_nickname"] == "真寻"
    partitions = {c["partition_title"]: c["goods_list"] for c in data["categories"]}
    assert "默认分区" in partitions and "小秘密" in partitions
    default_names = [g["name"] for g in partitions["默认分区"]]
    assert "好感度双倍加持卡Ⅰ" in default_names
    potion = partitions["小秘密"][0]
    assert potion["name"] == "神秘药水" and potion["price"] == 999999
    assert potion["daily_limit"] == "∞"
    assert potion["icon_url"].startswith("data:image/png;base64,")
    # 序号全局唯一且覆盖 1..N（按分区分组展示时跨分区不连续，与真寻一致）
    all_ids = [g["id"] for c in data["categories"] for g in c["goods_list"]]
    assert sorted(all_ids) == list(range(1, len(all_ids) + 1))
