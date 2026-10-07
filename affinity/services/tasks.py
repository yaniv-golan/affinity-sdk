"""
Task service for long-running operations.

Provides polling utilities for async operations like merges.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING

from ..exceptions import AffinityError
from ..exceptions import TimeoutError as AffinityTimeoutError
from ..models.secondary import MergeTask

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient


class TaskStatus:
    """Known task status values."""

    PENDING = "pending"
    IN_PROGRESS = "in-progress"  # V2 spec spelling
    SUCCESS = "success"
    FAILED = "failed"


class TaskService:
    """
    Service for managing long-running operations (tasks).

    Provides utilities to poll and wait for async operations like merges.

    Example:
        # Start a merge operation
        task_url = client.companies.merge(primary_id, duplicate_id)

        # Wait for completion with timeout
        task = client.tasks.wait(task_url, timeout=60.0)
        if task.status == "success":
            print("Merge completed!")
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    def get(self, task_url: str) -> MergeTask:
        """
        Get the current status of a task.

        Args:
            task_url: The task URL returned from an async operation

        Returns:
            MergeTask with current status
        """
        # Extract task path from full URL if needed
        data = self._client.get_url(task_url)
        return MergeTask.model_validate(data)

    def wait(
        self,
        task_url: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        max_poll_interval: float = 30.0,
    ) -> MergeTask:
        """
        Wait for a task to complete with exponential backoff.

        Args:
            task_url: The task URL returned from an async operation
            timeout: Maximum time to wait in seconds (default: 5 minutes)
            poll_interval: Initial polling interval in seconds
            max_poll_interval: Maximum polling interval after backoff

        Returns:
            MergeTask with final status

        Raises:
            TimeoutError: If task doesn't complete within timeout
            AffinityError: If task fails
        """
        start_time = time.monotonic()
        current_interval = poll_interval

        while True:
            task = self.get(task_url)

            if task.status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                if task.status == TaskStatus.FAILED:
                    raise AffinityError(
                        f"Task failed: {task_url}",
                        status_code=None,
                        response_body={"task": task.model_dump()},
                    )
                return task

            # Check timeout
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                raise AffinityTimeoutError(f"Task did not complete within {timeout}s: {task_url}")

            # Wait with jitter before next poll
            jitter = random.uniform(0, current_interval * 0.1)
            time.sleep(current_interval + jitter)

            # Exponential backoff, capped at max
            current_interval = min(current_interval * 1.5, max_poll_interval)


class AsyncTaskService:
    """
    Async version of TaskService.

    Example:
        # Start a merge operation
        task_url = await client.companies.merge(primary_id, duplicate_id)

        # Wait for completion with timeout
        task = await client.tasks.wait(task_url, timeout=60.0)
    """

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def get(self, task_url: str) -> MergeTask:
        """
        Get the current status of a task.

        Args:
            task_url: The task URL returned from an async operation

        Returns:
            MergeTask with current status
        """
        data = await self._client.get_url(task_url)
        return MergeTask.model_validate(data)

    async def wait(
        self,
        task_url: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        max_poll_interval: float = 30.0,
    ) -> MergeTask:
        """
        Wait for a task to complete with exponential backoff.

        Args:
            task_url: The task URL returned from an async operation
            timeout: Maximum time to wait in seconds (default: 5 minutes)
            poll_interval: Initial polling interval in seconds
            max_poll_interval: Maximum polling interval after backoff

        Returns:
            MergeTask with final status

        Raises:
            TimeoutError: If task doesn't complete within timeout
            AffinityError: If task fails
        """
        start_time = time.monotonic()
        current_interval = poll_interval

        while True:
            task = await self.get(task_url)

            if task.status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                if task.status == TaskStatus.FAILED:
                    raise AffinityError(
                        f"Task failed: {task_url}",
                        status_code=None,
                        response_body={"task": task.model_dump()},
                    )
                return task

            # Check timeout
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                raise AffinityTimeoutError(f"Task did not complete within {timeout}s: {task_url}")

            # Wait with jitter before next poll
            jitter = random.uniform(0, current_interval * 0.1)
            await asyncio.sleep(current_interval + jitter)

            # Exponential backoff, capped at max
            current_interval = min(current_interval * 1.5, max_poll_interval)
