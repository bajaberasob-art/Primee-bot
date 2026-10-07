"""Central Discord interaction safety runtime.

The bot has many independently-authored cogs.  This module keeps their
existing response style working while making acknowledgement, error logging,
and UI callback failures consistent at the dispatch boundary.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from typing import Any, Awaitable, Callable

import discord
from discord import app_commands

LOGGER = logging.getLogger("CoreRunner.interactions")
_WRAPPED_COMMAND = "_interaction_runtime_wrapped"
_WRAPPED_CALLBACK = "_interaction_runtime_callback_wrapped"
_OPENS_MODAL = "_interaction_runtime_opens_modal"


def _exception_info(error: BaseException):
    return (type(error), error, error.__traceback__)


async def defer_if_needed(interaction: Any, *, ephemeral: bool = False) -> bool:
    """Acknowledge an interaction once and return whether it is now deferred."""
    response = getattr(interaction, "response", None)
    if response is None:
        return False
    try:
        if response.is_done():
            return True
    except Exception:
        LOGGER.exception("[INTERACTION] response state check failed")
        return False
    try:
        await response.defer(ephemeral=ephemeral)
        return True
    except discord.InteractionResponded:
        return True
    except (discord.NotFound, discord.HTTPException):
        LOGGER.error(
            "[INTERACTION] acknowledgement failed guild=%s user=%s",
            getattr(getattr(interaction, "guild", None), "id", None),
            getattr(getattr(interaction, "user", None), "id", None),
            exc_info=True,
        )
        return False


async def send_interaction_message(
    interaction: Any,
    content: str | None = None,
    *,
    ephemeral: bool = False,
    **kwargs: Any,
) -> Any:
    """Send a response or follow-up without raising Unknown Interaction."""
    response = getattr(interaction, "response", None)
    try:
        if response is not None and not response.is_done():
            return await response.send_message(content, ephemeral=ephemeral, **kwargs)
        return await interaction.followup.send(content, ephemeral=ephemeral, **kwargs)
    except (discord.NotFound, discord.InteractionResponded):
        LOGGER.warning(
            "[INTERACTION] response expired or already sent guild=%s user=%s",
            getattr(getattr(interaction, "guild", None), "id", None),
            getattr(getattr(interaction, "user", None), "id", None),
            exc_info=True,
        )
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.error("[INTERACTION] response delivery failed", exc_info=True)
    return None


class _ResponseProxy:
    """Route legacy response.send_message calls to the deferred follow-up."""

    def __init__(self, interaction: Any, *, deferred: bool):
        self._interaction = interaction
        self._deferred = deferred

    def is_done(self) -> bool:
        return self._deferred

    async def defer(self, **kwargs: Any) -> None:
        self._deferred = True

    async def send_message(self, content: str | None = None, **kwargs: Any) -> Any:
        kwargs.pop("ephemeral", None) if getattr(self._interaction, "_shortcut_adapter", False) else None
        if self._deferred:
            return await self._interaction.followup.send(content, **kwargs)
        return await self._interaction.response.send_message(content, **kwargs)

    async def send_modal(self, modal: discord.ui.Modal) -> Any:
        # Modal-opening callbacks are explicitly executed without a defer.
        return await self._interaction.response.send_modal(modal)

    async def edit_message(self, *args: Any, **kwargs: Any) -> Any:
        return await self._interaction.followup.edit_message(*args, **kwargs)


class InteractionProxy:
    """Attribute-compatible interaction whose response is follow-up aware."""

    def __init__(self, interaction: Any, *, deferred: bool):
        self._interaction = interaction
        self.response = _ResponseProxy(interaction, deferred=deferred)
        self.followup = interaction.followup

    def __getattr__(self, name: str) -> Any:
        return getattr(self._interaction, name)


def mark_modal_callback(callback: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a callback whose first response is opened by a delegated method."""
    setattr(callback, _OPENS_MODAL, True)
    return callback


def _callback_opens_modal(callback: Callable[..., Any]) -> bool:
    """Modal launches cannot be preceded by deferReply in Discord's protocol."""
    candidates = [callback]
    nested_callback = getattr(callback, "callback", None)
    if nested_callback is not None:
        candidates.append(nested_callback)
    if any(getattr(candidate, _OPENS_MODAL, False) for candidate in candidates):
        return True
    sources: list[str] = []
    for candidate in candidates:
        try:
            sources.append(inspect.getsource(candidate))
        except (OSError, TypeError):
            continue
    source = "\n".join(sources)
    return ".send_modal(" in source


def _interaction_arg(args: tuple[Any, ...]) -> Any | None:
    for value in args:
        if hasattr(value, "response") and hasattr(value, "user"):
            return value
    return None


