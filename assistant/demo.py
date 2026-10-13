import asyncio
from dataclasses import dataclass
from .models import Course, Meeting


def meeting(day, start, end, weeks=None):
    return Meeting(weekday=day, start=start, end=end, weeks=weeks or list(range(1, 17)))


def sample_courses():
    specs = [
        ("photo-a", "摄影基础", "ART101", "张悦", 2, 7, 8, 30, 28, 1),
        ("photo-b", "摄影基础", "ART101", "李思", 4, 7, 8, 40, 32, 3),
        ("music", "西方音乐赏析", "ART204", "王宁", 5, 9, 10, 60, 59, 2),
        ("ai", "人工智能导论", "SCI108", "赵越", 1, 1, 2, 80, 70, 1),
        ("film", "电影与文化", "ART206", "林晓", 4, 5, 6, 50, 44, 2),
        ("ecology", "生态与可持续发展", "ENV105", "周岚", 2, 9, 10, 40, 40, 4),
        ("city", "城市文化观察", "SOC102", "肖然", 2, 7, 8, 40, 35, 1),
        ("speech", "学术表达与沟通", "GEN201", "孙雨", 1, 5, 6, 50, 30, 3),
        ("ethics", "工程伦理", "GEN103", "陈禾", 1, 1, 2, 60, 44, 2),
    ]
    result = []
    for ident, name, code, teacher, day, start, end, capacity, count, page in specs:
        weeks = [17, 18] if ident == "ethics" else None
        result.append(Course(id=f"demo:{ident}", lesson_id=ident, name=name, code=code,
                             class_code=f"{code}-{'02' if ident == 'photo-b' else '01'}",
                             teachers=[teacher], credits=2, meetings=[meeting(day, start, end, weeks)],
                             capacity=capacity, enrolled_count=count, page=page,
                             location="教学楼 B 区"))
    return result


def required_courses():
    return [
        Course(id="demo:math", name="高等数学", code="MAT101", credits=5, required=True,
               teachers=["李老师"], meetings=[meeting(1, 1, 2)]),
        Course(id="demo:english", name="大学英语", code="ENG101", credits=3, required=True,
               teachers=["王老师"], meetings=[meeting(3, 3, 4)]),
    ]


@dataclass
class Snapshot:
    courses: list[Course]
    page_selected: set[str]
    timetable_selected: set[str]


@dataclass
class Submission:
    state: str
    message: str


class DemoAdapter:
    mode = "demo"

    def __init__(self, store, latency=0.3):
        self.store, self.latency = store, latency
        self.catalog = sample_courses()
        self.selected = set(store.get("demo:selected", []))
        for course in self.catalog:
            if course.id in self.selected:
                course.enrolled_count += 1
        self.calls = {}
        self.active_submissions = 0
        self.max_concurrent_submissions = 0
        self.order = []

    async def snapshot(self):
        await asyncio.sleep(min(self.latency, 0.05))
        # Deliberately stale page status for speech: timetable alone confirms it.
        page = self.selected - {"demo:speech"}
        return Snapshot(self.catalog, page, set(self.selected))

    async def submit(self, course):
        self.active_submissions += 1
        self.max_concurrent_submissions = max(self.max_concurrent_submissions, self.active_submissions)
        self.order.append(course.id)
        try:
            await asyncio.sleep(self.latency)
            self.calls[course.id] = self.calls.get(course.id, 0) + 1
            if course.id in self.selected:
                return Submission("accepted", "该教学班已选中")
            if course.remaining == 0:
                return Submission("full", "人数已满")
            if course.id == "demo:film" and self.calls[course.id] == 1:
                return Submission("retryable", "模拟繁忙：稍后重试")
            self.selected.add(course.id)
            if course.enrolled_count is not None:
                course.enrolled_count += 1
            self.store.put("demo:selected", sorted(self.selected))
            if course.id == "demo:speech":
                return Submission("unknown", "模拟提交超时：先核验课表")
            return Submission("accepted", "模拟选课系统已受理")
        finally:
            self.active_submissions -= 1

    def reset(self):
        self.selected.clear()
        self.calls.clear()
        self.order.clear()
        self.store.put("demo:selected", [])
        self.catalog = sample_courses()
