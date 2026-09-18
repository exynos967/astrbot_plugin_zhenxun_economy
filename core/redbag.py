"""金币红包统计（对应真寻 RedbagUser，表 redbag_users）"""

from .database import db


class RedbagUser:
    @classmethod
    async def add_redbag_data(
        cls, user_id: str, group_id: str, i_type: str, money: int
    ) -> None:
        """记录红包统计，i_type: get | send"""
        row = await db.fetchone(
            "SELECT id FROM redbag_users WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        if not row:
            await db.execute(
                "INSERT INTO redbag_users (user_id, group_id) VALUES (?, ?)",
                (user_id, group_id),
            )
        if i_type == "get":
            await db.execute(
                "UPDATE redbag_users SET get_redbag_count = get_redbag_count + 1,"
                " get_gold = get_gold + ? WHERE user_id = ? AND group_id = ?",
                (money, user_id, group_id),
            )
        else:
            await db.execute(
                "UPDATE redbag_users SET send_redbag_count = send_redbag_count + 1,"
                " spend_gold = spend_gold + ? WHERE user_id = ? AND group_id = ?",
                (money, user_id, group_id),
            )
