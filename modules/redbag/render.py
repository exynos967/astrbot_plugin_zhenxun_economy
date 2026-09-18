"""金币红包图片合成（PIL 逐行复刻真寻 gold_redbag 原版逻辑）。

- 底图素材：assets/redbag_2（红包封面随机底图）、assets/redbag_1（开包结果随机底图），
  均拷贝自原版 resources/image/prts/
- 字体：assets/fonts/HYWenHei-85W.ttf（默认）、CJGaoDeGuo.otf（结算榜名次），
  拷贝自原版 resources/font/，PIL 直接加载本地文件
- 合成函数均为纯参数输入（不依赖 event），异常由调用方捕获并降级纯文本
- 文字尺寸/圆形头像/圆角/贴图蒙版等语义与原版 zhenxun.utils._build_image.BuildImage 对齐
"""

import base64
from io import BytesIO
import os
from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = Path(__file__).parent / "assets"
COVER_DIR = ASSETS_DIR / "redbag_2"
"""红包封面底图目录（原版 prts/redbag_2）"""
OPEN_DIR = ASSETS_DIR / "redbag_1"
"""开包结果底图目录（原版 prts/redbag_1）"""
FONT_DIR = ASSETS_DIR / "fonts"

DEFAULT_FONT = "HYWenHei-85W.ttf"
RANK_NO_FONT = "CJGaoDeGuo.otf"

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """加载字体（带缓存），对应原版 BuildImage.load_font"""
    key = (name, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(FONT_DIR / name), size)
    return _font_cache[key]


def _text_size(text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """文字宽高，对应原版 getsize：textbbox 宽高，高度 +10"""
    tmp = Image.new("RGB", (1, 1), (255, 255, 255))
    box = ImageDraw.Draw(tmp).textbbox((0, 0), str(text), font=font)
    return box[2] - box[0], box[3] - box[1] + 10


def _paste(bg: Image.Image, fg: Image.Image, pos=(0, 0), center_type: str | None = None):
    """贴图，对应原版 paste：以 fg 的 alpha 作蒙版，失败回退普通粘贴"""
    if center_type == "center":
        pos = (int((bg.width - fg.width) / 2), int((bg.height - fg.height) / 2))
    elif center_type == "width":
        pos = (int((bg.width - fg.width) / 2), pos[1])
    elif center_type == "height":
        pos = (pos[0], int((bg.height - fg.height) / 2))
    try:
        bg.paste(fg, pos, fg)
    except ValueError:
        bg.paste(fg, pos)


def _circle(img: Image.Image) -> Image.Image:
    """图像变圆，对应原版 circle（4x 抗锯齿掩膜）"""
    size = img.size
    r2 = min(size[0], size[1])
    if size[0] != size[1]:
        img = img.resize((r2, r2), Image.LANCZOS)
    width = 1
    antialias = 4
    ellipse_box = [0, 0, r2 - 2, r2 - 2]
    mask = Image.new(
        size=[int(dim * antialias) for dim in img.size], mode="L", color="black"
    )
    draw = ImageDraw.Draw(mask)
    for offset, fill in (width / -2.0, "black"), (width / 2.0, "white"):
        left, top = ((value + offset) * antialias for value in ellipse_box[:2])
        right, bottom = ((value - offset) * antialias for value in ellipse_box[2:])
        draw.ellipse([left, top, right, bottom], fill=fill)
    mask = mask.resize(img.size, Image.LANCZOS)
    try:
        img.putalpha(mask)
    except ValueError:
        pass
    return img


def _circle_corner(img: Image.Image, radii: int = 30) -> Image.Image:
    """矩形四角变圆，对应原版 circle_corner（默认四角全圆）"""
    img = img.convert("RGBA")
    alpha = img.split()[-1]
    circle = Image.new("L", (radii * 2, radii * 2), 0)
    draw = ImageDraw.Draw(circle)
    draw.ellipse((0, 0, radii * 2, radii * 2), fill=255)
    w, h = img.size
    alpha.paste(circle.crop((0, 0, radii, radii)), (0, 0))
    alpha.paste(circle.crop((radii, 0, radii * 2, radii)), (w - radii, 0))
    alpha.paste(circle.crop((0, radii, radii, radii * 2)), (0, h - radii))
    alpha.paste(circle.crop((radii, radii, radii * 2, radii * 2)), (w - radii, h - radii))
    img.putalpha(alpha)
    return img


def _build_text_image(text: str, size: int, font_color) -> Image.Image:
    """文字转透明底图片，对应原版 build_text_image（文字实际落点 (0,0)）"""
    font = _load_font(DEFAULT_FONT, size)
    w, h = _text_size(text, font)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 0), text, fill=font_color, font=font)
    return img


