"""Bounded internal requests to Flower; never proxy browser-supplied URLs."""

import httpx
from django.conf import settings
from ninja.errors import HttpError


async def fetch_tasks(path: str) -> dict:
    """Fetch a task mapping from the internal Flower API.

    Args:
        path: Server-selected API path, relative to Flower's API root.

    Returns:
        The task mapping or detail returned by Flower.

    Raises:
        HttpError: If Flower is unconfigured (502), the task is missing (404),
        or Flower fails to return a valid response (504/502).
    """
    flower_url = getattr(settings, "FLOWER_SERVICE_URL", "") or ""
    if not flower_url:
        raise HttpError(502, "task monitoring is not configured")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{flower_url.rstrip('/')}/api/{path}")
        if response.status_code == 404:
            raise HttpError(404, "Task not found")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Expected a task mapping")
        return data
    except httpx.TimeoutException as exc:
        raise HttpError(504, "Task monitoring timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HttpError(502, "Task monitoring unavailable") from exc
