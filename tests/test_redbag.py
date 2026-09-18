"""金币红包模块核心逻辑测试（event 相关逻辑已抽离，直接测纯逻辑/核心方法）"""

import time
import sys
from pathlib import Path

import pytest

import core as _core_pkg

# 模块内部使用 from ...core import ...（插件包内三级相对导入）。
# 测试以命名空间包 astrbot_plugin_zhenxun_economy 导入模块，并把其 core 子包
# 别名到 top-level core（conftest 已加入 sys.path），保证共用同一个 db 实例。
sys.modules.setdefault("astrbot_plugin_zhenxun_economy.core", _core_pkg)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core import UserConsole
from astrbot_plugin_zhenxun_economy.modules.redbag.game import (
    RedBag,
    RedbagModule,
    random_red_bag,
)


def make_module(config: dict | None = None) -> RedbagModule:
    return RedbagModule(None, config or {})


def test_random_red_bag_conservation():
    """分配算法：数量正确、总额守恒、无负数"""
    for amount, num in [(100, 5), (1, 1), (7, 7), (1000, 20), (10, 3), (5, 5)]:
        bags = random_red_bag(amount, num)
        assert len(bags) == num
        assert sum(bags) == amount  # 总额 = amount
        assert min(bags) >= 0


def test_cooldown_remaining():
    """冷却判定：距 start_time 不足 redbag_interval 秒则剩余 > 0"""
    module = make_module({"redbag_interval": 60})
    now = time.time()
    bag = RedBag(amount=100, num=5, promoter="甲", promoter_id="u1", group_id="g1")
    bag.start_time = now - 30
    assert module.cooldown_remaining(bag, now) > 0  # 冷却中，拒绝再发
    bag.start_time = now - 61
    assert module.cooldown_remaining(bag, now) <= 0  # 冷却结束，可再发


def test_is_expired():
    """过期判定：超过 redbag_timeout 秒视为过期"""
    module = make_module({"redbag_timeout": 600})
    now = time.time()
    bag = RedBag(amount=100, num=5, promoter="甲", promoter_id="u1", group_id="g1")
    bag.start_time = now - 599
    assert not module.is_expired(bag, now)
    bag.start_time = now - 601
    assert module.is_expired(bag, now)


@pytest.mark.asyncio
async def test_create_bag_deducts_gold_with_log(econ_db):
    """塞红包：扣金币且写 PLUGIN 流水（修复原版直接改字段无流水）"""
    module = make_module()
    await UserConsole.add_gold("u1", 500, "test")  # 600
    bag = await module._create_bag("g1", "u1", "甲", 200, 5)
    assert await UserConsole.get_gold("u1") == 400
    assert sum(bag.red_bag_list) == 200 and len(bag.red_bag_list) == 5
    rows = await econ_db.fetchall(
        "SELECT * FROM user_gold_log WHERE user_id = ? AND handle = 'PLUGIN'",
        ("u1",),
    )
    assert len(rows) == 1 and rows[0]["source"] == "gold_redbag"
    module._remove_bag("g1", "u1")  # 清理过期定时任务


@pytest.mark.asyncio
async def test_try_open_normal_assigner_and_settle(econ_db):
    """开红包：普通包每人一次、定向包仅指定人、抢完立即结算移除"""
    module = make_module()
    await UserConsole.add_gold("u1", 500, "test")
    await UserConsole.add_gold("u2", 500, "test")
    bag1 = await module._create_bag("g1", "u1", "甲", 100, 2)
    bag2 = await module._create_bag("g1", "u2", "乙", 50, 1, assigner="u9")

    # u8 只能开普通包，定向包不动
    opened, settled = await module._try_open("g1", "u8")
    assert len(opened) == 1 and opened[0][1] is bag1
    assert "u8" in bag1.open_user and not bag2.open_user
    assert not settled
    before = await UserConsole.get_gold("u8")
    # u8 重复开无效（每人一次）
    opened2, _ = await module._try_open("g1", "u8")
    assert not opened2
    assert await UserConsole.get_gold("u8") == before

    # u7 抢到最后一份 → 普通包立即结算移除；定向包仍不可开
    opened3, settled3 = await module._try_open("g1", "u7")
    assert len(opened3) == 1 and opened3[0][1] is bag1
    assert settled3 == [bag1]
    assert "u1" not in module._data["g1"]

    # 指定人开定向包 → 立即结算移除
    opened4, settled4 = await module._try_open("g1", "u9")
    assert len(opened4) == 1 and opened4[0][0] == 50
    assert settled4 == [bag2]
    assert "u2" not in module._data["g1"]
    assert await UserConsole.get_gold("u9") == 100 + 50


@pytest.mark.asyncio
async def test_try_open_skips_expired(econ_db):
    """开红包：已过期的包不可开（等定时任务结算退回）"""
    module = make_module({"redbag_timeout": 600})
    await UserConsole.add_gold("u1", 500, "test")
    bag = await module._create_bag("g1", "u1", "甲", 100, 2)
    bag.start_time = time.time() - 601  # 已过期
    opened, settled = await module._try_open("g1", "u8")
    assert not opened and not settled
    module._remove_bag("g1", "u1")


@pytest.mark.asyncio
async def test_expire_bag_refund_and_idempotent(econ_db):
    """过期/覆盖结算：剩余金额退回发包人且幂等（修复原版覆盖不退）"""
    module = make_module()
    await UserConsole.add_gold("u1", 500, "test")  # 600
    bag = await module._create_bag("g1", "u1", "甲", 100, 3)  # 剩 500
    await module._try_open("g1", "u8")  # 抢走一份
    left = sum(bag.red_bag_list)
    assert left > 0
    before = await UserConsole.get_gold("u1")
    refund = await module._expire_bag("g1", "u1", bag)
    assert refund == left
    assert await UserConsole.get_gold("u1") == before + refund
    # 幂等：重复结算不退第二次
    assert await module._expire_bag("g1", "u1", bag) == -1
    assert await UserConsole.get_gold("u1") == before + refund
    assert "u1" not in module._data.get("g1", {})
