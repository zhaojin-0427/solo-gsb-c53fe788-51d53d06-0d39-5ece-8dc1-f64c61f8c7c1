"""架次级冲突评估与“禁止起飞时差”预计算。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from .geometry import EPS, Segment, build_segments, position_at, segment_pair_conflicts


@dataclass
class Flight:
    """规范化后的架次。"""
    fid: str
    track: List[dict]
    segments: List[Segment]
    weight: int
    earliest: int       # 起飞窗口下界（相对秒）
    latest: int         # 起飞窗口上界（相对秒）
    duration: float     # 航迹持续时间（首航点到末航点）

    @property
    def start_t(self) -> float:
        return self.track[0]["t"]

    @property
    def end_t(self) -> float:
        return self.track[-1]["t"]


def make_flight(raw: dict) -> Flight:
    track = raw["track"]
    segments = build_segments(track)
    return Flight(
        fid=str(raw["id"]),
        track=track,
        segments=segments,
        weight=int(raw["weight"]),
        earliest=int(raw["earliest"]),
        latest=int(raw["latest"]),
        duration=float(track[-1]["t"] - track[0]["t"]),
    )


@dataclass
class PairInfo:
    """一对架次的预计算结果。"""
    forbidden: List[int] = field(default_factory=list)          # 导致冲突的整数时差 d = s_j - s_i
    forbidden_ranges: List[Tuple[int, int]] = field(default_factory=list)
    earliest_intervals: List[Tuple[float, float]] = field(default_factory=list)  # 绝对时间冲突区间


def evaluate_pair(
    fi: Flight, fj: Flight, shifts: Tuple[float, float], radius: float, height: float
) -> List[dict]:
    """给定两架次的实际起飞时刻，返回冲突报告列表（含区间、最小距离与冲突位置）。"""
    si, sj = shifts
    raw: List[Tuple[float, float, float, float, float]] = []
    for ai in fi.segments:
        for bj in fj.segments:
            raw.extend(segment_pair_conflicts(ai, bj, si, sj, radius, height))
    if not raw:
        return []
    # 合并相邻/重叠的绝对时间区间
    raw.sort(key=lambda x: x[0])
    reports: List[dict] = []
    cur = None
    for T_lo, T_hi, mh, mv, uu in raw:
        if cur is None:
            cur = [T_lo, T_hi, mh, mv]
        elif T_lo <= cur[1] + 1e-6:
            cur[1] = max(cur[1], T_hi)
            cur[2] = min(cur[2], mh)
            cur[3] = min(cur[3], mv)
        else:
            reports.append(cur)
            cur = [T_lo, T_hi, mh, mv]
    reports.append(cur)

    out = []
    for T_lo, T_hi, mh, mv in reports:
        Tm = (T_lo + T_hi) / 2.0
        pi = position_at(fi.segments, si, Tm)
        pj = position_at(fj.segments, sj, Tm)
        out.append({
            "pair": [fi.fid, fj.fid],
            "timeStart": round(T_lo, 3),
            "timeEnd": round(T_hi, 3),
            "minHorizontal": round(mh, 3),
            "minVertical": round(mv, 3),
            "positions": {
                fi.fid: [round(pi[0], 3), round(pi[1], 3), round(pi[2], 3)] if pi else None,
                fj.fid: [round(pj[0], 3), round(pj[1], 3), round(pj[2], 3)] if pj else None,
            },
        })
    return out


def evaluate_all(flights: Sequence[Flight], shifts: Dict[str, int], radius: float, height: float) -> List[dict]:
    """评估整套平移方案下的全部冲突。"""
    conflicts: List[dict] = []
    for idx in range(len(flights)):
        for jdx in range(idx + 1, len(flights)):
            fi, fj = flights[idx], flights[jdx]
            conflicts.extend(
                evaluate_pair(fi, fj, (float(shifts[fi.fid]), float(shifts[fj.fid])), radius, height)
            )
    conflicts.sort(key=lambda c: (c["timeStart"], c["pair"][0], c["pair"][1]))
    return conflicts


def precompute_pair(fi: Flight, fj: Flight, radius: float, height: float) -> PairInfo:
    """扫描全部可能的整数起飞时差，记录禁止值。

    d = s_j - s_i。只有两框架次飞行时间（含端点）重叠时才可能冲突，
    因此 d 的范围可收紧到 [t_start_i - t_end_j, t_end_i - t_start_j]。
    """
    info = PairInfo()
    d_lo = math_ceil(fi.start_t - fj.end_t - EPS)
    d_hi = math_floor(fi.end_t - fj.start_t + EPS)
    forbidden: List[int] = []
    for d in range(d_lo, d_hi + 1):
        # s_j = s_i + d；扫描时令 s_i = 0（冲突只依赖时差）
        hit = False
        for ai in fi.segments:
            for bj in fj.segments:
                if segment_pair_conflicts(ai, bj, 0.0, float(d), radius, height):
                    hit = True
                    break
            if hit:
                break
        if hit:
            forbidden.append(d)
    info.forbidden = forbidden
    info.forbidden_ranges = _compress(forbidden)
    # 最早起飞（平移 = 各自窗口下界）时的冲突区间，供对照展示
    info.earliest_intervals = [
        (c["timeStart"], c["timeEnd"])
        for c in evaluate_pair(fi, fj, (float(fi.earliest), float(fj.earliest)), radius, height)
    ]
    return info


def math_ceil(x: float) -> int:
    import math
    return int(math.ceil(x - 1e-9))


def math_floor(x: float) -> int:
    import math
    return int(math.floor(x + 1e-9))


def _compress(values: List[int]) -> List[Tuple[int, int]]:
    if not values:
        return []
    ranges: List[Tuple[int, int]] = []
    lo = prev = values[0]
    for v in values[1:]:
        if v == prev + 1:
            prev = v
        else:
            ranges.append((lo, prev))
            lo = prev = v
    ranges.append((lo, prev))
    return ranges


class PairCache:
    """按（半径, 高度）缓存全部架次对的预计算结果。"""

    def __init__(self, flights: Sequence[Flight]):
        self.flights = list(flights)
        self.index = {f.fid: i for i, f in enumerate(self.flights)}
        self._cache: Dict[tuple, Dict[str, PairInfo]] = {}

    def get(self, radius: float, height: float) -> Dict[str, PairInfo]:
        key = (round(float(radius), 6), round(float(height), 6))
        if key not in self._cache:
            table: Dict[str, PairInfo] = {}
            for i in range(len(self.flights)):
                for j in range(i + 1, len(self.flights)):
                    fi, fj = self.flights[i], self.flights[j]
                    table[f"{fi.fid}\x00{fj.fid}"] = precompute_pair(fi, fj, key[0], key[1])
            self._cache = {key: table}  # 场景内通常只评估一组参数，单槽即可
        return self._cache[key]
