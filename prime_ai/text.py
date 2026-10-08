"""Provider-safe Discord text normalization, shared with conversation storage."""

import re

_MEMBER_MENTION = re.compile(r"<@!?\d{15,22}>")
_ROLE_MENTION = re.compile(r"<@&\d{15,22}>")
_CHANNEL_MENTION = re.compile(r"<#\d{15,22}>")
_EMOJI = re.compile(r"<a?:([A-Za-z0-9_]{1,64}):\d{15,22}>")


def sanitize_discord_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    text = _MEMBER_MENTION.sub("[عضو مشار إليه]", text)
    text = _ROLE_MENTION.sub("[رتبة مشار إليها]", text)
    text = _CHANNEL_MENTION.sub("[قناة مشار إليها]", text)
    text = _EMOJI.sub(r":\1:", text)
    return text[:max(0, int(limit))]
