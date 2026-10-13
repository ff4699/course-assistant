from __future__ import annotations
import asyncio
import time
from datetime import datetime, timezone
from .models import Plan, Target, analyze_plan
from .activity import now_iso
from .workflow import run_summary


TERMINAL = {"succeeded", "full", "blocked", "exhausted"}


class Engine:
    """One sequential submitter and one shared confirmation snapshot."""
    def __init__(self, store, adapter, on_success=None):
        self.store, self.adapter = store, adapter
        self.on_success=on_success
        self.task = None
        self.start_lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.run_state = store.get("demo:run", {"phase": "idle", "targets": [], "events": []})
        self.run_state.setdefault("progress", {"stage": "idle", "message": "等待演练", "updated_at": None})
        if self.run_state["phase"] in {"running", "waiting", "stopping"}:
            self.run_state["phase"] = "interrupted"
            for item in self.run_state["targets"]:
                if item["status"] == "submitting":
                    item["status"] = "unconfirmed"
                    item["reason"] = "上次执行中断，重启后先核验"
            self.save()

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def save(self):
        self.store.put("demo:run", self.run_state)

    def event(self, message, kind="info", course_id=None):
        self.run_state["events"].append({"time": datetime.now(timezone.utc).isoformat(),
                                         "message": message, "kind": kind, "course_id": course_id})
        self.run_state["events"] = self.run_state["events"][-100:]
        self.save()

    def progress(self, stage, message, course_id=None):
        self.run_state["progress"] = {"stage": stage, "message": message, "course_id": course_id,
                                      "updated_at": datetime.now(timezone.utc).isoformat()}
        self.save()

    async def start(self, plan: Plan, existing):
        async with self.start_lock:
            await self._start(plan, existing)

    async def _start(self, plan: Plan, existing):
        if self.active:
            raise ValueError("已有执行正在进行")
        snapshot = await self.adapter.snapshot()
        check = analyze_plan(plan, snapshot.courses, existing)
        if not check["can_run"]:
            raise ValueError("请先处理课程冲突或不完整的课程时间")
        if plan.start_at:
            moment = datetime.fromisoformat(plan.start_at.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError("开始时间必须包含时区")
        self.stop_event = asyncio.Event()
        self.run_state = {"phase": "waiting" if plan.start_at else "running",
                          "targets": [Target(id=cid, priority=i + 1).model_dump() for i, cid in enumerate(plan.course_ids)],
                          "events": [], "started_at": datetime.now(timezone.utc).isoformat(),
                          "start_at": plan.start_at,"elapsed_ms":0,"segment_started_at":None,
                          "timetable_updated":False,"resume_count":0}
        self.event("模拟演练开始；所有提交按顺序执行")
        self.progress("waiting" if plan.start_at else "checking", "等待设定的开始时间" if plan.start_at else "已检查已有课程及预选课程冲突")
        self.task = asyncio.create_task(self._run(plan))

    async def resume(self,plan,existing):
        async with self.start_lock:
            if self.active:
                raise ValueError('已有执行正在进行')
            if not self.run_state.get('targets') or set(plan.course_ids)!={t['id'] for t in self.run_state['targets']}:
                raise ValueError('继续执行需保留原目标课程；更换计划后请重新开始')
            snapshot=await self.reconcile()
            if not analyze_plan(plan,snapshot.courses,existing)['can_run']:
                raise ValueError('请先处理时间冲突或未核实数据')
            courses={c.id:c for c in snapshot.courses}
            pending=[]
            for target in self.run_state['targets']:
                if target['status'] in {'succeeded','unconfirmed'}:continue
                course=courses.get(target['id'])
                if course and course.remaining!=0 and target['attempts']<1+plan.retries:
                    pending.append(target)
            if not pending:
                raise ValueError('没有可继续提交的课程；未确认结果只核验，已达重试上限需先调整次数')
            for target in pending:target.update(status='pending',reason='继续前已核验，等待处理')
            self.stop_event=asyncio.Event()
            self.run_state.update(phase='running',resume_count=self.run_state.get('resume_count',0)+1)
            self.event('先核验已有结果，仅继续未完成课程；保留已成功课程和累计尝试次数')
            self.task=asyncio.create_task(self._run(plan.model_copy(update={'start_at':None})))

    async def stop(self):
        if self.active:
            self.stop_event.set()
            self.run_state["phase"] = "stopping"
            self.event("停止新提交；仍会核验正在进行的请求", "warning")
            self.progress("stopping", "停止新提交，等待在途请求核验")

    def confirm(self, snapshot):
        for target in self.run_state["targets"]:
            cid = target["id"]
            if cid in snapshot.page_selected or cid in snapshot.timetable_selected:
                if target["status"] != "succeeded":
                    evidence = "选课页面" if cid in snapshot.page_selected else "课表"
                    target.update(status="succeeded", reason=f"{evidence}已确认该教学班", evidence=evidence)
                    target['confirmed_at']=now_iso()
                    if target.get('submitted_at'):
                        target['elapsed_ms']=round(max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(target['submitted_at'])).total_seconds()*1000),1)
                    self.event(f"选课成功，核验来源：{evidence}", "success", cid)
        self.save()

    async def reconcile(self):
        snapshot = await self.adapter.snapshot()
        self.confirm(snapshot)
        return snapshot

    async def _pause(self, delay):
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=max(delay, 0.01))
        except asyncio.TimeoutError:
            pass

    async def _run(self, plan):
        cooldowns = {}
        attempts_limit = 1 + plan.retries
        deadline = None
        segment_start=None
        try:
            if plan.start_at:
                start = datetime.fromisoformat(plan.start_at.replace("Z", "+00:00")).timestamp()
                while time.time() < start and not self.stop_event.is_set():
                    await self._pause(min(1, start - time.time()))
            deadline = time.monotonic() + plan.max_duration
            if not self.stop_event.is_set():
                segment_start=time.monotonic()
                self.run_state['segment_started_at']=now_iso()
                self.run_state.setdefault('execution_started_at',now_iso())
            self.run_state["phase"] = "running"
            self.save()
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                self.progress("checking", "统一核验已选状态，检查目标教学班名额")
                snapshot = await self.reconcile()
                by_id = {c.id: c for c in snapshot.courses}
                candidates = []
                for target in self.run_state["targets"]:
                    if target["status"] in TERMINAL:
                        continue
                    if target["status"] == "unconfirmed":
                        # Absence after a timed-out submission is not a definite failure.
                        continue
                    course = by_id.get(target["id"])
                    if course is None:
                        target.update(status="blocked", reason="课程不在当前批次目录")
                        continue
                    if course.remaining == 0:
                        target.update(status="full", reason="名额已满，不重复提交")
                        self.event("教学班人数已满，转向其他课程", "warning", course.id)
                        continue
                    if target["attempts"] >= attempts_limit:
                        target.update(status="exhausted", reason="达到重试次数上限")
                        continue
                    if time.monotonic() >= cooldowns.get(course.id, 0):
                        scarcity = course.remaining if course.remaining is not None else float("inf")
                        rank = (scarcity, target["priority"], course.page or 999) if plan.scarcity_first else (target["priority"], scarcity, course.page or 999)
                        candidates.append((rank, target, course))
                self.save()
                unfinished = [t for t in self.run_state["targets"] if t["status"] not in TERMINAL]
                if not unfinished:
                    break
                if not candidates:
                    if all(t["status"] == "unconfirmed" for t in unfinished):
                        self.event("仍有提交结果待确认，停止自动重发", "warning")
                        break
                    await self._pause(plan.retry_delay)
                    continue
                _, target, course = min(candidates, key=lambda item: item[0])
                target.update(status="submitting", reason="正在提交", attempts=target["attempts"] + 1)
                self.progress("submitting", f"正在提交「{course.name}」，第 {target['attempts']} 次", course.id)
                self.event(f"第 {target['attempts']} 次提交：{course.name}", course_id=course.id)
                target.setdefault('submitted_at',now_iso())
                attempt={'number':target['attempts'],'submitted_at':now_iso()}
                target.setdefault('attempt_records',[]).append(attempt)
                response_start=time.monotonic()
                response = await self.adapter.submit(course)
                attempt.update(response_at=now_iso(),response_ms=round((time.monotonic()-response_start)*1000,1),response_state=response.state)
                target['submission_ms']=attempt['response_ms']
                target.update(status="unconfirmed", reason=response.message)
                self.progress("verifying", f"核验「{course.name}」是否确实选上", course.id)
                verification_start=time.monotonic()
                await self.reconcile()
                attempt['verification_ms']=round((time.monotonic()-verification_start)*1000,1)
                target['verification_ms']=attempt['verification_ms']
                if target["status"] == "succeeded":
                    continue
                if response.state == "full":
                    target.update(status="full", reason=response.message)
                elif response.state == "retryable":
                    if target["attempts"] >= attempts_limit:
                        target.update(status="exhausted", reason="达到重试次数上限")
                    else:
                        target.update(status="cooldown", reason=response.message)
                        cooldowns[course.id] = time.monotonic() + plan.retry_delay
                        self.event("可重试错误，等待后继续；先处理其他课程", "warning", course.id)
                elif response.state == "blocked":
                    target.update(status="blocked", reason=response.message)
                # Unknown or accepted-but-unconfirmed is never blindly resubmitted.
                self.save()
            snapshot=await self.reconcile()
            for target in self.run_state["targets"]:
                if target["status"] in {"pending", "cooldown"}:
                    target.update(status="paused", reason="已停止，尚未完成" if self.stop_event.is_set() else "达到本次执行时限，先核验再继续")
            self.run_state["phase"] = "stopped" if self.stop_event.is_set() else "finished"
            successful = sum(t["status"] == "succeeded" for t in self.run_state["targets"])
            if successful:
                if self.on_success:
                    try:
                        synced=await self.on_success()
                        self.run_state['timetable_updated']=synced.get('state')=='success'
                    except Exception:
                        self.run_state['timetable_updated']=False
                    if not self.run_state['timetable_updated']:
                        self.event('选课结果已确认，课表更新待重试','warning')
                else:
                    self.run_state['timetable_updated']=all(t['id'] in snapshot.timetable_selected for t in self.run_state['targets'] if t['status']=='succeeded')
            self.progress("stopped" if self.stop_event.is_set() else "complete", f"已核验成功 {successful}/{len(self.run_state['targets'])} 门；查看未完成课程的原因")
            self.event("演练已停止" if self.stop_event.is_set() else "演练结束；查看各课程的核验结果")
        except Exception:
            self.run_state["phase"] = "interrupted"
            self.progress("error", "执行中断，保留结果；先核验，再继续")
            for target in self.run_state["targets"]:
                if target["status"] == "submitting":
                    target.update(status="unconfirmed", reason="请求中断，需先核验")
            self.event("执行中断，保留结果；先核验再重新执行", "warning")
        finally:
            if segment_start is not None:
                self.run_state['elapsed_ms']=round(self.run_state.get('elapsed_ms',0)+(time.monotonic()-segment_start)*1000,1)
            self.run_state['segment_started_at']=None
            self.run_state['finished_at']=now_iso()
            self.run_state['summary']=run_summary(self.run_state)
            self.save()
