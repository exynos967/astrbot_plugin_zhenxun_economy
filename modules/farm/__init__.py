"""真寻农场子模块（内嵌自开源插件 astrbot_plugin_farm）

出处: https://github.com/Shu-Ying/astrbot_plugin_farm
原作者: Shu-Ying    许可证: GPLv3（见本目录 LICENSE）

改造说明:
- 原插件为独立 AstrBot 插件（main.py 注册 Star 入口），此处改为大插件
  astrbot_plugin_zhenxun_economy 的子模块，由 main.py 统一分发命令。
- 用户数据 farm.db 移至本插件数据目录（cfg.py 中改造）。
- 新增「购买农场币」命令，打通 core 层金币 <-> 农场币（对齐真寻版
  buyPointByUid：fee=floor(num*tax)，扣 num+fee 金币，到账 num*rate 农场币）。
"""

import asyncio
import inspect
import math
import re
from typing import List

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Node
from astrbot.core.utils.session_waiter import (
    SessionController,
    session_waiter,
)

from . import cfg
from .database.database import g_pSqlManager
from .dbService import g_pDBService
from .farm.farm import g_pFarmManager
from .farm.shop import g_pShopManager
from .json import g_pJsonManager
from .request import g_pRequestManager
from .tool import g_pToolManager

try:
    # 插件正常运行时：modules.farm 的上上级即插件根包
    from ...core import GoldHandle, InsufficientGold, UserConsole
except ImportError:  # 测试环境：插件根目录直接位于 sys.path
    from core import GoldHandle, InsufficientGold, UserConsole


