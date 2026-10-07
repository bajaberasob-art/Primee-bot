"""Pure progression helpers for Lona; independent of legacy economy XP."""


def xp_required(level: int) -> int:
    """XP needed to advance from level to level + 1."""
    level = max(0, int(level))
    return 5 * level ** 2 + 50 * level + 100


def total_xp_for_level(level: int) -> int:
    """Sum the exact progression polynomial without a per-level loop."""
    level = max(0, int(level))
    return (
        5 * (level - 1) * level * (2 * level - 1) // 6
        + 25 * level * (level - 1)
        + 100 * level
    )


def level_from_xp(xp: int) -> int:
    """Integer binary search handles huge XP totals without blocking loops."""
    xp = max(0, int(xp))
    low, high = 0, 1
    while total_xp_for_level(high) <= xp:
        high *= 2
    while low + 1 < high:
        middle = (low + high) // 2
        if total_xp_for_level(middle) <= xp:
            low = middle
        else:
            high = middle
    return low


def text_progress(xp: int) -> dict:
    level = level_from_xp(xp)
    earned = max(0, int(xp)) - total_xp_for_level(level)
    required = xp_required(level)
    return {
        "level": level,
        "progress_xp": earned,
        "xp_required": required,
        "next_level_total_xp": total_xp_for_level(level + 1),
        "percentage": earned * 100 / required,
    }