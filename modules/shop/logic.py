"""商店模块业务逻辑（对应真寻 builtin_plugins/shop 的 ShopManage + gold_rank）

- handle_* 为 AstrBot 事件层薄封装（main.py 的 handler 调用）
- buy_prop / use_prop / get_shop_data / get_my_props_data / query_gold_rank
  不依赖 event，可直接单测
"""

from __future__ import annotations

import json
import time

from astrbot.api import logger

try:  # AstrBot 运行时：插件根目录是 data.plugins.<dirname> 包
    from ...core import (
        GoodsInfo,
        GoodsNotFound,
        GoodsRegistry,
        GoldHandle,
        NotMeetUseConditionsException,
        PropHandle,
        UserConsole,
        db,
        now_str,
    )
    from ...core.goods import call_use_func
except ImportError:  # 测试环境：插件根目录直接在 sys.path
    from core import (
        GoodsInfo,
        GoodsNotFound,
        GoodsRegistry,
        GoldHandle,
        NotMeetUseConditionsException,
        PropHandle,
        UserConsole,
        db,
        now_str,
    )
    from core.goods import call_use_func

try:
    from astrbot.api.event import AstrMessageEvent, MessageChain
    import astrbot.api.message_components as Comp
except ImportError:  # 测试环境无 astrbot 事件 API（仅测逻辑层）
    AstrMessageEvent = MessageChain = Comp = None

from .render import (
    build_props_text,
    build_shop_text,
    get_props_template,
    load_icon_b64,
    render_shop,
)

# 购买金币流水来源标识（与真寻一致）
SOURCE = "shop"


def _limit_time_str(end_time: int) -> str | None:
    """限时商品剩余时间（小时:分钟），非限时或已过期返回 None"""
    now = int(time.time())
    if not end_time or now > end_time:
        return None
    delta = end_time - now
    return f"{delta // 3600}:{(delta % 3600) // 60:02d}"


