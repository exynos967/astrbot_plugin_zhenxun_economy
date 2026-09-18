"""签到卡片渲染：模板加载 / HTML 渲染 / 纯文本降级。

远程 t2i 渲染端点访问不到本地文件，模板内所有资源均为 base64 data URI：
- 静态装饰图（rl/1/2/h1/h2）首次加载后缓存
- 天气图 / 标签图每次随机抽取
- 大图（1.png/2.png）按展示尺寸 2 倍降采样，控制端点请求体积
"""

import base64
import random
from pathlib import Path

from astrbot.api import logger

ASSETS_DIR = Path(__file__).parent / "assets"
IMG_DIR = ASSETS_DIR / "img"

# 大图降采样目标宽度（CSS 展示宽度的 2 倍，兼顾清晰度）
_DOWNSCALE_WIDTH = {"1.png": 240, "2.png": 264}

_template_cache: str | None = None
_static_uri_cache: dict[str, str] = {}
_fonts_css_cache: str | None = None

# 签到卡片用到的主题字体（woff2，渲染时 base64 内联，还原原版观感）
_FONTS = {
    "cr105Font": "ChillReunion_105S.woff2",
    "cr65sFont": "ChillReunion_65S.woff2",
    "shFont": "SourceHanSansSC-Bold.woff2",
    "rxxxtFont": "rxxxkat.woff2",
    "kcytFont": "jcyt.woff2",
}


def _build_fonts_css() -> str:
    """把主题 woff2 字体转为 base64 @font-face 块（带缓存）"""
    global _fonts_css_cache
    if _fonts_css_cache is None:
        rules = []
        for family, filename in _FONTS.items():
            data = (ASSETS_DIR / "fonts" / filename).read_bytes()
            b64 = base64.b64encode(data).decode()
            rules.append(
                f"@font-face {{ font-family: '{family}'; "
                f"src: url('data:font/woff2;base64,{b64}') format('woff2'); "
                f"font-display: swap; }}"
            )
        _fonts_css_cache = "\n".join(rules)
    return _fonts_css_cache


def _file_data_uri(path: Path, max_width: int | None = None) -> str:
    """读取图片文件转 base64 data URI，可选按宽度降采样"""
    data = path.read_bytes()
    if max_width:
        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(data))
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, max(1, round(img.height * ratio))))
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                data = buf.getvalue()
        except Exception as e:
            logger.warning(f"[zhenxun_economy] 签到卡片图片降采样失败 {path.name}: {e}")
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def get_static_asset(name: str) -> str:
    """静态装饰图 data URI（rl.png / 1.png / 2.png / h1.png / h2.png），带缓存"""
    if name not in _static_uri_cache:
        _static_uri_cache[name] = _file_data_uri(
            IMG_DIR / name, _DOWNSCALE_WIDTH.get(name)
        )
    return _static_uri_cache[name]


def random_weather_icon() -> str:
    """随机天气图（0~11），与真寻一致"""
    return _file_data_uri(IMG_DIR / "weather" / f"{random.randint(0, 11)}.png")


def random_tag_icon() -> str:
    """随机标签图（0~5），与真寻一致"""
    return _file_data_uri(IMG_DIR / "tag" / f"{random.randint(0, 5)}.png")


def load_template() -> str:
    """加载模板并把 style.css 内联进 <style>（端点不支持外部样式表）"""
    global _template_cache
    if _template_cache is None:
        tmpl = (ASSETS_DIR / "main.html").read_text(encoding="utf-8")
        css = (ASSETS_DIR / "style.css").read_text(encoding="utf-8")
        # 字体 @font-face 需先于使用处生效，注入到页面样式之前
        _template_cache = tmpl.replace(
            "/*__INLINE_STYLE__*/", _build_fonts_css() + "\n" + css
        )
    # 原版 body 的 -8px 绝对定位偏移是给原版渲染器的元素裁剪用的；
    # t2i 整页截图会在右侧留下 8px 白条，加载时中和掉
    return _template_cache + (
        "<style>body{position:static !important;left:0 !important;"
        "top:0 !important;margin:0 !important;padding:0 !important}</style>"
    )


async def render_card(plugin, card_data: dict) -> str | None:
    """调 AstrBot html_render 渲染卡片，失败返回 None（调用方降级纯文本）"""
    try:
        return await plugin.html_render(
            load_template(),
            card_data,
            return_url=True,
            options={
                "viewport_width": 465,
                "full_page": True,
                "type": "png",
                "device_scale_factor_level": "high",
            },
        )
    except Exception as e:
        logger.warning(f"[zhenxun_economy] 签到卡片 HTML 渲染失败，降级纯文本: {e}")
        return None


def render_text(d: dict) -> str:
    """纯文本卡片（渲染失败或 render_enabled=false 时的降级输出）"""
    u, f = d["user"], d["favorability"]
    title = "我的签到" if d["is_card_view"] else "签到成功"
    lines = [
        f"✨ {title} ✨",
        f"昵称：{u['nickname']}",
        f"UID：{u['uid_str']}",
    ]
    if not d["is_card_view"] and d.get("reward"):
        r = d["reward"]
        double = " (×2)" if r["is_double"] else ""
        lines.append(f"好感度 +{r['impression_added']:.2f}{double}")
        lines.append(f"金币 +{r['gold_added']}")
        if r["gift_received"]:
            lines.append(f"随机事件：{r['gift_received']}")
    else:
        lines.append(f"好感度排名第 {d.get('rank', 0)} 位")
        lines.append(f"总金币：{d.get('total_gold', 0)}")
    lines += [
        "────────",
        f"当前好感度：{f['current']:.2f}",
        f"好感度等级：{f['level_text']}",
        d["attitude"],
        f"累计签到：{u['sign_count']} 天",
        f"距离升级还差 {d['interpolation']} 好感度",
        d["bot_message"],
    ]
    return "\n".join(lines)