class FarmModule:
    """真寻农场子模块：构造函数接收 (plugin 实例, config dict)"""

    def __init__(self, plugin, config: dict):
        self.plugin = plugin
        self.config = config

        # 沿用原插件的可配置项（本大插件 _conf_schema.json 暂未收录时使用默认值）
        cfg.g_pConfigManager.sFarmDrawQuality = str(
            config.get("farm_draw_quality", "low")
        )
        cfg.g_pConfigManager.sFarmServerUrl = str(
            config.get("farm_server_url", "http://diuse.work")
        )
        cfg.g_pConfigManager.sFarmPrefix = str(config.get("farm_prefix", ""))

        self.commands = {
            "开通农场": self.registerFarm,
            "我的农场": self.myFarm,
            "农场详述": self.detail,
            "我的农场币": self.myPoint,
            "购买农场币": self.buyPoint,
            "种子商店": self.seedShop,
            "购买种子": self.buySeed,
            "我的种子": self.mySeed,
            "播种": self.sowing,
            "收获": self.harvest,
            "铲除": self.eradicate,
            "我的作物": self.myPlant,
            "开垦": self.reclamation,
            "出售作物": self.sellPlant,
            "偷菜": self.stealing,
            "更改农场名": self.changeName,
            "农场签到": self.signIn,
            "农场下阶段": self.god,
            "土地升级": self.soilUpgrade,
        }

    async def initialize(self):
        """初始化数据库 / Json / 作物资源（由 main.py 调用一次）"""
        # 初始化数据库
        await g_pSqlManager.init()

        # 初始化读取Json
        await g_pJsonManager.init()

        await g_pDBService.init()

        # 检查作物文件是否缺失 or 更新
        await g_pRequestManager.initPlantDBFile()

    async def terminate(self):
        """插件卸载时清理数据库连接"""
        await g_pSqlManager.cleanup()
        await g_pDBService.cleanup()

    async def dispatch(self, event: AstrMessageEvent) -> bool:
        """农场命令分发（移植自原插件 farmHandle）

        Returns:
            bool: 命中农场命令返回 True（并已 stop_event），未命中返回 False
        """
        prefix = cfg.g_pConfigManager.sFarmPrefix

        # 前缀模式
        if prefix:
            chain = event.get_messages()
            if not chain:
                return False

            first = chain[0]
            # 前缀触发
            if isinstance(first, Comp.Plain):
                if not first.text.startswith(prefix):
                    return False
            elif isinstance(first, Comp.Reply) and len(chain) > 1:
                second_seg = chain[1]
                if isinstance(
                    second_seg, Comp.Plain
                ) and not second_seg.text.startswith(prefix):
                    return False
            # @bot触发
            elif isinstance(first, Comp.At):
                if str(first.qq) != str(event.get_self_id()):
                    return False
            else:
                return False

        message = event.get_message_str().removeprefix(prefix)

        if not message:
            return False

        pattern = r"(\S+)\s*(.*)"
        match = re.match(pattern, message)

        if not match:
            return False

        cmd = match.group(1)
        args = match.group(2)

        # 解析参数，按空格分割
        if args:
            params = re.split(r"\s+", args)
        else:
            params = []

        if cmd not in self.commands:
            return False

        event.stop_event()

        cmdFunc = self.commands[cmd]

        try:
            if inspect.isasyncgenfunction(cmdFunc):
                agen = cmdFunc(event, params)
                async for piece in agen:
                    # piece 为 MessageEventResult（MessageChain 子类），直接发送
                    if piece is not None:
                        await event.send(piece)
            elif asyncio.iscoroutinefunction(cmdFunc):
                await cmdFunc(event, params)
            else:
                cmdFunc(event, params)
        except Exception as e:
            logger.exception(f"执行农场命令 {cmd} 时出错：{e}")
            await event.send(
                MessageChain([Comp.Plain(f"农场命令执行出错了捏... {e}")])
            )

        return True

    async def registerFarm(self, event: AstrMessageEvent, params: List[str]):
        """开通农场"""
        if not event.is_at_or_wake_command:
            return

        uid = event.get_sender_id()
        name = event.get_sender_name()

        user = await g_pDBService.user.getUserInfoByUid(uid)

        if user:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["register"]["repeat"]),
            ]

            yield event.chain_result(chain)
            return

        try:
            safe_name = g_pToolManager.sanitize_username(name)

            # 初始化用户信息
            success = await g_pDBService.user.initUserInfoByUid(
                uid=uid, name=safe_name, exp=0, point=500
            )

            msg = (
                cfg.g_pConfigManager.sTranslation["register"]["success"].format(
                    point=500
                )
                if success
                else cfg.g_pConfigManager.sTranslation["register"]["error"]
            )
            logger.info(f"用户注册 {'成功' if success else '失败'}：{uid}")
        except Exception as e:
            msg = cfg.g_pConfigManager.sTranslation["register"]["error"]
            logger.error(f"注册异常 | UID:{uid} | 错误：{e}")

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(f"{msg}"),
        ]

        yield event.chain_result(chain)

    async def myFarm(self, event: AstrMessageEvent, params: List[str]):
        """我的农场"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        image = await g_pFarmManager.drawFarmByUid(uid)

        chain = [
            Comp.At(qq=uid),
            Comp.Image.fromBase64(image),
        ]

        yield event.chain_result(chain)

    async def detail(self, event: AstrMessageEvent, params: List[str]):
        """农场详述"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        images = await g_pFarmManager.drawDetailFarmByUid(uid)

        node = Node(
            uin=event.message_obj.self_id,
            content=[Image.fromBase64(img) for img in images],
        )
        yield event.chain_result([node])

    async def myPoint(self, event: AstrMessageEvent, params: List[str]):
        """农场币"""
        uid = event.get_sender_id()
        point = await g_pDBService.user.getUserPointByUid(uid)

        if point < 0:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(f"你的当前农场币为: {point}"),
        ]

        yield event.chain_result(chain)

    async def buyPoint(self, event: AstrMessageEvent, params: List[str]):
        """购买农场币（新增：金币 -> 农场币兑换桥，数值对齐真寻版 buyPointByUid）"""
        uid = event.get_sender_id()

        if not params:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain("请在指令后跟需要购买农场币的数量捏~"),
            ]

            yield event.chain_result(chain)
            return

        try:
            num = int(params[0])
        except (ValueError, TypeError):
            num = 0

        if num <= 0:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain("你是怎么做到购买不是正数的农场币的捏？"),
            ]

            yield event.chain_result(chain)
            return

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        # 兑换倍数与手续费（对齐真寻版「兑换倍数」「手续费」配置）
        rate = float(self.config.get("farm_exchange_rate", 1))
        tax = float(self.config.get("farm_exchange_tax", 0.1))

        # 计算手续费
        fee = math.floor(num * tax)
        # 实际扣费金额
        deduction = num + fee

        gold = await UserConsole.get_gold(uid)
        if gold < deduction:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(
                    f"你的金币不足或不足承担手续费捏~ 需要{deduction}金币"
                    f"（含手续费{fee}），当前金币：{gold}"
                ),
            ]

            yield event.chain_result(chain)
            return

        try:
            await UserConsole.reduce_gold(
                uid, num, GoldHandle.PLUGIN, "zhenxun_farm"
            )
            if fee > 0:
                await UserConsole.reduce_gold(
                    uid, fee, GoldHandle.PLUGIN, "zhenxun_farm"
                )
        except InsufficientGold:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain("你的金币不足或不足承担手续费捏~"),
            ]

            yield event.chain_result(chain)
            return

        # 到账农场币 = 数量 x 兑换倍数
        point = int(num * rate)

        p = await g_pDBService.user.getUserPointByUid(uid)
        number = int(point + p)

        await g_pDBService.user.updateUserPointByUid(uid, number)

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(
                f"充值{point}农场币成功捏~ 手续费{fee}金币，当前农场币：{number}"
            ),
        ]

        yield event.chain_result(chain)

    async def seedShop(self, event: AstrMessageEvent, params: List[str]):
        """种子商店"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        filterKey: str | int | None = None
        page: int = 1

        if len(params) >= 1 and params[0] is not None:
            first = params[0]
            if isinstance(first, str) and first.isdigit():
                page = int(first)
            else:
                filterKey = first

        if (
            len(params) >= 2
            and params[1] is not None
            and isinstance(params[1], str)
            and params[1].isdigit()
        ):
            page = int(params[1])

        if filterKey is None:
            image = await g_pShopManager.getSeedShopImage(page)
        else:
            image = await g_pShopManager.getSeedShopImage(filterKey, page)

        chain = [
            Comp.At(qq=uid),
            Comp.Image.fromBase64(image),
        ]

        yield event.chain_result(chain)

    async def buySeed(self, event: AstrMessageEvent, params: List[str]):
        """购买种子"""
        uid = event.get_sender_id()

        if not params:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["buySeed"]["notSeed"]),
            ]

            yield event.chain_result(chain)
            return

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        seedName = params[0]
        count = None
        if len(params) > 1:
            try:
                count = int(params[1])
            except (ValueError, TypeError):
                count = None

        if count is not None:
            result = await g_pShopManager.buySeed(uid, seedName, count)
        else:
            result = await g_pShopManager.buySeed(uid, seedName)

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(result),
        ]

        yield event.chain_result(chain)

    async def mySeed(self, event: AstrMessageEvent, params: List[str]):
        """我的种子"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        result = await g_pFarmManager.getUserSeedByUid(uid)

        chain = [
            Comp.At(qq=uid),
            Comp.Image.fromBase64(result),
        ]

        yield event.chain_result(chain)

    async def sowing(self, event: AstrMessageEvent, params: List[str]):
        """播种"""
        uid = event.get_sender_id()

        if not params:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["sowing"]["notSeed"]),
            ]

            yield event.chain_result(chain)
            return

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        seedName = params[0]
        count = None
        if len(params) > 1:
            try:
                count = int(params[1])
            except (ValueError, TypeError):
                count = None

        if count is not None:
            result = await g_pFarmManager.sowing(uid, seedName, count)
        else:
            result = await g_pFarmManager.sowing(uid, seedName)

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(result),
        ]

        yield event.chain_result(chain)

    async def harvest(self, event: AstrMessageEvent, params: List[str]):
        """收获"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        result = await g_pFarmManager.harvest(uid)

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(result),
        ]

        yield event.chain_result(chain)

    async def eradicate(self, event: AstrMessageEvent, params: List[str]):
        """铲除"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        result = await g_pFarmManager.eradicate(uid)

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(result),
        ]

        yield event.chain_result(chain)

    async def myPlant(self, event: AstrMessageEvent, params: List[str]):
        """我的作物"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        result = await g_pFarmManager.getUserPlantByUid(uid)

        chain = [
            Comp.At(qq=uid),
            Comp.Image.fromBase64(result),
        ]

        yield event.chain_result(chain)

    async def reclamation(self, event: AstrMessageEvent, params: List[str]):
        """开垦"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        try:
            condition = await g_pFarmManager.reclamationCondition(uid)
            condition += (
                f"\n{cfg.g_pConfigManager.sTranslation['reclamation']['confirm']}"
            )

            chain = [
                Comp.At(qq=uid),
                Comp.Plain(condition),
            ]

            yield event.chain_result(chain)

            @session_waiter(timeout=60, record_history_chains=False)
            async def check(controller: SessionController, event: AstrMessageEvent):
                if not event.message_str == "是":
                    controller.stop()
                    return

                res = await g_pFarmManager.reclamation(uid)

                message = event.make_result()
                message.chain = [
                    Comp.At(qq=uid),
                    Comp.Plain(res),
                ]
                await event.send(message)

                controller.stop()

            try:
                await check(event)
            except TimeoutError as _:  # 当超时后，会话控制器会抛出 TimeoutError
                yield event.plain_result(
                    cfg.g_pConfigManager.sTranslation["reclamation"]["timeOut"]
                )
            except Exception as e:
                yield event.plain_result(
                    cfg.g_pConfigManager.sTranslation["reclamation"]["error1"].format(
                        e=e
                    )
                )
            finally:
                event.stop_event()
        except Exception as e:
            logger.error(
                cfg.g_pConfigManager.sTranslation["reclamation"]["error2"].format(e=e)
            )

    async def sellPlant(self, event: AstrMessageEvent, params: List[str]):
        """出售作物"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        if params:
            seedName = params[0]
        else:
            seedName = ""

        count = None
        if len(params) > 1:
            try:
                count = int(params[1])
            except (ValueError, TypeError):
                count = None

        if count is not None:
            result = await g_pShopManager.sellPlantByUid(uid, seedName, count)
        else:
            result = await g_pShopManager.sellPlantByUid(uid, seedName)

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(result),
        ]

        yield event.chain_result(chain)

    async def stealing(self, event: AstrMessageEvent, params: List[str]):
        """偷菜"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        targetList = []

        for comp in event.message_obj.message:
            if isinstance(comp, Comp.At):
                if isinstance(comp.qq, int):
                    targetList.append(str(comp.qq))

        if not targetList:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["stealing"]["noTarget"]),
            ]

            yield event.chain_result(chain)
            return

        # 只处理第一个用户
        exist = await g_pDBService.user.isUserExist(targetList[0])
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(
                    cfg.g_pConfigManager.sTranslation["stealing"]["targetNotFarm"]
                ),
            ]

            yield event.chain_result(chain)
            return

        result = await g_pFarmManager.stealing(uid, targetList[0])

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(result),
        ]

        yield event.chain_result(chain)

    async def changeName(self, event: AstrMessageEvent, params: List[str]):
        """更改农场名"""
        uid = event.get_sender_id()

        if not params:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["changeName"]["noName"]),
            ]

            yield event.chain_result(chain)
            return

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        safeName = g_pToolManager.sanitize_username(params[0])

        if safeName == "神秘农夫":
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["changeName"]["error"]),
            ]

            yield event.chain_result(chain)
            return

        result = await g_pDBService.user.updateUserNameByUid(uid, safeName)

        if result:
            message = cfg.g_pConfigManager.sTranslation["changeName"]["success"]
        else:
            message = cfg.g_pConfigManager.sTranslation["changeName"]["error1"]

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(message),
        ]

        yield event.chain_result(chain)

    async def signIn(self, event: AstrMessageEvent, params: List[str]):
        """农场签到"""
        uid = event.get_sender_id()

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        if not cfg.g_pConfigManager.bSignStatus:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["signIn"]["error"]),
            ]

            yield event.chain_result(chain)
            return

        toDay = g_pToolManager.dateTime().date().today()
        message = ""
        status = await g_pDBService.userSign.sign(uid, toDay.strftime("%Y-%m-%d"))

        # 如果完成签到
        if status == 1 or status == 2:
            # 获取签到总天数
            signDay = await g_pDBService.userSign.getUserSignCountByDate(
                uid, toDay.strftime("%Y-%m")
            )
            exp, point = await g_pDBService.userSign.getUserSignRewardByDate(
                uid, toDay.strftime("%Y-%m-%d")
            )

            message += cfg.g_pConfigManager.sTranslation["signIn"]["success"].format(
                day=signDay, exp=exp, num=point
            )

            reward = g_pJsonManager.m_pSign["continuou"].get(f"{signDay}", None)

            if reward:
                extraPoint = reward.get("point", 0)
                extraExp = reward.get("exp", 0)

                plant = reward.get("plant", {})

                message += cfg.g_pConfigManager.sTranslation["signIn"][
                    "grandTotal"
                ].format(exp=extraExp, num=extraPoint)

                vipPoint = reward.get("vipPoint", 0)

                if vipPoint > 0:
                    message += cfg.g_pConfigManager.sTranslation["signIn"][
                        "grandTotal1"
                    ].format(num=vipPoint)

                if plant:
                    for key, value in plant.items():
                        message += cfg.g_pConfigManager.sTranslation["signIn"][
                            "grandTotal2"
                        ].format(name=key, num=value)
        else:
            message = "签到失败！未知错误"

        chain = [
            Comp.At(qq=uid),
            Comp.Plain(message),
        ]

        yield event.chain_result(chain)

    async def god(self, event: AstrMessageEvent, params: List[str]):
        """农场下阶段"""  # 非常规手段才可以进该函数 故不做判断
        uid = event.get_sender_id()

        await g_pDBService.userSoil.nextPhase(uid, int(params[0]))

    async def soilUpgrade(self, event: AstrMessageEvent, params: List[str]):
        """土地升级"""
        uid = event.get_sender_id()

        if not params:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["soilInfo"]["noSoil"]),
            ]

            yield event.chain_result(chain)
            return

        exist = await g_pDBService.user.isUserExist(uid)
        if not exist:
            chain = [
                Comp.At(qq=uid),
                Comp.Plain(cfg.g_pConfigManager.sTranslation["basic"]["notFarm"]),
            ]

            yield event.chain_result(chain)
            return

        try:
            soilIndex = int(params[0])
            condition = await g_pFarmManager.soilUpgradeCondition(uid, soilIndex)

            chain = [
                Comp.At(qq=uid),
                Comp.Plain(condition),
            ]

            yield event.chain_result(chain)

            if not condition.startswith("将土地升级至："):
                return

            @session_waiter(timeout=60, record_history_chains=False)
            async def check(controller: SessionController, event: AstrMessageEvent):
                if not event.message_str == "是":
                    controller.stop()
                    return

                res = await g_pFarmManager.soilUpgrade(uid, soilIndex)

                message = event.make_result()
                message.chain = [
                    Comp.At(qq=uid),
                    Comp.Plain(res),
                ]
                await event.send(message)

                controller.stop()

            try:
                await check(event)
            except TimeoutError as _:  # 当超时后，会话控制器会抛出 TimeoutError
                yield event.plain_result(
                    cfg.g_pConfigManager.sTranslation["soilInfo"]["timeOut"]
                )
            except Exception as e:
                yield event.plain_result(
                    cfg.g_pConfigManager.sTranslation["soilInfo"]["error"].format(e=e)
                )
            finally:
                event.stop_event()
        except Exception as e:
            logger.error(
                cfg.g_pConfigManager.sTranslation["soilInfo"]["error2"].format(e=e)
            )
