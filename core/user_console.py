"""用户核心：金币与道具总账（对应真寻 UserConsole）。

API 语义与真寻保持一致：
- 新用户初始金币 100
- add_gold / reduce_gold 会写金币流水（user_gold_log）
- 道具存放在 user_console.props JSON 字段中，key 为商品 uuid
"""

import json
from dataclasses import dataclass, field

from .database import db, now_str
from .enums import GoldHandle
from .exceptions import GoodsNotFound, InsufficientGold


@dataclass
class UserRecord:
    id: int
    user_id: str
    uid: int
    gold: int
    props: dict[str, int] = field(default_factory=dict)
    platform: str | None = None
    create_time: str | None = None


def _row2user(row) -> UserRecord:
    props = row["props"]
    if isinstance(props, str):
        props = json.loads(props or "{}")
    return UserRecord(
        id=row["id"],
        user_id=row["user_id"],
        uid=row["uid"],
        gold=row["gold"],
        props=dict(props or {}),
        platform=row["platform"],
        create_time=row["create_time"],
    )


class UserConsole:
    """用户金币/道具核心 API"""

    @classmethod
    async def get_or_create_user(
        cls, user_id: str, platform: str | None = None
    ) -> tuple[UserRecord, bool]:
        row = await db.fetchone(
            "SELECT * FROM user_console WHERE user_id = ?", (user_id,)
        )
        if row:
            return _row2user(row), False
        uid = await cls.get_new_uid()
        await db.execute(
            "INSERT INTO user_console (user_id, uid, platform, create_time)"
            " VALUES (?, ?, ?, ?)",
            (user_id, uid, platform, now_str()),
        )
        row = await db.fetchone(
            "SELECT * FROM user_console WHERE user_id = ?", (user_id,)
        )
        return _row2user(row), True

    @classmethod
    async def get_user(cls, user_id: str, platform: str | None = None) -> UserRecord:
        user, _ = await cls.get_or_create_user(user_id, platform)
        return user

    @classmethod
    async def get_gold(cls, user_id: str) -> int:
        user = await cls.get_user(user_id)
        return user.gold

    @classmethod
    async def get_new_uid(cls) -> int:
        val = await db.fetchval("SELECT MAX(uid) FROM user_console", default=0)
        return int(val) + 1

    @classmethod
    async def _append_gold_log(
        cls, user_id: str, gold: int, handle: GoldHandle, source: str | None
    ) -> None:
        await db.execute(
            "INSERT INTO user_gold_log (user_id, gold, handle, source, create_time)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, gold, str(handle), source, now_str()),
        )

    @classmethod
    async def add_gold(
        cls, user_id: str, gold: int, source: str, platform: str | None = None
    ) -> None:
        """添加金币（原子更新 + GET 流水）"""
        await cls.get_or_create_user(user_id, platform)
        await db.execute(
            "UPDATE user_console SET gold = gold + ? WHERE user_id = ?",
            (gold, user_id),
        )
        await cls._append_gold_log(user_id, gold, GoldHandle.GET, source)

    @classmethod
    async def reduce_gold(
        cls,
        user_id: str,
        gold: int,
        handle: GoldHandle,
        source: str,
        platform: str | None = None,
    ) -> None:
        """扣减金币（条件原子更新，不足抛 InsufficientGold）"""
        await cls.get_or_create_user(user_id, platform)
        cur = await db.execute(
            "UPDATE user_console SET gold = gold - ? WHERE user_id = ? AND gold >= ?",
            (gold, user_id, gold),
        )
        if cur.rowcount == 0:
            raise InsufficientGold()
        await cls._append_gold_log(user_id, gold, handle, source)

    @classmethod
    async def set_gold(cls, user_id: str, gold: int, platform: str | None = None) -> None:
        """直接设置金币（管理用途/兜底），不写流水"""
        await cls.get_or_create_user(user_id, platform)
        await db.execute(
            "UPDATE user_console SET gold = ? WHERE user_id = ?", (gold, user_id)
        )

    # ---------- 道具 ----------

    @classmethod
    async def _save_props(cls, user_id: str, props: dict[str, int]) -> None:
        await db.execute(
            "UPDATE user_console SET props = ? WHERE user_id = ?",
            (json.dumps(props, ensure_ascii=False), user_id),
        )

    @classmethod
    async def add_props(
        cls, user_id: str, goods_uuid: str, num: int = 1, platform: str | None = None
    ) -> None:
        user = await cls.get_user(user_id, platform)
        user.props[goods_uuid] = user.props.get(goods_uuid, 0) + num
        await cls._save_props(user_id, user.props)

    @classmethod
    async def use_props(
        cls, user_id: str, goods_uuid: str, num: int = 1, platform: str | None = None
    ) -> None:
        user = await cls.get_user(user_id, platform)
        if user.props.get(goods_uuid, 0) < num:
            raise GoodsNotFound("未找到商品或道具数量不足...")
        user.props[goods_uuid] -= num
        if user.props[goods_uuid] <= 0:
            del user.props[goods_uuid]
        await cls._save_props(user_id, user.props)

    @classmethod
    async def add_props_by_name(
        cls, user_id: str, name: str, num: int = 1, platform: str | None = None
    ) -> None:
        from .goods import GoodsInfo

        goods = await GoodsInfo.get_by_name(name)
        if not goods:
            raise GoodsNotFound("未找到商品...")
        await cls.add_props(user_id, goods["uuid"], num, platform)

    @classmethod
    async def use_props_by_name(
        cls, user_id: str, name: str, num: int = 1, platform: str | None = None
    ) -> None:
        from .goods import GoodsInfo

        goods = await GoodsInfo.get_by_name(name)
        if not goods:
            raise GoodsNotFound("未找到商品...")
        await cls.use_props(user_id, goods["uuid"], num, platform)
