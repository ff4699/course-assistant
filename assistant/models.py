from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, model_validator


Mode = Literal["demo", "school"]


class Meeting(BaseModel):
    weekday: int = Field(ge=1, le=7)
    start: int = Field(ge=1, le=24)
    end: int = Field(ge=1, le=24)
    weeks: list[int] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end < self.start or any(w < 1 or w > 60 for w in self.weeks):
            raise ValueError("周次或节次范围无效")
        self.weeks = sorted(set(self.weeks))
        return self


class SchoolDisplay(BaseModel):
    """Only the eight visible school columns, with no authentication metadata."""
    verified: bool = False
    operation: str = Field(default="", max_length=6000)
    status: str = Field(default="", max_length=6000)
    info: str = Field(default="", max_length=6000)
    module: str = Field(default="", max_length=6000)
    lesson: str = Field(default="", max_length=6000)
    teachers: str = Field(default="", max_length=6000)
    time_place: str = Field(default="", max_length=6000)
    enrollment: str = Field(default="", max_length=6000)


class Course(BaseModel):
    id: str = Field(min_length=1, max_length=180)
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(default="", max_length=80)
    lesson_id: str | None = None
    class_code: str = Field(default="", max_length=80)
    teachers: list[str] = Field(default_factory=list, max_length=20)
    credits: float = Field(default=0, ge=0, le=50)
    meetings: list[Meeting] = Field(default_factory=list, max_length=30)
    schedule_known: bool = True
    semester: str = "2026-2027-1"
    batch: str = "demo-autumn"
    entry: str = "elective"
    capacity: int | None = Field(default=None, ge=0)
    enrolled_count: int | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1)
    location: str = Field(default="", max_length=120)
    required: bool = False
    source: Mode = "demo"
    school_display: SchoolDisplay | None = None

    @property
    def remaining(self) -> int | None:
        if self.capacity is None or self.enrolled_count is None:
            return None
        return max(0, self.capacity - self.enrolled_count)

    def public(self):
        return {**self.model_dump(), "remaining": self.remaining}


class Plan(BaseModel):
    course_ids: list[str] = Field(default_factory=list, max_length=5)
    retries: int = Field(default=3, ge=0, le=10)
    scarcity_first: bool = True
    retry_delay: float = Field(default=1.0, ge=0.5, le=10)
    max_duration: int = Field(default=120, ge=10, le=600)
    start_at: str | None = None

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.course_ids)) != len(self.course_ids):
            raise ValueError("同一教学班不能重复加入计划")
        if self.start_at:
            try:
                moment = datetime.fromisoformat(self.start_at.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("开始时间格式无效")
            if moment.tzinfo is None:
                raise ValueError("开始时间必须包含时区")
            self.start_at = moment.isoformat()
        return self


class Target(BaseModel):
    id: str
    priority: int
    status: str = "pending"
    attempts: int = 0
    reason: str = "等待执行"
    evidence: str | None = None


def conflicts(a: Course, b: Course) -> list[dict]:
    """Intersect actual weeks as well as weekday and period intervals."""
    matches = {}
    for left in a.meetings:
        for right in b.meetings:
            weeks = sorted(set(left.weeks) & set(right.weeks))
            start, end = max(left.start, right.start), min(left.end, right.end)
            if left.weekday == right.weekday and weeks and start <= end:
                matches.setdefault((left.weekday, start, end), set()).update(weeks)
    return [{"weekday":day,"start":start,"end":end,"weeks":sorted(weeks)}
            for (day,start,end),weeks in sorted(matches.items())]


def course_identity(course: Course):
    if course.lesson_id and course.semester not in {'', 'unverified'}:
        return (course.source, course.semester, course.lesson_id)
    return (course.source, course.semester, course.id)


def analyze_plan(plan: Plan, catalog: list[Course], existing: list[Course]):
    by_id = {course.id: course for course in catalog}
    chosen = [by_id[cid] for cid in plan.course_ids if cid in by_id]
    issues = []
    seen_pairs = set()
    for cid in plan.course_ids:
        if cid not in by_id:
            issues.append({"course_id": cid, "kind": "missing", "message": "课程已不在当前目录中"})
    for index, course in enumerate(chosen):
        if not course.schedule_known:
            issues.append({"course_id": course.id, "kind": "unknown", "message": "上课时间尚未完整核实"})
        for other, scope in [(c,'existing') for c in existing] + [(c,'plan') for c in chosen[:index]]:
            same_lesson = (course.lesson_id and course.lesson_id == other.lesson_id and
                           course.semester == other.semester and course.source == other.source)
            if course.id == other.id or same_lesson:
                continue
            if (course.semester not in {"", "unverified"} and other.semester not in {"", "unverified"}
                    and course.semester != other.semester):
                issues.append({"course_id": course.id, "other_id": other.id, "kind": "semester",
                               "scope": scope, "message": "课表与预选课程学期不一致，请先同步正确学期"})
                continue
            overlap = conflicts(course, other)
            if overlap:
                pair = tuple(sorted((course_identity(course),course_identity(other))))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                issues.append({"course_id": course.id, "other_id": other.id,
                               "kind": "conflict", "scope": scope,
                               "message": f"与{'已有' if scope == 'existing' else '预选'}课程「{other.name}」时间冲突",
                               "overlap": overlap})
        if course.remaining == 0:
            issues.append({"course_id": course.id, "kind": "full", "message": "当前名额已满"})
    unknown_existing = any(not course.schedule_known for course in existing)
    if unknown_existing:
        issues.append({"course_id": None, "kind": "unknown", "message": "已有课程存在未核实的上课时间"})
    blocking = [issue for issue in issues if issue["kind"] != "full"]
    overlap_issues = [i for i in issues if i['kind']=='conflict']
    existing_ids = {course_identity(c) for c in existing if any(i.get('scope')=='existing' and i.get('other_id')==c.id for i in overlap_issues)}
    identities = {c.id:course_identity(c) for c in catalog+existing}
    participants = {identities[cid] for i in overlap_issues for cid in (i['course_id'],i['other_id']) if cid in identities}
    planned_ids = {course_identity(c) for c in chosen} & participants
    summary = {'pairs':len(overlap_issues),'plan_pairs':sum(i['scope']=='plan' for i in overlap_issues),
               'existing_pairs':sum(i['scope']=='existing' for i in overlap_issues),
               'planned_courses':len(planned_ids),'existing_courses':len(existing_ids)}
    return {"issues": issues, "can_run": not blocking and bool(chosen), "conflict_summary":summary,
            "credits": sum(course.credits for course in chosen)}
