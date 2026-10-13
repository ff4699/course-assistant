from datetime import datetime, timezone

def now_iso():
    return datetime.now(timezone.utc).isoformat()

class Activity:
    """Actual milestones; never show fabricated percentage completion."""
    def __init__(self):
        self.data = {"state": "idle", "operation": "", "stage": "idle", "message": "等待操作",
                     "started_at": None, "updated_at": None, "steps": []}

    def begin(self, operation):
        self.data = {"state": "running", "operation": operation, "stage": "starting",
                     "message": "开始处理", "started_at": now_iso(), "updated_at": now_iso(), "steps": []}

    def step(self, stage, message):
        for item in self.data["steps"]:
            if item["state"] == "running":
                item["state"] = "done"
        self.data.update(stage=stage, message=message, updated_at=now_iso())
        self.data["steps"].append({"stage": stage, "message": message, "state": "running", "time": now_iso()})
        self.data["steps"] = self.data["steps"][-12:]

    def finish(self, state, message):
        self.data.update(state=state, message=message, updated_at=now_iso())
        if self.data["steps"]:
            self.data["steps"][-1]["state"] = "done" if state == "succeeded" else state

    def public(self):
        return {**self.data, "steps": [dict(item) for item in self.data["steps"]]}
