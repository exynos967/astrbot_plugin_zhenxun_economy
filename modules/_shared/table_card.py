"""通用表格卡片渲染：1:1 移植真寻 ui.table 组件（core/table 模板 + 样式）。

远程 t2i 渲染端点访问不到本地文件，模板内所有资源均为 base64 data URI：
- 主题字体（AlibabaHealthFont2 / fzrzFont）以 woff2 base64 @font-face 注入
- 单元格头像由调用方传入 data URI（("image", data_uri) 元组，圆形展示）

用法:
    url = await render_table_card(
        plugin, "好感度群组内排行", "数据每 10 分钟刷新",
        ["排名", "头像", "名称", "好感度"],
        [[1, ("image", avatar_uri), "真寻", 52.37], ...],
    )
"""

import base64
from pathlib import Path
from typing import Any

from astrbot.api import logger

ASSETS_DIR = Path(__file__).parent / "assets"
# 共享字体目录（插件根 assets/fonts，字体名与文件名一致）
FONTS_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"

# 表格卡片用到的主题字体（表头 fzrzFont / 正文 AlibabaHealthFont2，与原版一致）
_FONTS = ("fzrzFont", "AlibabaHealthFont2")

# 原版 table 组件 manifest 的渲染视口宽度
_DEFAULT_VIEWPORT_WIDTH = 800

_template_cache: str | None = None
_fonts_css_cache: str | None = None


def _build_fonts_css() -> str:
    """把主题 woff2 字体转为 base64 @font-face 块（带缓存）"""
    global _fonts_css_cache
    if _fonts_css_cache is None:
        rules = []
        for family in _FONTS:
            data = (FONTS_DIR / f"{family}.woff2").read_bytes()
            b64 = base64.b64encode(data).decode()
            rules.append(
                f"@font-face {{ font-family: '{family}'; "
                f"src: url('data:font/woff2;base64,{b64}') format('woff2'); "
                f"font-display: swap; }}"
            )
        _fonts_css_cache = "\n".join(rules)
    return _fonts_css_cache


def load_template() -> str:
    """加载模板并把 base64 @font-face 注入样式头部（端点不支持外部资源）"""
    global _template_cache
    if _template_cache is None:
        tmpl = (ASSETS_DIR / "table.html").read_text(encoding="utf-8")
        # 字体 @font-face 需先于使用处生效，注入到页面样式之前
        _template_cache = tmpl.replace("/*__INLINE_FONTS__*/", _build_fonts_css())
    return _template_cache


def _normalize_cell(cell: Any) -> dict:
    """将单元格数据标准化为原版 TableCell 结构

    - ("image", data_uri) 元组 -> ImageCell（圆形头像，默认 40x40，对齐原版默认值）
    - 其余（str/int/float/None 等）-> TextCell（None 对齐原版归一化为空串）
    """
    if isinstance(cell, tuple) and len(cell) == 2 and cell[0] == "image":
        return {
            "type": "image",
            "src": cell[1],
            "width": 40,
            "height": 40,
            "shape": "circle",
            "alt": "image",
        }
    if cell is None:
        return {"type": "text", "content": "", "bold": False, "color": None}
    return {"type": "text", "content": str(cell), "bold": False, "color": None}


def build_table_data(
    title: str, tip: str | None, headers: list[str], rows: list[list]
) -> dict:
    """组装模板数据（结构对齐原版 TableData：title/tip/headers/rows）"""
    return {
        "title": title,
        "tip": tip,
        "headers": [str(h) for h in headers],
        "rows": [[_normalize_cell(cell) for cell in row] for row in rows],
    }


async def render_table_card(
    plugin,
    title: str,
    tip: str | None,
    headers: list[str],
    rows: list[list],
    *,
    viewport_width: int | None = None,
) -> str | None:
    """调 AstrBot html_render 渲染表格卡片，失败返回 None（调用方降级纯文本）

    参数:
        plugin: 插件实例（提供 html_render）
        title: 表格标题
        tip: 标题旁的提示文本（可选，反引号包裹内容会渲染为 code 样式）
        headers: 表头字段列表
        rows: 数据行，单元格支持 str/int/float/None 或 ("image", data_uri) 元组
        viewport_width: 渲染视口宽度，默认 800（原版表格组件 manifest 值）
    """
    try:
        return await plugin.html_render(
            load_template(),
            {"data": build_table_data(title, tip, headers, rows)},
            return_url=True,
            options={
                "viewport_width": viewport_width or _DEFAULT_VIEWPORT_WIDTH,
                "full_page": True,
                "type": "png",
                "device_scale_factor_level": "high",
            },
        )
    except Exception as e:
        logger.warning(f"[zhenxun_economy] 表格卡片 HTML 渲染失败，降级纯文本: {e}")
        return None
