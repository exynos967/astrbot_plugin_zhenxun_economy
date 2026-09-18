"""俄罗斯轮盘业务模块（移植自真寻 russian 插件，数值与原版一致）

已修复的原版 bug：
- 原版对已超时对局的覆盖判断写反（超时后反而拒绝新对局），现在超时对局可被覆盖
- 原版手动结算的超时校验被注释掉，现在恢复（>30 秒无动作才能手动结算）
- 原版 QQ 平台（is_qbot）不注册超时自动结算，现在全平台统一注册
- 原版排行是升序，现在 core.RussianUser.rank 为降序（core 层已修复）
- 原版「左轮不是连发的」固定显示 player2 昵称，现在显示真正该开枪的玩家

战绩/排行走 core.RussianUser，金币走 core.UserConsole，本模块不直接写 SQL。
event 相关逻辑（回复、session_waiter）集中在 handle_* 方法，核心判定与结算
均为普通函数/方法，可直接单元测试。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from astrbot.api import logger

from ...core import GoldHandle, InsufficientGold, RussianUser, UserConsole
from .equipment import PlayerDeathException, get_weapon, get_weapons

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

EXPIRE_TIME = 30
"""对局无动作超时秒数"""
AUTO_END_DELAY = 31
"""超时自动结算延迟（比 EXPIRE_TIME 多 1 秒，保证手动结算优先）"""

Seg = tuple[str, str]
"""可发送消息段：("plain", 文本) | ("at", 用户id) | ("image", 图片URL)，延迟转 Comp 便于测试"""

death_messages = [
    '"嘭！"，你被击中了，直接去世了',
    "眼前一黑，你直接穿越到了异世界...(死亡)",
    "终究还是你先走一步...",
]

live_messages = [
    "呼呼，没有爆裂的声响，你活了下来",
    "虽然黑洞洞的枪口很恐怖，但好在没有子弹射出来，你活下来了",
    '"咔"，你没死，看来运气不错',
]


def build_bullet_arr(num: int) -> list[int]:
    """生成 7 格弹巢，随机放入 num 个子弹（1 为子弹，0 为空）"""
    bullet_list = [0, 0, 0, 0, 0, 0, 0]
    for i in random.sample(range(7), num):
        bullet_list[i] = 1
    return bullet_list


def compute_fee(money: int, rand: int) -> int:
    """结算手续费：money>10 时按 rand% 抽取，rand 非 0 时至少收 1 金币"""
    if money <= 10:
        return 0
    fee = int(money * float(rand) / 100)
    return 1 if fee < 1 and rand != 0 else fee


@dataclass
class Russian:
    """俄罗斯轮盘对局状态"""

    player1: tuple[str, str]
    """玩家1 (id, 昵称)"""
    money: int
    """挑战金额"""
    bullet_num: int
    """子弹数"""
    at_user: str | None = None
    """指定决斗对象 id"""
    player2: tuple[str, str] | None = None
    """玩家2 (id, 昵称)"""
    bullet_arr: list[int] = field(default_factory=list)
    """弹巢排列（7 格）"""
    bullet_index: int = 0
    """当前弹巢下标"""
    next_user: str = ""
    """下一个开枪用户 id"""
    weapon: str = "standard"
    """武器类型 id"""
    time: float = field(default_factory=time.time)
    """最近一次动作时间（超时判定基准，每次状态变更刷新）"""
    umo: str = ""
    """event.unified_msg_origin，超时主动发消息用"""

    def random_bullet(self) -> None:
        """随机重排剩余子弹（当前位及之前不动）"""
        rest = self.bullet_arr[self.bullet_index + 1 :]
        self.bullet_arr = self.bullet_arr[: self.bullet_index + 1]
        random.shuffle(rest)
        self.bullet_arr.extend(rest)


class RussianModule:
    """俄罗斯轮盘业务类：对局状态保存在内存 dict[group_id -> Russian]"""

    def __init__(self, plugin, config: dict) -> None:
        self.plugin = plugin
        self.config = config
        self._games: dict[str, Russian] = {}
        self._tasks: dict[str, asyncio.Task] = {}

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
            elif kind == "image":
                chain.append(Comp.Image.fromURL(val))
        return MessageChain(chain)

    async def _send(self, event: "AstrMessageEvent", segments: list[Seg]) -> None:
        await event.send(self._to_chain(segments))

    async def _require_group(self, event: "AstrMessageEvent") -> str | None:
        """限群聊，私聊提示后返回 None"""
        group_id = event.get_group_id()
        if not group_id:
            await self._send(event, [("plain", "笨蛋，轮盘只能在群里玩哦~")])
            return None
        return group_id

    def _cancel_timer(self, group_id: str) -> None:
        if task := self._tasks.pop(group_id, None):
            task.cancel()

    def _reset_timer(self, group_id: str, game: Russian) -> None:
        """每次状态变更取消旧定时并重建 31 秒自动结算任务"""
        self._cancel_timer(group_id)
        game.time = time.time()
        self._tasks[group_id] = asyncio.create_task(self._auto_end(group_id, game))

    async def _auto_end(self, group_id: str, game: Russian) -> None:
        """31 秒无动作自动结算（全平台注册，去掉原版 is_qbot 分支）"""
        try:
            await asyncio.sleep(AUTO_END_DELAY)
        except asyncio.CancelledError:
            return
        if self._games.get(group_id) is not game:
            return  # 对局已结算/被覆盖
        segments = await self._settle(group_id, None)
        if not segments or not game.umo or self.plugin is None:
            return
        try:
            await self.plugin.context.send_message(
                game.umo,
                self._to_chain([("plain", "对决超时，自动结算！\n")] + segments),
            )
        except Exception as e:
            logger.warning(f"[zhenxun_economy] 轮盘超时结算消息发送失败: {e}")

    # ---------- 装弹 ----------

    async def handle_load(
        self, event: "AstrMessageEvent", num: str | None, money: int | None
    ) -> None:
        """装弹/俄罗斯轮盘：开启对局"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        uname = event.get_sender_name() or user_id
        money = 200 if money is None else money
        if money <= 0:
            await self._send(event, [("plain", "赌注金额必须大于0!")])
            return
        # 子弹数缺省时追问
        if num is None:
            num = await self._ask_bullet_num(event)
            if num is None:
                return
        else:
            num = num.strip()
            if num in {"取消", "算了"}:
                await self._send(event, [("plain", "已取消装弹...")])
                return
            if not num.isdigit():
                await self._send(event, [("plain", "输入的子弹数必须是数字！")])
                return
            if not 1 <= int(num) <= 6:
                await self._send(event, [("plain", "子弹数量必须在1-6之间!")])
                return
            num = int(num)
        # 已有对局：未超时拒绝，超时覆盖（修复原版逻辑写反的 bug）
        if old := self._games.get(group_id):
            if old.time + EXPIRE_TIME >= time.time():
                if old.player2:
                    text = f"{old.player1[1]} 和 {old.player2[1]} 的对决还未结束！"
                else:
                    text = (
                        f"现在是 {old.player1[1]} 发起的对决,"
                        " 请接受对决或等待决斗超时..."
                    )
                await self._send(event, [("plain", text)])
                return
            self._cancel_timer(group_id)
        max_money = int(self.config.get("russian_max_bet_gold", 1000))
        if money > max_money:
            await self._send(
                event, [("plain", f"太多了！单次金额不能超过{max_money}！")]
            )
            return
        user = await UserConsole.get_user(user_id)
        if user.gold < money:
            await self._send(
                event,
                [
                    (
                        "plain",
                        "你没有足够的钱支撑起这场挑战，如果需要指定金额，"
                        "可以输入 装弹 1(子弹数) 100(金额)",
                    )
                ],
            )
            return
        from ...core import platform_utils as pu

        at_ids = pu.get_at_user_ids(event)
        at_user = at_ids[0] if at_ids else None
        game = Russian(
            player1=(user_id, uname),
            money=money,
            bullet_num=num,
            at_user=at_user,
            umo=event.unified_msg_origin,
        )
        game.bullet_arr = build_bullet_arr(num)
        game.weapon = random.choice(list(get_weapons().keys()))
        self._games[group_id] = game
        self._reset_timer(group_id, game)
        weapon = get_weapon(game.weapon)
        segments: list[Seg] = [
            (
                "plain",
                "咔 " * num
                + "装填完毕\n"
                + f"挑战金额：{money}\n"
                + f"第一枪的概率为：{float(num) / 7.0 * 100:.2f}%\n"
                + f"装备：{weapon.name}\n",
            )
        ]
        if at_user:
            at_name = await pu.get_user_name(event, at_user)
            segments += [
                ("plain", f"{uname} 向"),
                ("at", at_user),
                (
                    "plain",
                    f"发起了决斗！请 {at_name} 在{EXPIRE_TIME}秒内回复"
                    "‘接受对决’ or ‘拒绝对决’，超时此次决斗作废！",
                ),
            ]
        else:
            segments.append(
                (
                    "plain",
                    f"若{EXPIRE_TIME}秒内无人接受挑战则此次对决作废"
                    "（发送‘经济帮助’查看命令）",
                )
            )
        await self._send(event, segments)

    async def _ask_bullet_num(self, event: "AstrMessageEvent") -> int | None:
        """session_waiter 追问子弹数量（60s，用户级会话），返回 None 表示已取消/失败"""
        from astrbot.api.util import SessionController, session_waiter

        result: dict[str, int | str] = {}

        @session_waiter(timeout=60)
        async def waiter(controller: SessionController, ev: "AstrMessageEvent"):
            msg = ev.get_message_str().strip()
            if msg in {"取消", "算了"}:
                result["cancel"] = "1"
                controller.stop()
                return
            if not msg.isdigit():
                await ev.send(self._to_chain([("plain", "输入的子弹数必须是数字！")]))
                controller.stop()
                return
            if not 1 <= int(msg) <= 6:
                await ev.send(self._to_chain([("plain", "子弹数量必须在1-6之间!")]))
                controller.stop()
                return
            result["num"] = int(msg)
            controller.stop()

        await self._send(
            event, [("plain", "请输入子弹数量（最大6，或输入'取消'来取消装弹）")]
        )
        try:
            await waiter(event)
        except TimeoutError:
            await self._send(event, [("plain", "超时未输入，已取消装弹...")])
            return None
        if result.get("cancel"):
            await self._send(event, [("plain", "已取消装弹...")])
            return None
        return result.get("num")  # type: ignore[return-value]

    # ---------- 接受 / 拒绝 ----------

    async def handle_accept(self, event: "AstrMessageEvent") -> None:
        """接受对决：player2 就位后进入群级 waiter 轮流开枪"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        uname = event.get_sender_name() or user_id
        if not (game := self._games.get(group_id)):
            await self._send(
                event, [("plain", "目前没有进行的决斗，请发送 装弹 开启决斗吧！")]
            )
            return
        if game.at_user and game.at_user != user_id:
            await self._send(
                event, [("plain", "又不是找你决斗，你接受什么啊！气！")]
            )
            return
        if game.player2:
            await self._send(
                event, [("plain", "当前决斗已被其他玩家接受！请等待下局对决！")]
            )
            return
        if game.player1[0] == user_id:
            await self._send(
                event, [("plain", "你发起的对决，你接受什么啊！气！")]
            )
            return
        user = await UserConsole.get_user(user_id)
        if user.gold < game.money:
            await self._send(
                event, [("plain", "你没有足够的钱来接受这场挑战...")]
            )
            return
        game.player2 = (user_id, uname)
        game.next_user = game.player1[0]
        self._reset_timer(group_id, game)
        # 双方胜率文本
        r1 = await RussianUser.get_user(game.player1[0], group_id)
        r2 = await RussianUser.get_user(game.player2[0], group_id)
        t1 = r1.win_count + r1.fail_count
        t2 = r2.win_count + r2.fail_count
        wr1 = round(r1.win_count / t1 * 100, 2) if t1 else 0
        wr2 = round(r2.win_count / t2 * 100, 2) if t2 else 0
        weapon = get_weapon(game.weapon)
        img_seg = await self._start_card(game, weapon, r1, r2, wr1, wr2)
        if img_seg:
            await self._send(
                event,
                [("plain", "决斗已经开始！请"), ("at", game.player1[0]), ("plain", "先开枪！")]
                + img_seg,
            )
        else:
            await self._send(
                event,
                [
                    ("plain", "决斗已经开始！请"),
                    ("at", game.player1[0]),
                    (
                        "plain",
                        f"先开枪！\n"
                        f"【{game.player1[1]}】胜率 {wr1}%（{r1.win_count}胜） vs "
                        f"【{game.player2[1]}】胜率 {wr2}%（{r2.win_count}胜）\n"
                        f"装备：{weapon.name}（{weapon.description}）",
                    ),
                ],
            )
        await self._shoot_waiter(event, group_id, game)

    async def _start_card(
        self, game: Russian, weapon, r1, r2, wr1, wr2
    ) -> list[Seg]:
        """渲染开局对战卡（start.html），失败/无 plugin/关闭渲染时返回空列表"""
        if self.plugin is None or not self.config.get("render_enabled", True):
            return []
        try:
            from ...core import platform_utils as pu
            from . import render as russian_render

            data = {
                "russian": self._game_tpl_data(game),
                "player1_avatar": await pu.get_avatar_b64(game.player1[0]) or "",
                "player2_avatar": await pu.get_avatar_b64(game.player2[0]) or "",
                "player1_win_rate": wr1,
                "player1_wins": r1.win_count,
                "player2_win_rate": wr2,
                "player2_wins": r2.win_count,
                "weapon_name": weapon.name,
                "weapon_description": weapon.special_effect.name,
                "weapon_effect": weapon.special_effect.description,
            }
            url = await russian_render.render_start(self.plugin, data)
            return [("image", url)] if url else []
        except Exception as e:
            logger.warning(f"[zhenxun_economy] 轮盘开局卡渲染失败，降级纯文本: {e}")
            return []

    @staticmethod
    def _game_tpl_data(game: Russian) -> dict:
        """对局状态转模板变量（与原版 russian 模板变量一致，无人机模式固定 is_ai=False）"""
        return {
            "player1": [game.player1[0], game.player1[1]],
            "player2": [game.player2[0], game.player2[1]] if game.player2 else None,
            "money": game.money,
            "bullet_num": game.bullet_num,
            "bullet_arr": game.bullet_arr,
            "is_ai": False,
        }

    @staticmethod
    def _rec_tpl_data(rec) -> dict:
        """战绩记录转模板变量（与原版 RussianUser 字段一致）"""
        return {
            "win_count": rec.win_count,
            "fail_count": rec.fail_count,
            "make_money": rec.make_money,
            "lose_money": rec.lose_money,
            "winning_streak": rec.winning_streak,
            "losing_streak": rec.losing_streak,
            "max_winning_streak": rec.max_winning_streak,
            "max_losing_streak": rec.max_losing_streak,
        }

    async def handle_refuse(self, event: "AstrMessageEvent") -> None:
        """拒绝对决（仅被 at 的对手可拒绝）"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        uname = event.get_sender_name() or user_id
        if game := self._games.get(group_id):
            if game.at_user:
                if game.at_user != user_id:
                    await self._send(
                        event, [("plain", "又不是找你决斗，你拒绝什么啊！气！")]
                    )
                    return
                del self._games[group_id]
                self._cancel_timer(group_id)
                await self._send(
                    event,
                    [
                        ("at", game.player1[0]),
                        ("plain", f"{uname}拒绝了你的对决！"),
                    ],
                )
                return
            await self._send(
                event, [("plain", "当前决斗并没有指定对手，无法拒绝哦！")]
            )
            return
        await self._send(
            event, [("plain", "目前没有进行的决斗，请发送 装弹 开启决斗吧！")]
        )

    # ---------- 开枪 ----------

    async def handle_shoot(self, event: "AstrMessageEvent") -> None:
        """开枪/咔/嘭/嘣（非等待期的普通 handler 入口）"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        uname = event.get_sender_name() or user_id
        segments, _ = await self._shoot(group_id, user_id, uname)
        await self._send(event, segments)

    async def _shoot(
        self, group_id: str, user_id: str, uname: str
    ) -> tuple[list[Seg], bool]:
        """开枪核心逻辑，返回 (消息段, 是否已结算)"""
        if not (game := self._games.get(group_id)):
            return [("plain", "目前没有进行的决斗，请发送 装弹 开启决斗吧！")], False
        if not game.player2:
            return [("plain", "当前还没有玩家接受对决，无法开枪...")], False
        if user_id not in [game.player1[0], game.player2[0]]:
            # 非玩家随机嘲讽
            rand_list = [
                f"不要打扰 {game.player1[1]} 和 {game.player2[1]} 的决斗啊！",
                "给我好好做好一个观众！不然小真寻就要生气了",
                f"不要捣乱啊baka{uname}！",
            ]
            return [("plain", random.choice(rand_list))], False
        if user_id != game.next_user:
            next_name = (
                game.player1[1]
                if game.next_user == game.player1[0]
                else game.player2[1]
            )
            # 修复原版固定显示 player2 昵称的 bug
            return [("plain", f"左轮不是连发的！该 {next_name} 开枪了!")], False

        weapon = get_weapon(game.weapon)
        is_dead = False
        result = ""
        try:
            if r := weapon.special_effect.effect_func(game, user_id):
                result = r + "\n"
        except PlayerDeathException as e:
            is_dead = True
            result = e.message
        logger.info(
            f"[zhenxun_economy] 轮盘弹巢: {game.bullet_arr} 下标: {game.bullet_index}"
        )
        if is_dead:
            result = random.choice(death_messages) + "\n" + result
            settle = await self._settle(group_id, user_id)
            return [("plain", result)] + (settle or []), True
        p = (game.bullet_index + game.bullet_num + 1) / len(game.bullet_arr) * 100
        result += f"{random.choice(live_messages)}\n下一枪中弹的概率: {p:.2f}%, 轮到 "
        next_user = (
            game.player2[0]
            if game.next_user == game.player1[0]
            else game.player1[0]
        )
        game.bullet_index += 1
        game.next_user = next_user
        self._reset_timer(group_id, game)
        return [("plain", result), ("at", next_user), ("plain", " 了!")], False

    async def _shoot_waiter(
        self, event: "AstrMessageEvent", group_id: str, game: Russian
    ) -> None:
        """接受对决后进入群级 session_waiter 循环处理开枪，直到结算或超时"""
        from astrbot.api.util import SessionController, session_waiter
        from astrbot.core.utils.session_waiter import SessionFilter

        class GroupFilter(SessionFilter):
            """群级会话：整个群共享一个等待会话"""

            def filter(self, ev: "AstrMessageEvent") -> str:
                return ev.get_group_id() or ev.unified_msg_origin

        @session_waiter(timeout=EXPIRE_TIME)
        async def waiter(controller: SessionController, ev: "AstrMessageEvent"):
            text = ev.get_message_str().strip()
            if text not in ("开枪", "咔", "嘭", "嘣"):
                return  # 无关消息不续命
            uid = ev.get_sender_id()
            segments, settled = await self._shoot(
                group_id, uid, ev.get_sender_name() or uid
            )
            await ev.send(self._to_chain(segments))
            if settled:
                controller.stop()
                return
            controller.keep(EXPIRE_TIME, reset_timeout=True)

        try:
            await waiter(event, session_filter=GroupFilter())
        except TimeoutError:
            # 等待开枪超时：若对局还在（未被手动结算/覆盖）则自动结算
            if self._games.get(group_id) is game:
                segments = await self._settle(group_id, None)
                if segments:
                    await self._send(
                        event, [("plain", "对决超时，自动结算！\n")] + segments
                    )
        except Exception as e:
            logger.warning(f"[zhenxun_economy] 轮盘开枪等待会话异常: {e}")

    # ---------- 结算 ----------

    async def handle_settle(self, event: "AstrMessageEvent") -> None:
        """手动结算：要求对局已超时（>30 秒无动作，修复原版注释掉的校验）"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        if not (game := self._games.get(group_id)):
            await self._send(event, [("plain", "比赛并没有开始...无法结算...")])
            return
        if not game.player2:
            if game.time + EXPIRE_TIME < time.time():
                segments = await self._settle(group_id, user_id)
                if segments:
                    await self._send(event, segments)
                return
            await self._send(event, [("plain", "决斗还未开始,，无法结算哦...")])
            return
        if user_id not in [game.player1[0], game.player2[0]]:
            await self._send(
                event, [("plain", "吃瓜群众不要捣乱！黄牌警告！")]
            )
            return
        if game.time + EXPIRE_TIME >= time.time():
            await self._send(
                event,
                [
                    (
                        "plain",
                        f"{game.player1[1]} 和 {game.player2[1]} "
                        "比赛并未超时，请继续比赛",
                    )
                ],
            )
            return
        segments = await self._settle(group_id, user_id)
        if segments:
            await self._send(event, segments)

    async def _settle(self, group_id: str, user_id: str | None) -> list[Seg] | None:
        """结算核心逻辑：判定胜负、写战绩、划转金币，返回消息段（无对局返回 None）"""
        if not (game := self._games.get(group_id)):
            return None
        if not game.player2:
            # 超时且无人接受 → 决斗过期删局
            del self._games[group_id]
            self._cancel_timer(group_id)
            return [("plain", "规定时间内还未有人接受决斗，当前决斗过期...")]
        if user_id and user_id not in [game.player1[0], game.player2[0]]:
            return [("plain", "吃瓜群众不要捣乱！黄牌警告！")]
        # 轮到谁开枪谁死了 → next_user 不是 player1 说明 player1 活到最后
        if game.next_user != game.player1[0]:
            win_user, lose_user = game.player1, game.player2
        else:
            win_user, lose_user = game.player2, game.player1
        rand = random.randint(0, 5) if game.money > 10 else 0
        fee = compute_fee(game.money, rand)
        await RussianUser.add_count(win_user[0], group_id, "win")
        await RussianUser.add_count(lose_user[0], group_id, "lose")
        # 结算卡需要 add_count 之后、add_money 之前的战绩快照（与原版一致：
        # 卡片上累计赚取 = 旧 make_money + 本次赢取）
        winner_rec = loser_rec = None
        if self.plugin is not None and self.config.get("render_enabled", True):
            winner_rec = await RussianUser.get_user(win_user[0], group_id)
            loser_rec = await RussianUser.get_user(lose_user[0], group_id)
        await RussianUser.add_money(win_user[0], group_id, "win", game.money - fee)
        await RussianUser.add_money(lose_user[0], group_id, "lose", game.money)
        await UserConsole.add_gold(win_user[0], game.money - fee, "russian")
        try:
            await UserConsole.reduce_gold(
                lose_user[0], game.money, GoldHandle.PLUGIN, "russian"
            )
        except InsufficientGold:
            # 败者金币不足兜底清零（修复原版直接改字段，改用 core API）
            await UserConsole.set_gold(lose_user[0], 0)
        del self._games[group_id]
        self._cancel_timer(group_id)
        img_seg = await self._end_card(
            game, win_user, lose_user, winner_rec, loser_rec, fee, rand
        )
        if img_seg:
            return [
                ("plain", "这场决斗是 "),
                ("at", win_user[0]),
                ("plain", " 胜利了!"),
            ] + img_seg
        fee_text = (
            f"手续费 {fee} 金币（{rand}%）" if fee else "本次决斗免手续费哦~"
        )
        return [
            ("plain", "这场决斗是 "),
            ("at", win_user[0]),
            (
                "plain",
                f" 胜利了!\n{win_user[1]} 赢得 {game.money - fee} 金币，{fee_text}",
            ),
        ]

    async def _end_card(
        self, game: Russian, win_user, lose_user, winner_rec, loser_rec, fee, rand
    ) -> list[Seg]:
        """渲染结算卡（end.html），无战绩快照/失败时返回空列表走纯文本"""
        if not winner_rec or not loser_rec:
            return []
        try:
            from ...core import platform_utils as pu
            from . import render as russian_render

            weapon = get_weapon(game.weapon)
            data = {
                "russian": self._game_tpl_data(game),
                "win_user": [win_user[0], win_user[1]],
                "winner": self._rec_tpl_data(winner_rec),
                "winner_avatar": await pu.get_avatar_b64(win_user[0]) or "",
                "lose_user": [lose_user[0], lose_user[1]],
                "loser": self._rec_tpl_data(loser_rec),
                "loser_avatar": await pu.get_avatar_b64(lose_user[0]) or "",
                "weapon_config": {
                    "name": weapon.name,
                    "special_effect": {
                        "name": weapon.special_effect.name,
                        "description": weapon.special_effect.description,
                    },
                },
                "fee": fee,
                "rand": rand,
                "bot_nickname": self.config.get("bot_name", "真寻"),
            }
            url = await russian_render.render_end(self.plugin, data)
            return [("image", url)] if url else []
        except Exception as e:
            logger.warning(f"[zhenxun_economy] 轮盘结算卡渲染失败，降级纯文本: {e}")
            return []

    # ---------- 战绩 / 排行 ----------

    async def handle_record(self, event: "AstrMessageEvent") -> None:
        """我的战绩：HTML 战绩卡，失败/关闭渲染时降级纯文本"""
        if not (group_id := await self._require_group(event)):
            return
        user_id = event.get_sender_id()
        uname = event.get_sender_name() or user_id
        rec = await RussianUser.get_user(user_id, group_id)
        total = rec.win_count + rec.fail_count
        win_rate = rec.win_count / total * 100 if total else 0
        net_profit = rec.make_money - rec.lose_money
        if self.plugin is not None and self.config.get("render_enabled", True):
            try:
                from ...core import platform_utils as pu
                from . import render as russian_render

                data = {
                    "user": self._rec_tpl_data(rec),
                    "user_id": user_id,
                    "user_name": uname,
                    "user_avatar": await pu.get_avatar_b64(user_id) or "",
                    "total_games": total,
                    "win_rate": win_rate,
                    "net_profit": net_profit,
                }
                url = await russian_render.render_record(self.plugin, data)
                if url:
                    await self._send(event, [("image", url)])
                    return
            except Exception as e:
                logger.warning(f"[zhenxun_economy] 轮盘战绩卡渲染失败，降级纯文本: {e}")
        win_rate = round(win_rate, 2)
        await self._send(
            event,
            [
                (
                    "plain",
                    f"【{uname} 的轮盘战绩】\n"
                    f"胜场: {rec.win_count}\n"
                    f"败场: {rec.fail_count}\n"
                    f"胜率: {win_rate}%\n"
                    f"净收益: {net_profit} 金币",
                )
            ],
        )

    _RANK_MAP = {
        "胜场": ("win_count", "胜场排行", "场次"),
        "败场": ("fail_count", "败场排行", "场次"),
        "欧洲人": ("make_money", "欧洲人排行", "金币"),
        "慈善家": ("lose_money", "慈善家排行", "金币"),
        "最高连胜": ("max_winning_streak", "最高连胜排行", "场次"),
        "最高连败": ("max_losing_streak", "最高连败排行", "场次"),
    }

    async def handle_rank(
        self, event: "AstrMessageEvent", rank_type: str, num: int = 10
    ) -> None:
        """轮盘排行（降序，修复原版升序 bug；纯文本榜）"""
        if not (group_id := await self._require_group(event)):
            return
        if rank_type not in self._RANK_MAP:
            await self._send(event, [("plain", "没有这个排行类型哦~")])
            return
        if num > 51 or num < 10:
            num = 10
        field_name, title, unit = self._RANK_MAP[rank_type]
        users = await RussianUser.rank(group_id, field_name, num)
        if not users:
            await self._send(event, [("plain", "当前数据为空...")])
            return
        from ...core import platform_utils as pu

        # 优先渲染原版表格卡片（原版为 BuildMat 柱状图，此处用统一的 ui.table
        # 等价组件呈现，避免为单一视图引入 matplotlib 重依赖），失败降级纯文本
        if self.plugin is not None and self.config.get("render_enabled", True):
            try:
                import asyncio

                from .._shared import render_table_card

                names = await asyncio.gather(
                    *[pu.get_user_name(event, rec.user_id) for rec in users]
                )
                avatars = await asyncio.gather(
                    *[pu.get_avatar_b64(rec.user_id) for rec in users]
                )
                rows = [
                    [
                        i + 1,
                        ("image", avatars[i]) if avatars[i] else "-",
                        names[i],
                        f"{getattr(rec, field_name)} {unit}",
                    ]
                    for i, rec in enumerate(users)
                ]
                url = await render_table_card(
                    self.plugin, f"轮盘{title}", None,
                    ["排名", "头像", "名称", title.replace("排行", "")], rows,
                )
                if url:
                    await self._send(event, [("image", url)])
                    return
            except Exception as e:
                from astrbot.api import logger

                logger.warning(f"[zhenxun_economy] 轮盘排行渲染失败，降级纯文本: {e}")
        lines = [f"【轮盘{title}】"]
        for i, rec in enumerate(users, 1):
            name = await pu.get_user_name(event, rec.user_id)
            lines.append(f"{i}. {name}: {getattr(rec, field_name)} {unit}")
        await self._send(event, [("plain", "\n".join(lines))])
