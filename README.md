# 真寻经济系统（astrbot_plugin_zhenxun_economy）

将 [zhenxun_bot](https://github.com/zhenxun-org/zhenxun_bot)（真寻 bot）的经济生态完整移植到 AstrBot 的大型插件。AstrBot 没有为插件提供基础货币系统，本插件即填补这一空白：**为所有插件提供统一的金币 / 好感度 / 道具基础设施**，并内置六大互通模块。

## 功能模块

| 模块 | 命令（均为裸中文触发） | 说明 |
|---|---|---|
| 签到 | `签到` `我的签到` `好感度排行[N]` `好感度总排行[N]` | 每日签到得金币与好感度，随机事件掉双倍加持卡 |
| 商店 | `商店` `我的金币` `我的道具` `购买道具` `使用道具` `金币排行` | 商品购买/使用，道具效果注册机制开放给其他插件 |
| 银行 | `存款` `取款` `我的银行信息` `银行信息` | 小时利率随机，每日 00:00 结息，存款上限挂钩好感度 |
| 俄罗斯轮盘 | `装弹` `接受对决` `拒绝对决` `开枪` `结算` `我的战绩` `轮盘xx排行` | 7 格弹巢 1-6 发子弹，4 种随机武器，随机 0-5% 手续费 |
| 金币红包 | `塞红包 <金额> [数量] [@人]` `开` `退回红包` | 随机权重分配，可定向，过期自动退回 |
| 真寻农场 | `开通农场` `我的农场` `种子商店` `播种` `收获` `偷菜` `购买农场币` 等 | 内嵌 [astrbot_plugin_farm](https://github.com/Shu-Ying/astrbot_plugin_farm)（GPLv3，作者 Shu-Ying），农场币可用金币兑换 |

发送 `经济帮助` 查看命令速查。

## 经济核心（供其他插件调用）

所有模块共用同一套账本（SQLite，表结构与真寻完全一致）：

```python
md = context.get_registered_star("astrbot_plugin_zhenxun_economy")
eco = md.star_cls
from astrbot_plugin_zhenxun_economy.core import UserConsole, GoldHandle, SignUser

await UserConsole.add_gold(user_id, 100, "my_plugin")                # 发金币
await UserConsole.reduce_gold(user_id, 50, GoldHandle.PLUGIN, "my_plugin")  # 收金币（不足抛 InsufficientGold）
imp = await SignUser.get_impression(user_id)                          # 查好感度
```

道具注册机制（其他插件可上架商品）：

```python
from astrbot_plugin_zhenxun_economy.core import goods_register

@goods_register(name="我的道具", price=100, des="示例")
async def use_it(user_id: str, **kwargs):
    return "使用成功！"
```

## 数据迁移（从真寻 bot 迁移）

插件自带独立迁移脚本，一次性把真寻数据库导入本插件：

```bash
# --target-dir 省略时自动定位 AstrBot 插件数据目录：
#   $ASTRBOT_ROOT/data/plugin_data/astrbot_plugin_zhenxun_economy
#   桌面版 AstrBot：~/.astrbot/data/plugin_data/astrbot_plugin_zhenxun_economy
#   在 AstrBot 根目录下运行：./data/plugin_data/astrbot_plugin_zhenxun_economy
python migrate_zhenxun.py \
    --zhenxun-db /path/to/zhenxun/data/db/zhenxun.db \
    --farm-db /path/to/zhenxun/data/farm_db/farm.db
```

导入位置即插件运行时读取路径：`economy.db`（经济）与 `farm_db/farm.db`（农场）；也可用 `--target-dir` 显式指定其他目录。

- 迁移内容：用户金币/道具、金币流水、签到与好感度、商品表、银行账户与流水、轮盘战绩、红包统计、农场全部 9 张表
- 幂等：可重复执行，已存在的数据自动跳过
- 迁移前自动备份目标库（`*.bak.时间戳`），源库只读打开
- `--verify` 仅校验不写入

## 安装

1. 插件目录放入 AstrBot 的 `data/plugins/` 下
2. `pip install -r requirements.txt`（aiosqlite / Pillow / aiohttp）
3. 重启 AstrBot，在 WebUI 插件配置中调整经济参数（签到掉率、银行利率、轮盘上限、红包超时、农场兑换比例等）
4. 可选：执行上方迁移脚本导入真寻数据

## Web 控制台（真寻风格插件面板）

插件提供 AstrBot 插件页面：WebUI → 插件管理 → 真寻经济系统 → 打开「console」页面。

- 六个模块的开关卡片（签到/商店/银行/轮盘/红包/农场），风格复刻真寻 WebUI 插件面板
- 每张卡片可进入配置抽屉，按模块分组调整数值参数（掉率/利率/上限/超时/兑换比例等），保存即时生效
- 顶部统计概览：总用户数、总金币、今日签到数、银行总存款

## 配置

所有经济数值均可在 AstrBot WebUI 调整（见 `_conf_schema.json`）：签到掉率与金币上限、银行利率区间/存款上限/每日次数、轮盘赌注上限、红包超时与冷却、农场币兑换倍数与手续费、HTML 卡片渲染开关等。

## 开发与测试

```bash
uv venv .venv && uv pip install -r requirements.txt pytest pytest-asyncio
.venv/Scripts/python -m pytest tests/ -q   # 79 项单元/集成/装配测试
```

测试覆盖：core 经济核心（7）、签到（8）、商店（15）、银行（14）、轮盘（10）、红包（7）、农场（7）、迁移脚本（7）、主入口装配与命令路由（1+）。

## 许可证

本插件移植/改编自 zhenxun_bot（AGPLv3）与 astrbot_plugin_farm（GPLv3，作者 Shu-Ying），整体以 **AGPLv3** 发布。farm 模块保留原作者署名。
