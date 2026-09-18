"""轮盘三张卡的样例渲染脚本（直连 t2i 服务验证还原度）。

头像用 modules/sign_in/assets/img/ 下的图片转 base64 data URI 代替真实 QQ 头像。
输出: scripts/out/russian_start.png / russian_end.png / russian_record.png
"""
import asyncio
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
# 复用 conftest 的 astrbot stub
sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
import conftest  # noqa: F401 安装 astrbot stub

import aiohttp

# 直接按文件路径加载 render.py（避免触发 modules/russian/__init__ 的包级相对导入）
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "russian_render", Path(__file__).parent.parent / "modules" / "russian" / "render.py"
)
_russian_render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_russian_render)
load_template = _russian_render.load_template

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
IMG_DIR = Path(__file__).parent.parent / "modules" / "sign_in" / "assets" / "img"

T2I = "http://192.168.5.29:8999"


def avatar(name: str) -> str:
    return "data:image/png;base64," + base64.b64encode(
        (IMG_DIR / name).read_bytes()
    ).decode()


async def render(tmpl_name: str, data: dict, out_name: str):
    payload = {
        "tmpl": load_template(tmpl_name),
        "tmpldata": data,
        "json": True,
        "options": {
            "viewport_width": 640,
            "full_page": True,
            "type": "png",
            "device_scale_factor_level": "high",
            "animations": "disabled",
        },
    }
    t0 = time.time()
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{T2I}/text2img/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            ret = await resp.json()
            img_id = ret["data"]["id"]
        async with s.get(f"{T2I}/text2img/{img_id}") as resp:
            img = await resp.read()
    (OUT / out_name).write_bytes(img)
    print(f"{out_name} {time.time() - t0:.1f}s ({len(img) // 1024} KB)")


async def main():
    russian_start = {
        "player1": ["114514", "远枫"],
        "player2": ["1919810", "小真寻"],
        "money": 200,
        "bullet_num": 3,
        "bullet_arr": [0, 1, 0, 1, 0, 0, 1],
        "is_ai": False,
    }
    await render(
        "start.html",
        {
            "russian": russian_start,
            "player1_avatar": avatar("h1.png"),
            "player2_avatar": avatar("h2.png"),
            "player1_win_rate": 66.67,
            "player1_wins": 12,
            "player2_win_rate": 40.0,
            "player2_wins": 4,
            "weapon_name": "赌徒左轮",
            "weapon_description": "混乱",
            "weapon_effect": "每次射击后，都会随机打乱子弹排列",
        },
        "russian_start.png",
    )
    await render(
        "end.html",
        {
            "russian": russian_start,
            "win_user": ["114514", "远枫"],
            "winner": {"win_count": 13, "fail_count": 6, "make_money": 1500, "lose_money": 800,
                       "winning_streak": 3, "losing_streak": 0, "max_winning_streak": 5, "max_losing_streak": 2},
            "winner_avatar": avatar("h1.png"),
            "lose_user": ["1919810", "小真寻"],
            "loser": {"win_count": 4, "fail_count": 7, "make_money": 600, "lose_money": 1200,
                      "winning_streak": 0, "losing_streak": 2, "max_winning_streak": 2, "max_losing_streak": 3},
            "loser_avatar": avatar("h2.png"),
            "weapon_config": {
                "name": "赌徒左轮",
                "special_effect": {"name": "混乱", "description": "每次射击后，都会随机打乱子弹排列"},
            },
            "fee": 10,
            "rand": 5,
            "bot_nickname": "真寻",
        },
        "russian_end.png",
    )
    await render(
        "record.html",
        {
            "user": {"win_count": 13, "fail_count": 6, "make_money": 1500, "lose_money": 800,
                     "winning_streak": 3, "losing_streak": 0, "max_winning_streak": 5, "max_losing_streak": 2},
            "user_id": "114514",
            "user_name": "远枫",
            "user_avatar": avatar("h1.png"),
            "total_games": 19,
            "win_rate": 13 / 19 * 100,
            "net_profit": 700,
        },
        "russian_record.png",
    )


asyncio.run(main())
