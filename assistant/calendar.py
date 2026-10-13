"""2026–2027 Course calendar supplied by the user; server metadata takes priority."""
from copy import deepcopy
from datetime import date
import re

PERIODS = [
    (1, "09:30", "10:15"), (2, "10:20", "11:05"),
    (3, "11:25", "12:10"), (4, "12:15", "13:00"),
    (5, "13:05", "13:50"), (6, "16:00", "16:45"),
    (7, "16:50", "17:35"), (8, "17:55", "18:40"),
    (9, "18:45", "19:30"), (10, "20:30", "21:15"),
    (11, "21:20", "22:05"), (12, "22:10", "22:55"),
]

def clock(value):
    text = str(value or "").strip()
    if text.isdecimal():
        text = text.zfill(4)
        text = text[:2] + ":" + text[2:]
    if not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", text):
        return None
    hours, minutes = map(int, text[:5].split(":"))
    return text[:5] if 0 <= hours < 24 and 0 <= minutes < 60 else None

def default_calendar(name="2026-2027-1"):
    spring = name.endswith("-2") or "第二学期" in name
    return {"semester_name": name, "start_date": "2027-03-01" if spring else "2026-08-31",
            "total_weeks": 26, "week_start_on_sunday": False,
            "calendar_source": "用户提供的 2026–2027 学年校历",
            "time_source": "用户提供的校历上课时间",
            "time_slots": [{"number": n, "start": start, "end": end} for n, start, end in PERIODS]}

def merge_calendar(metadata=None, time_slots=None, name="2026-2027-1"):
    result = default_calendar(name)
    metadata = metadata or {}
    start = str(metadata.get("startDate") or metadata.get("start_date") or "")[:10]
    end = str(metadata.get("endDate") or "")[:10]
    try:
        start_date = date.fromisoformat(start)
    except ValueError:
        start_date = None
    if start_date:
        result.update(start_date=start, calendar_source="学校教务学期信息",
                      week_start_on_sunday=bool(metadata.get("weekStartOnSunday", metadata.get("week_start_on_sunday", False))))
        try:
            weeks = ((date.fromisoformat(end) - start_date).days // 7) + 1
            if 1 <= weeks <= 60:
                result["total_weeks"] = weeks
        except ValueError:
            pass
    if type(metadata.get("total_weeks")) is int and 1 <= metadata["total_weeks"] <= 60:
        result["total_weeks"] = metadata["total_weeks"]
    mapped = {item["number"]: item for item in deepcopy(result["time_slots"])}
    accepted = 0
    for item in time_slots or []:
        number = item.get("number", item.get("indexNo"))
        if isinstance(number, str) and number.isdecimal():
            number = int(number)
        start_clock = clock(item.get("start", item.get("startTime")))
        end_clock = clock(item.get("end", item.get("endTime")))
        if isinstance(number, int) and 1 <= number <= 24 and start_clock and end_clock and start_clock < end_clock:
            mapped[number] = {"number": number, "start": start_clock, "end": end_clock}
            accepted += 1
    result["time_slots"] = [mapped[n] for n in sorted(mapped)]
    if accepted:
        result["time_source"] = "教务节次时间优先，缺失节次采用用户校历"
    return result
