"""签到与好感度（对应真寻 SignUser / SignLog + 等级映射）。

- impression 好感度仅通过 SignUser.sign() 增减
- 等级映射与真寻完全一致
"""

from dataclasses import dataclass

from .database import db, now_str
from .user_console import UserConsole

# 好感度 -> 等级 阈值（达到该值即升入对应等级）
lik2level = {400: 8, 270: 7, 200: 6, 140: 5, 90: 4, 50: 3, 25: 2, 10: 1, 0: 0}
lik2relation = {
    "0": "路人", "1": "陌生", "2": "初识", "3": "普通",
    "4": "熟悉", "5": "信赖", "6": "相知", "7": "厚谊", "8": "亲密",
}
level2attitude = {
    "0": "排斥", "1": "警惕", "2": "可以交流", "3": "一般",
    "4": "是个好人", "5": "好朋友", "6": "可以分享小秘密", "7": "喜欢", "8": "恋人",
}


def get_level_and_next_impression(impression: float) -> tuple[int, float, float]:
    """返回 (当前等级, 下一级所需好感度, 当前等级起始好感度)"""
    thresholds = sorted(lik2level.keys(), reverse=True)
    for i, threshold in enumerate(thresholds):
        if impression >= threshold:
            level = lik2level[threshold]
            next_imp = thresholds[i - 1] if i > 0 else threshold
            return level, float(next_imp), float(threshold)
    return 0, 10.0, 0.0


@dataclass
class SignUserRecord:
    id: int
    user_id: str
    sign_count: int
    impression: float
    add_probability: float
    specify_probability: float
    platform: str | None = None


def _row2sign(row) -> SignUserRecord:
    return SignUserRecord(
        id=row["id"],
        user_id=row["user_id"],
        sign_count=row["sign_count"],
        impression=float(row["impression"]),
        add_probability=float(row["add_probability"]),
        specify_probability=float(row["specify_probability"]),
        platform=row["platform"],
    )


class SignUser:
    @classmethod
    async def get_user(cls, user_id: str, platform: str | None = None) -> SignUserRecord:
        row = await db.fetchone("SELECT * FROM sign_users WHERE user_id = ?", (user_id,))
        if row:
            return _row2sign(row)
        console = await UserConsole.get_user(user_id, platform)
        await db.execute(
            "INSERT INTO sign_users (user_id, user_console_id, platform)"
            " VALUES (?, ?, ?)",
            (user_id, console.id, platform),
        )
        row = await db.fetchone("SELECT * FROM sign_users WHERE user_id = ?", (user_id,))
        return _row2sign(row)

    @classmethod
    async def sign(
        cls,
        user_id: str,
        impression: float,
        bot_id: str | None = None,
        platform: str | None = None,
    ) -> SignUserRecord:
        """签到落库：好感度 += impression，双倍概率清零，次数 +1，写签到记录"""
        user = await cls.get_user(user_id, platform)
        new_impression = round(user.impression + impression, 3)
        await db.execute(
            "UPDATE sign_users SET impression = ?, add_probability = 0,"
            " specify_probability = 0, sign_count = sign_count + 1 WHERE user_id = ?",
            (new_impression, user_id),
        )
        await db.execute(
            "INSERT INTO sign_log (user_id, impression, create_time, bot_id, platform)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, round(impression, 3), now_str(), bot_id, platform),
        )
        return await cls.get_user(user_id, platform)

    @classmethod
    async def set_probability(
        cls, user_id: str, add_probability: float | None = None,
        specify_probability: float | None = None,
    ) -> None:
        """设置双倍概率（好感度加持卡效果）"""
        await cls.get_user(user_id)
        if add_probability is not None:
            await db.execute(
                "UPDATE sign_users SET add_probability = ? WHERE user_id = ?",
                (add_probability, user_id),
            )
        if specify_probability is not None:
            await db.execute(
                "UPDATE sign_users SET specify_probability = ? WHERE user_id = ?",
                (specify_probability, user_id),
            )

    @classmethod
    async def get_impression(cls, user_id: str) -> float:
        user = await cls.get_user(user_id)
        return user.impression

    @classmethod
    async def today_signed(cls, user_id: str) -> bool:
        """今天是否已签到（按最新一条签到记录判断，与真寻逻辑一致）"""
        row = await db.fetchone(
            "SELECT create_time FROM sign_log WHERE user_id = ?"
            " ORDER BY create_time DESC LIMIT 1",
            (user_id,),
        )
        if not row:
            return False
        return str(row["create_time"])[:10] == now_str()[:10]

    @classmethod
    async def rank_by_impression(cls, limit: int = 10, user_ids: list[str] | None = None) -> list[SignUserRecord]:
        """好感度排行（可选限定群成员范围）"""
        if user_ids is not None:
            if not user_ids:
                return []
            placeholders = ",".join("?" * len(user_ids))
            rows = await db.fetchall(
                f"SELECT * FROM sign_users WHERE user_id IN ({placeholders})"
                " ORDER BY impression DESC LIMIT ?",
                (*user_ids, limit),
            )
        else:
            rows = await db.fetchall(
                "SELECT * FROM sign_users ORDER BY impression DESC LIMIT ?", (limit,)
            )
        return [_row2sign(r) for r in rows]
