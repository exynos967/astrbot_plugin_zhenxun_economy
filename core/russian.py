"""俄罗斯轮盘战绩（对应真寻 RussianUser，表 russian_users）"""

from dataclasses import dataclass

from .database import db


@dataclass
class RussianRecord:
    id: int
    user_id: str
    group_id: str
    win_count: int = 0
    fail_count: int = 0
    make_money: int = 0
    lose_money: int = 0
    winning_streak: int = 0
    losing_streak: int = 0
    max_winning_streak: int = 0
    max_losing_streak: int = 0


def _row2rec(row) -> RussianRecord:
    return RussianRecord(**{k: row[k] for k in RussianRecord.__dataclass_fields__})


class RussianUser:
    @classmethod
    async def get_user(cls, user_id: str, group_id: str) -> RussianRecord:
        row = await db.fetchone(
            "SELECT * FROM russian_users WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        if row:
            return _row2rec(row)
        await db.execute(
            "INSERT INTO russian_users (user_id, group_id) VALUES (?, ?)",
            (user_id, group_id),
        )
        row = await db.fetchone(
            "SELECT * FROM russian_users WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        return _row2rec(row)

    @classmethod
    async def add_count(cls, user_id: str, group_id: str, type_: str) -> None:
        """更新胜/负场次与连胜连败，type_: win | lose"""
        rec = await cls.get_user(user_id, group_id)
        if type_ == "win":
            ws = rec.winning_streak + 1
            await db.execute(
                "UPDATE russian_users SET win_count = win_count + 1,"
                " winning_streak = ?, losing_streak = 0,"
                " max_winning_streak = MAX(max_winning_streak, ?)"
                " WHERE user_id = ? AND group_id = ?",
                (ws, ws, user_id, group_id),
            )
        else:
            ls = rec.losing_streak + 1
            await db.execute(
                "UPDATE russian_users SET fail_count = fail_count + 1,"
                " losing_streak = ?, winning_streak = 0,"
                " max_losing_streak = MAX(max_losing_streak, ?)"
                " WHERE user_id = ? AND group_id = ?",
                (ls, ls, user_id, group_id),
            )

    @classmethod
    async def add_money(cls, user_id: str, group_id: str, type_: str, count: int) -> None:
        col = "make_money" if type_ == "win" else "lose_money"
        await cls.get_user(user_id, group_id)
        await db.execute(
            f"UPDATE russian_users SET {col} = {col} + ? WHERE user_id = ? AND group_id = ?",
            (count, user_id, group_id),
        )

    @classmethod
    async def rank(cls, group_id: str, field: str, limit: int = 10) -> list[RussianRecord]:
        """排行（降序）。field: win_count|fail_count|make_money|lose_money|max_winning_streak|max_losing_streak"""
        assert field in (
            "win_count", "fail_count", "make_money", "lose_money",
            "max_winning_streak", "max_losing_streak",
        )
        rows = await db.fetchall(
            f"SELECT * FROM russian_users WHERE group_id = ? AND {field} != 0"
            f" ORDER BY {field} DESC LIMIT ?",
            (group_id, limit),
        )
        return [_row2rec(r) for r in rows]
