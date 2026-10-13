"""Asyncio helpers for the CLI."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def gather_or_raise(*aws: Awaitable[T]) -> list[T]:
    """Run awaitables concurrently and return their results in order, like ``asyncio.gather``.

    On the first error the others are cancelled, every task is awaited, and that error is raised.
    Plain ``gather`` leaves the rest running, and their later errors show up as "Task exception
    was never retrieved" on stderr.
    """
    tasks = [asyncio.ensure_future(aw) for aw in aws]
    if not tasks:
        return []
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except BaseException:
        # This coroutine was cancelled (e.g. Ctrl+C): stop the tasks too
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    first_error: BaseException | None = None
    for task in tasks:
        if task.cancelled():
            continue
        error = task.exception()  # retrieving it keeps asyncio from reporting it
        if error is not None and first_error is None:
            first_error = error
    if first_error is not None:
        raise first_error
    return [task.result() for task in tasks]
