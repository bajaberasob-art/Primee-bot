"""Compatibility wrapper for the canonical Discord bot entry point.

The project runs through ``main.py``.  This module remains available for older
workflow commands and imports so they cannot accidentally start a reduced,
second bot implementation.
"""

from main import EnterpriseBot as DiscordBot
from main import bot, main

__all__ = ["DiscordBot", "bot", "main"]


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())