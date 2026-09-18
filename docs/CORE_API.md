# 核心层 API 契约（modules/* 开发必读）

> 所有模块开发必须基于本契约调用 core 层，禁止直接操作 SQL（core 层之外）。
> import 方式：AstrBot 以 "data.plugins.<目录名>.main" 形式加载插件（star_manager.py 实证），因此 modules/xxx/ 内的正确相对导入是 `from ...core import ...`（上两级到插件根）；main.py 用 `from .core import ...`。推荐双写兼容 pytest 环境：
> ```python
> try:
>     from ...core import UserConsole   # AstrBot 运行时
> except ImportError:
>     from core import UserConsole      # pytest（sys.path 直挂插件根）
> ```

## 初始化

```python
from .core import db
await db.init()          # initialize() 中调用一次（自动建表，路径 = 插件数据目录/economy.db）
await db.close()         # terminate() 中调用
```

## UserConsole（金币/道具总账）

```python
from .core import UserConsole, InsufficientGold, GoodsNotFound, GoldHandle

user = await UserConsole.get_user(user_id: str, platform=None)  # -> UserRecord（不存在则创建，初始 gold=100）
# UserRecord: id, user_id, uid(展示编号), gold, props(dict[uuid,int]), platform, create_time

await UserConsole.add_gold(user_id, gold: int, source: str, platform=None)
# 加金币 + 写流水（handle=GET, source=来源模块名）

await UserConsole.reduce_gold(user_id, gold: int, handle: GoldHandle, source: str, platform=None)
# 扣金币，不足抛 InsufficientGold；写流水（handle 用 GoldHandle.PLUGIN / GoldHandle.BUY）

gold = await UserConsole.get_gold(user_id)  # 快捷查余额

await UserConsole.add_props(user_id, goods_uuid, num=1)       # 加道具（uuid 维度）
await UserConsole.use_props(user_id, goods_uuid, num=1)       # 扣道具，不足抛 GoodsNotFound
await UserConsole.add_props_by_name(user_id, "好感度双倍加持卡Ⅰ", 1)  # 按商品名
await UserConsole.use_props_by_name(user_id, "好感度双倍加持卡Ⅰ", 1)
```

## GoodsInfo + 商品注册（商店依赖）

```python
from .core import GoodsInfo, goods_register, GoodsRegistry, NotMeetUseConditionsException

# 注册商品（模块导入时装饰器收集，initialize 时统一入库）
@goods_register(name="好感度双倍加持卡Ⅰ", price=30, des="下次签到双倍概率+10%", icon="favorability_card_1.png", 好感度双倍加持卡Ⅰ_prob=0.1)
async def use_card(user_id: str, prob: float, **kwargs): ...   # 参数按签名名注入

await GoodsRegistry.load_register()   # main.py initialize() 中，在所有模块 import 之后调用
entry = GoodsRegistry.get_entry(uuid) # -> GoodsEntry(name, uuid, func, send_success_msg, max_num_limit, before_handles, after_handles, kwargs)

goods = await GoodsInfo.get_by_name("神秘药水")  # -> dict | None（含 uuid/goods_price/goods_discount/daily_limit/is_passive/partition/icon）
on_sale = await GoodsInfo.get_on_sale()        # 在售商品列表（按 id 升序）
count = await GoodsInfo.daily_buy_count(user_id, uuid)  # 当日已购次数（限购判断）
```

使用道具的调用约定：`func` 可用注入参数名：`user_id, group_id, event, num, text, at_user, at_users, goods_name, bot_name` + 注册时 `{商品名}_参数` 剥前缀后的 kwargs。

## SignUser（签到/好感度）

```python
from .core import SignUser, get_level_and_next_impression, lik2relation, level2attitude

su = await SignUser.get_user(user_id, platform=None)
# SignUserRecord: sign_count, impression(好感度), add_probability, specify_probability
signed = await SignUser.today_signed(user_id)          # 今天是否已签到
su = await SignUser.sign(user_id, impression=0.52, bot_id=None, platform=None)
# 好感度 += impression；add/specify_probability 清零；sign_count+1；写 sign_log
await SignUser.set_probability(user_id, add_probability=0.1)   # 加持卡效果
imp = await SignUser.get_impression(user_id)
level, next_imp, cur_start = get_level_and_next_impression(imp)  # 等级/下一级/本级起点
rank = await SignUser.rank_by_impression(limit=10, user_ids=None)  # user_ids 传入则群内排行
```

## MahiroBank（银行）

```python
from .core import MahiroBank, BankHandleType

acc = await MahiroBank.get_user(user_id)      # -> BankRecord(amount 存款, rate, loan_amount, loan_rate)
await MahiroBank.deposit(user_id, amount, rate)   # 只动银行账户！金币划转由业务层做
await MahiroBank.withdraw(user_id, amount)        # 超额抛 ValueError
n = await MahiroBank.today_deposit_count(user_id) # 当日存款笔数
locked = await MahiroBank.locked_amount(user_id)  # 当日锁定（不可取）金额
# 贷款/还款：MahiroBank.loan / repayment 存在但命令层不开放（与真寻一致）
```

结息所需原始查询可直接用 `db.fetchall/fetchval`（mahiro_bank_log 表，handle_type='DEPOSIT'/'INTEREST'，is_completed 标记）。

## RussianUser / RedbagUser（战绩统计）

```python
from .core import RussianUser, RedbagUser
rec = await RussianUser.get_user(user_id, group_id)
await RussianUser.add_count(user_id, group_id, "win")      # 或 "lose"，自动维护连胜连败
await RussianUser.add_money(user_id, group_id, "win", 500)
top = await RussianUser.rank(group_id, "win_count", 10)    # 降序排行
await RedbagUser.add_redbag_data(user_id, group_id, "send"|"get", money)
```

## platform_utils（平台适配）

```python
from ..core import platform_utils as pu
pu.is_aiocqhttp(event)
uids = pu.get_at_user_ids(event)                    # 消息中 at 的目标
name = await pu.get_user_name(event, user_id)       # 群名片/昵称兜底
members = await pu.get_group_user_ids(event)        # 群成员 id 列表（排行过滤）
avatar_b64 = await pu.get_avatar_b64(user_id)       # QQ 头像 data URI（HTML 模板用）
```

## AstrBot 侧约定

- 回复：handler 中 `yield event.plain_result(...)` / `event.chain_result([Comp.At(...), Comp.Plain(...)])`；图片 `Comp.Image.fromURL(url)`
- HTML 渲染：`url = await self.html_render(jinja2_str, data_dict, return_url=True)`；模板内引用资源一律用 base64 data URI（远程端点访问不到本地文件）；渲染失败要 try/except 降级为纯文本
- 交互会话：`from astrbot.api.util import SessionController, session_waiter`，群级会话需自定义 SessionFilter 返回 `event.get_group_id()`；waiter 内回复用 `await event.send(MessageChain([...]))`，续命 `controller.keep(timeout=30, reset_timeout=True)`
- 定时任务：`await self.context.cron_manager.add_basic_job(name=..., cron_expression="0 0 * * *", handler=..., timezone="Asia/Shanghai")`
- 命令：`@filter.command("签到", alias={"打卡"})`，参数按类型注解自动解析；不受唤醒前缀约束的全匹配用 `@filter.regex(r"^签到$")` 或 event_message_type 自行分发
- 配置：`self.config.get("max_sign_gold", 200)`，键名见 _conf_schema.json
- 所有模块的错误提示语气保持真寻风格（句尾"捏/哦/~"）
