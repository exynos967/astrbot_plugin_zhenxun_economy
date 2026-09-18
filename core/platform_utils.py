"""平台工具：头像 / 群成员昵称 / at 解析（主要适配 aiocqhttp，其他平台优雅降级）"""

import base64
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp

# QQ 头像通用 URL
AVA_URL = "https://q1.qlogo.cn/g?b=qq&nk={}&s=640"


def is_aiocqhttp(event: AstrMessageEvent) -> bool:
    return event.get_platform_name() == "aiocqhttp"


def get_at_user_ids(event: AstrMessageEvent) -> list[str]:
    """提取消息中所有 at 目标的用户 id"""
    result = []
    for seg in event.get_messages():
        if isinstance(seg, Comp.At) and str(seg.qq) != "all":
            result.append(str(seg.qq))
    return result


def get_avatar_url(user_id: str, platform: str = "qq") -> str:
    return AVA_URL.format(user_id)


async def get_avatar_bytes(user_id: str) -> bytes | None:
    """下载 QQ 头像字节（失败返回 None）"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                AVA_URL.format(user_id), timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        logger.warning(f"[zhenxun_economy] 下载头像失败 {user_id}: {e}")
    return None


async def get_avatar_b64(user_id: str) -> str | None:
    data = await get_avatar_bytes(user_id)
    return f"data:image/png;base64,{base64.b64encode(data).decode()}" if data else None


async def get_group_member_name(event: AstrMessageEvent, user_id: str) -> str:
    """获取群成员昵称（群名片优先），aiocqhttp 专用，失败回退为空串"""
    if not is_aiocqhttp(event):
        return ""
    try:
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
            AiocqhttpMessageEvent,
        )

        assert isinstance(event, AiocqhttpMessageEvent)
        info = await event.bot.api.call_action(
            "get_group_member_info",
            group_id=int(event.get_group_id()),
            user_id=int(user_id),
            no_cache=False,
            self_id=event.message_obj.self_id,
        )
        return info.get("card") or info.get("nickname", "")
    except Exception as e:
        logger.warning(f"[zhenxun_economy] 获取群成员信息失败 {user_id}: {e}")
        return ""


async def get_group_user_ids(event: AstrMessageEvent) -> list[str]:
    """获取群成员 id 列表（排行过滤用），非 aiocqhttp 返回空列表"""
    if not is_aiocqhttp(event):
        return []
    try:
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
            AiocqhttpMessageEvent,
        )

        assert isinstance(event, AiocqhttpMessageEvent)
        members = await event.bot.api.call_action(
            "get_group_member_list",
            group_id=int(event.get_group_id()),
            self_id=event.message_obj.self_id,
        )
        return [str(m["user_id"]) for m in members]
    except Exception as e:
        logger.warning(f"[zhenxun_economy] 获取群成员列表失败: {e}")
        return []


async def get_user_name(event: AstrMessageEvent, user_id: str) -> str:
    """取用户名：群名片/昵称，兜底为发送者昵称或 id"""
    if user_id == event.get_sender_id():
        name = event.get_sender_name()
        if name:
            return name
    name = await get_group_member_name(event, user_id)
    return name or user_id
