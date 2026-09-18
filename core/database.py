"""插件级 SQLite 数据库管理（aiosqlite）。

表结构（表名/列名）与真寻 bot 完全一致，保证数据可无损迁移。
"""

import asyncio
import datetime
from pathlib import Path

import aiosqlite

from astrbot.api import logger
from astrbot.api.star import StarTools

_DB_NAME = "economy.db"

# 与真寻 Tortoise ORM 生成的表结构对齐（SQLite 方言）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_console (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL UNIQUE,
    uid INT NOT NULL UNIQUE,
    gold INT NOT NULL DEFAULT 100,
    props JSON NOT NULL DEFAULT '{}',
    platform VARCHAR(255),
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_console_user_id ON user_console (user_id);
CREATE INDEX IF NOT EXISTS idx_user_console_uid ON user_console (uid);

CREATE TABLE IF NOT EXISTS user_gold_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL,
    gold INT NOT NULL,
    handle VARCHAR(255) NOT NULL,
    source VARCHAR(255),
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_gold_log_user_id ON user_gold_log (user_id);

CREATE TABLE IF NOT EXISTS user_props_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL,
    uuid VARCHAR(255) NOT NULL,
    num INT,
    gold INT,
    handle VARCHAR(255) NOT NULL,
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_props_log_user_id ON user_props_log (user_id);

CREATE TABLE IF NOT EXISTS goods_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid VARCHAR(255),
    goods_name VARCHAR(255) NOT NULL UNIQUE,
    goods_price INT NOT NULL,
    goods_description TEXT NOT NULL,
    goods_discount REAL NOT NULL DEFAULT 1,
    goods_limit_time BIGINT NOT NULL DEFAULT 0,
    daily_limit INT NOT NULL DEFAULT 0,
    is_passive BOOL NOT NULL DEFAULT 0,
    partition VARCHAR(255),
    icon TEXT
);

CREATE TABLE IF NOT EXISTS sign_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL UNIQUE,
    sign_count INT NOT NULL DEFAULT 0,
    impression DECIMAL(10,3) NOT NULL DEFAULT 0,
    user_console_id INT NOT NULL,
    add_probability DECIMAL(10,3) NOT NULL DEFAULT 0,
    specify_probability DECIMAL(10,3) NOT NULL DEFAULT 0,
    platform VARCHAR(255)
);
CREATE INDEX IF NOT EXISTS idx_sign_users_user_id ON sign_users (user_id);

CREATE TABLE IF NOT EXISTS sign_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL,
    impression DECIMAL(10,3) NOT NULL,
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    bot_id VARCHAR(255),
    platform VARCHAR(255)
);
CREATE INDEX IF NOT EXISTS idx_sign_log_user_id ON sign_log (user_id);

CREATE TABLE IF NOT EXISTS mahiro_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL,
    amount BIGINT NOT NULL DEFAULT 0,
    rate REAL NOT NULL DEFAULT 0.0005,
    loan_amount BIGINT NOT NULL DEFAULT 0,
    loan_rate REAL NOT NULL DEFAULT 0.0005,
    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mahiro_bank_user_id ON mahiro_bank (user_id);

CREATE TABLE IF NOT EXISTS mahiro_bank_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL,
    amount BIGINT NOT NULL DEFAULT 0,
    rate REAL NOT NULL DEFAULT 0,
    handle_type VARCHAR(255),
    is_completed BOOL NOT NULL DEFAULT 0,
    effective_hour INT NOT NULL DEFAULT 0,
    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mahiro_bank_log_user_id ON mahiro_bank_log (user_id);

CREATE TABLE IF NOT EXISTS russian_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    win_count INT NOT NULL DEFAULT 0,
    fail_count INT NOT NULL DEFAULT 0,
    make_money INT NOT NULL DEFAULT 0,
    lose_money INT NOT NULL DEFAULT 0,
    winning_streak INT NOT NULL DEFAULT 0,
    losing_streak INT NOT NULL DEFAULT 0,
    max_winning_streak INT NOT NULL DEFAULT 0,
    max_losing_streak INT NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_russian_users_ug ON russian_users (user_id, group_id);

CREATE TABLE IF NOT EXISTS redbag_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    send_redbag_count INT NOT NULL DEFAULT 0,
    get_redbag_count INT NOT NULL DEFAULT 0,
    spend_gold INT NOT NULL DEFAULT 0,
    get_gold INT NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_redbag_users_ug ON redbag_users (user_id, group_id);
"""


def now_str() -> str:
    """真寻 Tortoise 在 SQLite 中的时间格式"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


class EconomyDB:
    """经济系统数据库连接管理（单连接 + 写锁串行化）"""

    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self.db_path: Path | None = None

    async def init(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = StarTools.get_data_dir("astrbot_plugin_zhenxun_economy") / _DB_NAME
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._db = await aiosqlite.connect(str(db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA busy_timeout=30000; PRAGMA foreign_keys=ON;"
        )
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info(f"[zhenxun_economy] 数据库已初始化: {db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "数据库未初始化，请先调用 EconomyDB.init()"
        return self._db

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """写操作（串行化 + 自动提交）"""
        async with self._write_lock:
            cur = await self.db.execute(sql, params)
            await self.db.commit()
            return cur

    async def executemany(self, sql: str, params_list: list[tuple]) -> None:
        async with self._write_lock:
            await self.db.executemany(sql, params_list)
            await self.db.commit()

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with await self.db.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with await self.db.execute(sql, params) as cur:
            return await cur.fetchall()

    async def fetchval(self, sql: str, params: tuple = (), default=0):
        row = await self.fetchone(sql, params)
        if row is None:
            return default
        val = row[0]
        return default if val is None else val


db = EconomyDB()
