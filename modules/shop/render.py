"""商店/背包界面的 HTML 模板加载与纯文本降级文案

- 商店模板为原版 shop/main.html 的 1:1 移植（含 _base.html 骨架），结构逐字节保留
- 远程 t2i 渲染端点访问不到本地文件，模板内所有资源均为 base64 data URI：
  - 主题字体 hywhFont/fzrzFont/syhtFont（插件根 assets/fonts/，woff2 @font-face 内联）
  - 装饰图 split/head/title-bk/bag1/bag2 与左右侧立绘 left_right/*（asset() 静态解析）
  - 商品图标由 logic 层经 load_icon_b64 内联进 payload
- 模板首次加载完成全部内联后缓存，后续渲染零 IO
"""

import base64
import re
from pathlib import Path

from astrbot.api import logger

ASSETS_DIR = Path(__file__).parent / "assets"
IMG_DIR = ASSETS_DIR / "img"
ICON_DIR = ASSETS_DIR / "icons"
# 共享主题字体在插件根 assets/fonts/（与其它模块复用同一批 woff2）
FONT_DIR = Path(__file__).parent.parent.parent / "assets" / "fonts"

# 商店界面用到的主题字体（woff2，渲染时 base64 内联，还原原版观感；
# msyhFont 原版即未声明，保持悬空引用不声明）
_FONTS = {
    "hywhFont": "hywhFont.woff2",
    "fzrzFont": "fzrzFont.woff2",
    "syhtFont": "syhtFont.woff2",
}

# 原版 main.html 中按分区序号轮换的左右侧立绘（与 {% set %} 列表一一对应）
_LEFT_IMAGES = ["qq.png", "xx1.png", "xx2.png"]
_RIGHT_IMAGES = ["1.png", "2.png", "3.png", "4.png", "5.png"]

# 原版模板中的静态 asset() 调用：{{ asset('./img/xxx.png') }}
_ASSET_RE = re.compile(r"\{\{\s*asset\('\./img/([^']+)'\)\s*\}\}")

_template_cache: str | None = None
_img_uri_cache: dict[str, str] = {}
_fonts_css_cache: str | None = None
# 图标 base64 缓存（图标文件运行期不变）
_ICON_CACHE: dict[str, str] = {}


def _build_fonts_css() -> str:
    """把主题 woff2 字体转为 base64 @font-face 块（带缓存）"""
    global _fonts_css_cache
    if _fonts_css_cache is None:
        rules = []
        for family, filename in _FONTS.items():
            data = (FONT_DIR / filename).read_bytes()
            b64 = base64.b64encode(data).decode()
            rules.append(
                f"@font-face {{ font-family: '{family}'; "
                f"src: url('data:font/woff2;base64,{b64}') format('woff2'); "
                f"font-display: swap; }}"
            )
        _fonts_css_cache = "\n".join(rules)
    return _fonts_css_cache


def _img_uri(rel_path: str) -> str:
    """装饰图 data URI（相对 assets/img/ 的路径），带缓存"""
    if rel_path not in _img_uri_cache:
        data = (IMG_DIR / rel_path).read_bytes()
        _img_uri_cache[rel_path] = (
            "data:image/png;base64," + base64.b64encode(data).decode()
        )
    return _img_uri_cache[rel_path]


def _inline_assets(text: str) -> str:
    """把文本中的静态 asset() 调用替换为 base64 data URI"""
    return _ASSET_RE.sub(lambda m: _img_uri(m.group(1)), text)


def load_icon_b64(icon_name: str) -> str:
    """读取商品图标并转为 base64 data URI，缺失返回空串"""
    if not icon_name:
        return ""
    if icon_name in _ICON_CACHE:
        return _ICON_CACHE[icon_name]
    path = ICON_DIR / icon_name
    uri = ""
    if path.exists():
        uri = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    _ICON_CACHE[icon_name] = uri
    return uri