class ShopModule:
    """商店模块：商店界面 / 我的金币 / 我的道具 / 购买道具 / 使用道具 / 金币排行"""

    def __init__(self, plugin, config: dict) -> None:
        self.plugin = plugin
        self.config = config or {}

    # ================= 事件层 =================

    async def handle_shop(self, event: AstrMessageEvent) -> None:
        """商店界面：HTML 渲染，失败或 render_enabled=false 时降级纯文本"""
        data = await self.get_shop_data()
        if self._render_enabled():
            url = await render_shop(self.plugin, data)
            if url:
                await event.send(MessageChain([Comp.Image.fromURL(url)]))
                return
        await self._reply(event, build_shop_text(data))

    async def handle_my_gold(self, event: AstrMessageEvent) -> None:
        """我的金币（含 uid 展示编号）"""
        user = await UserConsole.get_user(event.get_sender_id())
        await self._reply(event, f"你的当前余额: {user.gold} 金币哦~\nUID: {user.uid}")

    async def handle_my_props(self, event: AstrMessageEvent) -> None:
        """我的道具背包：HTML 表格或纯文本降级，空则提示"""
        user_id = event.get_sender_id()
        user_name = event.get_sender_name() or user_id
        data = await self.get_my_props_data(user_id, user_name)
        if not data:
            await self._reply(event, "你的道具为空捏...")
            return
        if self._render_enabled():
            try:
                # 原版背包使用通用 ui.table 组件（列：图标/使用ID/名称/数量/简介）
                from .._shared import render_table_card

                rows = [
                    [
                        ("image", r["icon"]) if r["icon"] else "-",
                        r["id"],
                        r["name"],
                        r["count"],
                        r["description"],
                    ]
                    for r in data["rows"]
                ]
                url = await render_table_card(
                    self.plugin,
                    f"{user_name} 的道具背包",
                    "使用 [使用道具 序号] 来使用道具哦",
                    ["图标", "使用ID", "名称", "数量", "简介"],
                    rows,
                )
                if url:
                    await event.send(MessageChain([Comp.Image.fromURL(url)]))
                    return
            except Exception as e:
                logger.warning(f"[zhenxun_economy] 背包渲染失败，降级纯文本: {e}")
        await self._reply(event, build_props_text(data))

    async def handle_buy(self, event: AstrMessageEvent, goods: str, num: int = 1) -> None:
        """购买道具：goods 纯数字按在售列表序号(1起)，否则按名称精确匹配"""
        result = await self.buy_prop(event.get_sender_id(), goods, num)
        await self._reply(event, result)

    async def handle_use(self, event: AstrMessageEvent, goods: str, num: int = 1) -> None:
        """使用道具：goods 纯数字按背包序号(1起)，否则按名称精确匹配"""
        pu = self._get_pu()
        result = await self.use_prop(
            event.get_sender_id(),
            goods,
            num,
            event=event,
            group_id=event.get_group_id() or None,
            at_users=pu.get_at_user_ids(event),
        )
        await self._reply(event, result)

    async def handle_gold_rank(
        self, event: AstrMessageEvent, num: int = 10, is_global: bool = False
    ) -> None:
        """金币排行：默认群内过滤，is_global 为总排行；num>50 拒绝"""
        if num > 50:
            await self._reply(event, "排行榜人数不能超过50哦...")
            return
        user_ids = None
        if not is_global:
            if not event.get_group_id():
                await self._reply(
                    event, "私聊中无法查看 '金币排行'，请发送 '金币总排行'"
                )
                return
            user_ids = await self._get_pu().get_group_user_ids(event) or None
        rows, index = await self.query_gold_rank(
            event.get_sender_id(), max(1, num), user_ids
        )
        if not rows:
            await self._reply(event, "当前还没有人拥有金币哦...")
            return
        scope = "全局" if is_global else "群组内"
        pu = self._get_pu()
        tip_scope = "全局" if is_global else "本群"
        # 优先渲染原版表格卡片，失败降级纯文本
        if self._render_enabled():
            try:
                import asyncio

                from .._shared import render_table_card

                names = await asyncio.gather(
                    *[pu.get_user_name(event, uid) for uid, _ in rows]
                )
                avatars = await asyncio.gather(
                    *[pu.get_avatar_b64(uid) for uid, _ in rows]
                )
                table_rows = [
                    [
                        i + 1,
                        ("image", avatars[i]) if avatars[i] else "-",
                        names[i],
                        gold,
                    ]
                    for i, (uid, gold) in enumerate(rows)
                ]
                url = await render_table_card(
                    self.plugin,
                    f"金币{scope}排行",
                    None,
                    ["排名", "头像", "名称", "金币"],
                    table_rows,
                )
                if url:
                    await event.send(
                        MessageChain(
                            [
                                Comp.Image.fromURL(url),
                                Comp.Plain(f"你的排名在{tip_scope}第 {index} 位哦!"),
                            ]
                        )
                    )
                    return
            except Exception as e:
                logger.warning(f"[zhenxun_economy] 金币排行渲染失败，降级纯文本: {e}")
        lines = [f"✨金币{scope}排行✨"]
        for i, (uid, gold) in enumerate(rows):
            name = await pu.get_user_name(event, uid)
            lines.append(f"{i + 1}. {name} — {gold} 金币")
        lines.append(f"你的排名在{tip_scope}第 {index} 位哦!")
        await self._reply(event, "\n".join(lines))

    # ================= 业务逻辑（无 event 依赖，可单测） =================

    async def get_shop_data(self) -> dict:
        """商店渲染数据：按 partition 分区的在售商品（序号 1 起，全局连续）

        字段结构与真寻 prepare_shop_data() 完全一致（icon_url 为 base64 data URI）
        """
        goods_list = await GoodsInfo.get_on_sale()
        partition_dict: dict[str, list[dict]] = {}
        for idx, g in enumerate(goods_list):
            discount = g["goods_discount"]
            item = {
                "id": idx + 1,
                "name": g["goods_name"],
                "description": g["goods_description"],
                "price": g["goods_price"],
                "discount_price": None
                if discount == 1.0
                else int(g["goods_price"] * discount),
                "limit_time": _limit_time_str(g["goods_limit_time"]),
                "daily_limit": g["daily_limit"] or "∞",
                "icon_url": load_icon_b64(g["icon"]),
            }
            partition_dict.setdefault(g["partition"] or "默认分区", []).append(item)
        categories = [
            {"partition_title": p, "goods_list": items}
            for p, items in partition_dict.items()
        ]
        return {
            "bot_nickname": self.config.get("bot_name", "真寻"),
            "categories": categories,
        }

    async def buy_prop(
        self, user_id: str, name: str, num: int = 1, platform: str | None = None
    ) -> str:
        """购买道具，返回结果文案（数值/流程与真寻一致，修复 num=0 未拦截的问题）"""
        if num <= 0:
            return "购买的数量要大于0!"
        goods_list = await GoodsInfo.get_on_sale()
        if name.isdigit():
            idx = int(name)
            if idx <= 0 or idx > len(goods_list):
                return "道具编号不存在..."
            goods = goods_list[idx - 1]
        else:
            goods = next(
                (g for g in goods_list if g["goods_name"] == name), None
            )
            if not goods:
                return "道具名称不存在..."
        user = await UserConsole.get_user(user_id, platform)
        price = int(goods["goods_price"] * num * goods["goods_discount"])
        if user.gold < price:
            return "糟糕! 您的金币好像不太够哦..."
        if goods["daily_limit"]:
            count = await GoodsInfo.daily_buy_count(user_id, goods["uuid"])
            if count >= goods["daily_limit"]:
                return "今天的购买已达限制了喔!"
        await UserConsole.reduce_gold(user_id, price, GoldHandle.BUY, SOURCE, platform)
        await UserConsole.add_props(user_id, goods["uuid"], num, platform)
        await db.execute(
            "INSERT INTO user_props_log (user_id, uuid, num, gold, handle, create_time)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, goods["uuid"], num, price, str(PropHandle.BUY), now_str()),
        )
        logger.info(
            f"[zhenxun_economy] {user_id} 花费 {price} 金币购买"
            f" {goods['goods_name']} ×{num} 成功！"
        )
        return f"花费 {price} 金币购买 {goods['goods_name']} ×{num} 成功！"

    async def use_prop(
        self,
        user_id: str,
        name: str,
        num: int = 1,
        event=None,
        group_id: str | None = None,
        at_users: list[str] | None = None,
        platform: str | None = None,
    ) -> str:
        """使用道具，返回结果文案

        顺序：before_handles -> UserConsole.use_props -> func -> after_handles
        （before 抛 NotMeetUseConditionsException 时返回其 info 且不扣道具）
        """
        at_users = at_users or []
        if num <= 0:
            return "使用的数量要大于0!"
        if name.isdigit():
            props = await self._clean_props(user_id, platform)
            idx = int(name)
            if idx <= 0 or idx > len(props):
                return "仓库中道具不存在..."
            goods = await GoodsInfo.get_by_uuid(list(props.keys())[idx - 1])
        else:
            goods = await GoodsInfo.get_by_name(name)
        if not goods:
            return "对应的道具不存在..."
        if goods["is_passive"]:
            return f"{goods['goods_name']} 是被动道具, 无法使用..."
        entry = GoodsRegistry.get_entry(goods["uuid"])
        if not entry or not entry.func:
            return f"{goods['goods_name']} 未注册使用函数, 无法使用..."
        if num > entry.max_num_limit:
            return (
                f"{goods['goods_name']} 单次使用最大数量为{entry.max_num_limit}..."
            )
        available = {
            **entry.kwargs,
            "user_id": user_id,
            "group_id": group_id,
            "event": event,
            "num": num,
            "at_user": at_users[0] if at_users else None,
            "at_users": at_users,
            "goods_name": goods["goods_name"],
        }
        try:
            for func in entry.before_handles:
                await call_use_func(func, available)
        except NotMeetUseConditionsException as e:
            return e.info
        try:
            await UserConsole.use_props(user_id, goods["uuid"], num, platform)
        except GoodsNotFound:
            return f"没有找到道具 {name} 或道具数量不足..."
        result = await call_use_func(entry.func, available)
        for func in entry.after_handles:
            await call_use_func(func, available)
        if not result and entry.send_success_msg:
            result = f"使用道具 {entry.name} {num} 次成功！"
        return result

    async def get_my_props_data(
        self, user_id: str, user_name: str, platform: str | None = None
    ) -> dict | None:
        """背包渲染数据（空背包返回 None；序号与使用道具 ID 一致，1 起）"""
        props = await self._clean_props(user_id, platform)
        if not props:
            return None
        rows = []
        for i, goods_uuid in enumerate(props):
            g = await GoodsInfo.get_by_uuid(goods_uuid)
            if not g:
                continue
            rows.append(
                {
                    "id": i + 1,
                    "icon": load_icon_b64(g["icon"]),
                    "name": g["goods_name"],
                    "count": props[goods_uuid],
                    "description": g["goods_description"],
                }
            )
        if not rows:
            return None
        return {"user_name": user_name, "rows": rows}

    async def query_gold_rank(
        self, sender_id: str, num: int = 10, user_ids: list[str] | None = None
    ) -> tuple[list[tuple[str, int]], str]:
        """金币排行（gold 降序）

        Returns:
            (前 num 名 [(user_id, gold)], 发起人在完整榜单中的名次字符串)
        """
        if user_ids:
            marks = ",".join("?" * len(user_ids))
            rows = await db.fetchall(
                "SELECT user_id, gold FROM user_console"
                f" WHERE user_id IN ({marks}) ORDER BY gold DESC",
                tuple(user_ids),
            )
        else:
            rows = await db.fetchall(
                "SELECT user_id, gold FROM user_console ORDER BY gold DESC"
            )
        all_ids = [r["user_id"] for r in rows]
        index = (
            str(all_ids.index(sender_id) + 1)
            if sender_id in all_ids
            else "-1（未统计）"
        )
        return [(r["user_id"], r["gold"]) for r in rows[:num]], index

    # ================= 内部工具 =================

    async def _clean_props(
        self, user_id: str, platform: str | None = None
    ) -> dict[str, int]:
        """清理背包脏数据（count<=0 或商品已删除），有变更则落库，返回干净 props"""
        user = await UserConsole.get_user(user_id, platform)
        if not user.props:
            return {}
        marks = ",".join("?" * len(user.props))
        rows = await db.fetchall(
            f"SELECT uuid FROM goods_info WHERE uuid IN ({marks})",
            tuple(user.props.keys()),
        )
        existing = {r["uuid"] for r in rows}
        cleaned = {
            uuid: count
            for uuid, count in user.props.items()
            if count > 0 and uuid in existing
        }
        if cleaned != user.props:
            # core 层未提供 props 整体回写 API，此处与 UserConsole._save_props 同构
            await db.execute(
                "UPDATE user_console SET props = ? WHERE user_id = ?",
                (json.dumps(cleaned, ensure_ascii=False), user_id),
            )
        return cleaned

    def _render_enabled(self) -> bool:
        return bool(self.config.get("render_enabled", True))

    @staticmethod
    def _get_pu():
        """懒加载平台工具（其依赖 astrbot 事件 API，测试环境不触发）"""
        try:
            from ...core import platform_utils as pu
        except ImportError:
            from core import platform_utils as pu
        return pu

    async def _reply(self, event: AstrMessageEvent, text: str) -> None:
        await event.send(MessageChain([Comp.Plain(text)]))
