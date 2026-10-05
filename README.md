# 低轨卫星动量卸载整窗计划编译服务

姿控团队为每个卸载时隙预先计算多条候选磁力矩指令。本服务从 8–20 个时隙的
候选指令中编译**整窗**计划：每时隙恰选一条，动量按
`p_{t+1} = p_t + disturbance_t + correction_t` 连续演化，保证各时隙动量均位于
统一矩形安全域、末态进入目标矩形，并按严格优先级依次最小化：

1. **总能耗**（候选指令能耗均为非负）；
2. **模式切换次数**（相邻时隙模式不同计一次）；
3. **指令编号序列**（按字典序）。

部分指令还要求**上电与极性准备**：候选可声明 `allowed_predecessor_ids`
（1–5 个互异整数），仅可跟在紧邻前一时隙已选编号属于该名单的指令之后；
省略该字段表示不限制前序。接续约束与安全域、目标域及三级裁决共同决定
全局最优计划。

逐时隙选最低能耗可能把反作用轮动量推出安全边界，因此规划器使用前向动态规划
在整个窗口上求全局最优，而非逐时隙贪心。

## 目录结构

```
app/                 FastAPI 应用
  main.py            路由与错误处理（422 参数错误 / 409 真实无解）
  models.py          Pydantic 请求/响应模型
  planner.py         整窗动态规划与无解诊断
  validation.py      跨字段语义校验（定位到字段）
tests/               pytest 测试（含 60 组随机穷举对拍）
scripts/verify.py    Compose verify 服务的一次性核验脚本
Dockerfile           runtime / verify 多目标镜像
docker-compose.yml   api（带健康检查）+ verify（一次性）
```

## 本地运行（Docker Compose）

```bash
# 可选：复制环境变量模板并修改宿主机端口
cp .env.example .env          # HOST_PORT 控制宿主机端口，默认 8080

# 构建并以健康状态门控启动 API
docker compose up --build -d api

# 一次性核验：等待 API 健康后运行测试、镜像契约检查与业务冒烟
docker compose up --build verify
# verify 以退出码报告：0 全部通过，非 0 存在失败项（docker compose ps 可见）
```

清洁环境无需任何外部账号或人工初始化。宿主机端口通过环境变量配置：

```bash
HOST_PORT=9090 docker compose up --build -d api
```

容器内固定监听 8000；`api` 服务配置了 `healthcheck`（GET `/health`），
`verify` 通过 `depends_on: condition: service_healthy` 在 API 健康后才启动。

## 本地开发（无 Docker）

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload
```

## API

### 健康检查

`GET /health` → `200 {"status":"ok"}`

### 编译整窗计划

`POST /api/momentum-plans/compile`

请求（8–20 时隙；每时隙 2–5 条候选；所有动量/扰动/修正量为二维整数向量）：

```json
{
  "initial_momentum": [0, 0],
  "safety_region": {"x_min": -10, "x_max": 10, "y_min": -10, "y_max": 10},
  "target_region": {"x_min": -2, "x_max": 2, "y_min": -2, "y_max": 2},
  "slots": [
    {
      "disturbance": [1, 0],
      "commands": [
        {"id": 1, "mode": "A", "correction": [-1, 0], "energy": 1.5},
        {"id": 2, "mode": "B", "correction": [0, 1], "energy": 2.0}
      ]
    },
    {
      "disturbance": [0, 1],
      "commands": [
        {"id": 1, "mode": "A", "correction": [0, -1], "energy": 1.0,
         "allowed_predecessor_ids": [1, 2]},
        {"id": 2, "mode": "B", "correction": [0, 0], "energy": 1.0}
      ]
    }
  ]
}
```

`allowed_predecessor_ids`（可选）：该指令仅可跟在紧邻前一时隙已选编号属于
名单的指令之后。规则：

- 列出时为 1–5 个**互异**整数；省略表示不限制前序；
- **首时隙不得提供**（没有前一时隙）；
- 所列编号必须真实存在于紧邻前一时隙的候选中；
- 违反以上任一条即 422 并定位到
  `slots[i].commands[j].allowed_predecessor_ids`。

成功响应（200）：

```json
{
  "feasible": true,
  "slot_count": 8,
  "selected": [
    {"slot": 1, "command_id": 1, "mode": "A", "correction": [-1, 0], "energy": 1.5}
  ],
  "momentums": [[0, 0]],
  "final_momentum": [0, 0],
  "objectives": {
    "total_energy": 12.0,
    "mode_switches": 1,
    "command_id_sequence": [1, 2, 1, 1, 2, 1, 2, 1]
  }
}
```

### 错误区分（值班员视角）

- **422 参数错误**：类型/基数错误或输入自相矛盾，响应定位到字段，例如
  `{"error":"invalid_request","field":"slots[1].commands[1].id", ...}`
  （编号重复、初始动量在安全域外、目标域不包含于安全域、矩形边界反向、
  时隙数不在 8–20、每时隙指令数不在 2–5、能耗为负、首时隙声明接续名单、
  名单内编号重复或不存在于紧邻前一时隙等）。
- **409 真实无解**：输入合法但不存在满足全部约束（安全域、接续名单、
  末态目标）的完整指令序列，响应给出
  `last_reachable_slot`（仍有可达状态的最后时隙编号，0 表示第一隙后即被
  切断）与 `reachable_states`（该层不同可达动量状态数，按动量去重）：

```json
{
  "error": "no_feasible_plan",
  "reason": "no_safe_path",
  "last_reachable_slot": 3,
  "reachable_states": 2
}
```

`reason` 为 `target_unreachable` 时表示整窗安全域内均可达但末态无人进入
目标矩形（此时 `last_reachable_slot` 等于总时隙数）；`no_safe_path` 表示
可达前沿在某时隙被安全域或接续关系切断（接续断链同样按此诊断报告）。

## 算法要点

前向动态规划按层扩展，分桶键为 `(动量, 上一条指令模式, 上一条指令编号)`——
同一动量、不同末模式或不同末编号的路径不能互相支配（模式切换次数取决于
历史末模式，接续约束取决于历史末编号）；桶内未来代价与可行集只取决于桶键，
仅保留三级目标下最优标签。复杂度为
`O(T · C · |可达前沿|)`。末层在目标矩形内的桶中选最优标签并回溯，输出所选
指令与逐隙动量。浮点能耗在 1e-9 容差内视为并列（避免求和误差干扰裁决）。
未使用 `allowed_predecessor_ids` 时，末编号细化不改变支配关系，最优结果、
响应形态与失败语义与旧两元桶键完全一致。
