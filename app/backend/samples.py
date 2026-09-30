"""内置示例场景。"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

SAMPLES: List[Dict[str, Any]] = [
    {
        "name": "示例 A：4 机十字交汇（有解）",
        "safetyRadius": 50.0,
        "safetyHeight": 15.0,
        "flights": [
            {
                "id": "UAV-01", "weight": 1, "earliest": 0, "latest": 120,
                "track": [
                    {"x": 0.0, "y": 0.0, "z": 80.0, "t": 0.0},
                    {"x": 400.0, "y": 0.0, "z": 80.0, "t": 40.0},
                    {"x": 800.0, "y": 0.0, "z": 80.0, "t": 80.0},
                ],
            },
            {
                "id": "UAV-02", "weight": 2, "earliest": 0, "latest": 120,
                "track": [
                    {"x": 400.0, "y": -300.0, "z": 80.0, "t": 0.0},
                    {"x": 400.0, "y": 0.0, "z": 80.0, "t": 30.0},
                    {"x": 400.0, "y": 300.0, "z": 80.0, "t": 60.0},
                ],
            },
            {
                "id": "UAV-03", "weight": 3, "earliest": 0, "latest": 120,
                # 与 UAV-01 反向同线，最早起飞时在 t=35s、x≈350 处迎面相遇
                "track": [
                    {"x": 700.0, "y": 0.0, "z": 78.0, "t": 0.0},
                    {"x": 0.0, "y": 0.0, "z": 78.0, "t": 70.0},
                ],
            },
            {
                "id": "UAV-04", "weight": 1, "earliest": 0, "latest": 60,
                # 与 UAV-01 在 t=20s、x=200 处交汇（高度差 10 < 15）
                "track": [
                    {"x": 200.0, "y": -200.0, "z": 90.0, "t": 0.0},
                    {"x": 200.0, "y": 200.0, "z": 90.0, "t": 40.0},
                ],
            },
        ],
    },
    {
        "name": "示例 B：窗口内无法错开（无解，演示 MUS）",
        "safetyRadius": 50.0,
        "safetyHeight": 20.0,
        "flights": [
            {
                "id": "N-01", "weight": 1, "earliest": 0, "latest": 2,
                # 对头穿越：两机相距 300m、各 15m/s，需约 13.3s 时差才能错开，
                # 但起飞窗口只有 3 秒 => 无解，MUS = {N-01, N-02}
                "track": [
                    {"x": 0.0, "y": 0.0, "z": 100.0, "t": 0.0},
                    {"x": 300.0, "y": 0.0, "z": 100.0, "t": 20.0},
                ],
            },
            {
                "id": "N-02", "weight": 1, "earliest": 0, "latest": 2,
                "track": [
                    {"x": 300.0, "y": 0.0, "z": 100.0, "t": 0.0},
                    {"x": 0.0, "y": 0.0, "z": 100.0, "t": 20.0},
                ],
            },
            # N-03 与前两架时间窗完全错开，验证 MUS 只包含真正矛盾的架次
            {
                "id": "N-03", "weight": 2, "earliest": 100, "latest": 120,
                "track": [
                    {"x": 0.0, "y": 400.0, "z": 100.0, "t": 0.0},
                    {"x": 300.0, "y": 400.0, "z": 100.0, "t": 20.0},
                ],
            },
        ],
    },
]


def get_samples() -> List[Dict[str, Any]]:
    return copy.deepcopy(SAMPLES)
