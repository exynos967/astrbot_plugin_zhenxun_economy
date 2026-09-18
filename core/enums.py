from enum import StrEnum


class GoldHandle(StrEnum):
    """金币处理类型（值与真寻保持一致，保证日志数据可迁移）"""

    BUY = "BUY"
    GET = "GET"
    PLUGIN = "PLUGIN"


class PropHandle(StrEnum):
    """道具处理类型"""

    BUY = "BUY"
    USE = "USE"


class BankHandleType(StrEnum):
    """银行记录类型"""

    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    LOAN = "LOAN"
    REPAYMENT = "REPAYMENT"
    INTEREST = "INTEREST"
