import asyncio
import time
from .activity import Activity, now_iso
from .browser import BrowserIssue
from .calendar import default_calendar


class TimetableSync:
    """One shared updater. A failed/partial read never deletes a good full snapshot."""
    def __init__(self, store, school):
        self.store, self.school = store, school
        self.lock = asyncio.Lock()
        self.activity = Activity()
        self.meta = store.get("school:sync", {"state": "idle", "message": "先导入本学期已有课表",
                        "last_success": None, "semester": None, "source": None})
        if self.meta["state"] == "running":
            self.meta.update(state="interrupted", message="上次同步中断，保留课表，需检查会话后重试")
        self.config = store.get("school:sync_config", {"enabled": False, "started": False, "interval": 60, "semester": None})
        self.next_due = time.monotonic() + self.config["interval"]
        self.closed = asyncio.Event()
        self.worker = None

    def public(self):
        return {**self.meta, "config": dict(self.config), "activity": self.activity.public(),
                "next_in": max(0, round(self.next_due - time.monotonic())) if self.config["enabled"] else None}

    def save(self):
        self.store.put("school:sync", self.meta)
        self.store.put("school:sync_config", self.config)

    async def sync(self, semester, automatic=False):
        if self.lock.locked():
            raise BrowserIssue("课表正在同步，请查看流程监控，不必重复点击")
        async with self.lock:
            previous = self.store.get("school:existing", [])
            self.meta.update(state="running", message="正在读取学校本学期课表", last_attempt=now_iso())
            self.activity.begin("自动同步课表" if automatic else "导入已有课表")
            self.activity.step("read", "从当前学生的学校课表读取课程与上课时间")
            self.save()
            try:
                result = await self.school.schedule(semester)
                courses = [course.model_dump() for course in result["courses"]]
                if len({course["id"] for course in courses}) != len(courses):
                    raise BrowserIssue("课表包含无法区分的课程记录，保留上次数据")
                old_complete = self.store.get("school:coverage") == "complete" and self.meta.get("semester") == semester
                if old_complete and result["coverage"] != "complete":
                    self.meta.update(state="partial", message="本次只获得部分课程，保留上次完整课表，等待重新同步")
                    self.activity.finish("failed", self.meta["message"])
                    self.save()
                    return self.public()
                self.activity.step("merge", "核对全部课程、时间片段和当前学期")
                old = {c["id"]: c for c in previous}
                new = {c["id"]: c for c in courses}
                changes = {"added": len(new.keys() - old.keys()), "removed": len(old.keys() - new.keys()),
                           "changed": sum(old[key] != new[key] for key in new.keys() & old.keys())}
                self.store.put("school:existing", courses)
                self.store.put("school:coverage", result["coverage"])
                self.store.put("school:calendar", result.get("calendar") or default_calendar())
                self.meta.update(state="success", message=f"已同步 {len(courses)} 门已有课程" +
                                 ("，全部课程列表已核实" if result["coverage"] == "complete" else "，列表完整性仍需核实"),
                                 last_success=now_iso(), semester=semester, source="school", changes=changes,
                                 course_count=len(courses), coverage=result["coverage"])
                if not self.config.get("started"):
                    self.config.update(enabled=True, started=True)
                self.config["semester"] = semester
                self.activity.finish("succeeded", self.meta["message"])
                self.save()
                return self.public()
            except asyncio.CancelledError:
                self.meta.update(state="interrupted", message="同步已暂停，保留上次课表")
                self.activity.finish("failed", self.meta["message"])
                self.save()
                raise
            except Exception as error:
                manual = self.school.status["state"] in {"manual_required", "not_started"} or not self.school.context
                self.meta.update(state="waiting_login" if manual else "error",
                                 message=str(error) if isinstance(error, BrowserIssue) else "课表更新失败，保留上次课表，请检查连接后重试")
                self.activity.finish("waiting_user" if manual else "failed", self.meta["message"])
                if self.school.activity.data["state"] == "running" and self.school.activity.data["operation"] == "读取学校我的课表":
                    self.school.activity.finish("waiting_user" if manual else "failed", self.meta["message"])
                self.save()
                if not automatic:
                    raise BrowserIssue(self.meta["message"]) from None
                return self.public()
            finally:
                self.next_due = time.monotonic() + self.config["interval"]

    def configure(self, enabled, interval=60):
        if enabled and not self.config.get("semester"):
            raise BrowserIssue("先从学校成功导入本学期课表，再开启自动同步")
        self.config.update(enabled=enabled, interval=interval, started=True)
        self.next_due = time.monotonic() + interval
        self.save()

    def record_import(self, count, coverage):
        self.meta.update(state="imported", message=f"已从文件导入 {count} 门已有课程", last_success=now_iso(),
                         source="file", course_count=count, coverage=coverage, semester=None)
        self.config.update(enabled=False, semester=None, started=False)
        self.save()

    async def tick(self):
        if not self.config["enabled"] or time.monotonic() < self.next_due or self.lock.locked() or self.school.lock.locked():
            return
        if self.school.status["state"] != "connected":
            if self.meta["state"] != "waiting_login":
                self.meta.update(state="waiting_login", message="自动同步暂停，请检查登录会话；已有课表保留")
                self.activity.begin("自动同步课表")
                self.activity.step("session", "登录会话需要人工检查")
                self.activity.finish("waiting_user", self.meta["message"])
                self.save()
            return
        await self.sync(self.config["semester"], automatic=True)

    async def run(self):
        while not self.closed.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(self.closed.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass

    def start(self):
        self.worker = asyncio.create_task(self.run())

    async def close(self):
        self.closed.set()
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
