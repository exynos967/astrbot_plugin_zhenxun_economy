"""共享表格卡片组件测试：模板数据组装 / 模板加载 / 渲染调用"""

import pytest

from modules._shared.table_card import (
    build_table_data,
    load_template,
    render_table_card,
)


class TestBuildTableData:
    """build_table_data 数据组装（对齐原版 TableData 结构）"""

    def test_basic_fields(self):
        data = build_table_data("排行榜", "提示", ["排名", "名称"], [])
        assert data["title"] == "排行榜"
        assert data["tip"] == "提示"
        assert data["headers"] == ["排名", "名称"]
        assert data["rows"] == []

    def test_tip_none(self):
        data = build_table_data("排行榜", None, [], [])
        assert data["tip"] is None

    def test_text_cell_normalization(self):
        data = build_table_data("t", None, ["a", "b", "c"], [["文本", 42, 3.14]])
        cells = data["rows"][0]
        assert cells[0] == {
            "type": "text", "content": "文本", "bold": False, "color": None,
        }
        # int/float 归一化为字符串文本（对齐原版 TextCell(content=str(x))）
        assert cells[1]["content"] == "42"
        assert cells[2]["content"] == "3.14"

    def test_none_cell_becomes_empty_text(self):
        data = build_table_data("t", None, ["a"], [[None]])
        assert data["rows"][0][0]["type"] == "text"
        assert data["rows"][0][0]["content"] == ""

    def test_image_cell_tuple(self):
        uri = "data:image/png;base64,AAAA"
        data = build_table_data("t", None, ["头像"], [[("image", uri)]])
        cell = data["rows"][0][0]
        assert cell == {
            "type": "image",
            "src": uri,
            "width": 40,
            "height": 40,
            "shape": "circle",
            "alt": "image",
        }

    def test_headers_str_coercion(self):
        data = build_table_data("t", None, [1, "好感度"], [])
        assert data["headers"] == ["1", "好感度"]

    def test_multiple_rows(self):
        data = build_table_data(
            "t", None, ["排名", "名称"],
            [[1, "真寻"], [2, "远枫"]],
        )
        assert len(data["rows"]) == 2
        assert data["rows"][1][1]["content"] == "远枫"


class TestLoadTemplate:
    """模板加载：字体注入与结构完整性"""

    def test_fonts_inlined(self):
        tmpl = load_template()
        assert "/*__INLINE_FONTS__*/" not in tmpl
        assert tmpl.count("@font-face") == 2
        assert "fzrzFont" in tmpl
        assert "AlibabaHealthFont2" in tmpl
        assert "data:font/woff2;base64," in tmpl

    def test_template_structure(self):
        tmpl = load_template()
        # 原版 table/main.html 的关键结构保留
        assert '<div class="container">' in tmpl
        assert '<div class="header">' in tmpl
        assert '<div class="table-wrapper">' in tmpl
        assert "{{ data.title }}" in tmpl
        assert "cell-image {{ 'circular' if cell.shape == 'circle' }}" in tmpl
        # 原版 table/style.css 关键样式（CSS 变量已固化为 palette.json 字面值）
        assert "background-color: #EAEDF2;" in tmpl
        assert "border-radius: 50%;" in tmpl
        assert "var(--color-" not in tmpl


class _FakePlugin:
    """html_render stub：记录调用参数"""

    def __init__(self, url="http://example.com/table.png", error=None):
        self.url = url
        self.error = error
        self.calls = []

    async def html_render(self, tmpl, data, return_url=True, options=None):
        self.calls.append({"tmpl": tmpl, "data": data, "options": options})
        if self.error:
            raise self.error
        return self.url


class TestRenderTableCard:
    @pytest.mark.asyncio
    async def test_render_success(self):
        plugin = _FakePlugin()
        url = await render_table_card(
            plugin, "好感度群组内排行", None, ["排名"], [[1]],
        )
        assert url == "http://example.com/table.png"
        call = plugin.calls[0]
        assert call["data"]["data"]["title"] == "好感度群组内排行"
        assert call["data"]["data"]["rows"][0][0]["content"] == "1"
        # 默认视口宽度对齐原版 manifest（800），png + 高缩放
        assert call["options"] == {
            "viewport_width": 800,
            "full_page": True,
            "type": "png",
            "device_scale_factor_level": "high",
        }

    @pytest.mark.asyncio
    async def test_custom_viewport_width(self):
        plugin = _FakePlugin()
        await render_table_card(plugin, "t", None, [], [], viewport_width=600)
        assert plugin.calls[0]["options"]["viewport_width"] == 600

    @pytest.mark.asyncio
    async def test_render_failure_returns_none(self):
        plugin = _FakePlugin(error=RuntimeError("t2i 服务不可用"))
        url = await render_table_card(plugin, "t", None, ["a"], [["b"]])
        assert url is None
