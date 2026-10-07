"""Streak presentation helpers; atomic streak calculation remains in Phase 2."""
import math
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


STREAK_TIMEZONE = ZoneInfo("Asia/Riyadh")
DEFAULT_STREAK_REACTION = "🔥"
DEFAULT_DUPLICATE_TEMPLATE = (
    "🔥 تم تسجيل ستريكك اليوم بالفعل.\n"
    "ستريكك الحالي: {streak} يوم\n"
    "أفضل ستريك: {best} يوم\n"
    "المرحلة: {stage_name}\n"
    "⏳ الستريك القادم بعد {time_remaining}."
)
DEFAULT_STAGE_UP_TEMPLATE = (
    "{user} وصل إلى مرحلة {stage_name} بعد {streak} يومًا متواصلًا."
)
DEFAULT_MILESTONE_TEMPLATE = (
    "🎉 {user} حقق إنجازًا جديدًا عند {threshold} يومًا من الستريك."
)
DEFAULT_REMINDER_TEMPLATE = (
    "🔥 لا تنسَ تسجيل ستريكك اليوم. ستريكك الحالي: {streak} يوم."
)


def _local_now(value=None):
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(STREAK_TIMEZONE)


def _reset_time(value=None):
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    try:
        hour, minute = str(value or "00:00").strip().split(":", 1)
        hour, minute = int(hour), int(minute)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
    except (TypeError, ValueError, OverflowError):
        pass
    return time.min


def next_streak_reset(value=None, day_reset_time="00:00"):
    now = _local_now(value)
    reset_time = _reset_time(day_reset_time)
    today_reset = datetime.combine(now.date(), reset_time, tzinfo=STREAK_TIMEZONE)
    if now < today_reset:
        return today_reset
    return today_reset + timedelta(days=1)


def format_time_remaining(value=None, day_reset_time="00:00"):
    now = _local_now(value)
    reset_at = next_streak_reset(now, day_reset_time)
    total_minutes = max(0, math.ceil((reset_at - now).total_seconds() / 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}س {minutes}د"


def build_streak_context(
    member,
    guild,
    state,
    stages,
    ranks=None,
    now=None,
    day_reset_time="00:00",
):
    """Prepare display/template values from persisted streak state only."""
    state = dict(state or {})
    ranks = dict(ranks or {})
    streak = max(0, int(state.get("current_streak") or 0))
    best = max(streak, int(state.get("best_streak") or 0))
    ordered = sorted(
        (dict(stage) for stage in (stages or []) if stage.get("enabled", 1)),
        key=lambda stage: (int(stage["threshold"]), str(stage["stage_key"])),
    )
    active = None
    upcoming = None
    for stage in ordered:
        if int(stage["threshold"]) <= streak:
            active = stage
        elif upcoming is None:
            upcoming = stage

    if active is None:
        start = 0
    else:
        start = int(active["threshold"])
    if upcoming:
        end = int(upcoming["threshold"])
        remaining = max(0, end - streak)
        progress = min(1.0, max(0.0, (streak - start) / max(1, end - start)))
        upcoming_label = f"{upcoming.get('reaction') or ''} {upcoming['name']}".strip()
    else:
        remaining = 0
        progress = 1.0 if active else 0.0
        upcoming_label = "أعلى مرحلة"

    active_name = str(active.get("name")) if active else "—"
    active_key = str(active.get("stage_key")) if active else ""
    member_name = str(
        getattr(member, "display_name", None)
        or getattr(member, "name", None)
        or "عضو"
    )
    mention = str(getattr(member, "mention", "") or "")
    server_rank = max(1, int(ranks.get("server_rank") or 1))
    global_rank = max(1, int(ranks.get("global_rank") or 1))
    values = {
        "user": mention or member_name,
        "mention": mention or member_name,
        "username": member_name,
        "name": member_name,
        "server": str(getattr(guild, "name", "") or ""),
        "streak": streak,
        "current_streak": streak,
        "best": best,
        "best_streak": best,
        "stage": active_key,
        "stage_name": active_name,
        "next_stage": upcoming_label,
        "remaining": remaining,
        "progress": f"{progress:.0%}",
        "time_remaining": format_time_remaining(now, day_reset_time),
        "server_rank": server_rank,
        "global_rank": global_rank,
        "server_streak": server_rank,
        "global_streak": global_rank,
    }
    return {
        "stage": active,
        "next_stage": upcoming,
        "remaining": remaining,
        "progress": progress,
        "time_remaining": values["time_remaining"],
        "values": values,
    }