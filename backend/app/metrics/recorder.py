import time
from typing import Any
from app.schemas.common import Metrics


class Recorder:
    def __init__(self, request_id: str):
        self.request_id = request_id
        self.t_start = time.time()
        self.stages: dict[str, float] = {}
        self.validator_trips = 0
        self.current_stage = None
        self.stage_start = 0.0

    def start_stage(self, name: str) -> None:
        now = time.time()
        if self.current_stage:
            self.stages[self.current_stage] = round((now - self.stage_start) * 1000, 2)
        self.current_stage = name
        self.stage_start = now

    def end_stage(self) -> None:
        now = time.time()
        if self.current_stage:
            self.stages[self.current_stage] = round((now - self.stage_start) * 1000, 2)
            self.current_stage = None

    def record_trip(self) -> None:
        self.validator_trips += 1

    def finish(self) -> Metrics:
        self.end_stage()
        total_ms = round((time.time() - self.t_start) * 1000, 2)
        return Metrics(
            request_id=self.request_id,
            total_ms=total_ms,
            stages=self.stages,
            tokens={"embedder_input": None, "source": "unavailable"},
            validator_trips=self.validator_trips,
        )
