"""商店模块：导出 ShopModule；import 本包即注册默认商品"""

from . import default_goods  # noqa: F401  # 装饰器收集默认商品注册信息
from .logic import ShopModule

__all__ = ["ShopModule"]
