"""A wall-clock time in an IANA timezone, as a stage's `not_before` says it: `17:00
America/Toronto`. Shared here because Workflows checks it at save and Runs waits on it.

The rule is the local clock, not an offset from UTC, so a daylight saving change never moves it.
"""

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_FORM = re.compile(r"([01]?\d|2[0-3]):([0-5]\d) +(\S+)")


def local_time_problem(text: object) -> str | None:
    """Why `text` is not a local time with a timezone, or None when it is."""
    found = _FORM.fullmatch(text.strip()) if isinstance(text, str) else None
    if found is None:
        return "not_before must be a time and a timezone, as 17:00 America/Toronto"
    try:
        ZoneInfo(found[3])
    except (ZoneInfoNotFoundError, ValueError):
        return f"not_before names an unknown timezone '{found[3]}', use an IANA name"
    return None


def next_local(text: str, after: datetime) -> datetime:
    """The first moment at or after `after` that the clock in the timezone reads the time, in
    UTC. A time a spring-forward skips reads as the hour after. Raises ValueError for bad text."""
    if (problem := local_time_problem(text)) is not None:
        raise ValueError(problem)
    found = _FORM.fullmatch(text.strip())
    assert found is not None
    zone = ZoneInfo(found[3])
    at = time(int(found[1]), int(found[2]))
    day = after.astimezone(zone).date()
    while (when := datetime.combine(day, at, zone)) < after:
        day += timedelta(days=1)
    return when.astimezone(UTC)
