"""FastAPI 主程序：场景管理、冲突评估、求解、方案保存与回看。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .conflicts import PairCache, evaluate_all, make_flight
from .samples import get_samples
from .solver import solve as cp_solve
from .validation import validate_scenario

app = FastAPI(title="无人机航迹冲突消解台", version="1.0.0")

# 场景解析缓存：scenario_id -> (payload, flights, PairCache)
_scene_cache: Dict[int, tuple] = {}


def load_scene(scenario_id: int):
    row = db.get_scenario(scenario_id)
    if row is None:
        raise HTTPException(404, f"场景 {scenario_id} 不存在")
    payload = row["payload"]
    if scenario_id not in _scene_cache or _scene_cache[scenario_id][0] != payload:
        flights = [make_flight(f) for f in payload["flights"]]
        _scene_cache[scenario_id] = (payload, flights, PairCache(flights))
    return _scene_cache[scenario_id]


# ---------------- 请求模型 ----------------

class ScenarioIn(BaseModel):
    payload: Dict[str, Any]


class EvaluateIn(BaseModel):
    # 可选直接传入 payload（不落库，用于导入后即时预览）
    payload: Optional[Dict[str, Any]] = None
    scenarioId: Optional[int] = None
    shifts: Optional[Dict[str, int]] = None  # 缺省=最早起飞时刻
    safetyRadius: Optional[float] = None
    safetyHeight: Optional[float] = None


class SolveIn(BaseModel):
    scenarioId: int
    locks: Dict[str, int] = Field(default_factory=dict)
    safetyRadius: Optional[float] = None
    safetyHeight: Optional[float] = None
    timeLimit: float = 30.0


class PlanIn(BaseModel):
    scenarioId: int
    name: str
    result: Dict[str, Any]


def _use_payload(payload: Dict[str, Any], radius: Optional[float], height: Optional[float]):
    norm, errors = validate_scenario(payload)
    if errors:
        raise HTTPException(422, detail=errors)
    if radius is not None:
        norm["safetyRadius"] = float(radius)
    if height is not None:
        norm["safetyHeight"] = float(height)
    flights = [make_flight(f) for f in norm["flights"]]
    return norm, flights, PairCache(flights)


def _resolve(payload=None, scenario_id=None, radius=None, height=None):
    if scenario_id is not None:
        _, flights, cache = load_scene(scenario_id)
        stored = db.get_scenario(scenario_id)["payload"]
        r = float(radius if radius is not None else stored["safetyRadius"])
        h = float(height if height is not None else stored["safetyHeight"])
        return stored, flights, cache, r, h
    if payload is None:
        raise HTTPException(400, "必须提供 scenarioId 或 payload")
    norm, flights, cache = _use_payload(payload, radius, height)
    return norm, flights, cache, norm["safetyRadius"], norm["safetyHeight"]


def _flight_bounds(flights) -> dict:
    ids = [f.fid for f in flights]
    return {
        f.fid: {"earliest": f.earliest, "latest": f.latest, "weight": f.weight,
                "duration": f.duration, "startT": f.start_t, "endT": f.end_t}
        for f in flights
    }


# ---------------- API ----------------

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/samples")
def samples():
    return {"samples": get_samples()}


@app.get("/api/scenarios")
def scenarios_list():
    return {"scenarios": db.list_scenarios()}


@app.post("/api/scenarios")
def scenarios_create(body: ScenarioIn):
    norm, errors = validate_scenario(body.payload)
    if errors:
        raise HTTPException(422, detail=errors)
    sid = db.insert_scenario(norm)
    _scene_cache.pop(sid, None)
    return {"id": sid, "payload": norm}


@app.get("/api/scenarios/{scenario_id}")
def scenarios_get(scenario_id: int):
    row = db.get_scenario(scenario_id)
    if row is None:
        raise HTTPException(404, "场景不存在")
    return row


@app.delete("/api/scenarios/{scenario_id}")
def scenarios_delete(scenario_id: int):
    if not db.delete_scenario(scenario_id):
        raise HTTPException(404, "场景不存在")
    _scene_cache.pop(scenario_id, None)
    return {"ok": True}


@app.post("/api/evaluate")
def evaluate(body: EvaluateIn):
    """给定（或默认最早）起飞时刻评估冲突。"""
    norm, flights, cache, radius, height = _resolve(
        body.payload, body.scenarioId, body.safetyRadius, body.safetyHeight
    )
    if body.shifts:
        shifts = {}
        for f in flights:
            v = body.shifts.get(f.fid, f.earliest)
            if not (f.earliest <= v <= f.latest):
                raise HTTPException(400, f"{f.fid} 的起飞时刻 {v} 超出窗口 [{f.earliest}, {f.latest}]")
            shifts[f.fid] = int(v)
    else:
        shifts = {f.fid: f.earliest for f in flights}
    conflicts = evaluate_all(flights, shifts, radius, height)
    return {
        "shifts": shifts,
        "conflicts": conflicts,
        "conflictCount": len(conflicts),
        "conflictPairs": len({tuple(c["pair"]) for c in conflicts}),
        "flightBounds": _flight_bounds(flights),
        "safetyRadius": radius,
        "safetyHeight": height,
    }


@app.post("/api/solve")
def solve_endpoint(body: SolveIn):
    stored = db.get_scenario(body.scenarioId)
    if stored is None:
        raise HTTPException(404, "场景不存在")
    norm, flights, cache, radius, height = _resolve(
        None, body.scenarioId, body.safetyRadius, body.safetyHeight
    )
    # 校验锁定
    for fid, v in body.locks.items():
        f = next((x for x in flights if x.fid == fid), None)
        if f is None:
            raise HTTPException(400, f"锁定了不存在的架次 {fid}")
        if not (f.earliest <= v <= f.latest):
            raise HTTPException(400, f"{fid} 锁定值 {v} 超出窗口 [{f.earliest}, {f.latest}]")

    result = cp_solve(flights, cache, radius, height, locks=body.locks, time_limit=body.timeLimit)
    out: Dict[str, Any] = {"result": result}
    if result["feasible"]:
        out["earliestConflicts"] = evaluate_all(
            flights, {f.fid: f.earliest for f in flights}, radius, height
        )
        out["solvedConflicts"] = evaluate_all(flights, result["starts"], radius, height)
    else:
        out["earliestConflicts"] = evaluate_all(
            flights, {f.fid: f.earliest for f in flights}, radius, height
        )
        out["solvedConflicts"] = []
    out["flightBounds"] = _flight_bounds(flights)
    out["safetyRadius"] = radius
    out["safetyHeight"] = height
    return out


@app.get("/api/plans")
def plans_list(scenarioId: Optional[int] = None):
    return {"plans": db.list_plans(scenarioId)}


@app.post("/api/scenarios/{scenario_id}/plans")
def plans_create(scenario_id: int, body: PlanIn):
    row = db.get_scenario(scenario_id)
    if row is None:
        raise HTTPException(404, "场景不存在")
    if not body.result.get("feasible"):
        raise HTTPException(400, "只能保存有可行解的方案")
    if body.scenarioId != scenario_id:
        raise HTTPException(400, "路径与请求体中的 scenarioId 不一致")
    pid = db.insert_plan(scenario_id, body.name, body.result, row["payload"])
    return {"id": pid}


@app.get("/api/plans/{plan_id}")
def plans_get(plan_id: int):
    plan = db.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    return plan


@app.delete("/api/plans/{plan_id}")
def plans_delete(plan_id: int):
    if not db.delete_plan(plan_id):
        raise HTTPException(404, "方案不存在")
    return {"ok": True}


# ---------------- 静态前端 ----------------

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))
