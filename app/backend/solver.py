"""无冲突整数起飞时刻求解（OR-Tools CP-SAT）与无解 MUS 枚举。

目标（分层）：
  1. 最小化 sum(weight_j * (s_j - earliest_j))，即按最早起飞时刻计算的加权总延误；
  2. 在所有最优解中，取按架次 ID 升序排列的起飞时刻序列字典序最小者。

无解时枚举“包含关系极小的不可行架次集合”（Minimal Unsatisfiable Subsets, MUS），
并列出每个 MUS 中各架次对的互斥时差（导致冲突的禁止 s_j - s_i 区间）。
"""
from __future__ import annotations

import time
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from ortools.sat.python import cp_model

from .conflicts import Flight, PairCache, PairInfo


def _domain_complement(forbidden: Sequence[int], lo: int, hi: int) -> List[Tuple[int, int]]:
    """[lo, hi] 内去掉 forbidden 离散值后的区间表示。"""
    allowed: List[Tuple[int, int]] = []
    cur = lo
    for d in forbidden:
        if d < lo:
            continue
        if d > hi:
            break
        if d > cur:
            allowed.append((cur, d - 1))
        cur = d + 1
    if cur <= hi:
        allowed.append((cur, hi))
    return allowed


def _build_model(
    flights: Sequence[Flight],
    pair_table: Dict[str, PairInfo],
    locks: Dict[str, int],
    subset: Optional[Set[int]] = None,
):
    """构造 CP 模型。subset 非空时只对该子集做可行性检查（MUS 用）。"""
    model = cp_model.CpModel()
    n = len(flights)
    use = subset if subset is not None else set(range(n))
    starts: List[Optional[cp_model.IntVar]] = [None] * n
    impossible_lock = False

    for i in use:
        f = flights[i]
        if f.fid in locks:
            v = int(locks[f.fid])
            if not (f.earliest <= v <= f.latest):
                impossible_lock = True
            starts[i] = model.NewIntVar(v, v, f"s_{i}")
        else:
            starts[i] = model.NewIntVar(f.earliest, f.latest, f"s_{i}")

    trivial_pairs: List[Tuple[int, int]] = []

    def bound(idx):
        f = flights[idx]
        v = locks.get(f.fid)
        return (int(v), int(v)) if v is not None else (f.earliest, f.latest)

    def add_impossible():
        z = model.NewBoolVar("never_true")
        model.Add(z == 0)
        model.Add(z == 1)

    for i in sorted(use):
        for j in sorted(x for x in use if x > i):
            fi, fj = flights[i], flights[j]
            info = pair_table[f"{fi.fid}\x00{fj.fid}"]
            ilo, ihi = bound(i)
            jlo, jhi = bound(j)
            d_lo = jlo - ihi  # s_j - s_i 的最小可达值
            d_hi = jhi - ilo
            if d_lo > d_hi:
                trivial_pairs.append((i, j))
                add_impossible()
                continue
            allowed = _domain_complement(info.forbidden, d_lo, d_hi)
            if not allowed:
                trivial_pairs.append((i, j))
                add_impossible()
                continue
            dvar = model.NewIntVarFromDomain(
                cp_model.Domain.FromIntervals([list(r) for r in allowed]), f"d_{i}_{j}"
            )
            model.Add(starts[j] - starts[i] == dvar)

    if impossible_lock:
        add_impossible()
    return model, starts, trivial_pairs


def _solve_status(model, time_limit: float, workers: int = 2):
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, time_limit)
    solver.parameters.num_search_workers = workers
    status = solver.Solve(model)
    return solver, status


def solve(
    flights: Sequence[Flight],
    cache: PairCache,
    radius: float,
    height: float,
    locks: Optional[Dict[str, int]] = None,
    time_limit: float = 30.0,
    mus_time_limit: float = 3.0,
    max_mus: int = 20,
) -> dict:
    """主求解入口。"""
    locks = {k: int(v) for k, v in (locks or {}).items()}
    pair_table = cache.get(radius, height)
    n = len(flights)

    # ---- 阶段 1：最小化加权总延误 ----
    model, starts, trivial = _build_model(flights, pair_table, locks)
    model.Minimize(sum(
        f.weight * (starts[i] - f.earliest)
        for i, f in enumerate(flights) if f.fid not in locks
    ))
    solver, status = _solve_status(model, time_limit, workers=4)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if status == cp_model.INFEASIBLE:
            mus, truncated = enumerate_mus(flights, pair_table, locks, mus_time_limit, max_mus)
            return {
                "feasible": False,
                "reason": "infeasible",
                "mus": mus,
                "musTruncated": truncated,
                "status": status_name,
                "musForbidden": _mus_forbidden(flights, pair_table, mus, locks),
            }
        return {"feasible": False, "reason": "unknown", "status": status_name,
                "mus": [], "musTruncated": False, "musForbidden": []}

    values = [solver.Value(starts[i]) for i in range(n)]
    optimal = status == cp_model.OPTIMAL
    delay_bound = int(round(solver.ObjectiveValue()))
    t0 = time.time()

    # ---- 阶段 2：按架次 ID 升序逐分量字典序最小化 ----
    lex_order = sorted(range(n), key=lambda i: flights[i].fid)
    lex_applied = 0
    fixed: Dict[int, int] = {i: int(locks[flights[i].fid])
                             for i in range(n) if flights[i].fid in locks}

    for i in lex_order:
        if i in fixed:
            continue
        if time.time() - t0 > time_limit and not optimal:
            break  # 超时风险下保留当前可行解
        m2, s2, _trivial2 = _build_model(flights, pair_table, locks)
        m2.Add(sum(
            flights[k].weight * (s2[k] - flights[k].earliest)
            for k in range(n) if flights[k].fid not in locks
        ) <= delay_bound)
        for k, v in fixed.items():
            m2.Add(s2[k] == v)
        m2.Add(s2[i] <= values[i])
        m2.Minimize(s2[i])
        sv, st = _solve_status(m2, min(3.0, max(0.5, time_limit - (time.time() - t0))), workers=2)
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            values = [sv.Value(s2[k]) for k in range(n)]
            fixed[i] = values[i]
            lex_applied += 1
        else:
            # 无法更小：固定为当前值后继续下一个分量
            fixed[i] = values[i]

    starts_out = {flights[i].fid: values[i] for i in range(n)}
    return {
        "feasible": True,
        "status": status_name,
        "optimal": optimal,
        "starts": starts_out,
        "weightedDelay": sum(flights[i].weight * (values[i] - flights[i].earliest) for i in range(n)),
        "delayBound": delay_bound,
        "locks": dict(locks),
        "lexComponentsApplied": lex_applied,
    }


