"""控制台 Web API（WebUI 插件控制台页面 pages/console/ 的后端）

- build_state / validate_config_update / parse_module_toggle 为纯函数，可脱离 request 单测
- register_console_apis(plugin) 由 main.py 调用，注册 console/state、console/module、console/config 三个路由
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

try:  # AstrBot 运行时：插件根目录是 data.plugins.<dirname> 包
    from ..core import db
except ImportError:  # 测试环境：插件根目录直接在 sys.path
    from core import db

PLUGIN_NAME = "astrbot_plugin_zhenxun_economy"
SCHEMA_PATH = Path(__file__).parent.parent / "_conf_schema.json"

# 控制台展示的模块清单（enabled 由 build_state 按 config 动态填充）
MODULES: list[dict[str, str]] = [
    {"id": "sign_in", "name": "签到", "desc": "每日签到/好感度", "version": "v1.0"},
    {"id": "shop", "name": "商店", "desc": "道具商店/金币排行", "version": "v1.0"},
    {"id": "bank", "name": "小真寻银行", "desc": "存款取款/利息结算", "version": "v1.0"},
    {"id": "russian", "name": "俄罗斯轮盘", "desc": "装弹对决/战绩排行", "version": "v1.0"},
    {"id": "redbag", "name": "金币红包", "desc": "塞红包/抢红包", "version": "v1.0"},
    {"id": "farm", "name": "真寻农场", "desc": "农场种菜/农场币兑换", "version": "v1.0"},
]
MODULE_IDS = {m["id"] for m in MODULES}

_BOOL_TRUE = {"true", "1"}
_BOOL_FALSE = {"false", "0"}


def load_schema() -> dict[str, Any]:
    """读取插件根目录 _conf_schema.json，失败时返回空 dict 并记录日志"""
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"[zhenxun_economy] 读取 _conf_schema.json 失败: {e}")
        return {}


def build_state(config: dict, schema: dict, stats: dict) -> dict[str, Any]:
    """组装控制台总览状态（纯函数）

    Args:
        config: 当前插件配置（AstrBotConfig / dict）
        schema: _conf_schema.json 内容
        stats: collect_stats() 的统计结果

    Returns:
        {modules, config, schema, stats} 完整状态字典；config 缺失键用 schema default 补齐
    """
    modules = [
        {**m, "enabled": bool(config.get(f"module_{m['id']}_enabled", True))}
        for m in MODULES
    ]
    conf = {key: config.get(key, item.get("default")) for key, item in schema.items()}
    return {"modules": modules, "config": conf, "schema": schema, "stats": stats}


def _coerce_value(type_: str, value: Any) -> Any:
    """按 schema type 强转配置值，失败抛 ValueError/TypeError"""
    if type_ == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            low = value.strip().lower()
            if low in _BOOL_TRUE:
                return True
            if low in _BOOL_FALSE:
                return False
        raise ValueError(f"无法转换为 bool: {value!r}")
    if type_ == "int":
        if isinstance(value, bool):
            raise ValueError("bool 不能作为 int")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value.is_integer():
                return int(value)
            raise ValueError(f"非整数值: {value!r}")
        if isinstance(value, str):
            return int(value.strip())
        raise ValueError(f"无法转换为 int: {value!r}")
    if type_ == "float":
        if isinstance(value, bool):
            raise ValueError("bool 不能作为 float")
        return float(value)
    if type_ == "string":
        return value if isinstance(value, str) else str(value)
    raise ValueError(f"未知配置类型: {type_}")


def validate_config_update(
    schema: dict, payload: Any
) -> tuple[bool, str | None, dict[str, Any]]:
    """校验并按 schema 类型强转配置更新（纯函数，全部合法才返回转换结果）

    Returns:
        (ok, error_message, converted_values)；任一键非法/转换失败时 ok=False 且 values 为空
    """
    if not isinstance(payload, dict) or not payload:
        return False, "请求体必须是非空 JSON 对象", {}
    converted: dict[str, Any] = {}
    for key, value in payload.items():
        item = schema.get(key)
        if item is None:
            return False, f"非法配置键: {key}", {}
        type_ = item.get("type", "string")
        try:
            converted[key] = _coerce_value(type_, value)
        except (ValueError, TypeError):
            return False, f"配置项 {key} 的值 {value!r} 无法转换为 {type_}", {}
    return True, None, converted


def parse_module_toggle(payload: Any) -> tuple[bool, str | None, str, bool]:
    """校验模块开关请求（纯函数）

    Returns:
        (ok, error_message, module_id, enabled)
    """
    if not isinstance(payload, dict):
        return False, "请求体必须是 JSON 对象", "", False
    module = payload.get("module")
    if module not in MODULE_IDS:
        return False, f"非法模块名: {module}", "", False
    try:
        enabled = _coerce_value("bool", payload.get("enabled"))
    except (ValueError, TypeError):
        return False, "enabled 必须是布尔值", "", False
    return True, None, module, enabled


async def collect_stats() -> dict[str, Any]:
    """统计各表数据（SUM 在空表上返回 None，db.fetchval 自动兜底为 0）"""
    return {
        "user_count": await db.fetchval("SELECT COUNT(*) FROM user_console"),
        "total_gold": await db.fetchval("SELECT SUM(gold) FROM user_console"),
        "today_sign_count": await db.fetchval(
            "SELECT COUNT(*) FROM sign_log"
            " WHERE date(create_time) = date('now', 'localtime')"
        ),
        "bank_total_amount": await db.fetchval("SELECT SUM(amount) FROM mahiro_bank"),
        "redbag_total_sent": await db.fetchval(
            "SELECT SUM(send_redbag_count) FROM redbag_users"
        ),
        "russian_total_games": await db.fetchval(
            "SELECT SUM(win_count + fail_count) FROM russian_users"
        ),
    }


def register_console_apis(plugin) -> None:
    """注册控制台 Web API（由 main.py 调用）

    Args:
        plugin: Star 子类实例；plugin.config 为 AstrBotConfig（dict 子类，
            修改后调用 plugin.config.save_config() 落盘）
    """
    prefix = f"/{PLUGIN_NAME}/console"

    async def get_state():
        stats = await collect_stats()
        return json_response(build_state(plugin.config, load_schema(), stats))

    async def toggle_module():
        payload = await request.json(default={})
        ok, err, module, enabled = parse_module_toggle(payload)
        if not ok:
            return error_response(err, status_code=400)
        plugin.config[f"module_{module}_enabled"] = enabled
        plugin.config.save_config()
        logger.info(f"[zhenxun_economy] 控制台切换模块 {module} -> {enabled}")
        return json_response({"saved": True})

    async def update_config():
        payload = await request.json(default={})
        ok, err, values = validate_config_update(load_schema(), payload)
        if not ok:
            return error_response(err, status_code=400)
        for key, value in values.items():
            plugin.config[key] = value
        plugin.config.save_config()
        logger.info(f"[zhenxun_economy] 控制台更新配置: {list(values)}")
        return json_response({"saved": True})

    plugin.context.register_web_api(prefix + "/state", get_state, ["GET"], "控制台总览状态")
    plugin.context.register_web_api(
        prefix + "/module", toggle_module, ["POST"], "控制台模块开关"
    )
    plugin.context.register_web_api(
        prefix + "/config", update_config, ["POST"], "控制台配置更新"
    )
