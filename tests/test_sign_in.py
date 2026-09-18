"""签到模块单元测试（渲染与 event 相关不测）"""

import pytest

from core import SignUser, UserConsole
from modules.sign_in.logic import (
    CARD1,
    CARD2,
    CARD3,
    calc_impression_gain,
    do_sign,
    random_event,
)


# ---------- 好感度增量 ----------


def test_impression_gain_range():
    """好感度基础增量 0.01~0.99，双倍后 0.02~1.98"""
    for _ in range(500):
        gain, is_double = calc_impression_gain()
        if is_double:
            assert 0.02 <= gain <= 1.98
        else:
            assert 0.01 <= gain <= 0.99


def test_impression_gain_double_by_add_probability():
    """rand + add_probability > 0.97 触发双倍（加持卡加成）"""
    gain, is_double = calc_impression_gain(add_probability=0.1, rand=0.9, base=0.5)
    assert is_double and gain == 1.0
    # 边界：rand + prob == 0.97 不触发（原版为严格大于）
    gain, is_double = calc_impression_gain(add_probability=0.07, rand=0.9, base=0.5)
    assert not is_double and gain == 0.5
    gain, is_double = calc_impression_gain(add_probability=0.1, rand=0.5, base=0.5)
    assert not is_double and gain == 0.5


def test_impression_gain_double_by_specify_probability():
    """rand < specify_probability 触发双倍"""
    gain, is_double = calc_impression_gain(specify_probability=0.3, rand=0.2, base=0.5)
    assert is_double and gain == 1.0
    gain, is_double = calc_impression_gain(specify_probability=0.3, rand=0.5, base=0.5)
    assert not is_double and gain == 0.5


# ---------- 随机事件 ----------


def test_random_event_card_drop_order():
    """按 Ⅲ→Ⅱ→Ⅰ（原版字典序/概率升序）判定掉卡"""
    assert random_event(0, rand=0.04) == CARD3
    assert random_event(0, rand=0.07) == CARD2
    assert random_event(0, rand=0.15) == CARD1
    # rand 超过所有概率 → 返回额外金币 int
    assert isinstance(random_event(0, rand=0.99), int)
    # 好感度偏移使 rand 变负 → 必掉最稀有卡Ⅲ
    assert random_event(2000, rand=0.999) == CARD3


def test_random_event_gold_cap():
    """额外金币封顶 max_sign_gold，下限为 1"""
    for _ in range(300):
        gold = random_event(500, max_sign_gold=200, rand=0.999)
        assert isinstance(gold, int)
        assert 1 <= gold <= 200
    # 好感度 < 1 时额外金币恒为 1
    assert random_event(0.5, rand=0.999) == 1


# ---------- 已签到判定与完整签到流程 ----------


@pytest.mark.asyncio
async def test_today_signed(econ_db):
    uid = "sign_test_1"
    assert not await SignUser.today_signed(uid)
    await SignUser.sign(uid, 0.5)
    assert await SignUser.today_signed(uid)


@pytest.mark.asyncio
async def test_do_sign_flow(econ_db):
    uid = "sign_test_2"
    result = await do_sign(uid, {})
    # 好感度增量范围（含双倍）
    assert 0.01 <= result.impression_added <= 1.98
    # 落库：好感度、次数、双倍概率清零
    assert result.user.sign_count == 1
    assert result.user.impression == pytest.approx(result.impression_added, abs=1e-3)
    assert result.user.add_probability == 0
    assert result.user.specify_probability == 0
    # 金币入账（新用户 100 初始金币 + 签到金币）
    assert result.gold >= 1
    assert await UserConsole.get_gold(uid) == 100 + result.gold
    # 签到后今日已签到
    assert await SignUser.today_signed(uid)


@pytest.mark.asyncio
async def test_do_sign_clears_probability(econ_db):
    """签到后加持卡概率清零（卡片效果一次性生效）"""
    uid = "sign_test_3"
    await SignUser.set_probability(uid, add_probability=0.3, specify_probability=0.3)
    result = await do_sign(uid, {})
    assert result.user.add_probability == 0
    assert result.user.specify_probability == 0
