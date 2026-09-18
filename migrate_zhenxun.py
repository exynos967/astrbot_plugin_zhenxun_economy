"""真寻 bot SQLite 数据迁移脚本（纯标准库，可独立运行）。

把真寻 bot 的经济数据（zhenxun.db）与农场数据（farm.db）导入
AstrBot 插件 astrbot_plugin_zhenxun_economy 的数据目录。

用法：
    python migrate_zhenxun.py --zhenxun-db <zhenxun.db路径> [--farm-db <farm.db路径>]
    python migrate_zhenxun.py --zhenxun-db <zhenxun.db路径> --verify
    （--target-dir 省略时自动定位 AstrBot 插件数据目录：
      $ASTRBOT_ROOT/data/plugin_data/astrbot_plugin_zhenxun_economy，
      桌面版为 ~/.astrbot/data/plugin_data/astrbot_plugin_zhenxun_economy，
      在 AstrBot 根目录下运行则为 ./data/plugin_data/astrbot_plugin_zhenxun_economy）

说明：
    - 经济目标表按 core/database.py 的 _SCHEMA 创建（AST 提取，不导入插件环境）
    - 农场目标表按源 farm.db 的 sqlite_master DDL 原样创建
    - 全部使用 INSERT OR IGNORE，主键/唯一键冲突自动跳过，可重复执行（幂等）
    - goods_info 做同名商品 uuid 覆盖，保证用户背包 props 里的 uuid 与商品对上
    - 迁移前自动备份已存在的目标库为 *.bak.时间戳
"""

import argparse
import ast
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
ECONOMY_SCHEMA_FILE = PLUGIN_ROOT / "core" / "database.py"
ECONOMY_DB_NAME = "economy.db"
# 农场模块实际读取 <插件数据目录>/farm_db/farm.db（见 modules/farm/cfg.py）
FARM_DB_SUBPATH = Path("farm_db") / "farm.db"
PLUGIN_NAME = "astrbot_plugin_zhenxun_economy"
BATCH_SIZE = 10000