def _avatar_image(data: bytes | None, size: int, blank_color=(255, 255, 255)) -> Image.Image:
    """头像字节转指定尺寸图片，无头像时给纯色占位图（与原版一致）"""
    if data:
        return Image.open(BytesIO(data)).resize((size, size), Image.LANCZOS)
    return Image.new("RGBA", (size, size), blank_color)


def _to_png_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_cover_image(msg: str = "恭喜发财 大吉大利", avatar: bytes | None = None) -> bytes:
    """红包封面（原版 random_red_bag_background）：
    redbag_2 随机底图 + 发起人圆形头像(65x65, y=130) + 祝福语(y=210, 38号字)"""
    background_list = os.listdir(COVER_DIR)
    if not background_list:
        raise ValueError("redbag_2 背景图列表为空...")
    bg = Image.open(COVER_DIR / random.choice(background_list))
    font = _load_font(DEFAULT_FONT, 38)
    ava = _avatar_image(avatar, 65, blank_color=(0, 0, 0))
    ava = _circle(ava)
    # 原版先画祝福语再贴头像
    ImageDraw.Draw(bg).text(
        (int((bg.width - _text_size(msg, font)[0]) / 2), 210),
        msg,
        fill=(240, 218, 164),
        font=font,
    )
    _paste(bg, ava, (int((bg.width - 65) / 2), 130))
    return _to_png_bytes(bg)


def build_open_result_image(
    name: str,
    amount: int,
    opened_count: int,
    num: int,
    opened_sum: int,
    total_amount: int,
    avatar: bytes | None = None,
) -> bytes:
    """开包结果（原版 build_open_result_image）：
    redbag_1 随机底图(缩放至1000x980) + 头像昵称条 + 金额大字 + 领取进度"""
    background_list = os.listdir(OPEN_DIR)
    if not background_list:
        raise ValueError("redbag_1 背景图列表为空...")
    head = Image.open(OPEN_DIR / random.choice(background_list)).resize(
        (1000, 980), Image.LANCZOS
    )
    font30 = _load_font(DEFAULT_FONT, 30)
    font50 = _load_font(DEFAULT_FONT, 50)
    font150 = _load_font(DEFAULT_FONT, 150)
    # 头像 + 昵称横条
    name_w, _ = _text_size(name, font50)
    ava_bk = Image.new("RGBA", (100 + name_w, 66), (255, 255, 255, 0))
    ava = _avatar_image(avatar, 65, blank_color=(0, 0, 0))
    _paste(ava_bk, ava, (0, 0))
    ImageDraw.Draw(ava_bk).text((100, 7), name, fill=(0, 0, 0), font=font50)
    _paste(head, ava_bk, (int((1000 - ava_bk.width) / 2), 300))
    # 金额大字 + 金币中文
    amount_w, amount_h = _text_size(str(amount), font150)
    amount_img = Image.new("RGBA", (amount_w, amount_h + 50), (255, 255, 255, 0))
    ImageDraw.Draw(amount_img).text(
        (0, 0), str(amount), fill=(209, 171, 108), font=font150
    )
    _paste(head, amount_img, (int((1000 - amount_w) / 2) - 50, 460))
    draw = ImageDraw.Draw(head)
    draw.text(
        (int((1000 - amount_w) / 2 + amount_w) - 50, 500 + amount_h - 70),
        "金币",
        fill=(209, 171, 108),
        font=font30,
    )
    # 剩余数量和金额
    draw.text(
        (350, 900),
        f"已领取{opened_count}/{num}个，共{opened_sum}/{total_amount}金币",
        fill=(198, 198, 198),
        font=font30,
    )
    return _to_png_bytes(head)


