"""Stable exception contracts shared by the provider and application layers."""


class AISettingsConflict(RuntimeError):
    def __init__(self, current: dict):
        super().__init__("PRIME AI settings changed")
        self.current = current


class AISettingsDisabled(RuntimeError):
    pass


class AIChannelDenied(RuntimeError):
    pass


class AIProviderUnavailable(RuntimeError):
    def __init__(
        self, message: str, *, retryable: bool = False,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.retryable = bool(retryable)
        self.status_code = status_code


class AIMemoryCandidateRejected(ValueError):
    pass


class AIMemoryLimitReached(RuntimeError):
    pass


class AccessDenied(RuntimeError):
    pass


class InvalidToolPlan(ValueError):
    pass


class ActionOutcomeTrackingError(RuntimeError):
    """Discord succeeded, but its mandatory durable outcome could not be saved."""
