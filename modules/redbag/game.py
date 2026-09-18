"""金币红包业务模块（移植自真寻 gold_redbag 插件，数值与原版一致）

已修复的原版 bug：
- 原版塞红包直接改 user.gold 字段不写流水，现在走 UserConsole.reduce_gold（GoldHandle.PLUGIN）
- 原版普通红包 timeout 实际用了 add_red_bag 的默认值 60s（配置写的是 600s），现在用 redbag_timeout
- 原版覆盖未过期红包时剩余金币不退回，现在覆盖/过期都会退回剩余金额并写流水
- 原版普通红包没有自动过期结算，现在每个包创建时启动 asyncio 定时任务，超时自动结算退回

节日红包（超管全群广播）不移植：YAGNI，如后续需要再补。

红包统计走 core.RedbagUser，金币走 core.UserConsole，本模块不直接写 SQL。
event 相关逻辑（回复）集中在 handle_* 方法，分配算法/冷却/过期/开包核心均可直接单测。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from astrbot.api import logger

from ...core import GoldHandle, InsufficientGold, RedbagUser, UserConsole

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

Seg = tuple[str, str]
"""可发送消息段：("plain", 文本) | ("at", 用户id) | ("image_b64", 图片base64)，延迟转 Comp 便于测试"""


@dataclass
class RedBag:
    """红包"""

    amount: int
    """总金币"""
    num: int
    """红包数量"""
    promoter: str
    """发起人昵称"""
    promoter_id: str
    """发起人 id"""
    group_id: str
    """所属群聊"""
    name: str = ""
    """红包名称"""
    assigner: str | None = None
    """指定人 id（定向红包）"""
    start_time: float = field(default_factory=time.time)
    """发起时间"""
    open_user: dict[str, int] = field(default_factory=dict)
    """已开启用户 {user_id: 金额}"""
    red_bag_list: list[int] = field(default_factory=list)
    """剩余金额栈"""
    timeout: int = 600
    """过期时间（秒）"""
    umo: str = ""
    """event.unified_msg_origin，过期主动播报用"""


def random_red_bag(amount: int, num: int) -> list[int]:
    """原版随机分配算法：num 个 random() 权重比例分配（int 截断），
    最后一个兜底差额保证总额守恒，最后 shuffle"""
    rand_list = [random.random() for _ in range(num)]
    rand_sum = sum(rand_list)
    amount_sum = 0
    red_bag_list = []
    for i in range(num - 1):
        if amount_sum >= amount:
            red_bag_list.append(0)
        else:
            rand_amount = int(rand_list[i] / rand_sum * amount)
            red_bag_list.append(rand_amount)
            amount_sum += rand_amount
    red_bag_list.append(amount - sum(red_bag_list))
    random.shuffle(red_bag_list)
    return red_bag_list


class RedbagModule:
    """金币红包业务类：状态保存在内存 dict[group_id -> dict[promoter_id -> RedBag]]"""

    def __init__(self, plugin, config: dict) -> None:
        self.plugin = plugin
        self.config = config
        self._data: dict[str, dict[str, RedBag]] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def _interval(self) -> int:
        return int(self.config.get("redbag_interval", 60))

    def _timeout(self) -> int:
        return int(self.config.get("redbag_timeout", 600))

    def _rank_num(self) -> int:
        return int(self.config.get("redbag_rank_num", 10))

    # ---------- 内部工具 ----------

    @staticmethod
    def _to_chain(segments: list[Seg]):
        """消息段列表转 MessageChain（延迟导入 astrbot，便于测试）"""
        import astrbot.api.message_components as Comp
        from astrbot.api.event import MessageChain

        chain = []
        for kind, val in segments:
            if kind == "plain":
                chain.append(Comp.Plain(val))
            elif kind == "at":
                chain.append(Comp.At(qq=val))
            elif kind == "image_b64":
                chain.append(Comp.Image.fromBase64(val))
        return MessageChain(chain)

    async def _send(self, event: "AstrMessageEvent", segments: list[Seg]) -> None:
        await event.send(self._to_chain(segments))

    async def _require_group(self, event: "AstrMessageEvent") -> str | None:
        """限群聊，私聊提示后返回 None"""
        group_id = event.get_group_id()
        if not group_id:
            await self._send(event, [("plain", "红包只能在群里发哦~")])
            return None
        return group_id

    def cooldown_remaining(self, bag: RedBag, now: float | None = None) -> float:
        """距发红包冷却结束的剩余秒数（<=0 表示可以再发）"""
        now = time.time() if now is None else now
        return bag.start_time + self._interval() - now

    def is_expired(self, bag: RedBag, now: float | None = None) -> bool:
        """红包是否已过 redbag_timeout 秒"""
        now = time.time() if now is None else now
        return now > bag.start_time + bag.timeout

    def _remove_bag(self, group_id: str, promoter_id: str) -> None:
        """移除红包并取消其过期定时任务"""
        if bags := self._data.get(group_id):
            bags.pop(promoter_id, None)
        if task := self._tasks.pop((group_id, promoter_id), None):
            task.cancel()

    def _start_expire_task(self, group_id: str, promoter_id: str, bag: RedBag) -> None:
        """每个包创建时启动 redbag_timeout 秒自动结算任务（修复原版无过期结算）"""
        if task := self._tasks.pop((group_id, promoter_id), None):
            task.cancel()
        self._tasks[(group_id, promoter_id)] = asyncio.create_task(
            self._auto_expire(group_id, promoter_id, bag)
        )

    async def _expire_bag(self, group_id: str, promoter_id: str, bag: RedBag) -> int:
        """结算过期红包：退回剩余金额，返回退回金币数；包已被移除返回 -1（幂等）"""
        bags = self._data.get(group_id)
        if not bags or bags.get(promoter_id) is not bag:
            return -1
        self._remove_bag(group_id, promoter_id)
        refund = sum(bag.red_bag_list)
        if refund > 0:
            await UserConsole.add_gold(promoter_id, refund, "gold_redbag")
        return refund

    async def _auto_expire(self, group_id: str, promoter_id: str, bag: RedBag) -> None:
        """红包超时自动结算：退回剩余金额并播报手气榜"""
        try:
            await asyncio.sleep(bag.timeout)
        except asyncio.CancelledError:
            return
        refund = await self._expire_bag(group_id, promoter_id, bag)
        if refund < 0 or not bag.umo or self.plugin is None:
            return
        text = f"{bag.name}已过期结算\n"
        if refund > 0:
            text += f"剩余 {refund} 金币已退回 {bag.promoter} 捏~\n"
        segments: list[Seg] = [("plain", text)]
        rank_img = await self._build_rank_image(None, bag)
        if rank_img:
            segments.append(("image_b64", rank_img))
        else:
            segments[0] = ("plain", text + await self._build_rank_text(None, bag))
        try:
            await self.plugin.context.send_message(bag.umo, self._to_chain(segments))
        except Exception as e:
            logger.warning(f"[zhenxun_economy] 红包过期播报发送失败: {e}")

    def _render_on(self) -> bool:
        """是否启用图片渲染（render_enabled 配置开关，且 plugin 存在）"""
        return self.plugin is not None and bool(self.config.get("render_enabled", True))

    async def _build_rank_image(
        self, event: "AstrMessageEvent | None", bag: RedBag
    ) -> str | None:
        """手气榜图片（base64），失败/关闭渲染返回 None 走纯文本"""
        if not self._render_on():
            return None
        try:
            from ...core import platform_utils as pu
            from . import render as redbag_render

            sort_data = sorted(
                bag.open_user.items(), key=lambda kv: kv[1], reverse=True
            )
            ids = [uid for uid, _ in sort_data[: self._rank_num()]]
            names: dict[str, str] = {}
            avatars: dict[str, bytes | None] = {}
            for uid in ids:
                # event 缺失（过期自动播报）时昵称兜底为 id，与纯文本榜一致
                names[uid] = (
                    await pu.get_user_name(event, uid) if event is not None else uid
                )
                avatars[uid] = await pu.get_avatar_bytes(uid)
            promoter_avatar = await pu.get_avatar_bytes(bag.promoter_id)
            img = redbag_render.build_amount_rank(
                bag.name,
                dict(bag.open_user),
                names,
                avatars,
                promoter_avatar,
                self._rank_num(),
            )
            return redbag_render.to_base64(img)
        except Exception as e:
            logger.warning(f"[zhenxun_economy] 红包手气榜合成失败，降级纯文本: {e}")
            return None

    async def _build_rank_text(
        self, event: "AstrMessageEvent | None", bag: RedBag
    ) -> str:
        """手气榜纯文本：按金额降序前 redbag_rank_num 名"""
        if not bag.open_user:
            return "还没有人抢到红包..."
        sort_data = sorted(bag.open_user.items(), key=lambda kv: kv[1], reverse=True)
        lines = [f"手气榜（共 {sum(bag.open_user.values())} 金币）:"]
        for i, (uid, amount) in enumerate(sort_data[: self._rank_num()], 1):
            if event is not None:
                from ...core import platform_utils as pu

                name = await pu.get_user_name(event, uid)
            else:
                name = uid
            lines.append(f"{i}. {name}: {amount} 金币")
        return "\n".join(lines)

    # ---------- 塞红包 ----------

    async def _create_bag(
        self,
        group_id: str,
        user_id: str,
        uname: str,
        amount: int,
        num: int,
        assigner: str | None = None,
        umo: str = "",
    ) -> RedBag:
        """塞红包核心：扣金币（写流水）+ 建档 + 启动过期定时。前置校验由 handle_send 完成"""
        await UserConsole.reduce_gold(
            user_id, amount, GoldHandle.PLUGIN, "gold_redbag"
        )
        bag = RedBag(
            amount=amount,
            num=num,
            promoter=uname,
            promoter_id=user_id,
            group_id=group_id,
            name=f"{uname}的红包",
            assigner=assigner,
            timeout=self._timeout(),
            red_bag_list=random_red_bag(amount, num),
            umo=umo,
        )
        self._data.setdefault(group_id, {})[user_id] = bag
        await RedbagUser.add_redbag_data(user_id, group_id, "send", amount)
        self._start_expire_task(group_id, user_id, bag)
        return bag

    async def handle_send(
        self, event: "AstrMessageEvent", amount: int, num: int = 5
    ) -> None:
        """塞红包/金币红包，at 到人则为 1 个定向包"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        uname = event.get_sender_name() or user_id
        from ...core import platform_utils as pu

        at_ids = pu.get_at_user_ids(event)
        assigner = at_ids[0] if at_ids else None
        if assigner:
            num = 1
        # 参数校验（文案与原版一致）
        if amount < 1:
            await self._send(event, [("plain", "小气鬼，要别人倒贴金币给你嘛！")])
            return
        if num <= 0:
            await self._send(event, [("plain", "数量不能小于0哦！")])
            return
        if amount < num:
            await self._send(event, [("plain", "数量不能小于金额！小气鬼！")])
            return
        user = await UserConsole.get_user(user_id)
        if user.gold < amount:
            await self._send(event, [("plain", "没有金币的话请不要发红包...")])
            return
        # 冷却：有未消化完的红包且距发起不足 redbag_interval 秒则拒绝
        bags = self._data.setdefault(group_id, {})
        extra_note = ""
        if old := bags.get(user_id):
            remain_cd = self.cooldown_remaining(old)
            if remain_cd > 0:
                left = old.num - len(old.open_user)
                await self._send(
                    event,
                    [
                        (
                            "plain",
                            f"你的红包还没消化完捏...还剩下 {left} 个! "
                            f"请等待红包领取完毕...(或等待{int(remain_cd)}秒红包cd)",
                        )
                    ],
                )
                return
            # 覆盖旧包：退回剩余金币（修复原版覆盖不退的 bug）
            refund = await self._expire_bag(group_id, user_id, old)
            if refund > 0:
                extra_note = f"\n你上一个红包剩余的 {refund} 金币已退回捏~"
        try:
            bag = await self._create_bag(
                group_id,
                user_id,
                uname,
                amount,
                num,
                assigner=assigner,
                umo=event.unified_msg_origin,
            )
        except InsufficientGold:
            await self._send(event, [("plain", "没有金币的话请不要发红包...")])
            return
        segments: list[Seg] = [
            (
                "plain",
                f"{uname}发起了金币红包\n金额: {bag.amount}\n数量: {bag.num}\n",
            )
        ]
        if assigner:
            segments += [("plain", "指定人: "), ("at", assigner), ("plain", "\n")]
        if extra_note:
            segments.append(("plain", extra_note))
        if self._render_on():
            try:
                from . import render as redbag_render

                avatar = await pu.get_avatar_bytes(user_id)
                img = redbag_render.build_cover_image("恭喜发财 大吉大利", avatar)
                segments.append(("image_b64", redbag_render.to_base64(img)))
            except Exception as e:
                logger.warning(f"[zhenxun_economy] 红包封面合成失败，降级纯文本: {e}")
        await self._send(event, segments)

    # ---------- 开红包 ----------

    async def _try_open(
        self, group_id: str, user_id: str
    ) -> tuple[list[tuple[int, RedBag]], list[RedBag]]:
        """开红包核心逻辑：遍历群内红包逐个尝试开启

        返回 (开启结果 [(金额, 红包)], 被抢完而结算的红包列表)
        """
        opened: list[tuple[int, RedBag]] = []
        settled: list[RedBag] = []
        bags = self._data.get(group_id)
        if not bags:
            return opened, settled
        for promoter_id, bag in list(bags.items()):
            if bag.num <= len(bag.open_user) or not bag.red_bag_list:
                continue  # 没有余量
            if self.is_expired(bag):
                continue  # 过期包等定时任务结算退回
            if bag.assigner:
                if bag.assigner != user_id:
                    continue  # 定向包仅指定人可开
            elif user_id in bag.open_user:
                continue  # 普通包每人一次
            amount = bag.red_bag_list.pop()
            bag.open_user[user_id] = amount
            await UserConsole.add_gold(user_id, amount, "gold_redbag")
            await RedbagUser.add_redbag_data(user_id, group_id, "get", amount)
            opened.append((amount, bag))
            if bag.num == len(bag.open_user):
                # 红包抢完立即结算
                settled.append(bag)
                self._remove_bag(group_id, promoter_id)
        return opened, settled

    async def handle_open(self, event: "AstrMessageEvent") -> None:
        """开/抢红包"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        opened, settled = await self._try_open(group_id, user_id)
        if not opened:
            await self._send(event, [("plain", "没有红包给你开！")])
            return
        segments: list[Seg] = []
        for amount, bag in opened:
            segments.append(
                ("plain", f"开启了 {bag.promoter} 的红包, 获取 {amount} 个金币\n")
            )
            if self._render_on():
                try:
                    from ...core import platform_utils as pu
                    from . import render as redbag_render

                    avatar = await pu.get_avatar_bytes(user_id)
                    img = redbag_render.build_open_result_image(
                        bag.name,
                        amount,
                        len(bag.open_user),
                        bag.num,
                        sum(bag.open_user.values()),
                        bag.amount,
                        avatar,
                    )
                    segments.append(("image_b64", redbag_render.to_base64(img)))
                except Exception as e:
                    logger.warning(
                        f"[zhenxun_economy] 开包结果图合成失败，降级纯文本: {e}"
                    )
        await self._send(event, segments)
        for bag in settled:
            rank_img = await self._build_rank_image(event, bag)
            if rank_img:
                await self._send(
                    event,
                    [("plain", f"{bag.name}已结算\n"), ("image_b64", rank_img)],
                )
            else:
                rank_text = await self._build_rank_text(event, bag)
                await self._send(
                    event, [("plain", f"{bag.name}已结算\n{rank_text}")]
                )

    # ---------- 退回红包 ----------

    async def handle_return(self, event: "AstrMessageEvent") -> None:
        """退回自己未开完的红包（发起满 redbag_interval 秒后才可退回）"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        bags = self._data.get(group_id)
        bag = bags.get(user_id) if bags else None
        if not bag:
            await self._send(event, [("plain", "目前没有红包可以退回...")])
            return
        remain_cd = self.cooldown_remaining(bag)
        if remain_cd > 0:
            await self._send(
                event,
                [
                    (
                        "plain",
                        f"你的红包还没有过时, 在 {int(remain_cd)} 秒后可以退回...",
                    )
                ],
            )
            return
        refund = await self._expire_bag(group_id, user_id, bag)
        if refund <= 0:
            await self._send(
                event, [("plain", "金币红包的金币已经被抢完了...")]
            )
            return
        rank_img = await self._build_rank_image(event, bag)
        if rank_img:
            await self._send(
                event,
                [("plain", f"已成功退还了 {refund} 金币\n"), ("image_b64", rank_img)],
            )
            return
        rank_text = await self._build_rank_text(event, bag)
        await self._send(
            event,
            [("plain", f"已成功退还了 {refund} 金币\n{rank_text}")],
        )
