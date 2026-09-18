"""小真寻银行模块（对应真寻 mahiro_bank 插件）。

命令 handler（main.py 中的 Star 方法）调用本模块的 handle_* 方法；
每日 0 点结息由 register_cron() 注册到 AstrBot cron_manager。
金币划转走 UserConsole，银行账户变动走 MahiroBank，本模块不直接写 SQL
（结息/信息查询等 core 未覆盖的读取使用 core.db.fetchall，契约允许）。
"""

import random
from datetime import datetime, timedelta

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
import astrbot.api.message_components as Comp

try:  # 插件运行时（作为插件包加载，相对导入上两级到插件根的 core）
    from ...core import (
        BankHandleType,
        GoldHandle,
        MahiroBank,
        SignUser,
        UserConsole,
    )
    from ...core import platform_utils as pu
    from ...core.database import db, now_str
except ImportError:  # 单元测试环境（插件根目录直接在 sys.path，core 为顶层包）
    from core import (
        BankHandleType,
        GoldHandle,
        MahiroBank,
        SignUser,
        UserConsole,
    )
    from core import platform_utils as pu
    from core.database import db, now_str

from . import render

# 存款金额追问：超时秒数 / 最多错误次数
_ASK_TIMEOUT = 60
_ASK_MAX_RETRY = 3


