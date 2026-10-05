"""接续约束（allowed_predecessor_ids）：规划语义、校验定位与兼容性。"""

import copy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import CompileRequest
from app.planner import InfeasiblePlanError, compile_plan

from .conftest import brute_force_best, eight_slots, make_request, slot

client = TestClient(app)


def _compile(body):
    return client.post("/api/momentum-plans/compile", json=body)


def restricted_slots():
    """首隙 A 贵 B 便宜；后续隙受限：id=1 只能跟 id=1，id=2 只能跟 id=2。"""
    first = slot((0, 0), (1, "A", (0, 0), 5.0), (2, "B", (0, 0), 1.0))
    rest = slot(
        (0, 0),
        (1, "A", (0, 0), 0.1, [1]),
        (2, "B", (0, 0), 0.2, [2]),
    )
    return [first] + [copy.deepcopy(rest) for _ in range(7)]


def chain_broken_body():
    """第 2 隙全部指令只认 id=1，而 id=1 在第 1 隙越出安全域 → 断链。"""
    safety = {"x_min": 0, "x_max": 0, "y_min": -10, "y_max": 10}
    slots = [
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (0, 0), 1.0)),
        slot((0, 0), (1, "A", (0, 0), 1.0, [1]), (2, "B", (0, 0), 1.0, [1])),
    ] + [
        slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, 0), 1.0))
        for _ in range(6)
    ]
    return make_request(safety=safety, target=dict(safety), slots=slots)


# ---------------------------------------------------------------- 规划器语义


def test_restricted_continuation_changes_global_optimum():
    """不受限时最优会穿越接续断点；受限后只能整链同编号。"""
    restricted = make_request(slots=restricted_slots())
    result = compile_plan(CompileRequest.model_validate(restricted))
    obj = result["objectives"]
    # 仅 全A(5.7) 与 全B(2.4) 两条链合法，最优为全 B。
    assert obj["total_energy"] == pytest.approx(2.4)
    assert obj["mode_switches"] == 0
    assert obj["command_id_sequence"] == [2] * 8

    # 同一窗口去掉名单即回到不受限最优：首隙 B、其后 A（能耗 1.7）。
    unrestricted = make_request(slots=restricted_slots())
    for s in unrestricted["slots"]:
        for cmd in s["commands"]:
            cmd.pop("allowed_predecessor_ids", None)
    free = compile_plan(CompileRequest.model_validate(unrestricted))
    assert free["objectives"]["total_energy"] == pytest.approx(1.7)
    assert free["objectives"]["command_id_sequence"] == [2] + [1] * 7


def test_selected_plan_respects_predecessor_lists():
    """响应逐隙可复核：每条受限指令的前序已选编号都在其名单内。"""
    req = make_request(slots=restricted_slots())
    result = compile_plan(CompileRequest.model_validate(req))
    selected = result["selected"]
    for i in range(1, len(selected)):
        chosen = next(
            c for c in req["slots"][i]["commands"] if c["id"] == selected[i]["command_id"]
        )
        allowed = chosen.get("allowed_predecessor_ids")
        assert allowed is None or selected[i - 1]["command_id"] in allowed


def test_chain_broken_reports_last_reachable_slot():
    """接续断链：第 2 隙无可选指令，last_reachable_slot=1、可达动量数 1。"""
    req = chain_broken_body()
    assert brute_force_best(req) is None  # 独立参考同样无解
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.reason == "no_safe_path"
    assert exc.value.last_reachable_slot == 1
    assert exc.value.reachable_states == 1  # 仅 (0, 0) 经 id=2 可达


def test_mid_window_chain_break_counts_distinct_momentums():
    """窗口中部断链：准确报告最后可达层与该层去重后的动量数。"""
    slots = [
        slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (1, 0), 1.0))
        for _ in range(3)
    ]
    # 第 4 隙：id=3 修正量越界永远不可选，但合法存在于候选中。
    slots.append(
        slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (1, 0), 1.0), (3, "C", (100, 0), 1.0))
    )
    # 第 5 隙全部要求前序 id=3 → 断链。
    slots.append(
        slot((0, 0), (1, "A", (0, 0), 1.0, [3]), (2, "B", (0, 0), 1.0, [3]))
    )
    slots += [
        slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, 0), 1.0))
        for _ in range(3)
    ]
    # 安全域只容下 x ∈ [-10, 10]：id=3 的 +100 修正恒越界，不可选。
    region = {"x_min": -10, "x_max": 10, "y_min": -10, "y_max": 10}
    req = make_request(safety=region, target=dict(region), slots=slots)
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.reason == "no_safe_path"
    assert exc.value.last_reachable_slot == 4
    # 4 隙内 id=2 被选 0..4 次 → 动量 (0..4, 0) 共 5 个不同状态。
    assert exc.value.reachable_states == 5