async def _run_callback(
    callback: Callable[..., Awaitable[Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    label: str,
) -> Any:
    interaction = _interaction_arg(args)
    if interaction is None:
        return await callback(*args, **kwargs)

    try:
        # Discord only accepts send_modal as the initial interaction response.
        # Do not acknowledge modal-opening callbacks with defer first.
        if _callback_opens_modal(callback):
            return await callback(*args, **kwargs)
        deferred = await defer_if_needed(interaction)
        proxy = InteractionProxy(interaction, deferred=deferred)
        replaced = tuple(proxy if value is interaction else value for value in args)
        return await callback(*replaced, **kwargs)
    except Exception as error:
        LOGGER.error(
            "[INTERACTION] %s failed guild=%s user=%s: %s",
            label,
            getattr(getattr(interaction, "guild", None), "id", None),
            getattr(getattr(interaction, "user", None), "id", None),
            error,
            exc_info=_exception_info(error),
        )
        raise


def wrap_application_command(command: app_commands.Command) -> bool:
    """Wrap one leaf command while preserving its extracted schema/checks."""
    if getattr(command, _WRAPPED_COMMAND, False):
        return False
    original = command._callback
    label = f"slash /{command.qualified_name}"

    @functools.wraps(original)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        return await _run_callback(original, args, kwargs, label=label)

    command._callback = guarded
    setattr(command, _WRAPPED_COMMAND, True)
    return True


async def guarded_view_task(
    view: discord.ui.View,
    item: discord.ui.Item[Any],
    interaction: discord.Interaction,
) -> None:
    """Replacement for discord.py's View scheduler with safe ACK + proxy."""
    try:
        item._refresh_state(interaction, interaction.data)  # type: ignore[attr-defined]
        # A modal must be the first response to a component interaction.
        # This check must happen before defer_if_needed; otherwise callbacks
        # that eventually open a modal fail with InteractionResponded.
        if _callback_opens_modal(item.callback):
            proxy = interaction
        else:
            deferred = await defer_if_needed(interaction)
            proxy = InteractionProxy(interaction, deferred=deferred)
        allow = await item._run_checks(proxy) and await view.interaction_check(proxy)
        if not allow:
            return
        if view.timeout:
            view._View__timeout_expiry = time.monotonic() + view.timeout
        await item.callback(proxy)
    except Exception as error:
        LOGGER.error(
            "[INTERACTION] view callback failed custom_id=%s guild=%s",
            getattr(item, "custom_id", None),
            getattr(getattr(interaction, "guild", None), "id", None),
            exc_info=_exception_info(error),
        )
        await universal_view_error(view, interaction, error, item)


async def guarded_modal_task(
    modal: discord.ui.Modal,
    interaction: discord.Interaction,
    components: Any,
    resolved: Any,
) -> None:
    """Replacement for discord.py's Modal scheduler with early ACK + proxy."""
    try:
        modal._refresh_timeout()
        modal._refresh(interaction, components, resolved)
        await defer_if_needed(interaction)
        proxy = InteractionProxy(interaction, deferred=True)
        if await modal.interaction_check(proxy):
            await modal.on_submit(proxy)
    except Exception as error:
        LOGGER.error(
            "[INTERACTION] modal callback failed modal=%s guild=%s",
            type(modal).__name__,
            getattr(getattr(interaction, "guild", None), "id", None),
            exc_info=_exception_info(error),
        )
        await universal_modal_error(modal, interaction, error)
    else:
        modal.stop()


async def universal_view_error(
    view: discord.ui.View,
    interaction: discord.Interaction,
    error: Exception,
    item: discord.ui.Item[Any],
) -> None:
    LOGGER.error(
        "[UI] View %s item %s failed",
        type(view).__name__,
        getattr(item, "custom_id", None),
        exc_info=_exception_info(error),
    )
    await send_interaction_message(
        interaction,
        "⚠️ حدث خطأ أثناء تنفيذ هذا الزر. تم تسجيل التفاصيل للمراجعة.",
        ephemeral=True,
    )


async def universal_modal_error(
    modal: discord.ui.Modal,
    interaction: discord.Interaction,
    error: Exception,
) -> None:
    LOGGER.error(
        "[UI] Modal %s failed",
        type(modal).__name__,
        exc_info=_exception_info(error),
    )
    await send_interaction_message(
        interaction,
        "⚠️ تعذر حفظ هذا النموذج. تم تسجيل التفاصيل للمراجعة.",
        ephemeral=True,
    )


def install_ui_guards() -> None:
    """Install once at process startup; future Views/Modals inherit the guard."""
    if not getattr(discord.ui.View, "_interaction_runtime_guarded", False):
        discord.ui.View._scheduled_task = guarded_view_task  # type: ignore[assignment]
        discord.ui.View.on_error = universal_view_error  # type: ignore[assignment]
        discord.ui.View._interaction_runtime_guarded = True
    if not getattr(discord.ui.Modal, "_interaction_runtime_guarded", False):
        discord.ui.Modal._scheduled_task = guarded_modal_task  # type: ignore[assignment]
        discord.ui.Modal.on_error = universal_modal_error  # type: ignore[assignment]
        discord.ui.Modal._interaction_runtime_guarded = True