def load_template() -> str:
    """加载商店模板：内联 style.css/字体/全部装饰图（端点不支持外部资源），带缓存"""
    global _template_cache
    if _template_cache is None:
        tmpl = (ASSETS_DIR / "shop.html").read_text(encoding="utf-8")
        css = _inline_assets((ASSETS_DIR / "style.css").read_text(encoding="utf-8"))
        # 字体 @font-face 需先于使用处生效，注入到页面样式之前
        tmpl = tmpl.replace("/*__INLINE_STYLE__*/", _build_fonts_css() + "\n" + css)
        # 左右侧立绘：{% set %} 文件名列表直接替换为 data URI 列表，
        # 原版 asset('./img/left_right/' + ...) 动态拼接随之退化为列表取值
        left_list = "[" + ", ".join(f'"{_img_uri("left_right/" + n)}"' for n in _LEFT_IMAGES) + "]"
        right_list = "[" + ", ".join(f'"{_img_uri("left_right/" + n)}"' for n in _RIGHT_IMAGES) + "]"
        tmpl = tmpl.replace(
            '{% set left_images = ["qq.png", "xx1.png", "xx2.png"] %}',
            "{% set left_images = " + left_list + " %}",
        )
        tmpl = tmpl.replace(
            '{% set right_images = ["1.png", "2.png", "3.png", "4.png", "5.png"] %}',
            "{% set right_images = " + right_list + " %}",
        )
        tmpl = tmpl.replace(
            "{{ asset('./img/left_right/' + left_images[loop.index0 % left_images|length]) }}",
            "{{ left_images[loop.index0 % left_images|length] }}",
        )
        tmpl = tmpl.replace(
            "{{ asset('./img/left_right/' + right_images[loop.index0 % right_images|length]) }}",
            "{{ right_images[loop.index0 % right_images|length] }}",
        )
        # 剩余静态 asset()（bag1/bag2 等）
        tmpl = _inline_assets(tmpl)
        # 原版 body 的 -8px 绝对定位偏移是给原版渲染器的元素裁剪用的；
        # t2i 整页截图会在右侧留下 8px 白条，加载时中和掉
        tmpl += (
            "<style>body{position:static !important;left:0 !important;"
            "top:0 !important}</style>"
        )
        _template_cache = tmpl
    return _template_cache


async def render_shop(plugin, data: dict) -> str | None:
    """调 AstrBot html_render 渲染商店界面，失败返回 None（调用方降级纯文本）

    options 对齐原版 manifest.json（viewport 宽 850 = .wrapper 宽度）
    """
    try:
        return await plugin.html_render(
            load_template(),
            data,
            return_url=True,
            options={
                "viewport_width": 850,
                "full_page": True,
                "type": "png",
                "device_scale_factor_level": "high",
                "animations": "disabled",
            },
        )
    except Exception as e:
        logger.warning(f"[zhenxun_economy] 商店界面 HTML 渲染失败，降级纯文本: {e}")
        return None


def get_props_template() -> str:
    return (ASSETS_DIR / "props.html").read_text(encoding="utf-8")


def build_shop_text(data: dict) -> str:
    """商店纯文本降级文案（序号与购买指令的道具 ID 一致，1 起）"""
    lines = [
        f"{data['bot_nickname']}的神秘商店",
        "使用「购买道具 [道具ID/道具名称]」购买道具",
    ]
    for category in data["categories"]:
        lines.append(f"—— {category['partition_title']} ——")
        for goods in category["goods_list"]:
            if goods["discount_price"] is None:
                price = f"{goods['price']}金币"
            else:
                price = f"{goods['discount_price']}金币(原价{goods['price']})"
            line = f"{goods['id']}. {goods['name']} | {price} | 限购:{goods['daily_limit']}"
            if goods.get("limit_time"):
                line += f" | 限时:{goods['limit_time']}"
            lines.append(line)
            lines.append(f"    {goods['description']}")
    return "\n".join(lines)


def build_props_text(data: dict) -> str:
    """道具背包纯文本降级文案（序号与使用指令的道具 ID 一致，1 起）"""
    lines = [
        f"{data['user_name']}的道具仓库",
        "通过 使用道具[ID/名称] 令道具生效",
    ]
    for row in data["rows"]:
        lines.append(f"{row['id']}. {row['name']} ×{row['count']}")
        lines.append(f"    {row['description']}")
    return "\n".join(lines)
