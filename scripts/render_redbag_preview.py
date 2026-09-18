"""红包三张图的样例渲染脚本（PIL 本地合成，无需 t2i 服务）。

头像用 modules/sign_in/assets/img/ 下的图片代替真实 QQ 头像。
输出: scripts/out/redbag_cover.png / redbag_open.png / redbag_rank.png
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
# 复用 conftest 的 astrbot stub（render.py 只用到 logger，这里防御性安装）
sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
import conftest  # noqa: F401 安装 astrbot stub

# 直接按文件路径加载 render.py（避免触发 modules/redbag/__init__ 的包级相对导入）
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "redbag_render", Path(__file__).parent.parent / "modules" / "redbag" / "render.py"
)
redbag_render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redbag_render)

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
IMG_DIR = Path(__file__).parent.parent / "modules" / "sign_in" / "assets" / "img"


def avatar(name: str) -> bytes:
    return (IMG_DIR / name).read_bytes()


# 1. 红包封面（redbag_2 随机底图 + 头像 + 祝福语）
cover = redbag_render.build_cover_image("恭喜发财 大吉大利", avatar("h1.png"))
(OUT / "redbag_cover.png").write_bytes(cover)
print("redbag_cover.png", len(cover) // 1024, "KB")

# 2. 开包结果（redbag_1 随机底图 + 头像昵称条 + 金额大字 + 进度）
open_img = redbag_render.build_open_result_image(
    name="远枫的红包", amount=66, opened_count=3, num=5,
    opened_sum=158, total_amount=200, avatar=avatar("1.png"),
)
(OUT / "redbag_open.png").write_bytes(open_img)
print("redbag_open.png", len(open_img) // 1024, "KB")

# 3. 结算手气榜（红头 + 名次/圆角头像/昵称/金额）
open_user = {"u1": 88, "u2": 52, "u3": 36, "u4": 17, "u5": 7}
names = {"u1": "远枫", "u2": "小真寻", "u3": "笨蛋测试员", "u4": "路过的欧洲人", "u5": "非洲酋长"}
avatars = {u: avatar(a) for u, a in zip(names, ["h1.png", "h2.png", "1.png", "2.png", "rl.png"])}
rank_img = redbag_render.build_amount_rank(
    "远枫的红包", open_user, names, avatars, avatar("h1.png"), num=10
)
(OUT / "redbag_rank.png").write_bytes(rank_img)
print("redbag_rank.png", len(rank_img) // 1024, "KB")
