"""签到业务逻辑（对应真寻 sign_in 的 SignManage + random_event）。

数值与原版完全一致：
- 好感度增量 = (secrets.randbelow(99) + 1) / 100，即 0.01~0.99
- 双倍判定：random.random() + add_probability > 0.97 或 < specify_probability 时 ×2
- 金币 = random.randint(1, 100) + 随机事件加成
- 随机事件：rand = random.random() - impression/1000，按 Ⅲ→Ⅱ→Ⅰ（概率升序，
  即原版 PROB_DATA 字典序）判定掉卡；不掉卡则额外金币
  random.randint(1, random.randint(1, max(1, int(impression))))，max_sign_gold 封顶
"""

import random
import secrets
from dataclasses import dataclass
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
import astrbot.api.message_components as Comp

try:  # 插件运行时（作为插件包加载，相对导入上两级到插件根的 core）
    from ...core import (
        GoodsNotFound,
        SignUser,
        SignUserRecord,
        UserConsole,
        UserRecord,
        get_level_and_next_impression,
        level2attitude,
        lik2relation,
    )
    from ...core import platform_utils as pu
except ImportError:  # 单元测试环境（插件根目录直接在 sys.path，core 为顶层包）
    from core import (
        GoodsNotFound,
        SignUser,
        SignUserRecord,
        UserConsole,
        UserRecord,
        get_level_and_next_impression,
        level2attitude,
        lik2relation,
    )
    from core import platform_utils as pu

from .render import (
    get_static_asset,
    random_tag_icon,
    random_weather_icon,
    render_card,
    render_text,
)

# 三张好感度双倍加持卡（商品名与商店注册名一致）
CARD1 = "好感度双倍加持卡Ⅰ"
CARD2 = "好感度双倍加持卡Ⅱ"
CARD3 = "好感度双倍加持卡Ⅲ"

# 按小时问候语（移植自原版 utils.py）
MORNING_MESSAGE = [
    "早上好，希望今天是美好的一天！",
    "醒了吗，今天也要元气满满哦！",
    "早上好呀，今天也要开心哦！",
    "早安，愿你拥有美好的一天！",
]
LG_MESSAGE = [
    "今天要早点休息哦~",
    "可不要熬夜到太晚呀",
    "请尽早休息吧！",
    "不要熬夜啦！",
]


def calc_impression_gain(
    add_probability: float = 0.0,
    specify_probability: float = 0.0,
    rand: float | None = None,
    base: float | None = None,
) -> tuple[float, bool]:
    """计算本次签到好感度增量（rand/base 可注入以便测试）。

    返回:
        (好感度增量, 是否触发双倍)
    """
    if base is None:
        base = (secrets.randbelow(99) + 1) / 100
    if rand is None:
        rand = random.random()
    is_double = rand + add_probability > 0.97 or rand < specify_probability
    return (base * 2 if is_double else base), is_double


def random_event(
    impression: float,
    card1_prob: float = 0.2,
    card2_prob: float = 0.09,
    card3_prob: float = 0.05,
    max_sign_gold: int = 200,
    rand: float | None = None,
) -> str | int:
    """签到随机事件：掉卡（返回道具名）或额外金币（返回 int，封顶 max_sign_gold）"""
    if rand is None:
        rand = random.random()
    rand -= impression / 1000
    # 原版 PROB_DATA 按 Ⅲ→Ⅱ→Ⅰ 插入，判定顺序即概率升序（稀有卡优先）
    for prob, name in ((card3_prob, CARD3), (card2_prob, CARD2), (card1_prob, CARD1)):
        if rand <= prob:
            return name
    gold = random.randint(1, random.randint(1, max(1, int(impression))))
    return min(gold, max_sign_gold)


@dataclass
class SignResult:
    """一次签到的结果"""

    user: SignUserRecord  # 签到后的签到记录
    impression_added: float  # 本次增加的好感度（已含双倍）
    is_double: bool  # 是否触发双倍
    gold: int  # 实际入账金币（基础 + 随机事件额外）
    gift_text: str  # 卡片展示的随机事件文本（"额外金币 +N" / "好感度双倍加持卡Ⅰ + 1"）
    gift_name: str | None  # 掉落的道具名（未掉卡为 None）


