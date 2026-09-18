"""俄罗斯轮盘模块核心逻辑测试（event 相关逻辑已抽离为消息段列表，直接测纯逻辑）"""

import random
import sys
from pathlib import Path

import pytest

import core as _core_pkg

# 模块内部使用 from ...core import ...（插件包内三级相对导入）。
# 测试以命名空间包 astrbot_plugin_zhenxun_economy 导入模块，并把其 core 子包
# 别名到 top-level core（conftest 已加入 sys.path），保证共用同一个 db 实例。
sys.modules.setdefault("astrbot_plugin_zhenxun_economy.core", _core_pkg)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core import RussianUser, UserConsole
from astrbot_plugin_zhenxun_economy.modules.russian.equipment import (
    PlayerDeathException,
    get_weapon,
    get_weapons,
)
from astrbot_plugin_zhenxun_economy.modules.russian.game import (
    Russian,
    RussianModule,
    build_bullet_arr,
    compute_fee,
)


def make_module(config: dict | None = None) -> RussianModule:
    return RussianModule(None, config or {})


def _rus_with(arr: list[int], index: int = 0, num: int | None = None) -> Russian:
    rus = Russian(
        player1=("u1", "甲"),
        money=200,
        bullet_num=num if num is not None else sum(arr),
    )
    rus.bullet_arr = list(arr)
    rus.bullet_index = index
    return rus


def test_build_bullet_arr():
    """弹巢生成：7 格、num 个子弹、值域 {0,1}"""
    for num in range(1, 7):
        arr = build_bullet_arr(num)
        assert len(arr) == 7
        assert sum(arr) == num
        assert set(arr) <= {0, 1}


def test_random_bullet_reshuffle():
    """重排剩余子弹：当前位及之前不动，子弹总数守恒"""
    rus = _rus_with([1, 0, 1, 0, 0, 0, 0], index=0)
    rus.random_bullet()
    assert rus.bullet_arr[:1] == [1]
    assert sum(rus.bullet_arr) == 2
    assert len(rus.bullet_arr) == 7


def test_weapon_registry():
    """注册器：四把武器齐全，未知 id 兜底标准左轮"""
    assert set(get_weapons()) == {"standard", "lucky", "deceiver", "gambler"}
    assert get_weapon("不存在的武器").name == "标准左轮"


def test_standard_weapon():
    """标准左轮：当前位是子弹则死亡"""
    effect = get_weapon("standard").special_effect.effect_func
    with pytest.raises(PlayerDeathException):
        effect(_rus_with([1, 0, 0, 0, 0, 0, 0]), "u1")
    assert effect(_rus_with([0, 1, 0, 0, 0, 0, 0]), "u1") is None


def test_lucky_weapon():
    """幸运左轮：当前位有子弹时重排不涉及当前位，仍中弹死亡（与原版一致）"""
    effect = get_weapon("lucky").special_effect.effect_func
    with pytest.raises(PlayerDeathException) as exc:
        effect(_rus_with([1, 0, 0, 0, 0, 0, 0]), "u1")
    assert "天命不可违" in exc.value.message
    assert effect(_rus_with([0, 1, 0, 0, 0, 0, 0]), "u1") is None


def test_deceiver_weapon(monkeypatch):
    """欺诈左轮：不看弹巢，按 (index + num + 1) / 7 概率判定"""
    effect = get_weapon("deceiver").special_effect.effect_func
    # (0+6+1)/7 = 1.0 → 必死（即使当前位为空）
    monkeypatch.setattr(random, "random", lambda: 0.999)
    with pytest.raises(PlayerDeathException):
        effect(_rus_with([0] * 7, index=0, num=6), "u1")
    # (0+1+1)/7 ≈ 0.286 → random 0.9 存活（即使弹巢满子弹）
    monkeypatch.setattr(random, "random", lambda: 0.9)
    assert effect(_rus_with([1] * 7, index=0, num=1), "u1") is None
    # random 0.1 < 0.286 → 死亡（即使当前位为空）
    monkeypatch.setattr(random, "random", lambda: 0.1)
    with pytest.raises(PlayerDeathException):
        effect(_rus_with([0] * 7, index=0, num=1), "u1")


