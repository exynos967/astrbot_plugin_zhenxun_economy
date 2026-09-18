"""真寻经济系统 - AstrBot 大插件入口

移植自 zhenxun_bot（AGPLv3）：金币/好感度核心 + 签到/商店/银行/轮盘/红包/农场。
命令均为裸中文触发（不受唤醒前缀约束），与真寻用户习惯一致。
"""

import re

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.event_message_type import EventMessageType

from .core import GoodsRegistry, db

# 导入各业务模块（导入即触发商品注册装饰器收集）
from .modules.sign_in import SignInModule
from .modules.shop import ShopModule
from .modules.shop import default_goods as _shop_default_goods  # noqa: F401
from .modules.bank import BankModule
from .modules.russian import RussianModule
from .modules.redbag import RedbagModule
from .modules.farm import FarmModule
from .modules.console_api import register_console_apis


class ZhenxunEconomyPlugin(Star):
    """真寻经济系统：发送「经济帮助」查看全部命令"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.sign_in = SignInModule(self, config)
        self.shop = ShopModule(self, config)
        self.bank = BankModule(self, config)
        self.russian = RussianModule(self, config)
        self.redbag = RedbagModule(self, config)
        self.farm = FarmModule(self, config)
        # (正则, 处理器) 按注册顺序匹配，长命令需先于短命令注册
        self._handlers: list[tuple[re.Pattern, callable]] = []
        self._register_commands()
        register_console_apis(self)  # WebUI 控制台页面后端 API

    async def initialize(self):
        await db.init()
        await GoodsRegistry.load_register()  # 商品入库 + 使用函数映射
        await self.farm.initialize()
        await self.bank.register_cron()
        logger.info("[zhenxun_economy] 真寻经济系统初始化完成")

    async def terminate(self):
        await db.close()
        logger.info("[zhenxun_economy] 真寻经济系统已卸载")

    # ---------- 命令注册 ----------

    def _register(self, pattern: str, handler, module: str | None = None):
        """注册命令；module 为所属模块名（对应配置 module_<name>_enabled 开关）"""
        self._handlers.append((re.compile(pattern), handler, module))

    def _module_enabled(self, module: str | None) -> bool:
        if not module:
            return True
        return bool(self.config.get(f"module_{module}_enabled", True))

    def _register_commands(self):
        si, sh, bk, ru, rb = (
            self.sign_in, self.shop, self.bank, self.russian, self.redbag
        )
        # 签到
        reg = lambda p, h, _m="sign_in": self._register(p, h, _m)  # noqa: E731
        reg(r"^我的签到$", lambda e, m: si.handle_my_sign(e))
        reg(
            r"^(?:好感度|签到)总排行\s*(\d*)$",
            lambda e, m: si.handle_impression_rank(e, _num(m, 10), True),
        )
        reg(
            r"^(?:好感度|签到)排行\s*(\d*)$",
            lambda e, m: si.handle_impression_rank(e, _num(m, 10), False),
        )
        reg(r"^签到$", lambda e, m: si.handle_sign(e))
        # 商店
        reg = lambda p, h, _m="shop": self._register(p, h, _m)  # noqa: E731
        reg(r"^商店$", lambda e, m: sh.handle_shop(e))
        reg(r"^我的金币$", lambda e, m: sh.handle_my_gold(e))
        reg(r"^我的道具$", lambda e, m: sh.handle_my_props(e))
        reg(
            r"^购买道具\s+(\S+)(?:\s+(\d+))?$",
            lambda e, m: sh.handle_buy(e, m.group(1), _int(m.group(2), 1)),
        )
        reg(
            r"^使用道具\s+(\S+)(?:\s+(\d+))?$",
            lambda e, m: sh.handle_use(e, m.group(1), _int(m.group(2), 1)),
        )
        reg(
            r"^金币总排行\s*(\d*)$",
            lambda e, m: sh.handle_gold_rank(e, _num(m, 10), True),
        )
        reg(
            r"^金币排行\s*(\d*)$",
            lambda e, m: sh.handle_gold_rank(e, _num(m, 10), False),
        )
        # 银行
        reg = lambda p, h, _m="bank": self._register(p, h, _m)  # noqa: E731
        reg(
            r"^存款(?:\s+(\d+))?$",
            lambda e, m: bk.handle_deposit(e, _int(m.group(1), None)),
        )
        reg(
            r"^取款(?:\s+(\d+))?$",
            lambda e, m: bk.handle_withdraw(e, _int(m.group(1), None)),
        )
        reg(r"^我的银行信息$", lambda e, m: bk.handle_user_info(e))
        reg(r"^银行信息$", lambda e, m: bk.handle_bank_info(e))
        # 俄罗斯轮盘
        reg = lambda p, h, _m="russian": self._register(p, h, _m)  # noqa: E731
        reg(
            r"^(?:装弹|俄罗斯轮盘|俄罗斯转盘)(?:\s+(\S+))?(?:\s+(\d+))?$",
            lambda e, m: ru.handle_load(e, m.group(1), _int(m.group(2), None)),
        )
        reg(
            r"^接受(?:对决|决斗|挑战)$", lambda e, m: ru.handle_accept(e)
        )
        reg(
            r"^拒绝(?:对决|决斗|挑战)$", lambda e, m: ru.handle_refuse(e)
        )
        reg(r"^(?:开枪|咔|嘭|嘣)$", lambda e, m: ru.handle_shoot(e))
        reg(r"^结算$", lambda e, m: ru.handle_settle(e))
        reg(r"^我的战绩$", lambda e, m: ru.handle_record(e))
        reg(
            r"^轮盘(胜场|败场|欧洲人|慈善家|最高连胜|最高连败)排行\s*(\d*)$",
            lambda e, m: ru.handle_rank(e, m.group(1), _num(m, 10, idx=2)),
        )
        # 金币红包
        reg = lambda p, h, _m="redbag": self._register(p, h, _m)  # noqa: E731
        reg(
            r"^(?:塞红包|金币红包)\s+(\d+)(?:\s+(\d+))?$",
            lambda e, m: rb.handle_send(e, int(m.group(1)), _int(m.group(2), 5)),
        )
        reg(r"^(?:开|抢|开红包|抢红包)$", lambda e, m: rb.handle_open(e))
        reg(r"^(?:退回红包|退还红包)$", lambda e, m: rb.handle_return(e))
        # 帮助
        self._register(r"^经济帮助$", lambda e, m: self.handle_help(e))

    # ---------- 统一分发 ----------

    @filter.event_message_type(EventMessageType.ALL)
    async def dispatch(self, event: AstrMessageEvent):
        text = event.get_message_str().strip()
        if not text:
            return
        for pattern, handler, module in self._handlers:
            if m := pattern.match(text):
                event.stop_event()
                if not self._module_enabled(module):
                    await event.send(
                        MessageChain(
                            [Comp.Plain("该功能模块已被管理员关闭了捏...")]
                        )
                    )
                    return
                await handler(event, m)
                return
        # 农场命令兜底分发
        if self._module_enabled("farm") and await self.farm.dispatch(event):
            event.stop_event()

    async def handle_help(self, event: AstrMessageEvent):
        await event.send(MessageChain([_help_text()]))


def _int(s: str | None, default):
    return int(s) if s else default


def _num(m: re.Match, default: int, idx: int = 1) -> int:
    return int(m.group(idx)) if m.group(idx) else default


def _help_text():
    from astrbot.api.message_components import Plain

    return Plain(
        "【真寻经济系统命令】\n"
        "签到 / 我的签到 / 好感度排行[N] / 好感度总排行[N]\n"
        "商店 / 我的金币 / 我的道具 / 购买道具 <名|序号> [数量] / 使用道具 <名|背包ID> [数量]\n"
        "金币排行[N] / 金币总排行[N]\n"
        "存款 [金额] / 取款 [金额] / 我的银行信息 / 银行信息\n"
        "装弹 [子弹数] [金额] [@对手] / 接受对决 / 拒绝对决 / 开枪 / 结算 / 我的战绩 / 轮盘胜场排行\n"
        "塞红包 <金额> [数量] [@指定人] / 开 / 退回红包\n"
        "农场：开通农场 / 我的农场 / 种子商店 / 购买种子 / 播种 / 收获 / 偷菜 / 购买农场币 ..."
    )
