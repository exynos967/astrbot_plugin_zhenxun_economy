"""控制台 Web API 测试：纯函数单测 + 路由 handler 集成测试（stub 版 request/config）"""

import pytest

import modules.console_api as console_api
from modules.console_api import (
    MODULES,
    build_state,
    load_schema,
    parse_module_toggle,
    register_console_apis,
    validate_config_update,
)

SCHEMA = {
    "module_bank_enabled": {"description": "【模块开关】银行", "type": "bool", "default": True},
    "max_sign_gold": {"description": "签到金币上限", "type": "int", "default": 200},
    "sign_card1_prob": {"description": "卡Ⅰ掉率", "type": "float", "default": 0.2},
    "bot_name": {"description": "机器人昵称", "type": "string", "default": "真寻"},
}


# ---------- 纯函数：build_state ----------


def test_build_state_defaults_fill_from_schema():
    state = build_state({}, SCHEMA, {"user_count": 3})
    assert len(state["modules"]) == 6
    assert [m["id"] for m in state["modules"]] == [
        "sign_in", "shop", "bank", "russian", "redbag", "farm",
    ]
    assert all(m["enabled"] is True for m in state["modules"])
    assert all(m["version"] == "v1.0" for m in state["modules"])
    # 缺失键按 schema default 补齐
    assert state["config"]["max_sign_gold"] == 200
    assert state["config"]["bot_name"] == "真寻"
    assert state["stats"] == {"user_count": 3}
    assert state["schema"] is SCHEMA


def test_build_state_reads_existing_config():
    config = {"module_bank_enabled": False, "max_sign_gold": 500}
    state = build_state(config, SCHEMA, {})
    bank = next(m for m in state["modules"] if m["id"] == "bank")
    assert bank["enabled"] is False
    assert state["config"]["max_sign_gold"] == 500


def test_load_schema_reads_real_file():
    schema = load_schema()
    assert "module_sign_in_enabled" in schema
    assert schema["max_sign_gold"]["type"] == "int"
    assert schema["bot_name"]["default"] == "真寻"


# ---------- 纯函数：validate_config_update ----------


def test_validate_basic_types():
    ok, err, values = validate_config_update(
        SCHEMA, {"max_sign_gold": "300", "sign_card1_prob": "0.5", "bot_name": "小真寻"}
    )
    assert ok and err is None
    assert values == {"max_sign_gold": 300, "sign_card1_prob": 0.5, "bot_name": "小真寻"}
    assert isinstance(values["max_sign_gold"], int)


@pytest.mark.parametrize(
    "raw,expected",
    [(True, True), (False, False), ("true", True), ("false", False),
     ("True", True), ("FALSE", False), (1, True), (0, False), ("1", True), ("0", False)],
)
def test_validate_bool_variants(raw, expected):
    ok, err, values = validate_config_update(SCHEMA, {"module_bank_enabled": raw})
    assert ok, err
    assert values["module_bank_enabled"] is expected


@pytest.mark.parametrize("raw", ["yes", "on", 2, None, [1]])
def test_validate_bool_invalid(raw):
    ok, err, _ = validate_config_update(SCHEMA, {"module_bank_enabled": raw})
    assert not ok and err


def test_validate_rejects_unknown_key():
    ok, err, values = validate_config_update(SCHEMA, {"not_a_key": 1})
    assert not ok and "非法配置键" in err and values == {}


def test_validate_atomic_on_partial_failure():
    ok, err, values = validate_config_update(
        SCHEMA, {"max_sign_gold": 100, "bad_key": 1}
    )
    assert not ok and values == {}


@pytest.mark.parametrize("raw", ["abc", True, 1.5, None])
def test_validate_int_invalid(raw):
    ok, err, _ = validate_config_update(SCHEMA, {"max_sign_gold": raw})
    assert not ok and err


def test_validate_int_accepts_integral_float_and_str():
    ok, _, values = validate_config_update(SCHEMA, {"max_sign_gold": 300.0})
    assert ok and values["max_sign_gold"] == 300
    ok, _, values = validate_config_update(SCHEMA, {"max_sign_gold": " 250 "})
    assert ok and values["max_sign_gold"] == 250


def test_validate_string_coerces_and_float_rejects_bool():
    ok, _, values = validate_config_update(SCHEMA, {"bot_name": 123})
    assert ok and values["bot_name"] == "123"
    ok, err, _ = validate_config_update(SCHEMA, {"sign_card1_prob": True})
    assert not ok and err


@pytest.mark.parametrize("payload", [None, [], "x", {}])
def test_validate_rejects_non_dict_or_empty(payload):
    ok, err, _ = validate_config_update(SCHEMA, payload)
    assert not ok and err


# ---------- 纯函数：parse_module_toggle ----------


def test_parse_module_toggle_valid():
    ok, err, module, enabled = parse_module_toggle({"module": "sign_in", "enabled": False})
    assert ok and err is None
    assert module == "sign_in" and enabled is False