def test_gambler_weapon():
    """赌徒左轮：标准判定，存活后重排剩余子弹"""
    effect = get_weapon("gambler").special_effect.effect_func
    with pytest.raises(PlayerDeathException):
        effect(_rus_with([1, 0, 0, 0, 0, 0, 0]), "u1")
    rus = _rus_with([0, 1, 1, 0, 0, 0, 0])
    msg = effect(rus, "u1")
    assert "混乱" in msg
    assert rus.bullet_arr[:1] == [0]  # 当前位不动
    assert sum(rus.bullet_arr) == 2  # 子弹守恒


def test_compute_fee():
    """手续费：<=10 免手续费；rand% 抽成；rand!=0 时保底 1 金币"""
    assert compute_fee(10, 5) == 0
    assert compute_fee(200, 0) == 0
    assert compute_fee(200, 5) == 10
    assert compute_fee(50, 1) == 1  # int(0.5)=0 且 rand!=0 → 保底 1
    assert compute_fee(1000, 3) == 30


@pytest.mark.asyncio
async def test_settle_player1_win(econ_db, monkeypatch):
    """结算：next_user 不是 player1 → player1 胜；手续费/战绩/金币划转正确"""
    monkeypatch.setattr(random, "randint", lambda a, b: 5)  # 手续费 5%
    module = make_module()
    await UserConsole.add_gold("u1", 500, "test")  # 600
    await UserConsole.add_gold("u2", 500, "test")  # 600
    game = Russian(
        player1=("u1", "甲"), player2=("u2", "乙"), money=200, bullet_num=1
    )
    game.next_user = "u2"  # 轮到 u2 时中弹 → u1 胜
    module._games["g1"] = game
    segs = await module._settle("g1", None)
    assert segs is not None and ("at", "u1") in segs
    assert "g1" not in module._games  # 结算后删局
    assert await UserConsole.get_gold("u1") == 600 + 190  # 200 - 10 手续费
    assert await UserConsole.get_gold("u2") == 600 - 200
    r1 = await RussianUser.get_user("u1", "g1")
    r2 = await RussianUser.get_user("u2", "g1")
    assert r1.win_count == 1 and r1.make_money == 190
    assert r2.fail_count == 1 and r2.lose_money == 200


@pytest.mark.asyncio
async def test_settle_player2_win_and_insufficient_gold(econ_db, monkeypatch):
    """结算：next_user 是 player1 → player2 胜；败者金币不足兜底清零"""
    monkeypatch.setattr(random, "randint", lambda a, b: 0)  # 免手续费
    module = make_module()
    # u3 只有初始 100 金币，赌注 200 → 扣款抛 InsufficientGold → 清零
    game = Russian(
        player1=("u3", "丙"), player2=("u4", "丁"), money=200, bullet_num=1
    )
    game.next_user = "u3"  # 轮到 u3 时中弹 → u4 胜
    module._games["g2"] = game
    await module._settle("g2", None)
    assert await UserConsole.get_gold("u3") == 0
    assert await UserConsole.get_gold("u4") == 100 + 200
    r4 = await RussianUser.get_user("u4", "g2")
    assert r4.win_count == 1 and r4.max_winning_streak == 1


@pytest.mark.asyncio
async def test_settle_no_player2_expired(econ_db):
    """结算：超时且无 player2 → 决斗过期删局"""
    module = make_module()
    module._games["g3"] = Russian(player1=("u5", "戊"), money=200, bullet_num=1)
    segs = await module._settle("g3", None)
    assert segs is not None and "过期" in segs[0][1]
    assert "g3" not in module._games


@pytest.mark.asyncio
async def test_settle_no_game(econ_db):
    """结算：没有对局返回 None"""
    module = make_module()
    assert await module._settle("不存在的群", None) is None


@pytest.mark.asyncio
async def test_settle_onlooker_rejected(econ_db):
    """结算：非玩家手动结算被拒"""
    module = make_module()
    game = Russian(
        player1=("u1", "甲"), player2=("u2", "乙"), money=200, bullet_num=1
    )
    module._games["g4"] = game
    segs = await module._settle("g4", "路人")
    assert segs is not None and "吃瓜群众" in segs[0][1]
    assert "g4" in module._games  # 对局仍在
    module._cancel_timer("g4")
