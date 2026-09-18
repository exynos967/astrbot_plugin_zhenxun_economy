"""商品与道具注册机制（对应真寻 GoodsInfo + shop_register + ShopManage）。

- GoodsInfo: 商品表 CRUD
- goods_register: 装饰器，供各子模块注册商品与使用函数（真寻 shop_register 的等价物）
- GoodsRegistry: 启动时统一入库并建立 uuid -> 使用函数 的运行时映射
"""

import inspect
import time
import uuid as uuid_lib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .database import db, now_str
from .enums import GoldHandle, PropHandle
from .exceptions import GoodsNotFound, InsufficientGold


class GoodsInfo:
    """商品表 API（字段与真寻 goods_info 一致）"""

    @classmethod
    async def add_goods(
        cls,
        goods_name: str,
        goods_price: int,
        goods_description: str,
        goods_discount: float = 1,
        goods_limit_time: int = 0,
        is_passive: bool = False,
        partition: str | None = None,
        daily_limit: int = 0,
        icon: str = "",
    ) -> str:
        """添加商品，返回 uuid（同名幂等，返回已有 uuid）"""
        row = await db.fetchone(
            "SELECT uuid FROM goods_info WHERE goods_name = ?", (goods_name,)
        )
        if row:
            return row["uuid"]
        goods_uuid = str(uuid_lib.uuid1())
        await db.execute(
            "INSERT INTO goods_info (uuid, goods_name, goods_price, goods_description,"
            " goods_discount, goods_limit_time, is_passive, partition, daily_limit, icon)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                goods_uuid,
                goods_name,
                int(goods_price),
                goods_description,
                goods_discount,
                goods_limit_time,
                int(is_passive),
                partition,
                daily_limit,
                icon,
            ),
        )
        return goods_uuid

    @classmethod
    async def get_by_name(cls, goods_name: str) -> dict | None:
        row = await db.fetchone(
            "SELECT * FROM goods_info WHERE goods_name = ?", (goods_name,)
        )
        return dict(row) if row else None

    @classmethod
    async def get_by_uuid(cls, goods_uuid: str) -> dict | None:
        row = await db.fetchone(
            "SELECT * FROM goods_info WHERE uuid = ?", (goods_uuid,)
        )
        return dict(row) if row else None

    @classmethod
    async def get_on_sale(cls) -> list[dict]:
        """在售商品（未过期限时），按 id 升序"""
        now = int(time.time())
        rows = await db.fetchall(
            "SELECT * FROM goods_info WHERE goods_limit_time >= ? OR goods_limit_time = 0"
            " ORDER BY id",
            (now,),
        )
        return [dict(r) for r in rows]

    @classmethod
    async def delete_goods(cls, goods_name: str) -> bool:
        cur = await db.execute(
            "DELETE FROM goods_info WHERE goods_name = ?", (goods_name,)
        )
        return cur.rowcount > 0

    @classmethod
    async def daily_buy_count(cls, user_id: str, goods_uuid: str) -> int:
        """当日购买次数（用于每日限购）"""
        return await db.fetchval(
            "SELECT COUNT(*) FROM user_props_log WHERE user_id = ? AND uuid = ?"
            " AND handle = ? AND date(create_time) = date('now', 'localtime')",
            (user_id, goods_uuid, str(PropHandle.BUY)),
        )


class NotMeetUseConditionsException(Exception):
    """使用道具前置条件不满足（before_handle 抛出以阻断使用）"""

    def __init__(self, info: str):
        super().__init__(info)
        self.info = info


@dataclass
class GoodsEntry:
    """已注册商品（运行时）"""

    name: str
    uuid: str = ""
    func: Callable[..., Awaitable[Any]] | None = None
    send_success_msg: bool = True
    max_num_limit: int = 1
    before_handles: list[Callable[..., Awaitable[Any]]] = field(default_factory=list)
    after_handles: list[Callable[..., Awaitable[Any]]] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class _PendingRegister:
    """装饰器收集的待注册数据"""

    name: str
    price: float
    des: str
    discount: float = 1
    limit_time: int = 0
    load_status: bool = True
    daily_limit: int = 0
    is_passive: bool = False
    partition: str | None = None
    icon: str = ""
    send_success_msg: bool = True
    max_num_limit: int = 1
    func: Callable[..., Awaitable[Any]] | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)


