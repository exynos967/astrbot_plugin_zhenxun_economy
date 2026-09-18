"""俄罗斯轮盘装备系统（注册器模式，移植自真寻 russian/equipment.py）

武器效果函数约定：接收 (russian, user_id)，存活返回提示文本或 None，
死亡则抛 PlayerDeathException 由上层触发结算。
本文件不依赖 astrbot，可直接在测试中导入。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game import Russian


class PlayerDeathException(Exception):
    """玩家死亡异常"""

    def __init__(
        self, player_id: str, player_name: str, weapon_name: str = "", message: str = ""
    ):
        self.player_id = player_id
        self.player_name = player_name
        self.weapon_name = weapon_name
        self.message = message or f"玩家 {player_name} 被 {weapon_name} 击中死亡！"
        super().__init__(self.message)


@dataclass
class EquipmentEffect:
    """装备效果配置"""

    name: str  # 效果名称
    description: str  # 效果描述
    effect_func: Callable[["Russian", str], str | None]


@dataclass
class Weapon:
    """左轮枪配置"""

    name: str  # 武器名称
    special_effect: EquipmentEffect
    description: str  # 武器描述


class EquipmentRegistry:
    """装备注册器"""

    def __init__(self) -> None:
        self._weapons: dict[str, Weapon] = {}
        self._default_weapon = "standard"

    def register_weapon(self, weapon_id: str, weapon: Weapon) -> None:
        """注册武器"""
        self._weapons[weapon_id] = weapon

    def get_weapon(self, weapon_id: str) -> Weapon:
        """获取武器配置，未知 id 兜底为标准左轮"""
        return self._weapons.get(weapon_id, self._weapons[self._default_weapon])

    def get_weapons(self) -> dict[str, str]:
        """获取武器列表 {weapon_id: 武器名}"""
        return {weapon_id: weapon.name for weapon_id, weapon in self._weapons.items()}


# 全局装备注册器
equipment_registry = EquipmentRegistry()


def weapon_register(weapon_id: str, name: str, description: str, effect_name: str):
    """武器注册装饰器：将被装饰函数作为武器效果实现并登记武器"""

    def decorator(func: Callable[["Russian", str], str | None]):
        equipment_registry.register_weapon(
            weapon_id,
            Weapon(
                name=name,
                special_effect=EquipmentEffect(
                    name=effect_name, description=description, effect_func=func
                ),
                description=description,
            ),
        )
        return func

    return decorator


@weapon_register(
    "standard", "标准左轮", "平衡的经典左轮手枪，是最初始的左轮手枪", "普通射击"
)
def register_standard_weapon(russian: "Russian", user_id: str):
    """标准左轮：当前弹巢位是子弹则中弹"""
    if russian.bullet_arr[russian.bullet_index] == 1:
        raise PlayerDeathException(
            user_id,
            russian.player1[1],
            "标准左轮",
            "你中弹了！",
        )
    return None


@weapon_register(
    "lucky",
    "幸运左轮",
    "据说能带来好运的左轮手枪，当下一颗弹仓中非空弹时，有10%概率使子弹重新排序",
    "幸运一击",
)
def register_lucky_weapon(russian: "Russian", user_id: str):
    """幸运左轮：中弹时 10% 概率重排剩余子弹，重排后当前位仍是 1 才死"""
    if russian.bullet_arr[russian.bullet_index] == 1:
        if random.random() < 0.1:
            russian.random_bullet()
        if russian.bullet_arr[russian.bullet_index] == 1:
            # 重排后依旧中弹（当前位不参与重排，与原版一致）
            raise PlayerDeathException(
                user_id,
                russian.player1[1],
                "幸运左轮",
                "重新排列子弹后依旧中弹，天命不可违！",
            )
        return "幸运女神在上，成功触发了幸运一击！重新排列了子弹，成功躲避了死亡！"
    return None


@weapon_register(
    "deceiver",
    "欺诈左轮",
    "使用该左轮开枪时，将不再按照子弹排列的顺序开枪，而是完全由概率决定是否命中",
    "欺诈轨迹",
)
def register_deceiver_weapon(russian: "Russian", user_id: str):
    """欺诈左轮：不看弹巢，按概率 (bullet_index + bullet_num + 1) / 7 判定"""
    trigger_chance = (russian.bullet_index + russian.bullet_num + 1) / len(
        russian.bullet_arr
    )
    if random.random() < trigger_chance:
        raise PlayerDeathException(
            user_id,
            russian.player1[1],
            "欺诈左轮",
            "触发了欺诈轨迹！你中弹了！",
        )
    return None


@weapon_register(
    "gambler",
    "赌徒左轮",
    "每次射击后，都会随机打乱子弹排列",
    "混乱",
)
def register_gambler_weapon(russian: "Russian", user_id: str):
    """赌徒左轮：标准判定，存活则重排剩余子弹"""
    if russian.bullet_arr[russian.bullet_index] == 1:
        raise PlayerDeathException(
            user_id,
            russian.player1[1],
            "赌徒左轮",
            "你中弹了！",
        )
    russian.random_bullet()
    return "赌徒左轮触发了混乱！重新排列了剩余子弹！"


def get_weapon(weapon_id: str) -> Weapon:
    """获取武器配置"""
    return equipment_registry.get_weapon(weapon_id)


def get_weapons() -> dict[str, str]:
    """获取武器列表"""
    return equipment_registry.get_weapons()
