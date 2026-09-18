"""用样例数据渲染通用表格卡片（直连 t2i 服务验证还原度）

样例：好感度群组内排行（排名/头像/名称/好感度/签到次数），
头像复用 sign_in 模块的样例图片（base64 data URI）。
"""
import asyncio, base64, sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

# 复用 conftest 的完整 astrbot stub
sys.path.insert(0, str(PLUGIN_ROOT / "tests"))
import conftest  # noqa: F401 安装 astrbot stub

import aiohttp
from modules._shared.table_card import load_template, build_table_data

IMG_DIR = PLUGIN_ROOT / "modules" / "sign_in" / "assets" / "img"


def img_uri(name: str) -> str:
    return "data:image/png;base64," + base64.b64encode(
        (IMG_DIR / name).read_bytes()
    ).decode()


async def main():
    tmpl = load_template()
    print("模板大小: %.1f MB" % (len(tmpl) / 1024 / 1024))
    data = {"data": build_table_data(
        "好感度群组内排行", "使用 `签到` 可以提升好感度哦",
        ["排名", "头像", "名称", "好感度", "签到次数"],
        [
            [1, ("image", img_uri("1.png")), "真寻", 52.37, 128],
            [2, ("image", img_uri("2.png")), "远枫", 45.12, 96],
            [3, ("image", img_uri("3.png")), "小丧", 38.60, 77],
            [4, ("image", img_uri("h1.png")), "笨蛋", 21.05, 40],
            [5, ("image", img_uri("h2.png")), "呆子", 12.88, 13],
        ],
    )}
    payload = {"tmpl": tmpl, "tmpldata": data, "json": True,
               "options": {"viewport_width": 800, "full_page": True, "type": "png",
                           "device_scale_factor_level": "high"}}
    import time
    t0 = time.time()
    async with aiohttp.ClientSession() as s:
        async with s.post("http://192.168.5.29:8999/text2img/generate", json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            print("POST status:", resp.status)
            ret = await resp.json()
            img_id = ret["data"]["id"]
        async with s.get(f"http://192.168.5.29:8999/text2img/{img_id}") as resp:
            print("GET status:", resp.status, resp.headers.get("Content-Type"))
            img = await resp.read()
    out = PLUGIN_ROOT / "test_table.png"
    out.write_bytes(img)
    print(f"渲染完成 {time.time()-t0:.1f}s -> {out} ({len(img)//1024} KB)")


asyncio.run(main())
