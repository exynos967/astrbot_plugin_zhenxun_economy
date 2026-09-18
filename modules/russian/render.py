"""俄罗斯轮盘卡片渲染：模板加载 / HTML 渲染 / 失败降级。

模板为真寻原版 render/{start,end,record}.html 的逐字节拷贝，
仅将 var(--color-primary) 按默认主题 palette.json 机械替换为 #f67186。
远程 t2i 渲染端点访问不到本地文件，页脚字体 AlibabaHealthFont2
在模板加载时以 base64 @font-face 内联注入（共享字体 assets/fonts/aliFont.woff2）。
头像等动态资源由调用方以 base64 data URI 传入模板变量。
"""

import base64
from pathlib import Path

from astrbot.api import logger

ASSETS_DIR = Path(__file__).parent / "assets"
FONTS_DIR = Path(__file__).parent.parent.parent / "assets" / "fonts"

_template_cache: dict[str, str] = {}

# 原版 ui.render 视口宽 640（模板 max-width 600 + body padding 20*2）
_OPTIONS = {
    "viewport_width": 640,
    "full_page": True,
    "type": "png",
    "device_scale_factor_level": "high",
    "animations": "disabled",
}


def _fonts_css() -> str:
    """页脚字体 AlibabaHealthFont2 转 base64 @font-face 块"""
    data = (FONTS_DIR / "aliFont.woff2").read_bytes()
    b64 = base64.b64encode(data).decode()
    return (
        "@font-face { font-family: 'AlibabaHealthFont2'; "
        f"src: url('data:font/woff2;base64,{b64}') format('woff2'); "
        "font-display: swap; }"
    )


def load_template(name: str) -> str:
    """加载模板并在 </head> 前注入字体 @font-face（带缓存）"""
    if name not in _template_cache:
        tmpl = (ASSETS_DIR / name).read_text(encoding="utf-8")
        _template_cache[name] = tmpl.replace(
            "</head>", f"<style>{_fonts_css()}</style>\n</head>", 1
        )
    return _template_cache[name]


async def _render(plugin, name: str, data: dict) -> str | None:
    """调 AstrBot html_render 渲染卡片，失败返回 None（调用方降级纯文本）"""
    try:
        return await plugin.html_render(
            load_template(name), data, return_url=True, options=dict(_OPTIONS)
        )
    except Exception as e:
        logger.warning(f"[zhenxun_economy] 轮盘 {name} 渲染失败，降级纯文本: {e}")
        return None


async def render_start(plugin, data: dict) -> str | None:
    """开局对战卡（接受对决后）"""
    return await _render(plugin, "start.html", data)


async def render_end(plugin, data: dict) -> str | None:
    """结算卡"""
    return await _render(plugin, "end.html", data)


async def render_record(plugin, data: dict) -> str | None:
    """战绩卡"""
    return await _render(plugin, "record.html", data)
