"""场景 JSON 校验与规范化。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

ID_RE = re.compile(r"^[\w\-.一-鿿]+$")


def validate_scenario(payload: Any) -> Tuple[Dict[str, Any] | None, List[str]]:
    """返回（规范化场景，错误列表）。错误非空时场景为 None。"""
    errors: List[str] = []
    if not isinstance(payload, dict):
        return None, ["根对象必须是 JSON 对象"]

    name = payload.get("name", "未命名场景")
    if not isinstance(name, str):
        errors.append("name 必须是字符串")

    radius = payload.get("safetyRadius")
    height = payload.get("safetyHeight")
    if not isinstance(radius, (int, float)) or radius <= 0:
        errors.append("safetyRadius 必须为正数（米）")
    if not isinstance(height, (int, float)) or height <= 0:
        errors.append("safetyHeight 必须为正数（米）")

    flights_raw = payload.get("flights")
    if not isinstance(flights_raw, list) or len(flights_raw) < 1:
        errors.append("flights 必须为非空数组")
        return None, errors

    norm_flights = []
    seen_ids = set()
    for k, fl in enumerate(flights_raw):
        where = f"flights[{k}]"
        if not isinstance(fl, dict):
            errors.append(f"{where} 必须是对象")
            continue
        fid = fl.get("id")
        if not isinstance(fid, str) or not ID_RE.match(fid):
            errors.append(f"{where}.id 必须是非空字符串（字母/数字/_-./中文）")
        elif fid in seen_ids:
            errors.append(f"{where}.id 重复：{fid}")
        seen_ids.add(fid)

        weight = fl.get("weight")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
            errors.append(f"{where}.weight 必须为正整数")

        earliest = fl.get("earliest")
        latest = fl.get("latest")
        if not isinstance(earliest, int) or isinstance(earliest, bool):
            errors.append(f"{where}.earliest 必须为整数秒")
        if not isinstance(latest, int) or isinstance(latest, bool):
            errors.append(f"{where}.latest 必须为整数秒")
        if isinstance(earliest, int) and isinstance(latest, int) and latest < earliest:
            errors.append(f"{where}.latest 不得早于 earliest")

        track = fl.get("track")
        if not isinstance(track, list) or len(track) < 2:
            errors.append(f"{where}.track 必须包含至少 2 个航点")
            continue
        norm_track = []
        last_t = None
        for p, wp in enumerate(track):
            wwhere = f"{where}.track[{p}]"
            if not isinstance(wp, dict):
                errors.append(f"{wwhere} 必须是对象")
                continue
            coords = {}
            ok = True
            for axis in ("x", "y", "z"):
                v = wp.get(axis)
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    errors.append(f"{wwhere}.{axis} 必须是数字（米）")
                    ok = False
                coords[axis] = float(v) if ok else 0.0
            tv = wp.get("t")
            if not isinstance(tv, (int, float)) or isinstance(tv, bool):
                errors.append(f"{wwhere}.t 必须是数字（相对起飞秒数）")
                ok = False
            elif last_t is not None and tv < last_t:
                errors.append(f"{wwhere}.t 必须单调不减")
                ok = False
            if ok:
                last_t = float(tv)
                norm_track.append({"x": coords["x"], "y": coords["y"], "z": coords["z"], "t": float(tv)})
        if norm_track and norm_track[0]["t"] != 0.0:
            errors.append(f"{where}.track[0].t 必须为 0（相对起飞秒数）")

        if len(norm_track) >= 2 and not errors:
            norm_flights.append({
                "id": fid,
                "weight": int(weight),
                "earliest": int(earliest),
                "latest": int(latest),
                "track": norm_track,
            })

    if errors or len(norm_flights) != len(flights_raw):
        return None, errors or ["存在无法解析的架次"]

    norm = {
        "name": name,
        "safetyRadius": float(radius),
        "safetyHeight": float(height),
        "flights": norm_flights,
    }
    return norm, []
