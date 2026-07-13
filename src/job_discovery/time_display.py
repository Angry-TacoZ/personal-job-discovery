from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

EASTERN_TIME = ZoneInfo("America/New_York")


def format_eastern_time(value: datetime | None) -> str:
    """Render a stored UTC timestamp in U.S. Eastern time."""
    if value is None:
        return ""
    utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return utc_value.astimezone(EASTERN_TIME).strftime("%Y-%m-%d %I:%M %p %Z")
