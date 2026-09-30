# 无人机航迹冲突消解台（Drone Trajectory Conflict Resolution Console）

本地运行的 Web 应用：导入以**米制三维坐标 + 相对起飞秒数**描述的无人机分段直线航迹，
连续检测航迹在时间重叠处的冲突，并用 **OR-Tools CP-SAT** 求出所有架次无冲突的
**整数秒**起飞时刻；支持锁定部分起飞时刻重算、最早起飞与求解方案对照、
无解时给出极小不可行架次集合（MUS）及互斥时差、方案保存与输入快照回看。

- 后端：Python · FastAPI · SQLite · OR-Tools（CP-SAT）
- 前端：原生 HTML / CSS / JavaScript（Canvas 俯视图 + 时间轴，无需构建）
- 部署：Docker Compose 一键启动

---

## 一、快速启动（Docker Compose）

需要本机已安装 Docker 与 Docker Compose（v2）。

```bash
docker compose up --build
```

启动后用浏览器访问：

> **http://localhost:8000**

数据保存在 Docker 卷 `drone-data` 中的 SQLite 数据库里（容器重建不丢失）。
停止服务：`Ctrl+C` 后执行 `docker compose down`（加 `-v` 可同时删除数据卷）。

---

## 二、本地直接运行（不用 Docker）

需要 Python 3.11+。

```bash
cd app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 数据库路径可自定义（默认 /data/drone.db，本地建议用 ./data）
export DRONE_DB_PATH=./data/drone.db
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

浏览器访问 **http://localhost:8000**。

---

## 三、使用流程

1. 页面右上角点击 **示例 A（有解）** / **示例 B（无解）** 体验，或
   **导入 JSON** 选择本机航迹文件（也可先 **保存为场景** 持久化）。
2. 设置 **安全半径 R（米）** 与 **安全高度 H（米）**；左侧立即给出
   “最早起飞时刻”（所有架次均取窗口下界）下的冲突清单、地图红点与时间轴红块。
3. 点击 **求解无冲突时刻**：
   - 有解时，架次表“求解时刻/延误”列、地图与时间轴切换到求解方案（0 冲突）；
     目标为**加权总延误最小**，同分时按**架次 ID 升序的起飞时刻序列字典序**择优。
   - 无解时，底部列出所有**包含关系极小的不可行架次集合（MUS）**，
     展开可看每对架次“禁止的 s_j − s_i 整数时差区间”及其窗口可达范围。
4. **锁定重算**：在架次表勾选“锁定”，或在时间轴上**双击条带**锁定/解锁；
   也可直接拖动时间轴起点手柄、或在表格中输入整数秒。锁定后再次点击
   “求解无冲突时刻”，即以此为约束重新优化。
5. **保存当前方案**：把可行解连同**当时的场景输入快照**存入数据库；
   右上角 **已存方案** 可列出方案、**回看快照**（恢复该方案的航迹、参数与时刻）或删除。
6. 左侧“冲突报告”处可切换 **最早起飞时刻 / 求解方案** 两种视图对照。

---

## 四、输入 JSON 格式

```json
{
  "name": "任务名称（可选）",
  "safetyRadius": 50.0,
  "safetyHeight": 15.0,
  "flights": [
    {
      "id": "UAV-01",
      "weight": 3,
      "earliest": 0,
      "latest": 120,
      "track": [
        {"x": 0.0, "y": 0.0, "z": 80.0, "t": 0.0},
        {"x": 400.0, "y": 0.0, "z": 80.0, "t": 40.0},
        {"x": 800.0, "y": 0.0, "z": 80.0, "t": 80.0}
      ]
    }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `safetyRadius` / `safetyHeight` | 安全水平半径（米）、安全垂直高度（米），均为正数 |
| `flights[].id` | 架次唯一 ID（字母/数字/`_-.`/中文） |
| `flights[].weight` | 优先级权重，**正整数**；目标函数为 Σ weight·(起飞时刻 − earliest) |
| `flights[].earliest` / `latest` | 整数秒起飞窗口 `[earliest, latest]` |
| `flights[].track[]` | 航点 `{x, y, z, t}`：坐标单位米，`t` 为**相对该架次起飞**的秒数，单调不减，首点 `t = 0` |

约束与判定口径：

- 每架次航迹固定，仅允许**整体平移起飞时刻**（整数秒，且在窗口内）。
- 对任意两框架次，连续扫描飞行时间重叠区间（**含航段端点**）：
  - 水平距离 **< 安全半径** **且** 垂直距离 **< 安全高度** 判为冲突
    （严格小于；恰好相切不算冲突）。
  - 位置在重叠区间内为时间的线性插值：垂直差是线性函数，水平距离平方是凸二次函数，
    解析求出各自的“越界时间区间”并取交集；再补验重叠区间两端的端点时刻。
- 求解分两级：① 最小化按最早起飞计的加权总延误；
  ② 在全部最优解中取“按架次 ID 排序的起飞时刻向量”字典序最小者。
- 无解诊断枚举所有**包含关系极小**的不可行架次子集（超图击中集算法），
  并给出每对架次在窗口可达时差范围内的**互斥（禁止）时差区间**。

---

## 五、HTTP API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/samples` | 内置示例场景 |
| GET/POST | `/api/scenarios`，`/api/scenarios/{id}`（GET/DELETE） | 场景 CRUD |
| POST | `/api/evaluate` | 评估给定（默认最早）起飞时刻下的冲突；支持直接传 `payload` 即时预览 |
| POST | `/api/solve` | 求解（body：`scenarioId`、可选 `locks`、`safetyRadius/Height`） |
| GET/POST/DELETE | `/api/plans`、`/api/plans/{id}`、`/api/scenarios/{id}/plans` | 方案保存/回看（含输入快照） |

API 文档（Swagger）：http://localhost:8000/docs

---

## 六、目录结构

```
.
├── Dockerfile
├── docker-compose.yml
└── app/
    ├── requirements.txt
    ├── backend/
    │   ├── main.py        # FastAPI 路由
    │   ├── geometry.py    # 分段直线连续冲突几何（二次/线性区间求交，端点检查）
    │   ├── conflicts.py   # 架次级冲突评估、禁止整数时差预计算与缓存
    │   ├── solver.py      # CP-SAT 加权延误最优 + ID 字典序 + MUS 枚举
    │   ├── validation.py  # 输入校验
    │   ├── db.py          # SQLite 存储（场景 / 方案 / 快照）
    │   └── samples.py     # 内置示例
    └── frontend/
        ├── index.html
        ├── style.css
        └── app.js         # 原生 JS：Canvas 地图、时间轴、锁定/拖拽、方案回看
```

## 七、验证说明

核心算法已用随机场景做过对拍测试：60 组随机小规模场景下，CP-SAT 的
最优加权延误、字典序 tie-break 结果以及 MUS 枚举集合，均与对全部整数时刻
暴力枚举的结果完全一致；另含航段端点冲突、相切不算冲突、锁定导致无解等边界测试。
