"""一次性核验脚本（compose 服务 verify）。

顺序执行并以退出码报告：
  1. 等待并确认 API 健康；
  2. 核对代码测试（pytest 全套）；
  3. 核对镜像构建契约（非 root、应用与工具链可导入、健康端点契约）；
  4. 业务冒烟：可行计划、并列裁决（三级目标）、受限接续可行、接续断链无解、
     真实无解诊断、参数错误与无解的区分，以及旧请求回归。
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# 以脚本方式运行时，把项目根（app 包所在目录）加入 sys.path。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

API_BASE = os.environ.get("API_BASE_URL", "http://api:8000")
COMPILE_URL = f"{API_BASE}/api/momentum-plans/compile"
HEALTH_URL = f"{API_BASE}/health"


def _log(ok: bool, message: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {message}")


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _wait_for_health(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception as exc:  # noqa: BLE001 - 启动中任何异常都重试
            last_err = exc
            time.sleep(1.0)
    print(f"等待 API 健康超时：{last_err}")
    return False


def _slot(disturbance, *commands):
    built = []
    for c in commands:
        cid, mode, corr, energy = c[:4]
        cmd = {"id": cid, "mode": mode, "correction": list(corr), "energy": energy}
        if len(c) >= 5 and c[4] is not None:
            cmd["allowed_predecessor_ids"] = list(c[4])
        built.append(cmd)
    return {"disturbance": list(disturbance), "commands": built}


WIDE_SAFETY = {"x_min": -100, "x_max": 100, "y_min": -100, "y_max": 100}


def _base_body(slots, safety=None, target=None, initial=(0, 0)):
    return {
        "initial_momentum": list(initial),
        "safety_region": safety or dict(WIDE_SAFETY),
        "target_region": target or dict(WIDE_SAFETY),
        "slots": slots,
    }


def smoke_feasible() -> bool:
    """可行计划：返回所选指令、逐隙动量与三项目标值。"""
    slots = [
        _slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (-1, 0), 2.0)),
        _slot((0, 1), (1, "A", (0, -1), 1.0), (2, "B", (0, 0), 1.0)),
        _slot((1, 0), (1, "A", (-1, 0), 1.0), (2, "B", (0, 1), 3.0)),
        _slot((0, 0), (1, "A", (0, 0), 2.0), (2, "B", (0, 0), 1.0)),
        _slot((-1, 0), (1, "A", (1, 0), 1.0), (2, "B", (0, 0), 2.0)),
        _slot((0, -1), (1, "A", (0, 1), 1.0), (2, "B", (0, 0), 1.5)),
        _slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (1, 0), 0.5)),
        _slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, -1), 2.0)),
    ]
    status, data = _post_json(COMPILE_URL, _base_body(slots))
    if status != 200 or not data.get("feasible"):
        _log(False, f"可行计划应返回 200/feasible=true，实际 {status}: {data}")
        return False

    ok = True
    if len(data["selected"]) != 8 or len(data["momentums"]) != 8:
        ok = False
    obj = data["objectives"]
    for key in ("total_energy", "mode_switches", "command_id_sequence"):
        if key not in obj:
            ok = False

    # 连续演化复核：前态 + 扰动 + 修正量 == 逐隙动量，且均在安全域。
    state = (0, 0)
    for i, sel in enumerate(data["selected"]):
        d = slots[i]["disturbance"]
        c = sel["correction"]
        state = (state[0] + d[0] + c[0], state[1] + d[1] + c[1])
        if tuple(data["momentums"][i]) != state:
            ok = False
    if tuple(data["final_momentum"]) != tuple(data["momentums"][-1]):
        ok = False

    _log(ok, "业务冒烟 1/7：可行计划（选择、逐隙动量、三项目标）")
    return ok


def smoke_tie_breaking() -> bool:
    """并列裁决：能耗相同优先少切换，再并列取编号序列字典序最小。"""
    # 每时隙两条同模式指令、修正相同、能耗相同：id=3 字典序更优、零切换。
    slots = [
        _slot((0, 0), (7, "A", (0, 0), 1.0), (3, "A", (0, 0), 1.0))
    ] * 8
    status, data = _post_json(COMPILE_URL, _base_body(slots))
    ok = (
        status == 200
        and data["objectives"]["total_energy"] == 8.0
        and data["objectives"]["mode_switches"] == 0
        and data["objectives"]["command_id_sequence"] == [3] * 8
    )
    _log(ok, f"业务冒烟 2/7：并列裁决（能耗→切换数→编号序列） 实际目标={data.get('objectives') if status == 200 else data}")
    return ok


def smoke_restricted_chain_feasible() -> bool:
    """受限接续可行：B 更省能但只能跟随 A，输出序列须满足逐隙接续关系。"""
    slots = []
    for t in range(1, 9):
        ca = (1, "A", (0, 0), 1.0)
        cb = (2, "B", (0, 0), 0.1)
        if t >= 2:
            cb = (*cb, [1])  # B 只允许紧邻前隙选中 id=1（A）
        slots.append(_slot((0, 0), ca, cb))
    status, data = _post_json(COMPILE_URL, _base_body(slots))
    ok = status == 200 and data.get("feasible") is True
    if ok:
        ids = [sel["command_id"] for sel in data["selected"]]
        ok = all(prev == 1 for prev, cur in zip(ids, ids[1:]) if cur == 2)
        # 4 个不相邻的 B 为能耗最优：总能耗 4*1.0 + 4*0.1。
        ok = ok and abs(data["objectives"]["total_energy"] - 4.4) < 1e-9
    _log(
        ok,
        f"业务冒烟 3/7：受限接续可行（白名单逐隙成立） 实际 {status}: "
        f"{data.get('objectives') if status == 200 else data}",
    )
    return ok


def smoke_broken_chain_infeasible() -> bool:
    """接续断链无解：409 且 reason=broken_predecessor_chain，报告最后可达层。"""
    # 第 1 隙 id=1 修正 (100,0) 被安全域排除，仅 id=2 可达；
    # 第 2 隙两条指令白名单都只含编号 1（编号 1 在第 1 隙存在，输入合法），断链。
    slots = [
        _slot((0, 0), (1, "A", (100, 0), 1.0), (2, "B", (0, 0), 1.0)),
        _slot((0, 0), (1, "A", (0, 0), 1.0, [1]), (2, "B", (0, 0), 1.0, [1])),
    ] + [
        _slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, 0), 1.0))
        for _ in range(6)
    ]
    region = {"x_min": -10, "x_max": 10, "y_min": -10, "y_max": 10}
    body = _base_body(slots, safety=dict(region), target=dict(region))
    status, data = _post_json(COMPILE_URL, body)
    ok = (
        status == 409
        and data.get("error") == "no_feasible_plan"
        and data.get("reason") == "broken_predecessor_chain"
        and data.get("last_reachable_slot") == 1
        and data.get("reachable_states") == 1
    )
    _log(
        ok,
        f"业务冒烟 4/7：接续断链无解（409、最后可达时隙、可达动量数） 实际 {status}: {data}",
    )
    return ok


def smoke_legacy_request_regression() -> bool:
    """旧请求回归：不带 allowed_predecessor_ids 时请求、响应与裁决保持兼容。"""
    slots = [
        _slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (-1, 0), 2.0)),
        _slot((0, 1), (1, "A", (0, -1), 1.0), (2, "B", (0, 0), 1.0)),
        _slot((1, 0), (1, "A", (-1, 0), 1.0), (2, "B", (0, 1), 3.0)),
        _slot((0, 0), (1, "A", (0, 0), 2.0), (2, "B", (0, 0), 1.0)),
        _slot((-1, 0), (1, "A", (1, 0), 1.0), (2, "B", (0, 0), 2.0)),
        _slot((0, -1), (1, "A", (0, 1), 1.0), (2, "B", (0, 0), 1.5)),
        _slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (1, 0), 0.5)),
        _slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, -1), 2.0)),
    ]
    body = _base_body(slots)
    assert all(
        "allowed_predecessor_ids" not in cmd
        for s in slots
        for cmd in s["commands"]
    )
    status, data = _post_json(COMPILE_URL, body)
    ok = (
        status == 200
        and data.get("feasible") is True
        and set(data)
        == {"feasible", "slot_count", "selected", "momentums",
            "final_momentum", "objectives"}
        and set(data["objectives"])
        == {"total_energy", "mode_switches", "command_id_sequence"}
        and len(data["selected"]) == 8
    )
    _log(
        ok,
        f"业务冒烟 5/7：旧请求回归（无新字段时响应契约不变） 实际 {status}: "
        f"{data.get('objectives') if status == 200 else data}",
    )
    return ok


def smoke_infeasible() -> bool:
    """真实无解：409 且报告最后可达时隙与该层可达状态数。"""
    # 第一隙结果 x ∈ {+1,-1}，安全域只含原点 → 立即切断。
    slots = [
        _slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (-1, 0), 1.0))
    ] * 8
    body = _base_body(
        slots,
        safety={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        target={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
    )
    status, data = _post_json(COMPILE_URL, body)
    ok = (
        status == 409
        and data.get("error") == "no_feasible_plan"
        and data.get("last_reachable_slot") == 0
        and data.get("reachable_states") == 1
    )
    _log(
        ok,
        f"业务冒烟 6/7：真实无解诊断（409、最后可达时隙、可达状态数） 实际 {status}: {data if not ok else 'last_reachable_slot=0, reachable_states=1'}",
    )
    return ok


def smoke_param_error() -> bool:
    """参数矛盾：422 且定位字段，与 409 真实无解明确区分。"""
    slots = [
        _slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (-1, 0), 1.0))
    ] * 8
    body = _base_body(slots)
    body["initial_momentum"] = [0, "bad"]
    status, data = _post_json(COMPILE_URL, body)
    ok = status == 422 and data.get("error") == "invalid_request"
    located = any(
        "initial_momentum" in d.get("field", "") for d in data.get("details", [])
    )
    _log(
        ok and located,
        f"业务冒烟 7/7：参数错误定位字段（422，区别于 409） 实际 {status}: {data}",
    )
    return ok and located


def image_contract() -> bool:
    """镜像构建契约核验。"""
    checks = [
        os.geteuid() != 0,  # 以非 root 运行
    ]
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        from app.main import app  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"应用导入失败：{exc}")
        return False
    _log(all(checks), f"镜像构建契约（非 root uid={os.geteuid()}、应用与工具链可导入）")
    return all(checks)


def run_pytest() -> bool:
    print("\n=== 运行代码测试 (pytest) ===")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cwd=_PROJECT_ROOT,
    )
    _log(proc.returncode == 0, f"代码测试（退出码 {proc.returncode}）")
    return proc.returncode == 0


def main() -> int:
    print("=== 等待 API 健康 ===")
    if not _wait_for_health():
        _log(False, "API 健康检查")
        return 1
    _log(True, "API 健康检查")

    results = [
        image_contract(),
        run_pytest(),
        smoke_feasible(),
        smoke_tie_breaking(),
        smoke_restricted_chain_feasible(),
        smoke_broken_chain_infeasible(),
        smoke_legacy_request_regression(),
        smoke_infeasible(),
        smoke_param_error(),
    ]
    print("\n=== 核验汇总 ===")
    print(f"通过 {sum(results)}/{len(results)} 项")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