# ---------------- 无解诊断：MUS 枚举 ----------------

def _feasible_subset(
    flights: Sequence[Flight],
    pair_table: Dict[str, PairInfo],
    locks: Dict[str, int],
    subset: FrozenSet[int],
    time_limit: float,
) -> bool:
    """子集可满足性。UNKNOWN 状态保守视为可行，避免错误产生 MUS。"""
    if not subset:
        return True
    model, _, trivial = _build_model(flights, pair_table, locks, set(subset))
    if trivial:
        return False
    _, status = _solve_status(model, time_limit, workers=1)
    return status != cp_model.INFEASIBLE


def enumerate_mus(
    flights: Sequence[Flight],
    pair_table: Dict[str, PairInfo],
    locks: Dict[str, int],
    time_limit: float,
    max_mus: int,
) -> Tuple[List[List[str]], bool]:
    """枚举全部包含关系极小的不可行子集（MUS）。

    使用 Eiter–Gottlob 超图对偶（hitting-set）递归：
      hs(H, S)：H 为已选“证据”路径，S 为待选元素。
      * 若存在已找到的 MUS 完全包含于 H，则剪枝（H 不可能再产生新的极小集）；
      * 若 H 不可行，对 H 做删除过滤得到 MUS；
      * 否则依次选取 S 中的元素递归（H∪{x}, S\\{x}）。
    这是 Reiter 击中集树的标准形式，枚举结果不重不漏。
    """
    allset = frozenset(range(len(flights)))
    found: List[FrozenSet[int]] = []
    deadline = time.time() + max(8.0, time_limit * 8)
    state = {"truncated": False}

    def sat(C: FrozenSet[int]) -> bool:
        return _feasible_subset(flights, pair_table, locks, C, time_limit)

    def deletion_filter(core: FrozenSet[int]) -> FrozenSet[int]:
        """core 不可行；逐项删除，返回包含关系极小的不可行子集。"""
        kept = set(core)
        for i in sorted(core):
            trial = kept - {i}
            if not sat(frozenset(trial)):
                kept.discard(i)
        return frozenset(kept)

    def hs(path: FrozenSet[int], candidates: FrozenSet[int]):
        if state["truncated"] or time.time() > deadline:
            state["truncated"] = True
            return
        # 剪枝：路径上已经包含某个已找到的 MUS
        if any(m <= path for m in found):
            return
        if not sat(path):
            mus = deletion_filter(path) if path else path
            if mus not in found and not any(m <= mus for m in found):
                found.append(mus)
                if len(found) >= max_mus:
                    state["truncated"] = True
            return
        for x in sorted(candidates):
            if len(found) >= max_mus or time.time() > deadline:
                state["truncated"] = True
                return
            hs(path | {x}, candidates - {x})

    hs(frozenset(), allset)
    # 去除被其他结果包含的集合（算法本身应已极小，此处兜底）
    minimal: List[FrozenSet[int]] = []
    for m in found:
        if not any(other < m for other in found):
            minimal.append(m)
    return [[flights[i].fid for i in sorted(m)] for m in minimal], state["truncated"]


def _mus_forbidden(
    flights: Sequence[Flight],
    pair_table: Dict[str, PairInfo],
    mus: List[List[str]],
    locks: Optional[Dict[str, int]] = None,
) -> List[dict]:
    """为每个 MUS 列出架次对互斥时差（窗口可达范围内的禁止 d 区间）。"""
    locks = locks or {}
    idx = {f.fid: i for i, f in enumerate(flights)}
    out = []
    for group in mus:
        pairs = []
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                fa, fb = group[a], group[b]
                i, j = idx[fa], idx[fb]
                fi, fj = flights[i], flights[j]
                info = pair_table[f"{fi.fid}\x00{fj.fid}"]
                if fa in locks and fb in locks:
                    d_lo = d_hi = int(locks[fb]) - int(locks[fa])
                else:
                    ilo = int(locks[fa]) if fa in locks else fi.earliest
                    ihi = int(locks[fa]) if fa in locks else fi.latest
                    jlo = int(locks[fb]) if fb in locks else fj.earliest
                    jhi = int(locks[fb]) if fb in locks else fj.latest
                    d_lo = jlo - ihi
                    d_hi = jhi - ilo
                if d_lo <= d_hi:
                    ranges = [
                        [max(lo, d_lo), min(hi, d_hi)]
                        for lo, hi in info.forbidden_ranges
                        if hi >= d_lo and lo <= d_hi
                    ]
                else:
                    ranges = [[d_lo, d_hi]]
                pairs.append({
                    "pair": [fa, fb],
                    "difference": f"s({fb}) − s({fa})",
                    "forbiddenRanges": ranges,
                    "reachableRange": [d_lo, d_hi],
                })
        out.append({"members": group, "pairs": pairs})
    return out
