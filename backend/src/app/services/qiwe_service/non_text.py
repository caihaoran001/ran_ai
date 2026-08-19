"""把企微回调里的「非文本消息」翻译成人能看懂的一行字。

背景：这些消息此前一律被存成 `[表情/图片等非文本消息]`，后台聊天记录里成片的占位符，
运营看不出发生过什么。线上真实样本里它们其实分得很清楚：表情、视频、链接卡片（体重秤分享
就在这类，卡片正文里带着体重数字）、转账，以及进群/撤回/置顶/改群名这些群系统通知。

本模块只做纯解析：不联网、不查库、不调模型，方便单测覆盖每一种 msgType。
需要下载缩略图的类型（表情、视频封面）只返回下载线索，真正的下载在
`ranren_ai/attachments.py` 里做。

映射依据是生产近两个月的真实回调样本；**查不实的语义不臆造**——例如 msgType=2118 占了
最大头，但反查群成员表确认当事人事后仍在群里（不是退群），故只写「群成员信息变更」。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
import re
from typing import Any


logger = logging.getLogger("uvicorn.error")

# 落库的 message_type：前端据此决定渲染成气泡、卡片、图片还是灰色系统提示行。
TYPE_STICKER = "sticker"
TYPE_VIDEO = "video"
TYPE_LINK = "link"
TYPE_MINIPROGRAM = "miniprogram"
TYPE_TRANSFER = "transfer"
TYPE_SYSTEM_NOTICE = "system_notice"
TYPE_UNKNOWN = "non_text"

# 缩略图下载类型
MEDIA_STICKER = "sticker"
MEDIA_VIDEO_COVER = "video_cover"

_MAX_TITLE_CHARS = 120
_MAX_DESC_CHARS = 300

# 从 protobuf 裸包里捞可读文本：只认 ASCII 可见字符与 2/3 字节 UTF-8 序列连成的片段，
# 控制字节（protobuf 的 tag/长度）天然成为分隔符。转账消息的文案就是这么藏在里面的。
_READABLE_RUN = re.compile(rb"(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2})+")
_CJK = re.compile(r"[一-鿿]")


@dataclass(frozen=True)
class MediaHint:
    """缩略图下载线索（表情原图 / 视频封面）。"""

    kind: str
    url: str
    aes_key: str = ""
    auth_key: str = ""


@dataclass(frozen=True)
class NonTextMessage:
    message_type: str
    content: str
    media: MediaHint | None = None
    # 卡片消息的结构化字段（标题/描述/原链接），供后台聊天记录还原成一张卡片。
    card: dict[str, str] | None = None


def describe_non_text(item: dict[str, Any]) -> NonTextMessage:
    """按 msgType 给出 (落库类型, 展示正文, 缩略图线索)。无法识别时给保底占位。"""
    msg_type = item.get("msgType")
    msg_data = item.get("msgData") if isinstance(item.get("msgData"), dict) else {}
    identifier = str(item.get("msgUniqueIdentifier") or "")

    if msg_type == 104:
        return _sticker(msg_data)
    if msg_type == 103:
        return _video(msg_data)
    if msg_type == 13:
        return _link(msg_data)
    if msg_type == 78:
        return _miniprogram(msg_data)
    if msg_type in (590, 591):
        return _transfer(msg_data, item)
    if msg_type == 2063:
        return NonTextMessage(TYPE_SYSTEM_NOTICE, "撤回了一条消息")
    if msg_type == 1052:
        text = _decode_base64_text(msg_data.get("base64RawData") or item.get("base64RawData"))
        return NonTextMessage(TYPE_SYSTEM_NOTICE, text or "置顶了一条消息")
    if msg_type == 1001:
        return _group_renamed(msg_data, item)
    if msg_type == 1002:
        return _members_joined(msg_data, item, identifier)
    if msg_type in (2118, 1005):
        return _member_changed(identifier)
    if msg_type == 1011:
        # 风控拦截那种 1011 走 inbox.qiwe_spam_block_notice，不会到这里。
        text = _decode_base64_text(msg_data.get("base64RawData") or item.get("base64RawData"))
        return NonTextMessage(TYPE_SYSTEM_NOTICE, f"系统提示：{text}" if text else "系统提示")

    return _unknown(msg_type, msg_data, item)


def _sticker(msg_data: dict[str, Any]) -> NonTextMessage:
    url = _clean_url(msg_data.get("fileHttpUrl") or msg_data.get("cdnUrl"))
    media = (
        MediaHint(
            kind=MEDIA_STICKER,
            url=url,
            aes_key=str(msg_data.get("fileAesKey") or ""),
            auth_key=str(msg_data.get("fileAuthKey") or ""),
        )
        if url
        else None
    )
    return NonTextMessage(TYPE_STICKER, "[动画表情]", media)


def _video(msg_data: dict[str, Any]) -> NonTextMessage:
    duration = _safe_int(msg_data.get("duration"))
    content = f"[视频] 时长 {duration} 秒" if duration else "[视频]"
    url = _clean_url(msg_data.get("coverImageHttpUrl"))
    media = (
        MediaHint(
            kind=MEDIA_VIDEO_COVER,
            url=url,
            aes_key=str(msg_data.get("fileAesKey") or ""),
            auth_key=str(msg_data.get("fileAuthKey") or ""),
        )
        if url
        else None
    )
    return NonTextMessage(TYPE_VIDEO, content, media)


def _link(msg_data: dict[str, Any]) -> NonTextMessage:
    title = _clip(msg_data.get("title"), _MAX_TITLE_CHARS)
    desc = _clip(msg_data.get("desc"), _MAX_DESC_CHARS)
    head = f"[链接] {title}" if title else "[链接]"
    card = {"title": title, "desc": desc, "url": _clean_url(msg_data.get("linkUrl"))}
    return NonTextMessage(
        TYPE_LINK,
        "\n".join(part for part in (head, desc) if part),
        card=card,
    )


def _miniprogram(msg_data: dict[str, Any]) -> NonTextMessage:
    app_name = _clip(msg_data.get("appName"), _MAX_TITLE_CHARS)
    title = _clip(msg_data.get("title"), _MAX_TITLE_CHARS)
    desc = _clip(msg_data.get("desc"), _MAX_DESC_CHARS)
    parts = [part for part in (app_name, title) if part]
    if len(parts) == 2 and parts[0] == parts[1]:
        parts = parts[:1]
    label = " · ".join(parts)
    head = f"[小程序] {label}" if label else "[小程序]"
    body = desc if desc and desc != title else ""
    card = {"title": label, "desc": body, "url": ""}
    return NonTextMessage(
        TYPE_MINIPROGRAM,
        "\n".join(part for part in (head, body) if part),
        card=card,
    )


def _transfer(msg_data: dict[str, Any], item: dict[str, Any]) -> NonTextMessage:
    raw = msg_data.get("base64RawData") or item.get("base64RawData")
    text = ""
    for candidate in _readable_texts(raw):
        # 文案本身就带「[转账]」前缀（如 "[转账]请收款"），取第一段中文即可。
        text = candidate
        break
    return NonTextMessage(TYPE_TRANSFER, text or "[转账]")


def _group_renamed(msg_data: dict[str, Any], item: dict[str, Any]) -> NonTextMessage:
    name = _decode_base64_text(
        msg_data.get("changedMemberList") or msg_data.get("base64RawData") or item.get("base64RawData")
    )
    name = _clip(name, _MAX_TITLE_CHARS)
    return NonTextMessage(TYPE_SYSTEM_NOTICE, f"把群名改为「{name}」" if name else "修改了群名")


def _members_joined(msg_data: dict[str, Any], item: dict[str, Any], identifier: str) -> NonTextMessage:
    raw = msg_data.get("changedMemberList") or item.get("base64RawData")
    decoded = _decode_base64_text(raw)
    members = [part for part in re.split(r"[;,]", decoded) if part.strip()] if decoded else []
    count = len(members)
    if "newroom" in identifier.lower():
        return NonTextMessage(
            TYPE_SYSTEM_NOTICE,
            f"创建了群聊（{count} 人）" if count else "创建了群聊",
        )
    return NonTextMessage(
        TYPE_SYSTEM_NOTICE,
        f"邀请 {count} 人加入群聊" if count else "邀请成员加入群聊",
    )


def _member_changed(identifier: str) -> NonTextMessage:
    """群成员变动通知。**只有 `quit_` 开头的那种才是真的退群**，别把两类混为一谈。

    企微这类通知的正文是空的，msgData 只有一个成员 id，两类报文其余字段完全一样，
    唯一的区别在 msgUniqueIdentifier。拿群成员表逐个对过账：

    - `quit_associate_chatroom_*`（全库 29 条）：抽 12 个当事人查当前群成员表，12 个全部
      已不在群里 → 退群/被移出，是真流失信号。
    - 随机串（全库 474 条）：抽 12 个，12 个今天全都还在群里；且能查到建群记录的样本里
      41/43 当事人就是建群的那个人，111 次与「邀请 N 人加入群聊」同时发生，114/200 落在
      建群 10 分钟内 → 是群主侧的成员变动通知，人没走，不能当退群用。
    """
    if identifier.lower().startswith("quit"):
        return NonTextMessage(TYPE_SYSTEM_NOTICE, "退出了群聊")
    return NonTextMessage(TYPE_SYSTEM_NOTICE, "更新了群成员信息")


def _unknown(msg_type: Any, msg_data: dict[str, Any], item: dict[str, Any]) -> NonTextMessage:
    # 回调自带正文的先用正文；否则给保底占位，并把 msgType 打进日志，便于后续补映射。
    content = str(msg_data.get("content") or "").strip()
    if not content:
        raw = msg_data.get("base64RawData") or item.get("base64RawData")
        content = _decode_base64_text(raw)
        if not content:
            # 卡片类消息把标题包在 protobuf 里，捞第一段中文当提要，好过一句"其他类型消息"。
            hint = next(iter(_readable_texts(raw)), "")
            content = f"[卡片] {hint}" if hint else ""
    if content:
        return NonTextMessage(TYPE_UNKNOWN, _clip(content, _MAX_DESC_CHARS))
    logger.info("QiWe unmapped non-text message msgType=%s keys=%s", msg_type, sorted(msg_data.keys()))
    return NonTextMessage(TYPE_UNKNOWN, "[其他类型消息]")


def _decode_base64_text(raw: Any) -> str:
    """base64 段整体就是一段 UTF-8 文本时返回它，否则返回空串。"""
    if not raw or not isinstance(raw, str):
        return ""
    try:
        data = base64.b64decode(raw + "=" * ((4 - len(raw) % 4) % 4))
    except Exception:
        return ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if any(ord(ch) < 32 and ch not in "\r\n\t" for ch in text):
        return ""
    return " ".join(text.split()).strip()


def _readable_texts(raw: Any) -> list[str]:
    """从 protobuf 裸包里按序捞出含中文的可读片段（转账文案就藏在这里）。"""
    if not raw or not isinstance(raw, str):
        return []
    try:
        data = base64.b64decode(raw + "=" * ((4 - len(raw) % 4) % 4))
    except Exception:
        return []
    texts: list[str] = []
    for match in _READABLE_RUN.finditer(data):
        try:
            text = match.group().decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if text and _CJK.search(text):
            texts.append(_clip(text, _MAX_DESC_CHARS))
    return texts


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit] if len(text) > limit else text


def _clean_url(value: Any) -> str:
    return str(value or "").replace("\n", "").replace("\r", "").strip()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
