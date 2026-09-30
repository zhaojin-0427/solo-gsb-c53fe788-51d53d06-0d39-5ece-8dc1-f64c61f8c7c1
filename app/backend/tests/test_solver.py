"""核心算法对拍测试（可独立运行：python -m backend.tests.test_solver）。

用全整数时刻暴力枚举验证：
  1) 加权延误最优值与按架次 ID 的字典序 tie-break；
  2) 无解时 MUS 集合的完备性与极小性；
  3) 航段端点冲突与“相切不算冲突”的严格判定。
"""
from __future__ import annotations

import itertools
import random

from ..conflicts import evaluate_all, make_flight, PairCache
from ..solver import _feasible_subset, solve


def _brute(flights, r, h, locks=None):
    locks = locks or {}
    best = best_vec = None
    domains = [[locks[f.fid]] if f.fid in locks else range(f.earliest, f.latest + 1)
               for f in flights]
    for combo in itertools.product(*domains):
        shifts = {f.fid: v for f, v in zip(flights, combo)}
        if evaluate_all(flights, shifts, r, h):
            continue
        delay = sum(f.weight * (v - f.earliest) for f, v in zip(flights, combo))
        vec = tuple(v for _, v in sorted(zip([f.fid for f in flights], combo)))
        if best is None or (delay, vec) < (best, best_vec):
            best, best_vec = delay, vec
    return best, best_vec


def test_random_scenarios():
    random.seed(42)
    n_feas = n_infeas = 0
    for trial in range(60):
        n = random.randint(2, 4)
        flights = []
        for k in range(n):
            x = y = random.uniform(-100, 100)
            z = random.choice([0, 50, 100, 120])
            pts = [{"x": x, "y": y, "z": float(z), "t": 0.0}]
            t = 0.0
            for _ in range(random.randint(1, 3)):
                x += random.uniform(-150, 150)
                y += random.uniform(-150, 150)
                t += random.uniform(8, 25)
                if random.random() < 0.3:
                    z = max(0, z + random.choice([-30, 0, 30]))
                pts.append({"x": x, "y": y, "z": float(z), "t": round(t, 3)})
            lo = random.choice([0, 0, 5])
            flights.append(make_flight({"id": f"F{k}", "weight": random.randint(1, 4),
                                        "earliest": lo, "latest": lo + random.randint(0, 8),
                                        "track": pts}))
        r = random.choice([20.0, 40.0, 60.0])
        h = random.choice([10.0, 25.0, 60.0])
        cache = PairCache(flights)
        res = solve(flights, cache, r, h, time_limit=10)
        bd, bv = _brute(flights, r, h)
        if bd is None:
            n_infeas += 1
            assert not res["feasible"], trial
            table = cache.get(r, h)
            all_mins = set()
            for cm in range(1, 1 << n):
                sub = frozenset(i for i in range(n) if cm >> i & 1)
                if not _feasible_subset(flights, table, {}, sub, 2.0):
                    if all(_feasible_subset(flights, table, {}, sub - {i}, 2.0) for i in sub):
                        all_mins.add(frozenset(sorted(flights[i].fid for i in sub)))
            got = {frozenset(m) for m in res["mus"]}
            assert all_mins == got, (trial, all_mins, got)
        else:
            n_feas += 1
            vec = tuple(res["starts"][fid] for fid in sorted(f.fid for f in flights))
            assert (res["weightedDelay"], vec) == (bd, bv), trial
            assert not evaluate_all(flights, res["starts"], r, h)
    assert n_feas > 0 and n_infeas > 0
    print(f"random scenarios: {n_feas} feasible + {n_infeas} infeasible all match brute force")


def test_endpoint_and_tangent():
    a = make_flight({"id": "A", "weight": 1, "earliest": 0, "latest": 0, "track": [
        {"x": 0, "y": 0, "z": 0, "t": 0}, {"x": 100, "y": 0, "z": 0, "t": 10}]})
    b = make_flight({"id": "B", "weight": 1, "earliest": -10, "latest": 10, "track": [
        {"x": 100, "y": 0, "z": 0, "t": 0}, {"x": 100, "y": 100, "z": 0, "t": 10}]})
    hit = evaluate_all([a, b], {"A": 0, "B": 10}, 10.0, 5.0)
    assert len(hit) == 1 and hit[0]["timeStart"] == 10.0 and hit[0]["timeEnd"] == 10.0
    assert not evaluate_all([a, b], {"A": 0, "B": 8}, 10.0, 5.0)
    c = make_flight({"id": "C", "weight": 1, "earliest": 0, "latest": 0, "track": [
        {"x": 0, "y": 10, "z": 0, "t": 0}, {"x": 100, "y": 10, "z": 0, "t": 10}]})
    assert evaluate_all([a, c], {"A": 0, "C": 0}, 10.0, 5.0) == []
    assert len(evaluate_all([a, c], {"A": 0, "C": 0}, 10.5, 5.0)) == 1
    print("endpoint + tangent boundary tests passed")


if __name__ == "__main__":
    test_random_scenarios()
    test_endpoint_and_tangent()
    print("ALL TESTS PASSED")
