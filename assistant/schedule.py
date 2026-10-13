"""Adapted from ShiGuangSchedule/shiguang_warehouse's Course adapter (MIT).

See THIRD_PARTY_NOTICES.md. Preserve lesson identities where supplied, validate
week arrays, and mark coverage as scheduled-only instead of claiming completeness.
"""
import hashlib
import re
from .models import Course, Meeting


def teacher_names(values):
    if isinstance(values, str):
        return [values]
    return [str((item.get("person") or item).get("nameZh", (item.get("person") or item).get("name", ""))) if isinstance(item, dict) else str(item)
            for item in (values or [])]


def parse_schedule_text(text):
    """Conservative text fallback: explicit weeks, odd/even weeks and all segments."""
    slots, known = [], True
    for part in re.split(r"[;；]|<br\s*/?>", text or ""):
        part = re.sub(r"<[^>]*>", " ", part).strip()
        if not part:
            continue
        day = re.search(r"(?:星期|周)([一二三四五六日天])", part)
        periods = re.search(r"(?:第)?(\d+)\s*(?:[~～\-–]\s*(\d+))?\s*节", part)
        weeks = re.search(r"(\d+\s*(?:[~～\-–]\s*\d+)?(?:\s*[,，、]\s*\d+\s*(?:[~～\-–]\s*\d+)?)*)\s*([（(]?[单双][）)]?)?\s*周\s*([（(][单双](?:周)?[）)])?", part)
        if not day or not periods or not weeks:
            known = False
            continue
        values = set()
        for token in re.split(r"[,，、]", weeks[1]):
            bounds = re.split(r"[~～\-–]", token.strip())
            start, end = int(bounds[0]), int(bounds[-1])
            if not 1 <= start <= end <= 60:
                known = False
                values.clear()
                break
            values.update(range(start, end + 1))
        parity = (weeks[2] or "") + (weeks[3] or "")
        if "单" in parity:
            values = {w for w in values if w % 2 == 1}
        if "双" in parity:
            values = {w for w in values if w % 2 == 0}
        try:
            slot = Meeting(weekday={"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}[day[1]],
                           start=int(periods[1]), end=int(periods[2] or periods[1]), weeks=sorted(values))
            if slot not in slots:
                slots.append(slot)
        except ValueError:
            known = False
    return slots, known and bool(slots)


def full_lessons(table):
    for key in ("allCourses", "lessonList", "lessons"):
        items = table.get(key)
        if isinstance(items, list):
            total = table.get("totalLessons", table.get("lessonCount"))
            complete = (key == "allCourses" or table.get("allCoursesComplete") is True or
                        (type(total) is int and total == len(items)))
            return items, complete
    return [], False


def normalize_print_data(data, semester, full_semester=False):
    tables = data.get("studentTableVms", [])
    if len(tables) != 1:
        raise ValueError("需要当前学生的单份课表数据，不能混合多个学生课表")
    table = tables[0]
    lessons, complete = full_lessons(table)
    complete = complete or (full_semester and isinstance(table.get("lessons"), list))
    result = {}
    for activity in table.get("activities", []):
        name = str(activity.get("courseName", "")).strip()
        if not name:
            continue
        teachers = teacher_names(activity.get("teachers"))
        lesson = activity.get("lesson") or {}
        lesson_id = activity.get("lessonId", activity.get("lessonAssoc", lesson.get("id")))
        class_code = str(activity.get("lessonCode") or lesson.get("code") or "")
        if lesson_id is None and class_code:
            matches = [item for item in lessons if isinstance(item, dict) and
                       str(item.get("code") or item.get("lessonCode") or "") == class_code]
            if len(matches) == 1:
                lesson_id = matches[0].get("id", matches[0].get("lessonId"))
        code = str(activity.get("courseCode") or "")
        # No fabricated lesson ID. A digest is only our local schedule record key.
        ident = str(lesson_id) if lesson_id is not None else hashlib.sha256(
            repr((name, teachers, code, activity.get("weekday"), activity.get("startUnit"),
                  activity.get("endUnit"), activity.get("weekIndexes"))).encode()).hexdigest()[:20]
        key = f"school:schedule:{semester}:{ident}"
        weeks = activity.get("weekIndexes")
        valid = (isinstance(weeks, list) and bool(weeks) and
                 all(type(w) is int and 1 <= w <= 60 for w in weeks))
        slots = []
        if valid:
            try:
                slots = [Meeting(weekday=activity["weekday"], start=activity["startUnit"],
                                 end=activity["endUnit"], weeks=weeks)]
            except (KeyError, ValueError):
                valid = False
        if key not in result:
            result[key] = Course(id=key, name=name, code=code, class_code=class_code,
                                 lesson_id=str(lesson_id) if lesson_id is not None else None,
                                 teachers=teachers, meetings=slots, schedule_known=valid,
                                 semester=str(semester), batch="schedule", entry="course-table",
                                 source="school", required=activity.get("required") is True,
                                 location=" ".join(str(activity.get(field) or "") for field in ("campus", "room")).strip())
        else:
            result[key].meetings.extend(slot for slot in slots if slot not in result[key].meetings)
            result[key].schedule_known = result[key].schedule_known and valid
    unresolved = False
    for lesson in lessons:
        if not isinstance(lesson, dict):
            unresolved = True
            continue
        course_info = lesson.get("course") or {}
        name = str(lesson.get("courseName") or course_info.get("nameZh") or lesson.get("nameZh") or "").strip()
        ident = lesson.get("lessonId", (lesson.get("lesson") or {}).get("id"))
        if ident is None and isinstance(lesson.get("course"), dict) and lesson.get("code"):
            ident = lesson.get("id")
        if not name:
            unresolved = True
            continue
        if ident is None:
            unresolved = True
        local_id = str(ident) if ident is not None else hashlib.sha256(repr((name, lesson.get("code"), lesson.get("teachers"))).encode()).hexdigest()[:20]
        key = f"school:schedule:{semester}:{local_id}"
        if key not in result:
            result[key] = Course(id=key, name=name, lesson_id=str(ident) if ident is not None else None,
                                 schedule_known=lesson.get("noFixedTime") is True,
                                 semester=str(semester), batch="schedule", entry="course-table", source="school")
        record = result[key]
        record.code = str(course_info.get("code") or lesson.get("courseCode") or record.code)
        record.class_code = str(lesson.get("code") or lesson.get("lessonCode") or record.class_code)
        record.teachers = teacher_names(lesson.get("teachers") or lesson.get("teacherAssignmentList")) or record.teachers
        schedule = lesson.get("scheduleText") or {}
        if not record.meetings and isinstance(schedule, dict):
            summary = schedule.get("dateTimePlacePersonText") or {}
            text = (summary.get("textZh") or summary.get("text") or "") if isinstance(summary, dict) else ""
            if text:
                record.meetings, record.schedule_known = parse_schedule_text(text)
        credits = course_info.get("credits", lesson.get("credits"))
        if isinstance(credits, (float, int)) and 0 <= credits <= 50:
            record.credits = credits
        record.required = lesson.get("required") is True or "必修" in str(lesson.get("courseType") or "")
    full_ids = {str(item.get("lessonId", (item.get("lesson") or {}).get("id", item.get("id")))) for item in lessons if isinstance(item, dict)}
    missing = any(not c.lesson_id or c.lesson_id not in full_ids for c in result.values()) if complete else False
    units = (table.get("timeTableLayout") or {}).get("courseUnitList", [])
    return {"courses": list(result.values()), "coverage": "complete" if complete and not unresolved and not missing else "scheduled_only",
            "time_slots": [{"number": unit.get("indexNo"), "start": unit.get("startTime"),
                            "end": unit.get("endTime")} for unit in units]}


def merge_schedule_sources(scheduled, full):
    """Never let a supposedly complete response hide a printed scheduled lesson."""
    courses = list(full["courses"])
    consistent = True
    for old in scheduled["courses"]:
        matches = [c for c in courses if old.lesson_id and c.lesson_id == old.lesson_id]
        if not old.lesson_id and old.class_code:
            matches = [c for c in courses if c.class_code == old.class_code and c.name == old.name]
        if len(matches) == 1:
            record = matches[0]
            if not record.meetings and old.meetings:
                record.meetings, record.schedule_known = old.meetings, old.schedule_known
            record.location = record.location or old.location
        else:
            consistent = False
            if all(c.id != old.id for c in courses):
                courses.append(old)
    return {**full, "courses": courses,
            "coverage": full["coverage"] if consistent else "scheduled_only",
            "time_slots": scheduled["time_slots"] or full["time_slots"]}


def merge_all_course_rows(result, rows, semester, total=None):
    """Supplement from the normal page's explicitly labelled 全部课程 table."""
    courses = {c.id: c for c in result["courses"]}
    seen, identities, ambiguous = set(), set(), False
    for row in rows:
        ident = row.get("lesson_id")
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        key = f"school:schedule:{semester}:{ident}" if ident else "school:unscheduled:" + hashlib.sha256(
            repr((semester, row.get("code"), row.get("class_code"), name, row.get("teachers"))).encode()).hexdigest()[:20]
        if key in seen:
            ambiguous = True
            continue
        seen.add(key)
        if ident:
            identities.add(str(ident))
        record = courses.get(key)
        if record is None and not ident:
            matches = [c for c in courses.values() if c.name == name and c.class_code and c.class_code == row.get("class_code")]
            record = matches[0] if len(matches) == 1 else None
        if record is None:
            record = Course(id=key, name=name, lesson_id=str(ident) if ident else None,
                            code=str(row.get("code") or ""), class_code=str(row.get("class_code") or ""),
                            teachers=teacher_names(row.get("teachers")), semester=str(semester), batch="schedule",
                            entry="course-table", source="school", schedule_known=row.get("no_fixed_time") is True)
            courses[key] = record
    scheduled_ids = {c.lesson_id for c in result["courses"] if c.lesson_id}
    complete = (type(total) is int and total == len(seen) and not ambiguous and
                scheduled_ids <= identities and all(c.lesson_id for c in courses.values()))
    extra = len(courses) > len(result["courses"])
    return {**result, "courses": list(courses.values()),
            "coverage": "complete" if complete else "scheduled_only" if extra else result["coverage"]}
