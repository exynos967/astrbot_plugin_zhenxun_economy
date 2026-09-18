"""默认商品注册（对应真寻 sign_in/goods_register.py 与 shop/goods_register.py）

import 本模块即通过 goods_register 装饰器收集注册信息；
main.py initialize() 中调用 GoodsRegistry.load_register() 统一入库。
"""

try:  # AstrBot 运行时：插件根目录是 data.plugins.<dirname> 包
    from ...core import SignUser, UserConsole, goods_register
except ImportError:  # 测试环境：插件根目录直接在 sys.path
    from core import SignUser, UserConsole, goods_register


async def _use_favorability_card(user_id: str, prob: float):
    """好感度双倍加持卡：设置下次签到双倍好感度概率（同类商品覆盖）"""
    await SignUser.set_probability(user_id, add_probability=prob)


# prob 通过 "{商品名}_prob" 前缀参数传入（注册时剥前缀为 prob 注入使用函数）
goods_register(
    name="好感度双倍加持卡Ⅰ",
    price=30,
    des="下次签到双倍好感度概率 + 10%（谁才是真命天子？）（同类商品将覆盖）",
    icon="favorability_card_1.png",
    # 注意：必须用 **{...} 字典展开传 prob——直接写关键字参数会被 Python
    # 标识符 NFKC 规范化（Ⅰ→I），导致 "{商品名}_prob" 前缀匹配失败
    **{"好感度双倍加持卡Ⅰ_prob": 0.1},  # type: ignore
)(_use_favorability_card)

goods_register(
    name="好感度双倍加持卡Ⅱ",
    price=150,
    des="下次签到双倍好感度概率 + 20%（平平庸庸）（同类商品将覆盖）",
    icon="favorability_card_2.png",
    **{"好感度双倍加持卡Ⅱ_prob": 0.2},  # type: ignore
)(_use_favorability_card)

goods_register(
    name="好感度双倍加持卡Ⅲ",
    price=250,
    des="下次签到双倍好感度概率 + 30%（金币才是真命天子！）（同类商品将覆盖）",
    icon="favorability_card_3.png",
    **{"好感度双倍加持卡Ⅲ_prob": 0.3},  # type: ignore
)(_use_favorability_card)


@goods_register(
    name="神秘药水",
    price=999999,
    des="鬼知道会有什么效果，要不试试？",
    partition="小秘密",
    icon="mysterious_potion.png",
)
async def _use_mysterious_potion(user_id: str):
    """神秘药水：金币+1000000（与真寻一致）"""
    await UserConsole.add_gold(user_id, 1000000, "shop")
    return "使用道具神秘药水成功！你滴金币+1000000！"