class GoodsRegistry:
    """商品注册中心（真寻 shop_register 单例的等价物）"""

    _pending: list[_PendingRegister] = []
    _pending_before: list[tuple[str, Callable]] = []
    _pending_after: list[tuple[str, Callable]] = []
    uuid2goods: dict[str, GoodsEntry] = {}

    _KNOWN_KEYS = {
        "name", "price", "des", "discount", "limit_time", "load_status",
        "daily_limit", "is_passive", "partition", "icon",
        "send_success_msg", "max_num_limit",
    }

    @classmethod
    def register(cls, **kwargs):
        """@goods_register(name=..., price=..., des=..., ...) 装饰器

        额外 kwargs 中 "{商品名}_参数" 前缀写法会剥前缀后仅对该商品生效，
        其余额外参数对所有注册商品生效（与真寻 shop_register 一致）。
        """
        name = kwargs.get("name", "")
        known = {k: v for k, v in kwargs.items() if k in cls._KNOWN_KEYS}
        extra = {}
        for k, v in kwargs.items():
            if k in cls._KNOWN_KEYS:
                continue
            if k.startswith(f"{name}_"):
                extra[k[len(name) + 1:]] = v
            else:
                extra[k] = v

        def decorator(func):
            cls._pending.append(_PendingRegister(func=func, kwargs=extra, **known))
            return func

        return decorator

    @classmethod
    def register_before(cls, name: str):
        def decorator(func):
            cls._pending_before.append((name, func))
            return func

        return decorator

    @classmethod
    def register_after(cls, name: str):
        def decorator(func):
            cls._pending_after.append((name, func))
            return func

        return decorator

    @classmethod
    async def load_register(cls) -> None:
        """启动时统一入库并建立运行时映射（幂等）"""
        name2entry: dict[str, GoodsEntry] = {}
        for p in cls._pending:
            entry = GoodsEntry(
                name=p.name,
                func=p.func,
                send_success_msg=p.send_success_msg,
                max_num_limit=p.max_num_limit,
                kwargs=p.kwargs,
            )
            name2entry[p.name] = entry
            if p.load_status:
                entry.uuid = await GoodsInfo.add_goods(
                    goods_name=p.name,
                    goods_price=int(p.price),
                    goods_description=p.des,
                    goods_discount=p.discount,
                    goods_limit_time=p.limit_time,
                    is_passive=p.is_passive,
                    partition=p.partition,
                    daily_limit=p.daily_limit,
                    icon=p.icon,
                )
            else:
                row = await GoodsInfo.get_by_name(p.name)
                entry.uuid = row["uuid"] if row else ""
        for name, func in cls._pending_before:
            if name in name2entry:
                name2entry[name].before_handles.append(func)
        for name, func in cls._pending_after:
            if name in name2entry:
                name2entry[name].after_handles.append(func)
        cls.uuid2goods = {e.uuid: e for e in name2entry.values() if e.uuid}

    @classmethod
    def get_entry(cls, uuid: str) -> GoodsEntry | None:
        return cls.uuid2goods.get(uuid)


async def call_use_func(func: Callable[..., Awaitable[Any]], available: dict[str, Any]):
    """按函数签名参数名注入参数（真寻 ShopManage 的注入规则）"""
    sig = inspect.signature(func)
    kwargs = {k: v for k, v in available.items() if k in sig.parameters}
    return await func(**kwargs)


# 供业务模块使用的装饰器实例（与真寻 shop_register 用法对齐）
goods_register = GoodsRegistry.register
goods_register_before = GoodsRegistry.register_before
goods_register_after = GoodsRegistry.register_after
