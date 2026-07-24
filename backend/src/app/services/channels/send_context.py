from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterator


SendRequestStarted = Callable[[], Awaitable[None]]


@dataclass
class _BoundSendRequestHook:
    callback: SendRequestStarted
    called: bool = False

    async def call_once(self) -> None:
        if self.called:
            return
        await self.callback()
        self.called = True


_send_request_hook: ContextVar[_BoundSendRequestHook | None] = ContextVar(
    "channel_send_request_hook",
    default=None,
)


@contextmanager
def bind_send_request_started(
    callback: SendRequestStarted,
) -> Iterator[None]:
    """Bind one task-local hook for the next real channel send request."""

    token = _send_request_hook.set(_BoundSendRequestHook(callback=callback))
    try:
        yield
    finally:
        _send_request_hook.reset(token)


async def notify_send_request_started() -> None:
    """Run the bound hook immediately before the external send request."""

    hook = _send_request_hook.get()
    if hook is not None:
        await hook.call_once()
