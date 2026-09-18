"""小真寻银行（对应真寻 MahiroBank / MahiroBankLog）。

注意：金币划转不在本层做，由业务层调用 UserConsole.add_gold/reduce_gold。
贷款/还款保留模型层（与真寻一致），但命令层不开放。
"""

from dataclasses import dataclass

from .database import db, now_str
from .enums import BankHandleType


@dataclass
class BankRecord:
    id: int
    user_id: str
    amount: int
    rate: float
    loan_amount: int
    loan_rate: float


def _row2bank(row) -> BankRecord:
    return BankRecord(
        id=row["id"], user_id=row["user_id"], amount=row["amount"],
        rate=row["rate"], loan_amount=row["loan_amount"], loan_rate=row["loan_rate"],
    )


class MahiroBank:
    @classmethod
    async def get_user(cls, user_id: str) -> BankRecord:
        row = await db.fetchone("SELECT * FROM mahiro_bank WHERE user_id = ?", (user_id,))
        if row:
            return _row2bank(row)
        await db.execute("INSERT INTO mahiro_bank (user_id) VALUES (?)", (user_id,))
        row = await db.fetchone("SELECT * FROM mahiro_bank WHERE user_id = ?", (user_id,))
        return _row2bank(row)

    @classmethod
    async def _append_log(
        cls, user_id: str, amount: int, rate: float,
        handle_type: BankHandleType, effective_hour: int = 0,
    ) -> None:
        await db.execute(
            "INSERT INTO mahiro_bank_log (user_id, amount, rate, handle_type,"
            " is_completed, effective_hour, update_time, create_time)"
            " VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
            (user_id, amount, rate, str(handle_type), effective_hour, now_str(), now_str()),
        )

    @classmethod
    async def deposit(cls, user_id: str, amount: int, rate: float) -> None:
        """存款：累加存款并回写账户利率，写 DEPOSIT 日志（effective_hour = 当日剩余小时）"""
        user = await cls.get_user(user_id)
        effective_hour = 24 - int(now_str()[11:13])
        await db.execute(
            "UPDATE mahiro_bank SET amount = amount + ?, rate = ?, update_time = ?"
            " WHERE user_id = ?",
            (amount, rate, now_str(), user_id),
        )
        await cls._append_log(user_id, amount, rate, BankHandleType.DEPOSIT, effective_hour)

    @classmethod
    async def withdraw(cls, user_id: str, amount: int) -> None:
        user = await cls.get_user(user_id)
        if amount <= 0 or amount > user.amount:
            raise ValueError("存款数量不足哦...")
        await db.execute(
            "UPDATE mahiro_bank SET amount = amount - ?, update_time = ? WHERE user_id = ?",
            (amount, now_str(), user_id),
        )
        await cls._append_log(user_id, amount, 0, BankHandleType.WITHDRAW)

    @classmethod
    async def today_deposit_count(cls, user_id: str) -> int:
        return await db.fetchval(
            "SELECT COUNT(*) FROM mahiro_bank_log WHERE user_id = ?"
            " AND handle_type = ? AND is_completed = 0"
            " AND date(create_time) = date('now', 'localtime')",
            (user_id, str(BankHandleType.DEPOSIT)),
        )

    @classmethod
    async def locked_amount(cls, user_id: str) -> int:
        """当日未结算存款（锁定金额，不可取）"""
        return await db.fetchval(
            "SELECT SUM(amount) FROM mahiro_bank_log WHERE user_id = ?"
            " AND handle_type = ? AND is_completed = 0"
            " AND date(create_time) = date('now', 'localtime')",
            (user_id, str(BankHandleType.DEPOSIT)),
        )

    @classmethod
    async def loan(cls, user_id: str, amount: int, rate: float) -> None:
        await cls.get_user(user_id)
        await db.execute(
            "UPDATE mahiro_bank SET loan_amount = loan_amount + ?, loan_rate = ?,"
            " update_time = ? WHERE user_id = ?",
            (amount, rate, now_str(), user_id),
        )
        await cls._append_log(user_id, amount, rate, BankHandleType.LOAN)

    @classmethod
    async def repayment(cls, user_id: str, amount: int) -> None:
        user = await cls.get_user(user_id)
        if amount <= 0 or amount > user.loan_amount:
            raise ValueError("还款金额异常...")
        await db.execute(
            "UPDATE mahiro_bank SET loan_amount = loan_amount - ?, update_time = ?"
            " WHERE user_id = ?",
            (amount, now_str(), user_id),
        )
        await cls._append_log(user_id, amount, 0, BankHandleType.REPAYMENT)
