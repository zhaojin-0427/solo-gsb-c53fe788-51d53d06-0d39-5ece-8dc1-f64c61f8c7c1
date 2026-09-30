"""SQLite 存储层：场景与已保存方案（含输入快照）。"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("DRONE_DB_PATH", "/data/drone.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    starts TEXT NOT NULL,          -- 求解出的起飞时刻 {flightId: seconds}
    locks TEXT NOT NULL,           -- 求解时使用的锁定
    weighted_delay INTEGER NOT NULL,
    result TEXT NOT NULL,          -- 完整求解结果
    scenario_snapshot TEXT NOT NULL, -- 输入快照（保存时的场景 JSON）
    created_at REAL NOT NULL,
    FOREIGN KEY (scenario_id) REFERENCES scenarios(id) ON DELETE CASCADE
);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), 0o755, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        _conn.executescript(_SCHEMA)
    return _conn


def insert_scenario(payload: Dict[str, Any]) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO scenarios (name, payload, created_at) VALUES (?, ?, ?)",
        (payload.get("name", "未命名场景"), json.dumps(payload, ensure_ascii=False), time.time()),
    )
    conn.commit()
    return cur.lastrowid


def list_scenarios() -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, created_at FROM scenarios ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_scenario(scenario_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name, payload, created_at FROM scenarios WHERE id = ?", (scenario_id,)
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"],
            "payload": json.loads(row["payload"]), "createdAt": row["created_at"]}


def delete_scenario(scenario_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
    conn.commit()
    return cur.rowcount > 0


def insert_plan(scenario_id: int, name: str, result: Dict[str, Any], snapshot: Dict[str, Any]) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO plans
           (scenario_id, name, starts, locks, weighted_delay, result, scenario_snapshot, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            scenario_id, name,
            json.dumps(result.get("starts", {}), ensure_ascii=False),
            json.dumps(result.get("locks", {}), ensure_ascii=False),
            int(result.get("weightedDelay", 0)),
            json.dumps(result, ensure_ascii=False),
            json.dumps(snapshot, ensure_ascii=False),
            time.time(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_plans(scenario_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    if scenario_id is None:
        rows = conn.execute(
            "SELECT id, scenario_id, name, starts, locks, weighted_delay, created_at "
            "FROM plans ORDER BY id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, scenario_id, name, starts, locks, weighted_delay, created_at "
            "FROM plans WHERE scenario_id = ? ORDER BY id DESC",
            (scenario_id,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "scenarioId": r["scenario_id"],
            "name": r["name"],
            "starts": json.loads(r["starts"]),
            "locks": json.loads(r["locks"]),
            "weightedDelay": r["weighted_delay"],
            "createdAt": r["created_at"],
        })
    return out


def get_plan(plan_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "scenarioId": row["scenario_id"],
        "name": row["name"],
        "starts": json.loads(row["starts"]),
        "locks": json.loads(row["locks"]),
        "weightedDelay": row["weighted_delay"],
        "result": json.loads(row["result"]),
        "scenarioSnapshot": json.loads(row["scenario_snapshot"]),
        "createdAt": row["created_at"],
    }


def delete_plan(plan_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    conn.commit()
    return cur.rowcount > 0
