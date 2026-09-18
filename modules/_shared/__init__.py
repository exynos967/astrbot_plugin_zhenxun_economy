"""跨模块共享的渲染组件（表格卡片等）"""

from .table_card import build_table_data, render_table_card

__all__ = ["build_table_data", "render_table_card"]
