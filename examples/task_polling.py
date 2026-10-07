"""
Task polling example for the Affinity SDK (FR-011).

This example demonstrates:
- Using `client.tasks.wait(task_url, ...)` to poll a long-running operation

To run:
- Set `AFFINITY_API_KEY`
- Set `AFFINITY_TASK_URL` to a taskUrl returned by an async endpoint (e.g., merges)
"""

import os

from affinity import Affinity


def main() -> None:
    api_key = os.environ.get("AFFINITY_API_KEY")
    if not api_key:
        print("Please set AFFINITY_API_KEY environment variable")
        return

    task_url = os.environ.get("AFFINITY_TASK_URL")
    if not task_url:
        print("Please set AFFINITY_TASK_URL to a taskUrl you want to poll")
        return

    with Affinity(api_key=api_key) as client:
        task = client.tasks.wait(task_url, timeout=60.0)
        print(f"Task status: {task.status}")


if __name__ == "__main__":
    main()