def build_amount_rank(
    bag_name: str,
    open_user: dict[str, int],
    names: dict[str, str],
    avatars: dict[str, bytes | None],
    promoter_avatar: bytes | None = None,
    num: int = 10,
) -> bytes:
    """结算手气榜（原版 RedBag.build_amount_rank）：
    红头(发起人圆形头像+包名+结算排行) + 每行(名次/圆角头像/昵称/金额)"""
    font30 = _load_font(DEFAULT_FONT, 30)
    font_no = _load_font(RANK_NO_FONT, 65)
    user_image_list: list[Image.Image] = []
    if open_user:
        sort_data = sorted(open_user.items(), key=lambda item: item[1], reverse=True)
        num = min(num, len(open_user))
        for i in range(num):
            user_id, amount = sort_data[i]
            row = Image.new("RGBA", (600, 100), (255, 255, 255))
            ava = _avatar_image(avatars.get(user_id), 80)
            ava = _circle_corner(ava, 10)
            _paste(row, ava, (130, 10))
            # 名次（高德国字体居中）+ 右侧分隔线
            no_img = Image.new("RGBA", (100, 100), (255, 255, 255))
            no_w, no_h = _text_size(str(i + 1), font_no)
            no_draw = ImageDraw.Draw(no_img)
            no_draw.text(
                (int((100 - no_w) / 2), int((100 - no_h) / 2)),
                str(i + 1),
                fill=(0, 0, 0),
                font=font_no,
            )
            no_draw.line((99, 10, 99, 90), "#b9b9b9", 1)
            _paste(row, no_img, (0, 0))
            row_draw = ImageDraw.Draw(row)
            row_draw.text((225, 15), names.get(user_id, ""), fill=(0, 0, 0), font=font30)
            amount_img = _build_text_image(f"{amount} 金币", 30, "#cdac72")
            _paste(row, amount_img, (600 - amount_img.width - 20, 50))
            row_draw.line((225, 99, 590, 99), "#b9b9b9", 1)
            user_image_list.append(row)
    background = Image.new(
        "RGBA", (600, 150 + len(user_image_list) * 100), (255, 255, 255)
    )
    # 红色头部：发起人圆形头像 + 包名 + 结算排行
    top = Image.new("RGBA", (600, 100), "#f55545")
    promoter_ava = _circle(_avatar_image(promoter_avatar, 60))
    _paste(top, promoter_ava, (10, 0), "height")
    ImageDraw.Draw(top).text((80, 33), bag_name, fill=(255, 255, 255), font=font30)
    right_text = Image.new("RGBA", (150, 100), "#f55545")
    right_draw = ImageDraw.Draw(right_text)
    right_draw.text((10, 33), "结算排行", fill=(255, 255, 255), font=font30)
    right_draw.line((4, 10, 4, 90), (255, 255, 255), 2)
    _paste(top, right_text, (460, 0))
    _paste(background, top, (0, 0))
    cur_h = 110
    for user_image in user_image_list:
        _paste(background, user_image, (0, cur_h))
        cur_h += user_image.height
    return _to_png_bytes(background)


def to_base64(img_bytes: bytes) -> str:
    """图片字节转 base64 字符串（Comp.Image.fromBase64 用）"""
    return base64.b64encode(img_bytes).decode()
