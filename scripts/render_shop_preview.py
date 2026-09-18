"""用样例数据渲染商店界面（直连 t2i 服务验证 1:1 还原度）"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# 复用 conftest 的完整 astrbot stub
sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
import conftest  # noqa: F401 安装 astrbot stub

import aiohttp
from modules.shop.render import load_template, load_icon_b64

async def main():
    tmpl = load_template()
    print("模板大小: %.1f MB" % (len(tmpl) / 1024 / 1024))
    # 样例 payload：2 个分区 4 个商品，字段与 prepare_shop_data() 对齐
    data = {
        "bot_nickname": "真寻",
        "categories": [
            {
                "partition_title": "好感度专区",
                "goods_list": [
                    {"id": 1, "name": "好感度双倍加持卡Ⅰ", "description": "下次签到双倍好感度概率 + 10%（谁才是真命天子？）（同类商品将覆盖）",
                     "price": 30, "discount_price": None, "limit_time": None, "daily_limit": "∞",
                     "icon_url": load_icon_b64("favorability_card_1.png")},
                    {"id": 2, "name": "好感度双倍加持卡Ⅱ", "description": "下次签到双倍好感度概率 + 20%（平平庸庸）（同类商品将覆盖）",
                     "price": 150, "discount_price": 120, "limit_time": "47:32", "daily_limit": 1,
                     "icon_url": load_icon_b64("favorability_card_2.png")},
                    {"id": 3, "name": "好感度双倍加持卡Ⅲ", "description": "下次签到双倍好感度概率 + 30%（金币才是真命天子！）（同类商品将覆盖）",
                     "price": 250, "discount_price": None, "limit_time": None, "daily_limit": 3,
                     "icon_url": load_icon_b64("favorability_card_3.png")},
                ],
            },
            {
                "partition_title": "小秘密",
                "goods_list": [
                    {"id": 4, "name": "神秘药水", "description": "鬼知道会有什么效果，要不试试？",
                     "price": 999999, "discount_price": None, "limit_time": None, "daily_limit": "∞",
                     "icon_url": load_icon_b64("mysterious_potion.png")},
                ],
            },
        ],
    }
    payload = {"tmpl": tmpl, "tmpldata": data, "json": True,
               "options": {"viewport_width": 850, "full_page": True, "type": "png",
                           "device_scale_factor_level": "high", "animations": "disabled"}}
    import time
    t0 = time.time()
    async with aiohttp.ClientSession() as s:
        async with s.post("http://192.168.5.29:8999/text2img/generate", json=payload, timeout=aiohttp.ClientTimeout(total=180)) as resp:
            print("POST status:", resp.status)
            ret = await resp.json()
            img_id = ret["data"]["id"]
        async with s.get(f"http://192.168.5.29:8999/text2img/{img_id}") as resp:
            print("GET status:", resp.status, resp.headers.get("Content-Type"))
            img = await resp.read()
    Path("test_shop.png").write_bytes(img)
    print(f"渲染完成 {time.time()-t0:.1f}s -> test_shop.png ({len(img)//1024} KB)")

asyncio.run(main())
