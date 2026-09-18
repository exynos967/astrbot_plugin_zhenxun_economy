"""migrate_zhenxun.py 的单元测试：造假源库 -> 跑迁移 -> 断言正确性与幂等性"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

import migrate_zhenxun as mz

# 与真寻真实库一致的 DDL（注意 goods_info 是 icon 在 partition 前、
# sign_users 的 user_console_id 在最后，与目标 _SCHEMA 列序不同，专门用来覆盖列序问题）
ZX_DDL = """
CREATE TABLE user_console (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, uid INTEGER NOT NULL, gold INTEGER NOT NULL, props TEXT NOT NULL, platform TEXT, create_time TEXT NOT NULL);
CREATE TABLE user_gold_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, gold INTEGER NOT NULL, handle TEXT NOT NULL, source TEXT, create_time TEXT NOT NULL);
CREATE TABLE user_props_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, uuid TEXT NOT NULL, num INTEGER, gold INTEGER, handle TEXT NOT NULL, create_time TEXT NOT NULL);
CREATE TABLE goods_info (id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT, goods_name TEXT NOT NULL, goods_price INTEGER NOT NULL, goods_description TEXT NOT NULL, goods_discount REAL NOT NULL, goods_limit_time INTEGER NOT NULL, daily_limit INTEGER NOT NULL, is_passive INTEGER NOT NULL, icon TEXT, partition TEXT);
CREATE TABLE sign_users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, sign_count INTEGER NOT NULL, impression REAL NOT NULL, add_probability REAL NOT NULL, specify_probability REAL NOT NULL, platform TEXT, user_console_id INTEGER NOT NULL);
CREATE TABLE sign_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, impression REAL NOT NULL, create_time TEXT NOT NULL, bot_id TEXT, platform TEXT);
CREATE TABLE mahiro_bank (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, amount INTEGER NOT NULL, rate REAL NOT NULL, loan_amount INTEGER NOT NULL, loan_rate REAL NOT NULL, update_time TEXT NOT NULL, create_time TEXT NOT NULL);
CREATE TABLE mahiro_bank_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, amount INTEGER NOT NULL, rate REAL NOT NULL, handle_type TEXT, is_completed INTEGER NOT NULL, effective_hour INTEGER NOT NULL, update_time TEXT NOT NULL, create_time TEXT NOT NULL);
CREATE TABLE russian_users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, group_id TEXT NOT NULL, win_count INTEGER NOT NULL, fail_count INTEGER NOT NULL, make_money INTEGER NOT NULL, lose_money INTEGER NOT NULL, winning_streak INTEGER NOT NULL, losing_streak INTEGER NOT NULL, max_winning_streak INTEGER NOT NULL, max_losing_streak INTEGER NOT NULL);
CREATE TABLE redbag_users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, group_id TEXT NOT NULL, send_redbag_count INTEGER NOT NULL, get_redbag_count INTEGER NOT NULL, spend_gold INTEGER NOT NULL, get_gold INTEGER NOT NULL);
"""

FARM_DDL = """
CREATE TABLE "user" (uid TEXT PRIMARY KEY, name TEXT NOT NULL, exp INTEGER DEFAULT 0, point INTEGER DEFAULT 0, vipPoint INTEGER DEFAULT 0, soil INTEGER DEFAULT 3, stealTime TEXT DEFAULT '', stealCount INTEGER DEFAULT 0);
CREATE TABLE userSoil (uid TEXT, soilIndex INTEGER, plantName TEXT DEFAULT '', plantTime INTEGER DEFAULT 0, matureTime INTEGER DEFAULT 0, soilLevel INTEGER DEFAULT 0, wiltStatus INTEGER DEFAULT 0, fertilizerStatus INTEGER DEFAULT 0, bugStatus INTEGER DEFAULT 0, weedStatus INTEGER DEFAULT 0, waterStatus INTEGER DEFAULT 0, harvestCount INTEGER DEFAULT 0, isSoilPlanted INTEGER DEFAULT NULL, PRIMARY KEY (uid, soilIndex));
CREATE TABLE userPlant (uid TEXT, plant TEXT, count INTEGER DEFAULT 0, isLock INTEGER DEFAULT 0, PRIMARY KEY (uid, plant));
CREATE TABLE userSeed (uid TEXT, seed TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (uid, seed));
CREATE TABLE userItem (uid TEXT, item TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (uid, item));
CREATE TABLE userSteal (uid TEXT, soilIndex INTEGER, stealerUid TEXT, stealCount INTEGER NOT NULL, stealTime INTEGER NOT NULL, PRIMARY KEY (uid, soilIndex, stealerUid));
CREATE TABLE userSignLog (uid TEXT, signDate DATE NOT NULL, isSupplement TINYINT NOT NULL DEFAULT 0, exp INT NOT NULL DEFAULT 0, point INT NOT NULL DEFAULT 0, createdAt DATETIME NOT NULL DEFAULT (DATETIME(CURRENT_TIMESTAMP, 'LOCALTIME')), PRIMARY KEY (uid, signDate));
CREATE TABLE userSignSummary (uid TEXT PRIMARY KEY, totalSignDays INT NOT NULL DEFAULT 0, currentMonth CHAR(7) NOT NULL DEFAULT '', monthSignDays INT NOT NULL DEFAULT 0, lastSignDate DATE DEFAULT NULL, continuousDays INT NOT NULL DEFAULT 0, supplementCount INT NOT NULL DEFAULT 0, updatedAt DATETIME NOT NULL DEFAULT (DATETIME(CURRENT_TIMESTAMP, 'LOCALTIME')));
CREATE TABLE userPlantCount (uid TEXT, plant TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (uid, plant));
"""

FARM_TABLES = ["user", "userSoil", "userPlant", "userSeed", "userItem",
               "userSteal", "userSignLog", "userSignSummary", "userPlantCount"]

NOW = "2024-01-01 12:00:00"


def build_zhenxun_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(ZX_DDL)
    props_a = json.dumps({"src-uuid-药水": 2, "src-uuid-双倍卡": 1})
    conn.executemany(
        "INSERT INTO user_console (user_id, uid, gold, props, platform, create_time) VALUES (?,?,?,?,?,?)",
        [
            ("123456", 10001, 5000, props_a, "qq", NOW),
            ("789012", 10002, 300, "{}", None, NOW),
        ],
    )
    conn.executemany(
        "INSERT INTO user_gold_log (user_id, gold, handle, source, create_time) VALUES (?,?,?,?,?)",
        [
            ("123456", 100, "GET", "sign_in", NOW),
            ("123456", 50, "BUY", None, NOW),
            ("789012", 200, "PLUGIN", "shop", NOW),
        ],
    )
    conn.execute(
        "INSERT INTO user_props_log (user_id, uuid, num, gold, handle, create_time) VALUES (?,?,?,?,?,?)",
        ("123456", "src-uuid-药水", 2, 2000, "BUY", NOW),
    )
    conn.execute(
        "INSERT INTO goods_info (uuid, goods_name, goods_price, goods_description, goods_discount,"
        " goods_limit_time, daily_limit, is_passive, icon, partition) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("src-uuid-药水", "神秘药水", 1000, "来历不明的药水", 1.0, 0, 0, 0, None, "药店"),
    )
    conn.execute(
        "INSERT INTO sign_users (user_id, sign_count, impression, add_probability, specify_probability, platform, user_console_id)"
        " VALUES (?,?,?,?,?,?,?)",
        ("123456", 42, 123.456, 0.0, 0.0, "qq", 1),
    )
    conn.executemany(
        "INSERT INTO sign_log (user_id, impression, create_time, bot_id, platform) VALUES (?,?,?,?,?)",
        [("123456", 1.5, NOW, "bot1", "qq"), ("123456", 2.0, NOW, None, None)],
    )
    conn.execute(
        "INSERT INTO mahiro_bank (user_id, amount, rate, loan_amount, loan_rate, update_time, create_time)"
        " VALUES (?,?,?,?,?,?,?)",
        ("123456", 8000, 0.0005, 1000, 0.001, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO mahiro_bank_log (user_id, amount, rate, handle_type, is_completed, effective_hour, update_time, create_time)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("123456", 8000, 0.0005, "DEPOSIT", 0, 12, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO russian_users (user_id, group_id, win_count, fail_count, make_money, lose_money,"
        " winning_streak, losing_streak, max_winning_streak, max_losing_streak) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("123456", "group1", 3, 1, 900, 300, 2, 0, 2, 1),
    )
    conn.execute(
        "INSERT INTO redbag_users (user_id, group_id, send_redbag_count, get_redbag_count, spend_gold, get_gold)"
        " VALUES (?,?,?,?,?,?)",
        ("123456", "group1", 5, 7, 500, 700),
    )
    conn.commit()
    conn.close()


def build_farm_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(FARM_DDL)
    conn.execute("INSERT INTO user (uid, name, exp, point) VALUES ('u1', '农民甲', 100, 50)")
    conn.execute("INSERT INTO user (uid, name) VALUES ('u2', '农民乙')")
    conn.execute("INSERT INTO userSoil (uid, soilIndex, plantName, plantTime) VALUES ('u1', 0, '小麦', 1700000000)")
    conn.execute("INSERT INTO userSoil (uid, soilIndex) VALUES ('u1', 1)")
    conn.execute("INSERT INTO userPlant (uid, plant, count, isLock) VALUES ('u1', '小麦', 3, 0)")
    conn.execute("INSERT INTO userSeed (uid, seed, count) VALUES ('u1', '小麦种子', 5)")
    conn.execute("INSERT INTO userItem (uid, item, count) VALUES ('u1', '化肥', 2)")
    conn.execute("INSERT INTO userSteal (uid, soilIndex, stealerUid, stealCount, stealTime) VALUES ('u1', 0, 'u2', 1, 1700000001)")
    conn.execute("INSERT INTO userSignLog (uid, signDate, isSupplement, exp, point) VALUES ('u1', '2024-01-01', 0, 10, 5)")
    conn.execute("INSERT INTO userSignSummary (uid, totalSignDays, currentMonth, monthSignDays, lastSignDate, continuousDays)"
                 " VALUES ('u1', 10, '2024-01', 1, '2024-01-01', 1)")
    conn.execute("INSERT INTO userPlantCount (uid, plant, count) VALUES ('u1', '小麦', 7)")
    conn.commit()
    conn.close()


@pytest.fixture()
def env(tmp_path):
    zx = tmp_path / "zhenxun.db"
    farm = tmp_path / "farm.db"
    target = tmp_path / "plugin_data"
    build_zhenxun_db(zx)
    build_farm_db(farm)
    return zx, farm, target


def run_migrate(zx, farm, target, *extra):
    argv = ["--zhenxun-db", str(zx), "--target-dir", str(target)]
    if farm is not None:
        argv += ["--farm-db", str(farm)]
    argv += list(extra)
    return mz.main(argv)


def table_count(db_path, table):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        conn.close()


def test_migrate_economy_and_farm(env):
    zx, farm, target = env
    assert run_migrate(zx, farm, target) == 0

    eco = target / "economy.db"
    assert eco.exists()
    assert table_count(eco, "user_console") == 2
    assert table_count(eco, "user_gold_log") == 3
    assert table_count(eco, "user_props_log") == 1
    assert table_count(eco, "goods_info") == 1
    assert table_count(eco, "sign_users") == 1
    assert table_count(eco, "sign_log") == 2
    assert table_count(eco, "mahiro_bank") == 1
    assert table_count(eco, "mahiro_bank_log") == 1
    assert table_count(eco, "russian_users") == 1
    assert table_count(eco, "redbag_users") == 1

    conn = sqlite3.connect(str(eco))
    conn.row_factory = sqlite3.Row
    u = conn.execute("SELECT * FROM user_console WHERE user_id='123456'").fetchone()
    assert u["uid"] == 10001
    assert u["gold"] == 5000
    assert json.loads(u["props"]) == {"src-uuid-药水": 2, "src-uuid-双倍卡": 1}
    assert u["platform"] == "qq"
    g = conn.execute("SELECT * FROM goods_info WHERE goods_name='神秘药水'").fetchone()
    assert g["uuid"] == "src-uuid-药水"
    assert g["goods_price"] == 1000
    assert g["partition"] == "药店"  # 源库 icon/partition 列序与目标不同，验证按列名对齐
    s = conn.execute("SELECT * FROM sign_users WHERE user_id='123456'").fetchone()
    assert s["sign_count"] == 42
    assert abs(s["impression"] - 123.456) < 1e-6
    assert s["user_console_id"] == 1
    b = conn.execute("SELECT * FROM mahiro_bank WHERE user_id='123456'").fetchone()
    assert b["amount"] == 8000 and b["loan_amount"] == 1000
    r = conn.execute("SELECT * FROM russian_users WHERE user_id='123456'").fetchone()
    assert r["win_count"] == 3 and r["max_winning_streak"] == 2
    rb = conn.execute("SELECT * FROM redbag_users WHERE user_id='123456'").fetchone()
    assert rb["send_redbag_count"] == 5 and rb["get_gold"] == 700
    conn.close()

    fdb = target / "farm_db" / "farm.db"
    assert fdb.exists()
    for t in FARM_TABLES:
        assert table_count(fdb, t) >= 1, t
    fconn = sqlite3.connect(str(fdb))
    row = fconn.execute("SELECT name, exp, point FROM user WHERE uid='u1'").fetchone()
    assert row == ("农民甲", 100, 50)
    soil = fconn.execute("SELECT plantName, plantTime FROM userSoil WHERE uid='u1' AND soilIndex=0").fetchone()
    assert soil == ("小麦", 1700000000)
    fconn.close()


def test_migrate_idempotent(env):
    zx, farm, target = env
    assert run_migrate(zx, farm, target) == 0
    assert run_migrate(zx, farm, target) == 0  # 再跑一遍

    eco = target / "economy.db"
    expected = {
        "user_console": 2, "user_gold_log": 3, "user_props_log": 1, "goods_info": 1,
        "sign_users": 1, "sign_log": 2, "mahiro_bank": 1, "mahiro_bank_log": 1,
        "russian_users": 1, "redbag_users": 1,
    }
    for table, cnt in expected.items():
        assert table_count(eco, table) == cnt, f"{table} 行数翻倍了"
    for t in FARM_TABLES:
        assert table_count(target / "farm_db" / "farm.db", t) == table_count(farm, t), f"farm.{t} 行数不一致"


def test_goods_uuid_override(env, capsys):
    """目标库已有同名默认商品（插件自动注册、uuid 不同）时，迁移要以源库 uuid 覆盖"""
    zx, farm, target = env
    target.mkdir(parents=True)
    eco = target / "economy.db"
    conn = sqlite3.connect(str(eco))
    conn.executescript(mz.load_economy_schema())
    conn.execute(
        "INSERT INTO goods_info (uuid, goods_name, goods_price, goods_description) VALUES (?,?,?,?)",
        ("plugin-gen-uuid", "神秘药水", 1000, "插件注册的默认商品"),
    )
    conn.commit()
    conn.close()

    assert run_migrate(zx, None, target) == 0

    conn = sqlite3.connect(str(eco))
    rows = conn.execute("SELECT uuid FROM goods_info WHERE goods_name='神秘药水'").fetchall()
    conn.close()
    assert len(rows) == 1  # 没有重复插入
    assert rows[0][0] == "src-uuid-药水"  # uuid 已被源库覆盖
    out = capsys.readouterr().out
    assert "uuid覆盖" in out and "神秘药水" in out


def test_backup_created(env):
    zx, farm, target = env
    assert run_migrate(zx, farm, target) == 0
    # 第二次迁移前应生成 .bak 备份
    assert run_migrate(zx, farm, target) == 0
    baks = list(target.glob("economy.db.bak.*"))
    assert len(baks) >= 1


def test_verify_mode(env, capsys):
    zx, farm, target = env
    assert run_migrate(zx, farm, target) == 0
    capsys.readouterr()
    assert run_migrate(zx, farm, target, "--verify") == 0
    out = capsys.readouterr().out
    assert "全部一致" in out
    assert "user_console] 源=2 目标=2 ✓" in out
    assert "gold=5000 一致" in out
    # 校验模式不产生新写入：目标行数不变
    assert table_count(target / "economy.db", "user_console") == 2


def test_verify_detects_difference(env, capsys):
    zx, farm, target = env
    assert run_migrate(zx, farm, target) == 0
    # 人为篡改目标库金币
    conn = sqlite3.connect(str(target / "economy.db"))
    conn.execute("UPDATE user_console SET gold = 999999 WHERE user_id='123456'")
    conn.commit()
    conn.close()
    capsys.readouterr()
    assert run_migrate(zx, farm, target, "--verify") == 1
    out = capsys.readouterr().out
    assert "存在差异" in out


def test_uid_conflict_remap(env, capsys):
    """源库脏数据：两个不同 user_id 共用同一 uid，后者应被重新分配 uid 而非整行丢弃"""
    zx, farm, target = env
    conn = sqlite3.connect(str(zx))
    conn.execute(
        "INSERT INTO user_console (user_id, uid, gold, props, platform, create_time) VALUES (?,?,?,?,?,?)",
        ("999999", 10001, 777, "{}", "qq", NOW),  # uid 与 123456 冲突
    )
    conn.commit()
    conn.close()

    assert run_migrate(zx, None, target) == 0
    out = capsys.readouterr().out
    assert "uid 冲突" in out and "重新分配" in out and "999999" in out

    eco = target / "economy.db"
    conn = sqlite3.connect(str(eco))
    rows = conn.execute("SELECT user_id, uid, gold FROM user_console").fetchall()
    conn.close()
    assert len(rows) == 3  # 没有用户被丢弃
    by_user = {r[0]: (r[1], r[2]) for r in rows}
    assert by_user["123456"] == (10001, 5000)  # 先到的保留原 uid
    assert by_user["999999"][1] == 777  # 金币没丢
    new_uid = by_user["999999"][0]
    assert new_uid == 10003  # 重新分配为 MAX(uid)+1
    assert len({r[1] for r in rows}) == 3  # uid 全部唯一

    # 幂等：再跑一遍，行数不变、重映射结果稳定
    assert run_migrate(zx, None, target) == 0
    assert table_count(eco, "user_console") == 3
    conn = sqlite3.connect(str(eco))
    row = conn.execute("SELECT uid FROM user_console WHERE user_id='999999'").fetchone()
    conn.close()
    assert row[0] == new_uid
