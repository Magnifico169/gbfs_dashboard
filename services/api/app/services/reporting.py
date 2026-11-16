"""Непересекающиеся уровни отчёта, границы дочитываются до 10 минут."""

from calendar import monthrange
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

SUM_FIELDS = (
    "ebikes_seconds",
    "ebikes_observed_seconds",
    "bikes_with_ebikes_seconds",
    "observed_seconds",
    "bikes_seconds",
    "docks_seconds",
    "docks_observed_seconds",
    "empty_seconds",
    "full_seconds",
    "bikes_added",
    "bikes_removed",
)


def resolution_for(start, end):
    seconds = (end - start).total_seconds()
    return "10m" if seconds <= 600 * 998 else "hour" if seconds <= 3600 * 998 else "day"


def segments(start, end, resolution, timezone):
    """План чтения без перекрытий, включая 23/25-часовые местные сутки."""
    zone = ZoneInfo(timezone)
    result = []
    cursor = start
    while cursor < end:
        local = cursor.astimezone(zone)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = (midnight + timedelta(days=1)).astimezone(UTC)
        hour = cursor.replace(minute=0, second=0, microsecond=0)
        # Часовая сетка привязана к местной полуночи, включая зоны с :30/:45.
        elapsed = (cursor - midnight.astimezone(UTC)).total_seconds()
        hour = midnight.astimezone(UTC) + timedelta(seconds=int(elapsed // 3600) * 3600)
        ten = hour + timedelta(minutes=int((cursor - hour).total_seconds() // 600) * 10)
        if resolution == "day" and cursor == midnight and next_day <= end:
            level, finish = "day", next_day
        elif resolution != "10m" and cursor == hour and hour + timedelta(hours=1) <= end:
            level, finish = "hour", hour + timedelta(hours=1)
        else:
            level, finish = "10m", min(ten + timedelta(minutes=10), end)
        if result and result[-1][0] == level and result[-1][2] == cursor:
            result[-1] = (level, result[-1][1], finish)
        else:
            result.append((level, cursor, finish))
        cursor = finish
    return result


def metrics(row):
    observed = row.get("observed_seconds", 0) or 0
    docks = row.get("docks_observed_seconds", 0) or 0
    return {
        **row,
        "bikes_mean": row["bikes_seconds"] / observed if observed else None,
        "docks_mean": row["docks_seconds"] / docks if docks else None,
    }


def parse_period(start, end, timezone, preset="month"):
    zone = ZoneInfo(timezone)
    now = datetime.now(UTC)
    if not start and not end:
        local = now.astimezone(zone)
        if preset in ("day", "week"):
            left = local - timedelta(days=1 if preset == "day" else 7)
        else:
            months = {"month": 1, "quarter": 3, "half": 6, "nine": 9, "year": 12}.get(preset, 1)
            year, month = divmod(local.year * 12 + local.month - 1 - months, 12)
            day = min(local.day, monthrange(year, month + 1)[1])
            left = local.replace(year=year, month=month + 1, day=day)
        return left.astimezone(UTC), now
    if not start or not end:
        raise ValueError("Укажите начало и конец интервала")

    def parse(value):
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            aware = dt.replace(tzinfo=zone)
            back = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
            if back != dt or aware.utcoffset() != dt.replace(tzinfo=zone, fold=1).utcoffset():
                raise ValueError(
                    "Неоднозначное или пропущенное время DST: укажите ISO-дату со смещением"
                )
            dt = aware
        return dt.astimezone(UTC)

    left, right = parse(start), parse(end)
    if not left < right or right - left > timedelta(days=3660):
        raise ValueError("Начало должно быть раньше конца; максимум 3660 дней")
    if right > now + timedelta(seconds=60):
        raise ValueError("Конец отчёта не может быть в будущем")
    return left, right
