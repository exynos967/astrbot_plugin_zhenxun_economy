"""用样例数据渲染签到卡片（直连 t2i 服务验证还原度）"""
import asyncio, sys, types, tempfile
from pathlib import Path
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

# 复用 conftest 的完整 astrbot stub
sys.path.insert(0, str(PLUGIN_ROOT / "tests"))
import conftest  # noqa: F401 安装 astrbot stub

import aiohttp
from modules.sign_in.render import load_template, get_static_asset, random_weather_icon, random_tag_icon

async def main():
    tmpl = load_template()
    print("模板大小: %.1f MB" % (len(tmpl) / 1024 / 1024))
    data = {
        "is_card_view": False,
        "user": {"nickname": "远枫", "uid_str": "0000 0002 0257", "avatar_url": "", "sign_count": 128, "font_size": 45},
        "favorability": {"current": 52.37, "level": 3, "level_text": "3 [普通]", "heart2": [1,1,1], "heart1": [1]*5, "next_level_at": 90, "previous_level_at": 50},
        "reward": {"impression_added": 0.93, "gold_added": 48, "gift_received": "", "is_double": False},
        "page": {"date_str": "2026-09-18 10:23:09", "weather_icon": random_weather_icon(), "temperature": 22, "tag_icon": random_tag_icon()},
        "assets": {"rl": get_static_asset("rl.png"), "deco1": get_static_asset("1.png"), "deco2": get_static_asset("2.png"), "heart1": get_static_asset("h1.png"), "heart2": get_static_asset("h2.png")},
        "bot_message": "真寻希望你开心！",
        "rank": 1, "total_gold": 148, "attitude": "一般", "progress": 5.9, "interpolation": "9.07",
    }
    payload = {"tmpl": tmpl, "tmpldata": data, "json": True,
               "options": {"viewport_width": 465, "full_page": True, "type": "png", "device_scale_factor_level": "high"}}
    import time
    t0 = time.time()
    async with aiohttp.ClientSession() as s:
        async with s.post("http://192.168.5.29:8999/text2img/generate", json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            print("POST status:", resp.status)
            ret = await resp.json()
            print("resp keys:", ret)
            img_id = ret["data"]["id"]
        async with s.get(f"http://192.168.5.29:8999/text2img/{img_id}") as resp:
            print("GET status:", resp.status, resp.headers.get("Content-Type"))
            img = await resp.read()
    Path("test_card.png").write_bytes(img)
    print(f"渲染完成 {time.time()-t0:.1f}s -> test_card.png ({len(img)//1024} KB)")

asyncio.run(main())
