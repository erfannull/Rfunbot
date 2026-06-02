from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(value: str | int) -> str:
    return str(value).translate(PERSIAN_DIGITS)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    if jy > 979:
        gy = 1600
        jy -= 979
    else:
        gy = 621
    days = 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + 78 + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += (jm - 7) * 30 + 186
    gy += 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal_a = [0, 31, 29 if (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def jalali_month_length(jy: int, jm: int) -> int:
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    # Esfand leap handling via difference to next Farvardin.
    start = date(*jalali_to_gregorian(jy, 12, 1))
    end = date(*jalali_to_gregorian(jy + 1, 1, 1))
    return (end - start).days


def local_jalali(dt: datetime) -> tuple[int, int, int]:
    return gregorian_to_jalali(dt.year, dt.month, dt.day)


def jalali_date_label(dt: datetime, persian: bool = True) -> str:
    jy, jm, jd = local_jalali(dt)
    label = f"{jy:04d}/{jm:02d}/{jd:02d}"
    return fa_digits(label) if persian else label


def jalali_month_key(dt: datetime) -> str:
    jy, jm, _ = local_jalali(dt)
    return f"{jy:04d}-{jm:02d}"


def local_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def iran_week_bounds(now: datetime) -> tuple[datetime, datetime]:
    # Python weekday: Monday=0. Iran week starts Saturday=5.
    days_since_saturday = (now.weekday() - 5) % 7
    start = (now - timedelta(days=days_since_saturday)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def jalali_month_bounds(now: datetime) -> tuple[datetime, datetime]:
    tz = now.tzinfo
    jy, jm, _ = local_jalali(now)
    gy, gm, gd = jalali_to_gregorian(jy, jm, 1)
    if jm == 12:
        next_jy, next_jm = jy + 1, 1
    else:
        next_jy, next_jm = jy, jm + 1
    ngy, ngm, ngd = jalali_to_gregorian(next_jy, next_jm, 1)
    return datetime(gy, gm, gd, tzinfo=tz), datetime(ngy, ngm, ngd, tzinfo=tz)


def previous_period_bounds(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    span = end - start
    return start - span, start


def three_month_backup_bounds(now: datetime) -> tuple[datetime, datetime]:
    end = now.replace(microsecond=0)
    start = (end - timedelta(days=92)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def parse_jalali_or_gregorian_date(value: str, tz: ZoneInfo, current_local: datetime | None = None) -> datetime | None:
    text = value.strip().replace("-", "/")
    if text in {"امروز", "today"}:
        base = current_local or datetime.now(tz)
        return base.replace(hour=12, minute=0, second=0, microsecond=0)
    if text in {"دیروز", "yesterday"}:
        base = (current_local or datetime.now(tz)) - timedelta(days=1)
        return base.replace(hour=12, minute=0, second=0, microsecond=0)
    parts = [part for part in text.split("/") if part]
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    year, month, day = map(int, parts)
    try:
        if year < 1700:
            gy, gm, gd = jalali_to_gregorian(year, month, day)
            return datetime.combine(date(gy, gm, gd), time(12, 0), tzinfo=tz)
        return datetime.combine(date(year, month, day), time(12, 0), tzinfo=tz)
    except ValueError:
        return None