async def do_sign(
    user_id: str,
    config,
    platform: str | None = None,
    bot_id: str | None = None,
) -> SignResult:
    """执行签到：好感度/金币/随机事件落库（调用方需先确认今日未签到）"""
    user = await SignUser.get_user(user_id, platform)
    gain, is_double = calc_impression_gain(
        float(user.add_probability), float(user.specify_probability)
    )
    # 原版在 sign() 之后用更新后的好感度参与随机事件判定
    new_user = await SignUser.sign(user_id, gain, bot_id=bot_id, platform=platform)
    gold = random.randint(1, 100)
    gift = random_event(
        new_user.impression,
        card1_prob=float(config.get("sign_card1_prob", 0.2)),
        card2_prob=float(config.get("sign_card2_prob", 0.09)),
        card3_prob=float(config.get("sign_card3_prob", 0.05)),
        max_sign_gold=int(config.get("max_sign_gold", 200)),
    )
    gift_name = None
    if isinstance(gift, int):
        gold += gift
        gift_text = f"额外金币 +{gift}"
    else:
        gift_name = gift
        gift_text = f"{gift} + 1"
        try:
            await UserConsole.add_props_by_name(user_id, gift, 1, platform)
        except GoodsNotFound:
            # 商店模块未注册该道具时降级：不掉卡但保证金币正常入账
            logger.warning(f"[zhenxun_economy] 签到掉落道具未在商店注册: {gift}")
            gift_name = None
            gift_text = ""
    await UserConsole.add_gold(user_id, gold, "sign_in", platform)
    return SignResult(new_user, round(gain, 3), is_double, gold, gift_text, gift_name)


async def build_card_data(
    sign_user: SignUserRecord,
    console: UserRecord,
    *,
    nickname: str,
    bot_name: str,
    is_card_view: bool,
    reward: dict | None = None,
) -> dict:
    """构造签到卡片渲染数据（参照原版 utils.py get_card）"""
    impression = float(sign_user.impression)
    level, next_imp, prev_imp = get_level_and_next_impression(impression)
    uid = f"{console.uid}".rjust(12, "0")
    uid_str = f"{uid[:4]} {uid[4:8]} {uid[8:]}"

    now = datetime.now()
    hour = now.hour
    if 6 < hour < 10:
        message = random.choice(MORNING_MESSAGE)
    elif 0 <= hour < 6:
        message = random.choice(LG_MESSAGE)
    else:
        message = f"{bot_name}希望你开心！"

    avatar = await pu.get_avatar_b64(sign_user.user_id) or ""

    denominator = next_imp - prev_imp
    progress = (
        100.0
        if denominator == 0
        else min(100.0, (impression - prev_imp) / denominator * 100)
    )

    data = {
        "is_card_view": is_card_view,
        "user": {
            "nickname": nickname,
            "uid_str": uid_str,
            "avatar_url": avatar,
            "sign_count": sign_user.sign_count,
            "font_size": 45 if len(nickname) <= 6 else 27,
        },
        "favorability": {
            "current": impression,
            "level": level,
            "level_text": f"{level} [{lik2relation.get(str(level), '未知')}]",
            "heart2": [1] * level,
            "heart1": [1] * (len(lik2relation) - level - 1),
            "next_level_at": next_imp,
            "previous_level_at": prev_imp,
        },
        "reward": reward
        or {"impression_added": 0, "gold_added": 0, "gift_received": "", "is_double": False},
        "page": {
            "date_str": str(now.replace(microsecond=0)),
            "weather_icon": random_weather_icon(),
            "temperature": random.randint(1, 40),
            "tag_icon": random_tag_icon(),
        },
        "assets": {
            "rl": get_static_asset("rl.png"),
            "deco1": get_static_asset("1.png"),
            "deco2": get_static_asset("2.png"),
            "heart1": get_static_asset("h1.png"),
            "heart2": get_static_asset("h2.png"),
        },
        "bot_message": f"{bot_name}说: {message}",
        "attitude": f"对你的态度: {level2attitude.get(str(level), '未知')}",
        "interpolation": f"{max(0, next_imp - impression):.2f}",
        "progress": progress,
        "rank": None,
        "total_gold": None,
    }
    if is_card_view:
        # 我的签到：展示全局好感度名次与总金币
        all_users = await SignUser.rank_by_impression(limit=1_000_000)
        ids = [u.user_id for u in all_users]
        data["rank"] = (
            ids.index(sign_user.user_id) + 1 if sign_user.user_id in ids else 0
        )
        data["total_gold"] = console.gold
    return data