class BankModule:
    """小真寻银行业务模块"""

    def __init__(self, plugin, config: dict) -> None:
        self.plugin = plugin
        self.config = config or {}

    def _cfg(self, key: str, default):
        return self.config.get(key, default)

    # ---------------- 通用回复 ----------------

    @staticmethod
    async def _reply(event: AstrMessageEvent, text: str, at: bool = False) -> None:
        chain = []
        if at and pu.is_aiocqhttp(event):
            chain.append(Comp.At(qq=event.get_sender_id(), name=event.get_sender_name()))
        chain.append(Comp.Plain(text))
        await event.send(MessageChain(chain))

    # ---------------- 金额追问（session_waiter） ----------------

    async def _ask_amount(self, event: AstrMessageEvent, action: str) -> int | None:
        """追问金币数量。返回 None 表示超时/取消。纯数字校验，最多错 3 次"""
        from astrbot.api.util import SessionController, session_waiter

        result: dict = {"amount": None}
        wrong = {"count": 0}

        @session_waiter(timeout=_ASK_TIMEOUT)
        async def waiter(controller: SessionController, evt: AstrMessageEvent):
            text = evt.message_str.strip()
            if text.isdigit():
                result["amount"] = int(text)
                controller.stop()
                return
            wrong["count"] += 1
            left = _ASK_MAX_RETRY - wrong["count"]
            if left <= 0:
                await evt.send(
                    MessageChain([Comp.Plain("错误次数太多啦，小真寻柜员已取消本次操作...")])
                )
                controller.stop()
                return
            await evt.send(
                MessageChain([Comp.Plain(f"输入错误，请输入数字。剩余次数：{left}")])
            )
            controller.keep(timeout=_ASK_TIMEOUT, reset_timeout=True)

        await event.send(MessageChain([Comp.Plain(f"请输入{action}金币数量")]))
        try:
            await waiter(event)
        except TimeoutError:
            await event.send(
                MessageChain([Comp.Plain(f"输入超时了哦，小真寻柜员已取消本次{action}操作...")])
            )
            return None
        return result["amount"]

    # ---------------- 存款 ----------------

    def _random_event(self, impression: float) -> float | None:
        """好感度加息事件：达到门槛且命中概率时返回额外小时利率"""
        if impression >= self._cfg("bank_impression_event", 25) and random.random() < self._cfg(
            "bank_impression_event_prop", 0.3
        ):
            return random.uniform(
                self._cfg("bank_impression_event_rate_min", 0.00001),
                self._cfg("bank_impression_event_rate_max", 0.0003),
            )
        return None

    async def _deposit_check(self, user_id: str, amount: int) -> str | None:
        """存款校验链，返回 None 表示通过，否则为提示文案"""
        if amount <= 0:
            return "存款数量必须大于 0 啊笨蛋！"
        user = await UserConsole.get_user(user_id)
        sign_user = await SignUser.get_user(user_id)
        bank_user = await MahiroBank.get_user(user_id)
        max_deposit = max(
            int(float(sign_user.impression) * self._cfg("bank_sign_max_deposit", 100)), 100
        )
        if user.gold < amount:
            return f"金币数量不足，当前你的金币为：{user.gold}."
        if bank_user.amount + amount > max_deposit:
            return (
                f"存款超过上限，存款上限为：{max_deposit}，"
                f"当前你的还可以存款金额：{max_deposit - bank_user.amount}。"
            )
        max_daily = self._cfg("bank_max_daily_deposit_count", 3)
        if await MahiroBank.today_deposit_count(user_id) >= max_daily:
            return f"存款次数超过上限，每日存款次数上限为：{max_daily}。"
        return None

    async def _do_deposit(self, user_id: str, amount: int) -> tuple[float, float | None]:
        """执行存款：扣金币 + 入账，返回 (小时利率, 加息事件增量)"""
        rate = random.uniform(
            self._cfg("bank_rate_min", 0.0005), self._cfg("bank_rate_max", 0.001)
        )
        sign_user = await SignUser.get_user(user_id)
        event_rate = self._random_event(float(sign_user.impression))
        if event_rate:
            rate += event_rate
        await UserConsole.reduce_gold(user_id, amount, GoldHandle.PLUGIN, "bank")
        await MahiroBank.deposit(user_id, amount, rate)
        return rate, event_rate

    async def handle_deposit(self, event: AstrMessageEvent, amount: int | None = None) -> None:
        user_id = event.get_sender_id()
        if amount is None:
            amount = await self._ask_amount(event, "存款")
            if amount is None:
                return
        if error := await self._deposit_check(user_id, amount):
            await self._reply(event, error)
            return
        rate, event_rate = await self._do_deposit(user_id, amount)
        effective_hour = int(24 - datetime.now().hour)
        result = f"存款成功！\n此次存款金额为: {amount}\n当前小时利率为: {rate * 100:.2f}%"
        if event_rate:
            result += f"（小真寻偷偷将小时利率给你增加了 {event_rate * 100:.2f}% 哦）"
        result += f"\n预计总收益为: {int(amount * rate * effective_hour) or 1} 金币。"
        logger.info(f"[zhenxun_economy] 银行存款: user={user_id} amount={amount} rate={rate}")
        await self._reply(event, result, at=True)

    # ---------------- 取款 ----------------

    async def _withdraw_check(self, user_id: str, amount: int) -> str | None:
        """取款校验链（当日存款锁定不可取）"""
        if amount <= 0:
            return "取款数量必须大于 0 啊笨蛋！"
        bank_user = await MahiroBank.get_user(user_id)
        locked = await MahiroBank.locked_amount(user_id)
        if bank_user.amount - locked < amount:
            return f"取款金额不足，当前你的存款为：{bank_user.amount}（{locked}已被锁定）！"
        return None

    async def handle_withdraw(self, event: AstrMessageEvent, amount: int | None = None) -> None:
        user_id = event.get_sender_id()
        if amount is None:
            amount = await self._ask_amount(event, "取款")
            if amount is None:
                return
        if error := await self._withdraw_check(user_id, amount):
            await self._reply(event, error)
            return
        try:
            await UserConsole.add_gold(user_id, amount, "bank")
            await MahiroBank.withdraw(user_id, amount)
        except ValueError:
            await self._reply(event, "你的银行内的存款数量不足哦...")
            return
        bank_user = await MahiroBank.get_user(user_id)
        logger.info(f"[zhenxun_economy] 银行取款: user={user_id} amount={amount}")
        await self._reply(
            event, f"取款成功！\n当前取款金额为: {amount}\n当前存款金额为: {bank_user.amount}"
        )

    # ---------------- 我的银行信息 ----------------

    async def _user_info_payload(self, user_id: str, name: str) -> dict:
        """组装"我的银行信息"数据（当前存款/全服排名/今日生效存款/累计利息/存款明细）"""
        dep = str(BankHandleType.DEPOSIT)
        bank_user = await MahiroBank.get_user(user_id)
        rank = await db.fetchval(
            "SELECT COUNT(*) FROM mahiro_bank WHERE amount > ?", (bank_user.amount,)
        )
        deposit_count = await db.fetchval(
            "SELECT COUNT(*) FROM mahiro_bank_log WHERE user_id = ?", (user_id,)
        )
        logs = await db.fetchall(
            "SELECT id, amount, rate, effective_hour, create_time FROM mahiro_bank_log"
            " WHERE user_id = ? AND handle_type = ? AND is_completed = 0",
            (user_id, dep),
        )
        cumulative = await db.fetchval(
            "SELECT SUM(amount) FROM mahiro_bank_log WHERE user_id = ? AND handle_type = ?",
            (user_id, str(BankHandleType.INTEREST)),
        )
        now = datetime.now()
        end_time = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        deposit_list = [
            {
                "id": log["id"],
                "date": str(now.date()),
                "start_time": str(log["create_time"]).split(".")[0],
                "end_time": str(end_time),
                "amount": log["amount"],
                "rate": f"{log['rate'] * 100:.2f}",
                "projected_revenue": int(log["amount"] * log["rate"] * log["effective_hour"]) or 1,
            }
            for log in logs
        ]
        return {
            "name": name,
            "rank": int(rank) + 1,
            "avatar_url": "",
            "amount": bank_user.amount,
            "deposit_count": int(deposit_count),
            "today_deposit_count": len(logs),
            "cumulative_gain": int(cumulative),
            "projected_revenue": int(
                sum(log["rate"] * log["amount"] * log["effective_hour"] for log in logs)
            ),
            "today_deposit_amount": sum(log["amount"] for log in logs),
            "deposit_list": deposit_list,
            "create_time": str(now.replace(microsecond=0)),
        }

    async def handle_user_info(self, event: AstrMessageEvent) -> None:
        user_id = event.get_sender_id()
        name = await pu.get_user_name(event, user_id)
        payload = await self._user_info_payload(user_id, name)
        if self._cfg("render_enabled", True):
            try:
                avatar = await pu.get_avatar_b64(user_id)
                if avatar:
                    payload["avatar_url"] = avatar
                url = await render.render_user_info(self.plugin, payload)
                await event.send(MessageChain([Comp.Image.fromURL(url)]))
                return
            except Exception as e:
                logger.warning(f"[zhenxun_economy] 银行个人信息渲染失败，降级纯文本: {e}")
        await event.send(MessageChain([Comp.Plain(render.user_info_text(payload))]))

    # ---------------- 银行总览 ----------------

    async def _bank_info_payload(self) -> dict:
        """组装银行总览数据（总存款/用户数/今日笔数/累计利息/近7日趋势）"""
        dep = str(BankHandleType.DEPOSIT)
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)
        week_start_str = week_start.strftime("%Y-%m-%d %H:%M:%S")
        today_start_str = today_start.strftime("%Y-%m-%d %H:%M:%S")

        row = await db.fetchone("SELECT SUM(amount) AS s, COUNT(id) AS c FROM mahiro_bank")
        amount_sum = int(row["s"] or 0)
        user_count = int(row["c"] or 0)
        today_count = await db.fetchval(
            "SELECT COUNT(*) FROM mahiro_bank_log WHERE handle_type = ? AND create_time > ?",
            (dep, today_start_str),
        )
        interest_amount = await db.fetchval(
            "SELECT SUM(amount) FROM mahiro_bank_log WHERE handle_type = ?",
            (str(BankHandleType.INTEREST),),
        )
        active_user_count = await db.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM mahiro_bank_log"
            " WHERE handle_type = ? AND create_time >= ?",
            (dep, week_start_str),
        )
        date_rows = await db.fetchall(
            "SELECT DATE(create_time) AS d, SUM(amount) AS s FROM mahiro_bank_log"
            " WHERE handle_type = ? AND create_time >= ? GROUP BY DATE(create_time)",
            (dep, week_start_str),
        )
        date2amount = {str(r["d"]): int(r["s"] or 0) for r in date_rows}
        # 近 7 日趋势（含今天，按日期升序）
        trend = []
        day = now.date()
        dates = []
        for _ in range(7):
            dates.append(day)
            day -= timedelta(days=1)
        dates.reverse()
        amounts = [date2amount.get(str(d), 0) for d in dates]
        max_amount = max(amounts) if amounts else 0
        for d, a in zip(dates, amounts):
            trend.append(
                {
                    "date": str(d)[5:],
                    "amount": a,
                    "pct": round(a / max_amount * 100) if max_amount else 0,
                }
            )
        # 日均存款 = 总存款 / 营业天数（自首条日志起算）
        days = 1
        first = await db.fetchone("SELECT create_time FROM mahiro_bank_log ORDER BY create_time LIMIT 1")
        if first:
            first_date = datetime.strptime(str(first["create_time"])[:10], "%Y-%m-%d").date()
            days = ((now.date() - first_date).days or 1) + 1
        return {
            "amount_sum": amount_sum,
            "user_count": user_count,
            "today_count": int(today_count),
            "day_amount": int(amount_sum / days),
            "interest_amount": int(interest_amount),
            "active_user_count": int(active_user_count),
            # 原版 overview.html echarts 折线图字段（MM-DD 日期 / 当日存款总额）
            "e_data": [str(d)[5:] for d in dates],
            "e_amount": amounts,
            # 纯文本降级用（含占比百分比）
            "trend": trend,
            "create_time": str(now.replace(microsecond=0)),
        }

    async def handle_bank_info(self, event: AstrMessageEvent) -> None:
        payload = await self._bank_info_payload()
        if self._cfg("render_enabled", True):
            try:
                url = await render.render_bank_info(self.plugin, payload)
                await event.send(MessageChain([Comp.Image.fromURL(url)]))
                return
            except Exception as e:
                logger.warning(f"[zhenxun_economy] 银行总览渲染失败，降级纯文本: {e}")
        await event.send(MessageChain([Comp.Plain(render.bank_info_text(payload))]))

    # ---------------- 每日结息 ----------------

    @staticmethod
    async def _append_interest_log(user_id: str, amount: int, rate: float) -> None:
        """写 INTEREST 日志（core 未提供该写入接口，直接走 db）"""
        await db.execute(
            "INSERT INTO mahiro_bank_log (user_id, amount, rate, handle_type,"
            " is_completed, effective_hour, update_time, create_time)"
            " VALUES (?, ?, ?, ?, 1, 0, ?, ?)",
            (user_id, amount, rate, str(BankHandleType.INTEREST), now_str(), now_str()),
        )

    async def settle_interest(self) -> None:
        """每日 0 点结息（cron handler）。

        ① 未结算的存款日志：利息 = int(amount × rate × effective_hour) or 1，
           发放金币、标记已结算、写 INTEREST 日志；
        ② 存量存款（账户余额 - 当日未结算存款）：利息 = int(存量 × 账户rate)，
           > 0 时发放并写 INTEREST 日志。
        """
        dep = str(BankHandleType.DEPOSIT)
        logs = await db.fetchall(
            "SELECT id, user_id, amount, rate, effective_hour FROM mahiro_bank_log"
            " WHERE is_completed = 0 AND handle_type = ?",
            (dep,),
        )
        pending_sum: dict[str, int] = {}
        total = 0
        for log in logs:
            interest = int(log["amount"] * log["rate"] * log["effective_hour"]) or 1
            await UserConsole.add_gold(log["user_id"], interest, "bank")
            await db.execute(
                "UPDATE mahiro_bank_log SET is_completed = 1, update_time = ? WHERE id = ?",
                (now_str(), log["id"]),
            )
            await self._append_interest_log(log["user_id"], interest, log["rate"])
            pending_sum[log["user_id"]] = pending_sum.get(log["user_id"], 0) + log["amount"]
            total += interest
        accounts = await db.fetchall(
            "SELECT user_id, amount, rate FROM mahiro_bank WHERE amount > 0"
        )
        for acc in accounts:
            stock = acc["amount"] - pending_sum.get(acc["user_id"], 0)
            if stock <= 0:
                continue
            interest = int(stock * acc["rate"])
            if interest <= 0:
                continue
            await UserConsole.add_gold(acc["user_id"], interest, "bank")
            await self._append_interest_log(acc["user_id"], interest, acc["rate"])
            total += interest
        logger.info(
            f"[zhenxun_economy] 银行每日结息完成: 结算存款 {len(logs)} 笔,"
            f" 账户 {len(accounts)} 个, 共发放利息 {total} 金币"
        )

    async def register_cron(self) -> None:
        """注册每日 0 点（Asia/Shanghai）结息定时任务，由 main.py initialize 调用"""
        await self.plugin.context.cron_manager.add_basic_job(
            name="zhenxun_bank_interest",
            cron_expression="0 0 * * *",
            handler=self.settle_interest,
            description="小真寻银行每日结息",
            timezone="Asia/Shanghai",
        )