# 真寻经济相关表（导入顺序无关，无外键）
ECONOMY_TABLES = [
    "user_console",
    "user_gold_log",
    "user_props_log",
    "goods_info",
    "sign_users",
    "sign_log",
    "mahiro_bank",
    "mahiro_bank_log",
    "russian_users",
    "redbag_users",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def open_ro(path: Path) -> sqlite3.Connection:
    """以只读 URI 模式打开 SQLite 库（避免对源库产生 WAL 写锁）"""
    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def backup_if_exists(path: Path) -> Path | None:
    """目标库已存在则备份为 *.bak.时间戳，返回备份路径"""
    if not path.exists():
        return None
    bak = path.with_name(path.name + ".bak." + time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(path, bak)
    log(f"[备份] {path.name} -> {bak.name}")
    return bak


def load_economy_schema() -> str:
    """从 core/database.py 中用 AST 提取 _SCHEMA 字符串（不导入插件模块）"""
    tree = ast.parse(ECONOMY_SCHEMA_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_SCHEMA":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    raise RuntimeError(f"未能在 {ECONOMY_SCHEMA_FILE} 中找到 _SCHEMA 定义")


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def insert_ignore(dst: sqlite3.Connection, table: str, cols: list[str], rows: list[tuple]) -> int:
    """批量 INSERT OR IGNORE，返回实际插入行数"""
    if not rows:
        return 0
    col_sql = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    before = dst.total_changes
    dst.executemany(
        f'INSERT OR IGNORE INTO "{table}" ({col_sql}) VALUES ({placeholders})', rows
    )
    return dst.total_changes - before


def migrate_user_console(src: sqlite3.Connection, dst: sqlite3.Connection) -> tuple[int, int, int]:
    """user_console 导入。

    冲突处理：
    - id 或 user_id 已在目标库存在 → 视为重复迁移，跳过
    - uid 已被其他 user_id 占用（源库脏数据：同一 uid 对应不同 user_id）→
      为该行重新分配 uid（取两库 MAX(uid)+1 递增）再插入，保证用户金币不丢
    """
    cols = [c for c in table_columns(src, "user_console") if c in table_columns(dst, "user_console")]
    col_sql = ", ".join(f'"{c}"' for c in cols)
    id_idx = cols.index("id")
    uid_idx = cols.index("uid")
    user_id_idx = cols.index("user_id")

    existing_ids = {row[0] for row in dst.execute("SELECT id FROM user_console")}
    existing_user_ids = {row[0] for row in dst.execute("SELECT user_id FROM user_console")}
    used_uids = {row[0] for row in dst.execute("SELECT uid FROM user_console")}
    dst_max = dst.execute("SELECT MAX(uid) FROM user_console").fetchone()[0] or 0
    src_max = src.execute("SELECT MAX(uid) FROM user_console").fetchone()[0] or 0
    next_uid = max(dst_max, src_max) + 1

    total = src.execute("SELECT COUNT(*) FROM user_console").fetchone()[0]
    imported = skipped = 0
    cur = src.execute(f"SELECT {col_sql} FROM user_console")
    while True:
        batch = cur.fetchmany(BATCH_SIZE)
        if not batch:
            break
        rows = []
        for row in batch:
            row_id, uid, user_id = row[id_idx], row[uid_idx], row[user_id_idx]
            if row_id in existing_ids or user_id in existing_user_ids:
                skipped += 1  # 同一行/同一用户重复迁移
                continue
            if uid in used_uids:
                new_uid = next_uid
                next_uid += 1
                log(f"  [警告] user_console uid 冲突: uid={uid} 已被占用，"
                    f"为 user_id={user_id} 重新分配 uid={new_uid}")
                row = tuple(new_uid if i == uid_idx else v for i, v in enumerate(row))
                uid = new_uid
            rows.append(row)
            used_uids.add(uid)
            existing_user_ids.add(user_id)
        inserted = insert_ignore(dst, "user_console", cols, rows)
        imported += inserted
        skipped += len(rows) - inserted
        if total > BATCH_SIZE:
            log(f"  user_console 进度: {imported + skipped}/{total}")
    return total, imported, skipped


def migrate_goods_info(src: sqlite3.Connection, dst: sqlite3.Connection) -> tuple[int, int, int]:
    """goods_info 导入：同名商品以源库 uuid 覆盖目标 uuid（保证背包 props 的 uuid 能对上）"""
    cols = [c for c in table_columns(src, "goods_info") if c in table_columns(dst, "goods_info")]
    col_sql = ", ".join(f'"{c}"' for c in cols)
    name_idx, uuid_idx = cols.index("goods_name"), cols.index("uuid")

    imported = skipped = 0
    rows = src.execute(f"SELECT {col_sql} FROM goods_info").fetchall()
    for row in rows:
        name, src_uuid = row[name_idx], row[uuid_idx]
        tgt = dst.execute(
            "SELECT uuid FROM goods_info WHERE goods_name = ?", (name,)
        ).fetchone()
        if tgt is None:
            dst.execute(
                f'INSERT INTO goods_info ({col_sql}) VALUES ({", ".join("?" for _ in cols)})',
                row,
            )
            imported += 1
            log(f"  [商品] 新增: {name} (uuid={src_uuid})")
        elif tgt[0] != src_uuid:
            dst.execute(
                "UPDATE goods_info SET uuid = ? WHERE goods_name = ?", (src_uuid, name)
            )
            skipped += 1
            log(f"  [商品] uuid覆盖: {name} 目标uuid={tgt[0]} -> 源uuid={src_uuid}")
        else:
            skipped += 1
            log(f"  [商品] 已存在跳过: {name} (uuid={src_uuid})")
    return len(rows), imported, skipped


def migrate_table_generic(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> tuple[int, int, int]:
    """通用逐表导入：列名交集 + INSERT OR IGNORE + fetchmany 分批 + 单事务进度打印"""
    src_cols, dst_cols = table_columns(src, table), table_columns(dst, table)
    cols = [c for c in src_cols if c in dst_cols]
    missing = [c for c in src_cols if c not in dst_cols]
    if missing:
        log(f"  [提示] {table} 目标库缺少列 {missing}，对应数据不导入")
    col_sql = ", ".join(f'"{c}"' for c in cols)
    total = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    imported = scanned = 0
    cur = src.execute(f'SELECT {col_sql} FROM "{table}"')
    while True:
        batch = cur.fetchmany(BATCH_SIZE)
        if not batch:
            break
        imported += insert_ignore(dst, table, cols, batch)
        scanned += len(batch)
        if total > BATCH_SIZE:
            log(f"  {table} 进度: {scanned}/{total}（已导入 {imported}）")
    return total, imported, total - imported


def print_report(table: str, total: int, imported: int, skipped: int) -> None:
    log(f"[{table}] 源行数={total} 导入={imported} 跳过={skipped}")


def migrate_economy(zhenxun_db: Path, target_dir: Path) -> None:
    log(f"=== 经济库迁移: {zhenxun_db} -> {target_dir / ECONOMY_DB_NAME} ===")
    src = open_ro(zhenxun_db)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        dst_path = target_dir / ECONOMY_DB_NAME
        backup_if_exists(dst_path)
        dst = sqlite3.connect(str(dst_path))
        dst.isolation_level = None  # 手动控制事务
        try:
            # executescript 会隐式提交，建表放在事务外（DDL 本身幂等）
            dst.executescript(load_economy_schema())
            dst.execute("BEGIN")
            for table in ECONOMY_TABLES:
                if not table_exists(src, table):
                    log(f"[{table}] 源库不存在该表，跳过")
                    continue
                if table == "user_console":
                    total, imported, skipped = migrate_user_console(src, dst)
                elif table == "goods_info":
                    total, imported, skipped = migrate_goods_info(src, dst)
                else:
                    total, imported, skipped = migrate_table_generic(src, dst, table)
                print_report(table, total, imported, skipped)
            dst.execute("COMMIT")
        except Exception:
            dst.execute("ROLLBACK")
            raise
        finally:
            dst.close()
    finally:
        src.close()


def migrate_farm(farm_db: Path, target_dir: Path) -> None:
    log(f"=== 农场库迁移: {farm_db} -> {target_dir / FARM_DB_SUBPATH} ===")
    src = open_ro(farm_db)
    try:
        # 按源库 sqlite_master DDL 建目标表/索引（加 IF NOT EXISTS 保证幂等）
        ddls = src.execute(
            "SELECT type, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC"
        ).fetchall()
        tables = [
            row[0]
            for row in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        dst_path = target_dir / FARM_DB_SUBPATH
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        backup_if_exists(dst_path)
        dst = sqlite3.connect(str(dst_path))
        dst.isolation_level = None
        try:
            # 建表/索引放在事务外（幂等 DDL）
            for obj_type, ddl in ddls:
                ddl = re.sub(
                    r"^\s*CREATE\s+" + obj_type.upper() + r"\s+",
                    f"CREATE {obj_type.upper()} IF NOT EXISTS ",
                    ddl,
                    flags=re.IGNORECASE,
                )
                dst.execute(ddl)
            dst.execute("BEGIN")
            for table in tables:
                total, imported, skipped = migrate_table_generic(src, dst, table)
                print_report(table, total, imported, skipped)
            dst.execute("COMMIT")
        except Exception:
            dst.execute("ROLLBACK")
            raise
        finally:
            dst.close()
    finally:
        src.close()


def verify(zhenxun_db: Path, farm_db: Path | None, target_dir: Path) -> bool:
    """只读校验：对比两库各表行数 + user_console 的 gold 抽样 10 条"""
    log("=== 校验模式（只读，不写入） ===")
    ok = True
    eco_target = target_dir / ECONOMY_DB_NAME
    if not eco_target.exists():
        log(f"[错误] 目标经济库不存在: {eco_target}")
        return False
    src = open_ro(zhenxun_db)
    dst = open_ro(eco_target)
    try:
        log("--- 经济库行数对比 ---")
        for table in ECONOMY_TABLES:
            if not table_exists(src, table):
                log(f"[{table}] 源库不存在该表，跳过")
                continue
            if not table_exists(dst, table):
                log(f"[{table}] 目标库不存在该表 ✗")
                ok = False
                continue
            s = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            d = dst.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            mark = "✓" if s == d else "✗"
            if s != d:
                ok = False
            log(f"[{table}] 源={s} 目标={d} {mark}")

        log("--- user_console.gold 抽样对比（10 条） ---")
        samples = src.execute(
            "SELECT user_id, gold FROM user_console ORDER BY id LIMIT 10"
        ).fetchall()
        for user_id, gold in samples:
            row = dst.execute(
                "SELECT gold FROM user_console WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                log(f"  user_id={user_id}: 目标缺失 ✗")
                ok = False
            elif row[0] != gold:
                log(f"  user_id={user_id}: 源gold={gold} 目标gold={row[0]} ✗")
                ok = False
            else:
                log(f"  user_id={user_id}: gold={gold} 一致 ✓")
    finally:
        src.close()
        dst.close()

    if farm_db is not None:
        farm_target = target_dir / FARM_DB_SUBPATH
        if not farm_target.exists():
            log(f"[错误] 目标农场库不存在: {farm_target}")
            return False
        fsrc = open_ro(farm_db)
        fdst = open_ro(farm_target)
        try:
            log("--- 农场库行数对比 ---")
            tables = [
                row[0]
                for row in fsrc.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                if not table_exists(fdst, table):
                    log(f"[{table}] 目标库不存在该表 ✗")
                    ok = False
                    continue
                s = fsrc.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                d = fdst.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                mark = "✓" if s == d else "✗"
                if s != d:
                    ok = False
                log(f"[{table}] 源={s} 目标={d} {mark}")
        finally:
            fsrc.close()
            fdst.close()

    log("=== 校验结果: " + ("全部一致 ✓" if ok else "存在差异 ✗") + " ===")
    return ok


def resolve_target_dir(target_dir: Path | None) -> Path:
    """解析目标插件数据目录。

    优先级（与 AstrBot astrbot_path.py 的目录规则一致）：
    1. 显式 --target-dir
    2. 环境变量 ASTRBOT_ROOT → <root>/data/plugin_data/<插件名>
    3. 桌面版运行时目录 ~/.astrbot/data/plugin_data/<插件名>（存在则采用）
    4. 当前工作目录 ./data/plugin_data/<插件名>（在 AstrBot 根目录下运行时的默认）
    """
    if target_dir is not None:
        return target_dir.resolve()
    if root := os.environ.get("ASTRBOT_ROOT"):
        base = Path(root).resolve() / "data"
    elif (Path.home() / ".astrbot" / "data").is_dir():
        base = Path.home() / ".astrbot" / "data"
    else:
        base = Path.cwd() / "data"
    return base / "plugin_data" / PLUGIN_NAME


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="真寻 bot SQLite 数据迁移到 AstrBot 插件数据库（幂等，可重复执行）",
        epilog="示例: python migrate_zhenxun.py --zhenxun-db D:/zhenxun_bot/data/db/zhenxun.db "
               "--farm-db D:/zhenxun_bot/data/farm_db/farm.db --target-dir D:/AstrBot/data/plugin_data/astrbot_plugin_zhenxun_economy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--zhenxun-db", required=True, type=Path, help="真寻经济库 zhenxun.db 路径（必填）")
    parser.add_argument("--farm-db", type=Path, default=None, help="真寻农场库 farm.db 路径（可省略，省略则跳过农场迁移）")
    parser.add_argument("--target-dir", type=Path, default=None,
                        help="AstrBot 插件数据目录。缺省自动定位：$ASTRBOT_ROOT/data/plugin_data/astrbot_plugin_zhenxun_economy，"
                             "其次 ~/.astrbot/data/...（桌面版），最后 ./data/plugin_data/...（在 AstrBot 根目录运行时）")
    parser.add_argument("--verify", action="store_true",
                        help="校验模式：只读对比两库各表行数与 user_console.gold 抽样，打印报告不写入")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.zhenxun_db.exists():
        log(f"[错误] 真寻数据库不存在: {args.zhenxun_db}")
        return 1
    if args.farm_db is not None and not args.farm_db.exists():
        log(f"[错误] 农场数据库不存在: {args.farm_db}")
        return 1

    target_dir = resolve_target_dir(args.target_dir)
    log(f"目标插件数据目录: {target_dir}")

    if args.verify:
        return 0 if verify(args.zhenxun_db, args.farm_db, target_dir) else 1

    migrate_economy(args.zhenxun_db, target_dir)
    if args.farm_db is not None:
        migrate_farm(args.farm_db, target_dir)
    log("=== 迁移完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
