"""Token sink for streamed answers: a channel sets a callback for the turn (SSE in BS-20); nodes forward tokens."""

import contextvars
from collections.abc import Awaitable, Callable

TokenSink = Callable[[str], Awaitable[None]]
token_sink: contextvars.ContextVar[TokenSink | None] = contextvars.ContextVar("token_sink", default=None)
