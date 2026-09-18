"""银行 HTML 渲染（Jinja2 模板 + base64 内联资源，1:1 还原真寻原版）。

模板/CSS 逐字节保留原版（modules/bank/assets/ 下为原版拷贝），加载时做机械替换：
- {% include %} → 内联 CSS（var(--color-xxx) 按 palette.json 替换为字面值）
- asset('img/xxx') → 原版图片 base64 data URI（远程 t2i 端点访问不到本地文件）
- asset('js/echarts.min.js') → 内联 echarts 源码 <script> 块
- @font-face 注入插件根 assets/fonts/ 的主题字体（aliFont/fandolFont/freeFont/fzrzFont）
渲染依赖 AstrBot t2i 端点（plugin.html_render），抛异常时由调用方降级为纯文本。
"""

import base64
import re
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"
FONTS_DIR = Path(__file__).parent.parent.parent / "assets" / "fonts"

# 原版模板用到的主题字体（woff2，渲染时 base64 内联，还原原版观感）
_FONTS = ["aliFont", "fandolFont", "freeFont", "fzrzFont"]

# palette.json 配色：CSS var(--color-xxx) → 字面值
# 全局 colors + component_colors.mahiro_bank（组件键名下划线对应 CSS 连字符）
_CSS_VARS = {
    # 全局 colors
    "text_light": "#ffffff",
    "text_dark": "#333333",
    "text_muted": "#666666",
    "background_main": "#ffffff",
    "border_light": "#fde2e6",
    "border_dark": "#8f8f8f",
    "accent_green": "#67C23A",
    "accent_red": "#F56C6C",
    # mahiro_bank 组件配色
    "mahiro_bank_gradient_start": "#edbce6",
    "mahiro_bank_gradient_end": "#eed6e0",
    "mahiro_bank_header_text_light": "#f5e4ef",
    "mahiro_bank_avatar_bg": "#e6e6e6",
    "mahiro_bank_deco_bg": "#efdedf",
    "mahiro_bank_accent": "#eec8e5",
    "mahiro_bank_record_title": "#e74c8c",
    "mahiro_bank_dashed_border": "#e0d0d5",
    "mahiro_bank_status_expired_bg": "#cccccc",
}

_MIME = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg"}

_template_cache: dict[str, str] = {}
_fonts_css_cache: str | None = None
_data_uri_cache: dict[str, str] = {}


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


def _resolve_css_vars(css: str) -> str:
    """var(--color-xxx) 机械替换为 palette 字面值（xxx 连字符对应配色键下划线）"""
    def _sub(m: re.Match) -> str:
        return _CSS_VARS[m.group(1).replace("-", "_")]

    return re.sub(r"var\(--color-([a-z0-9-]+)\)", _sub, css)


def _data_uri(rel_path: str) -> str:
    """读取 assets 下的图片并转为 base64 data URI（带缓存）"""
    if rel_path not in _data_uri_cache:
        path = ASSETS_DIR / rel_path
        b64 = base64.b64encode(path.read_bytes()).decode()
        _data_uri_cache[rel_path] = f"data:{_MIME[path.suffix.lower()]};base64,{b64}"
    return _data_uri_cache[rel_path]


def _inline_include(m: re.Match) -> str:
    """{% include './xxx.css' %} → 内联 CSS（_base.css 前先注入字体 @font-face）"""
    css = _resolve_css_vars((ASSETS_DIR / m.group(1)).read_text(encoding="utf-8"))
    if m.group(1) == "_base.css":
        return _build_fonts_css() + "\n" + css
    return css


def _inline_asset(m: re.Match) -> str:
    """asset('img/xxx') → base64 data URI（原版原图，不降采样）"""
    return _data_uri(m.group(1).removeprefix("./"))


def load_template(name: str) -> str:
    """加载模板并完成全部内联（带缓存），返回可直接交给 html_render 的完整 HTML"""
    if name not in _template_cache:
        tmpl = (ASSETS_DIR / name).read_text(encoding="utf-8")
        # echarts 外链 script → 内联源码块（必须先于 asset() 替换处理）
        if "echarts.min.js" in tmpl:
            js = (ASSETS_DIR / "js" / "echarts.min.js").read_text(encoding="utf-8")
            tmpl = tmpl.replace(
                "<script src=\"{{ asset('js/echarts.min.js') }}\"></script>",
                "<script>" + js + "</script>",
            )
        tmpl = re.sub(r"\{% include '\./([^']+)' %\}", _inline_include, tmpl)
        tmpl = re.sub(r"\{\{ asset\('([^']+)'\) \}\}", _inline_asset, tmpl)
        # 原版 body 的 -8px 绝对定位偏移是给原版渲染器的元素裁剪用的；
        # t2i 整页截图会在右侧留下 8px 白条，加载时中和掉
        tmpl += (
            "<style>body{position:static !important;left:0 !important;"
            "top:0 !important;margin:0 !important;padding:0 !important}</style>"
        )
        _template_cache[name] = (
            '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
            "</head>\n<body>\n" + tmpl + "\n</body>\n</html>"
        )
    return _template_cache[name]


_RENDER_OPTIONS = {
    "full_page": True,
    "type": "png",
    "device_scale_factor_level": "high",
    "animations": "disabled",
}


async def render_user_info(plugin, payload: dict) -> str:
    """渲染"我的银行信息"卡片（原版 user.html，宽 386），返回图片 URL"""
    return await plugin.html_render(
        load_template("user.html"), {"data": payload}, return_url=True,
        options={**_RENDER_OPTIONS, "viewport_width": 386},
    )


async def render_bank_info(plugin, payload: dict) -> str:
    """渲染银行总览卡片（原版 overview.html，宽 450），返回图片 URL"""
    return await plugin.html_render(
        load_template("overview.html"), {"data": payload}, return_url=True,
        options={**_RENDER_OPTIONS, "viewport_width": 450},
    )


def user_info_text(payload: dict) -> str:
    """我的银行信息 - 纯文本降级"""
    lines = [
        f"【小真寻银行】{payload['name']} 的账户",
        f"当前存款: {payload['amount']} 金币 | 全服排名: No.{payload['rank']}",
        f"今日生效存款: {payload['today_deposit_count']} 笔 / {payload['today_deposit_amount']} 金币",
        f"预计收益: {payload['projected_revenue']} 金币 | 累计利息: {payload['cumulative_gain']} 金币",
    ]
    if payload["deposit_list"]:
        lines.append("—— 存款明细 ——")
        for dep in payload["deposit_list"]:
            lines.append(
                f"#{dep['id']} {dep['amount']}金币 @ {dep['rate']}%/小时"
                f" → 预计+{dep['projected_revenue']}（{dep['start_time']} 存入）"
            )
    else:
        lines.append("暂无生效中的存款哦~")
    return "\n".join(lines)


def bank_info_text(payload: dict) -> str:
    """银行总览 - 纯文本降级"""
    lines = [
        "【小真寻银行 · 总览】",
        f"总存款: {payload['amount_sum']} 金币 | 用户数: {payload['user_count']}",
        f"今日新增存款: {payload['today_count']} 笔 | 日均存款: {payload['day_amount']} 金币",
        f"累计发放利息: {payload['interest_amount']} 金币 | 近7日活跃用户: {payload['active_user_count']}",
        "—— 近7日存款 ——",
    ]
    lines.append(
        " / ".join(f"{item['date']}:{item['amount']}" for item in payload["trend"])
    )
    return "\n".join(lines)
