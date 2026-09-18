"""用样例数据渲染银行两张卡片（直连 t2i 服务验证 1:1 还原度）

用法：cd astrbot_plugin_zhenxun_economy && .venv/Scripts/python scripts/render_bank_preview.py
产物：scripts/out/bank_user.png / bank_overview.png
"""
import asyncio, sys, time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

# 复用 conftest 的完整 astrbot stub（modules.bank.__init__ 会导入 logic 依赖 astrbot）
sys.path.insert(0, str(PLUGIN_ROOT / "tests"))
import conftest  # noqa: F401 安装 astrbot stub

import aiohttp
from modules.bank.render import load_template

T2I = "http://192.168.5.29:8999"
OUT_DIR = Path(__file__).parent / "out"

# “我的银行信息”样例数据（字段对齐原版 get_user_info_data）
USER_DATA = {
    "name": "远枫",
    "rank": 1,
    "avatar_url": "",
    "amount": 12800,
    "deposit_count": 15,
    "today_deposit_count": 2,
    "cumulative_gain": 666,
    "projected_revenue": 88,
    "today_deposit_amount": 3000,
    "deposit_list": [
        {
            "id": 1024,
            "date": "2026-09-18",
            "start_time": "2026-09-18 09:12:33",
            "end_time": "2026-09-19 00:00:00",
            "amount": 2000,
            "rate": "0.08",
            "projected_revenue": 58,
        },
        {
            "id": 1025,
            "date": "2026-09-18",
            "start_time": "2026-09-18 14:45:01",
            "end_time": "2026-09-19 00:00:00",
            "amount": 1000,
            "rate": "0.10",
            "projected_revenue": 30,
        },
    ],
    "create_time": "2026-09-18 15:30:00",
}

# 银行总览样例数据（字段对齐原版 get_bank_info_data）
OVERVIEW_DATA = {
    "user_count": 42,
    "amount_sum": 123456,
    "today_count": 7,
    "day_amount": 5678,
    "active_user_count": 13,
    "interest_amount": 8888,
    "e_data": ["09-12", "09-13", "09-14", "09-15", "09-16", "09-17", "09-18"],
    "e_amount": [3200, 5800, 1200, 7600, 4300, 9100, 6600],
    "create_time": "2026-09-18 15:30:00",
}


async def render(tmpl_name: str, data: dict, width: int, out: Path):
    tmpl = load_template(tmpl_name)
    print(f"{tmpl_name} 模板大小: {len(tmpl) / 1024 / 1024:.1f} MB")
    payload = {
        "tmpl": tmpl,
        "tmpldata": {"data": data},
        "json": True,
        "options": {
            "viewport_width": width,
            "full_page": True,
            "type": "png",
            "device_scale_factor_level": "high",
            "animations": "disabled",
        },
    }
    t0 = time.time()
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{T2I}/text2img/generate", json=payload,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            print("POST status:", resp.status)
            ret = await resp.json()
            img_id = ret["data"]["id"]
        async with s.get(f"{T2I}/text2img/{img_id}") as resp:
            print("GET status:", resp.status, resp.headers.get("Content-Type"))
            img = await resp.read()
    out.write_bytes(img)
    print(f"渲染完成 {time.time() - t0:.1f}s -> {out} ({len(img) // 1024} KB)")


async def main():
    OUT_DIR.mkdir(exist_ok=True)
    await render("user.html", USER_DATA, 386, OUT_DIR / "bank_user.png")
    await render("overview.html", OVERVIEW_DATA, 450, OUT_DIR / "bank_overview.png")


asyncio.run(main())