class SignInModule:
    """签到模块：签到 / 我的签到 / 好感度排行"""

    def __init__(self, plugin, config):
        self.plugin = plugin  # Star 子类实例（html_render 来源）
        self.config = config

    @property
    def _bot_name(self) -> str:
        return self.config.get("bot_name", "真寻")

    async def _send_card(self, event: AstrMessageEvent, data: dict, prefix: str = ""):
        """发送卡片：优先 HTML 渲染图片，失败/关闭时降级纯文本"""
        url = None
        if self.config.get("render_enabled", True):
            url = await render_card(self.plugin, data)
        if url:
            chain = ([Comp.Plain(prefix)] if prefix else []) + [Comp.Image.fromURL(url)]
            await event.send(MessageChain(chain))
        else:
            text = (prefix + "\n" if prefix else "") + render_text(data)
            await event.send(MessageChain([Comp.Plain(text)]))

    async def _view_card_data(
        self, event: AstrMessageEvent, user_id: str, platform: str, nickname: str
    ) -> dict:
        sign_user = await SignUser.get_user(user_id, platform)
        console = await UserConsole.get_user(user_id, platform)
        return await build_card_data(
            sign_user,
            console,
            nickname=nickname,
            bot_name=self._bot_name,
            is_card_view=True,
        )

    async def handle_sign(self, event: AstrMessageEvent) -> None:
        """签到：已签到则提示并展示当日卡片，否则执行签到"""
        user_id = event.get_sender_id()
        platform = event.get_platform_name()
        nickname = await pu.get_user_name(event, user_id)
        if await SignUser.today_signed(user_id):
            data = await self._view_card_data(event, user_id, platform, nickname)
            await self._send_card(event, data, "你今天已经签到过了哦~")
            return
        try:
            bot_id = event.get_self_id()
        except Exception:
            bot_id = None
        result = await do_sign(user_id, self.config, platform, bot_id=bot_id)
        console = await UserConsole.get_user(user_id, platform)
        data = await build_card_data(
            result.user,
            console,
            nickname=nickname,
            bot_name=self._bot_name,
            is_card_view=False,
            reward={
                "impression_added": result.impression_added,
                "gold_added": result.gold,
                "gift_received": result.gift_text,
                "is_double": result.is_double,
            },
        )
        await self._send_card(event, data)

    async def handle_my_sign(self, event: AstrMessageEvent) -> None:
        """我的签到：只看不签"""
        user_id = event.get_sender_id()
        platform = event.get_platform_name()
        nickname = await pu.get_user_name(event, user_id)
        data = await self._view_card_data(event, user_id, platform, nickname)
        await self._send_card(event, data)

    async def handle_impression_rank(
        self, event: AstrMessageEvent, num: int = 10, is_global: bool = False
    ) -> None:
        """好感度排行（群内默认过滤群成员；num>50 拒绝）"""
        if num > 50:
            await event.send(MessageChain([Comp.Plain("排行榜人数不能超过50哦...")]))
            return
        group_id = event.get_group_id()
        if not is_global and not group_id:
            await event.send(
                MessageChain(
                    [Comp.Plain("私聊中无法查看「好感度排行」，请发送「好感度总排行」哦~")]
                )
            )
            return
        user_ids = None
        scope = "全局"
        if not is_global:
            members = await pu.get_group_user_ids(event)
            if members:  # 拿不到群成员列表时与原版一致：不过滤
                user_ids = members
                scope = "本群"
        records = await SignUser.rank_by_impression(num, user_ids)
        if not records:
            await event.send(MessageChain([Comp.Plain("当前还没有人签到过哦...")]))
            return
        # 自己的名次（在完整排行中查找）
        full = await SignUser.rank_by_impression(1_000_000, user_ids)
        ids = [u.user_id for u in full]
        my_id = event.get_sender_id()
        index = ids.index(my_id) + 1 if my_id in ids else "-1（未统计）"
        # 优先渲染原版表格卡片（ui.table 等价物），失败降级纯文本
        if self.config.get("render_enabled", True):
            import asyncio

            from .._shared import render_table_card

            names = await asyncio.gather(
                *[pu.get_user_name(event, u.user_id) for u in records]
            )
            avatars = await asyncio.gather(
                *[pu.get_avatar_b64(u.user_id) for u in records]
            )
            rows = [
                [
                    i + 1,
                    ("image", avatars[i] or get_static_asset("rl.png")),
                    names[i],
                    f"{u.impression:.2f}",
                    u.sign_count,
                ]
                for i, u in enumerate(records)
            ]
            title = "好感度群组内排行" if not is_global else "好感度全局排行"
            url = await render_table_card(
                self.plugin,
                title,
                "使用 [签到] 可以提升好感度哦",
                ["排名", "头像", "名称", "好感度", "签到次数"],
                rows,
            )
            if url:
                await event.send(
                    MessageChain(
                        [
                            Comp.Image.fromURL(url),
                            Comp.Plain(f"你的排名在{scope}第 {index} 位哦~"),
                        ]
                    )
                )
                return
        lines = [f"【好感度{scope}排行】"]
        for i, u in enumerate(records):
            name = await pu.get_user_name(event, u.user_id)
            lines.append(
                f"{i + 1}. {name} — 好感度 {u.impression:.2f}（签到 {u.sign_count} 天）"
            )
        lines.append(f"你的排名在{scope}第 {index} 位哦~")
        await event.send(MessageChain([Comp.Plain("\n".join(lines))]))