def test_restricted_target_unreachable():
    """接续链完好但末态无人进入目标矩形：reason 仍为 target_unreachable。"""
    slots = [
        slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, 0), 1.0)),
    ] + [
        slot((0, 0), (1, "A", (0, 0), 1.0, [1, 2]), (2, "B", (0, 0), 1.0, [1, 2]))
        for _ in range(7)
    ]
    req = make_request(
        safety={"x_min": -10, "x_max": 10, "y_min": -10, "y_max": 10},
        target={"x_min": 5, "x_max": 5, "y_min": 5, "y_max": 5},
        slots=slots,
    )
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.reason == "target_unreachable"
    assert exc.value.last_reachable_slot == 8
    assert exc.value.reachable_states == 1


def test_vacuous_list_equivalent_to_omitted():
    """名单列全前隙所有编号 ≡ 省略字段：响应逐字节一致。"""
    legacy = make_request(slots=eight_slots())
    decorated = copy.deepcopy(legacy)
    for i in range(1, len(decorated["slots"])):
        prev_ids = [c["id"] for c in decorated["slots"][i - 1]["commands"]]
        for cmd in decorated["slots"][i]["commands"]:
            cmd["allowed_predecessor_ids"] = list(prev_ids)

    legacy_result = compile_plan(CompileRequest.model_validate(legacy))
    decorated_result = compile_plan(CompileRequest.model_validate(decorated))
    assert decorated_result == legacy_result


# ---------------------------------------------------------------- HTTP 层


def test_api_restricted_feasible_payload():
    r = _compile(make_request(slots=restricted_slots()))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["feasible"] is True
    assert data["objectives"]["mode_switches"] == 0
    assert data["objectives"]["command_id_sequence"] == [2] * 8
    assert data["objectives"]["total_energy"] == pytest.approx(2.4)
    assert len(data["selected"]) == 8 and len(data["momentums"]) == 8


def test_api_chain_broken_returns_409_with_diagnostics():
    r = _compile(chain_broken_body())
    assert r.status_code == 409
    data = r.json()
    assert data["error"] == "no_feasible_plan"
    assert data["reason"] == "no_safe_path"
    assert data["last_reachable_slot"] == 1
    assert data["reachable_states"] == 1


def test_first_slot_must_not_declare_predecessors():
    body = make_request(slots=eight_slots())
    body["slots"][0]["commands"][0]["allowed_predecessor_ids"] = [1]
    r = _compile(body)
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_request"
    assert r.json()["field"] == "slots[0].commands[0].allowed_predecessor_ids"


def test_predecessor_ids_must_be_distinct():
    body = make_request(slots=eight_slots())
    body["slots"][2]["commands"][1]["allowed_predecessor_ids"] = [1, 1]
    r = _compile(body)
    assert r.status_code == 422
    assert r.json()["field"] == "slots[2].commands[1].allowed_predecessor_ids"


def test_predecessor_id_must_exist_in_previous_slot():
    body = make_request(slots=eight_slots())
    body["slots"][3]["commands"][0]["allowed_predecessor_ids"] = [1, 99]
    r = _compile(body)
    assert r.status_code == 422
    data = r.json()
    assert data["field"] == "slots[3].commands[0].allowed_predecessor_ids"
    assert "99" in data["message"]


def test_predecessor_ids_cardinality_and_type():
    body = make_request(slots=eight_slots())
    body["slots"][1]["commands"][0]["allowed_predecessor_ids"] = []
    r = _compile(body)
    assert r.status_code == 422
    assert any(
        "allowed_predecessor_ids" in d["field"] for d in r.json()["details"]
    )

    body = make_request(slots=eight_slots())
    body["slots"][1]["commands"][0]["allowed_predecessor_ids"] = [1, 2, 3, 4, 5, 6]
    r = _compile(body)
    assert r.status_code == 422

    body = make_request(slots=eight_slots())
    body["slots"][1]["commands"][0]["allowed_predecessor_ids"] = ["x"]
    r = _compile(body)
    assert r.status_code == 422


def test_legacy_request_regression_objectives():
    """未使用新字段：既有场景的最优结果保持不变（与穷举参考一致）。"""
    req = make_request(slots=eight_slots())
    r = _compile(req)
    assert r.status_code == 200, r.text
    obj = r.json()["objectives"]
    expected = brute_force_best(req)
    assert obj["total_energy"] == pytest.approx(expected[0])
    assert obj["mode_switches"] == expected[1]
    assert obj["command_id_sequence"] == list(expected[2])