@pytest.mark.parametrize(
    "payload",
    [{"module": "farm2", "enabled": True}, {"module": "sign_in"},
     {"enabled": True}, {"module": "shop", "enabled": "maybe"}, None, "x",
],
)
def test_parse_module_toggle_invalid(payload):
    ok, err, _, _ = parse_module_toggle(payload)
    assert not ok and err


# ---------- 路由 handler 集成测试 ----------


class _FakeConfig(dict):
    def __init__(self):
        super().__init__()
        self.saved = 0

    def save_config(self):
        self.saved += 1


class _FakeContext:
    def __init__(self):
        self.routes = {}

    def register_web_api(self, route, handler, methods, desc):
        self.routes[(route, tuple(methods))] = handler


class _FakePlugin:
    def __init__(self):
        self.context = _FakeContext()
        self.config = _FakeConfig()


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self, default=None):
        return self._payload


@pytest.fixture
def plugin():
    p = _FakePlugin()
    register_console_apis(p)
    return p


def _handler(plugin, route, method):
    return plugin.context.routes[(route, (method,))]


def test_register_routes(plugin):
    assert set(plugin.context.routes) == {
        ("/astrbot_plugin_zhenxun_economy/console/state", ("GET",)),
        ("/astrbot_plugin_zhenxun_economy/console/module", ("POST",)),
        ("/astrbot_plugin_zhenxun_economy/console/config", ("POST",)),
    }


@pytest.mark.asyncio
async def test_state_handler(plugin, econ_db):
    await econ_db.execute(
        "INSERT INTO user_console (user_id, uid, gold) VALUES ('u1', 1, 250)"
    )
    await econ_db.execute(
        "INSERT INTO sign_log (user_id, impression) VALUES ('u1', 0.52)"
    )
    await econ_db.execute(
        "INSERT INTO mahiro_bank (user_id, amount) VALUES ('u1', 800)"
    )
    await econ_db.execute(
        "INSERT INTO redbag_users (user_id, group_id, send_redbag_count)"
        " VALUES ('u1', 'g1', 2)"
    )
    await econ_db.execute(
        "INSERT INTO russian_users (user_id, group_id, win_count, fail_count)"
        " VALUES ('u1', 'g1', 1, 2)"
    )
    resp = await _handler(
        plugin, "/astrbot_plugin_zhenxun_economy/console/state", "GET"
    )()
    body = resp["body"]
    assert resp["_status"] == 200
    assert len(body["modules"]) == 6
    assert body["config"]["max_sign_gold"] == 200  # schema default 补齐
    stats = body["stats"]
    assert stats["user_count"] == 1
    assert stats["total_gold"] == 250
    assert stats["today_sign_count"] == 1
    assert stats["bank_total_amount"] == 800
    assert stats["redbag_total_sent"] == 2
    assert stats["russian_total_games"] == 3


@pytest.mark.asyncio
async def test_module_toggle_handler(plugin, monkeypatch):
    handler = _handler(plugin, "/astrbot_plugin_zhenxun_economy/console/module", "POST")
    monkeypatch.setattr(
        console_api, "request", _FakeRequest({"module": "bank", "enabled": False})
    )
    resp = await handler()
    assert resp["_status"] == 200 and resp["body"] == {"saved": True}
    assert plugin.config["module_bank_enabled"] is False
    assert plugin.config.saved == 1

    # 非法模块名 -> 400 且不写配置
    monkeypatch.setattr(
        console_api, "request", _FakeRequest({"module": "nope", "enabled": True})
    )
    resp = await handler()
    assert resp["_status"] == 400
    assert resp["body"]["status"] == "error"
    assert plugin.config.saved == 1


@pytest.mark.asyncio
async def test_config_update_handler(plugin, monkeypatch):
    handler = _handler(plugin, "/astrbot_plugin_zhenxun_economy/console/config", "POST")
    monkeypatch.setattr(
        console_api, "request",
        _FakeRequest({"max_sign_gold": "300", "module_shop_enabled": "false"}),
    )
    resp = await handler()
    assert resp["_status"] == 200 and resp["body"] == {"saved": True}
    assert plugin.config["max_sign_gold"] == 300
    assert plugin.config["module_shop_enabled"] is False
    assert plugin.config.saved == 1

    # 类型转换失败 -> 400 且不落盘
    monkeypatch.setattr(
        console_api, "request", _FakeRequest({"max_sign_gold": "abc"})
    )
    resp = await handler()
    assert resp["_status"] == 400 and plugin.config.saved == 1

    # 非法键 -> 400 且已有配置不被污染
    monkeypatch.setattr(
        console_api, "request",
        _FakeRequest({"sign_card1_prob": 0.5, "evil_key": 1}),
    )
    resp = await handler()
    assert resp["_status"] == 400
    assert "sign_card1_prob" not in plugin.config
    assert plugin.config.saved == 1
