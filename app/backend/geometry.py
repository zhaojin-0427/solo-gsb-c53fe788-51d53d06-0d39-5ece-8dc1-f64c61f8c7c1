"""分段直线航迹的连续冲突几何计算。

航迹以“相对起飞秒数”描述：每个航点 (x, y, z, t)，单位为米与秒。
将架次 j 整体平移 s_j（整数秒起飞时刻）后，架次在绝对时刻 T 的位置等于
其原始航迹在相对时刻 tau = T - s_j 的位置。

本模块在给定平移量（连续值）下，对两框架次做逐航段扫描：
  * 时间重叠区间上线性插值位置；
  * 垂直距离 z 之差为线性函数；
  * 水平距离平方为凸二次函数；
  * 严格判定：水平距离 < 安全半径 且 垂直距离 < 安全高度 才为冲突；
  * 航段端点同样检查（重叠区间闭区间上的极小点 + 端点验证）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# 数值容差：距离判定与“相切不算冲突”的严格比较容差（米）
EPS = 1e-7


@dataclass
class Segment:
    ax: float
    ay: float
    az: float
    bx: float
    by: float
    bz: float
    t0: float
    t1: float

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    def pos(self, tau: float) -> Tuple[float, float, float]:
        r = 0.0 if self.duration == 0.0 else (tau - self.t0) / self.duration
        return (
            self.ax + (self.bx - self.ax) * r,
            self.ay + (self.by - self.ay) * r,
            self.az + (self.bz - self.az) * r,
        )


def build_segments(track: Sequence[dict]) -> List[Segment]:
    """把航点序列展开为分段直线（含可能存在的零时长停顿点）。"""
    segs: List[Segment] = []
    for p, q in zip(track, track[1:]):
        segs.append(
            Segment(
                float(p["x"]), float(p["y"]), float(p["z"]),
                float(q["x"]), float(q["y"]), float(q["z"]),
                float(p["t"]), float(q["t"]),
            )
        )
    return segs


def _linear_subinterval(b0: float, k: float, bound: float) -> Optional[Tuple[float, float]]:
    """在 u in [0,1] 上求 |b0 + k*u| < bound 的开不等式区间。

    返回按严格 < 判定、以 EPS 容差收紧后的 [u_lo, u_hi]，无可行区间返回 None。
    """
    if abs(k) <= EPS:
        if abs(b0) + EPS < bound:
            return (0.0, 1.0)
        return None
    u_a = (-bound - b0) / k
    u_b = (bound - b0) / k
    lo, hi = (u_a, u_b) if u_a < u_b else (u_b, u_a)
    lo = max(0.0, lo)
    hi = min(1.0, hi)
    # 严格不等式：在数值上内缩 EPS，切点（零长度区间）不算冲突
    if hi - lo <= EPS:
        return None
    return (lo, hi)


def _quadratic_below(a: float, b: float, c: float, bound: float) -> Optional[Tuple[float, float]]:
    """在 u in [0,1] 上求 a*u^2 + b*u + c < bound（a >= 0，凸二次）。

    返回以 EPS 收紧后的区间，无可行区间返回 None。这里 a 为水平距离平方，
    bound 为安全半径平方。
    """
    # 与常数比较：先看极小点
    if a <= EPS:
        # 退化为线性（航段方向水平分量相同）
        if abs(b) <= EPS:
            return (0.0, 1.0) if c + EPS < bound else None
        u0 = (bound - c) / b  # b*u < bound - c
        if b > 0:
            lo, hi = 0.0, min(1.0, u0)
        else:
            lo, hi = max(0.0, u0), 1.0
        return (lo, hi) if hi - lo > EPS else None

    vert = -b / (2.0 * a)
    if 0.0 <= vert <= 1.0:
        vmin = a * vert * vert + b * vert + c
        if vmin + EPS >= bound:
            return None
        disc = b * b - 4.0 * a * (c - bound)
        root = math.sqrt(max(0.0, disc))
        lo = max(0.0, (-b - root) / (2.0 * a))
        hi = min(1.0, (-b + root) / (2.0 * a))
    elif vert < 0.0:
        if a * 0.0 + b * 0.0 + c + EPS >= bound:
            return None
        disc = b * b - 4.0 * a * (c - bound)
        root = math.sqrt(max(0.0, disc))
        lo = 0.0
        hi = min(1.0, (-b + root) / (2.0 * a))
    else:
        val1 = a + b + c
        if val1 + EPS >= bound:
            return None
        disc = b * b - 4.0 * a * (c - bound)
        root = math.sqrt(max(0.0, disc))
        lo = max(0.0, (-b - root) / (2.0 * a))
        hi = 1.0
    return (lo, hi) if hi - lo > EPS else None


def _intersect_intervals(a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    if a is None or b is None:
        return None
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return (lo, hi) if hi - lo > EPS else None


def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged: List[List[float]] = [[intervals[0][0], intervals[0][1]]]
    for lo, hi in intervals[1:]:
        if lo <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def segment_pair_conflicts(
    seg_a: Segment,
    seg_b: Segment,
    shift_a: float,
    shift_b: float,
    radius: float,
    height: float,
) -> List[Tuple[float, float, float, float, float]]:
    """计算一对航段在各自平移后的冲突绝对时间区间。

    航段 A 占用相对时间 [t0a, t1a]，航段 B 同理。重叠时（端点也计入），
    参数化公共重叠时间 T = T0 + u*L，u in [0,1]。

    返回 (T_lo, T_hi, min_horizontal_distance, min_vertical_distance, closest_u)
    列表；T_lo == T_hi 表示仅在单个时刻（端点/相切前）发生的冲突。
    """
    T0 = max(seg_a.t0 + shift_a, seg_b.t0 + shift_b)
    T1 = min(seg_a.t1 + shift_a, seg_b.t1 + shift_b)
    if T1 + EPS < T0:
        return []
    L = T1 - T0
    pa0 = seg_a.pos(T0 - shift_a)
    qa0 = seg_b.pos(T0 - shift_b)
    # u=0 时的位移差，以及随 u（参数化绝对时间）的变化率
    if L <= EPS:
        vx = vy = vz = 0.0
    else:
        va = (
            (seg_a.bx - seg_a.ax) / seg_a.duration if seg_a.duration > EPS else 0.0,
            (seg_a.by - seg_a.ay) / seg_a.duration if seg_a.duration > EPS else 0.0,
            (seg_a.bz - seg_a.az) / seg_a.duration if seg_a.duration > EPS else 0.0,
        )
        vb = (
            (seg_b.bx - seg_b.ax) / seg_b.duration if seg_b.duration > EPS else 0.0,
            (seg_b.by - seg_b.ay) / seg_b.duration if seg_b.duration > EPS else 0.0,
            (seg_b.bz - seg_b.az) / seg_b.duration if seg_b.duration > EPS else 0.0,
        )
        vx = (va[0] - vb[0]) * L
        vy = (va[1] - vb[1]) * L
        vz = (va[2] - vb[2]) * L

    dx0 = pa0[0] - qa0[0]
    dy0 = pa0[1] - qa0[1]
    dz0 = pa0[2] - qa0[2]

    # 垂直：|dz0 + vz*u| < height
    z_int = _linear_subinterval(dz0, vz, height) if L > EPS else (
        (0.0, 0.0) if abs(dz0) + EPS < height else None)
    # 水平：|(dx0+vx*u, dy0+vy*u)| < radius，凸二次 < radius^2
    a = vx * vx + vy * vy
    b = 2.0 * (dx0 * vx + dy0 * vy)
    c = dx0 * dx0 + dy0 * dy0
    h_int = _quadratic_below(a, b, c, radius * radius)
    common = _intersect_intervals(z_int, h_int)

    hits: List[Tuple[float, float, float, float, float]] = []
    if common is not None:
        u_lo, u_hi = common
        hits.append((T0 + u_lo * L, T0 + u_hi * L, *_dist_stats(dx0, dy0, dz0, vx, vy, vz, u_lo, u_hi)))
    else:
        # 区间不等式无解时，仍须检查重叠区间端点（航段端点）。
        for u in (0.0, 1.0):
            dh = math.hypot(dx0 + vx * u, dy0 + vy * u)
            dv = abs(dz0 + vz * u)
            if dh + EPS < radius and dv + EPS < height:
                T = T0 + u * L
                hits.append((T, T, dh, dv, u))
    return hits


def _dist_stats(dx0, dy0, dz0, vx, vy, vz, u_lo, u_hi) -> Tuple[float, float, float]:
    """返回区间内最小水平距离、最小垂直距离及对应 u（用于报告严重程度）。"""
    # 水平距离平方凸二次：极小点在顶点或端点
    a = vx * vx + vy * vy
    b = 2.0 * (dx0 * vx + dy0 * vy)
    c = dx0 * dx0 + dy0 * dy0
    candidates = [u_lo, u_hi]
    if a > EPS:
        uv = -b / (2.0 * a)
        if u_lo < uv < u_hi:
            candidates.append(uv)
    min_h2 = min(a * u * u + b * u + c for u in candidates)
    # 垂直线性绝对值：零点或端点
    if abs(vz) > EPS:
        uz = -dz0 / vz
        zv = uz if u_lo < uz < u_hi else (u_lo if abs(dz0 + vz * u_lo) <= abs(dz0 + vz * u_hi) else u_hi)
    else:
        zv = u_lo
    min_v = abs(dz0 + vz * zv)
    return math.sqrt(max(0.0, min_h2)), min_v, zv


def position_at(segments: Sequence[Segment], shift: float, time_t: float) -> Optional[Tuple[float, float, float]]:
    """绝对时刻 time_t 时架次位置；不在飞行时间内返回 None。"""
    tau = time_t - shift
    if tau < segments[0].t0 - EPS or tau > segments[-1].t1 + EPS:
        return None
    for seg in segments:
        if seg.t0 - EPS <= tau <= seg.t1 + EPS:
            return seg.pos(min(max(tau, seg.t0), seg.t1))
    return None
