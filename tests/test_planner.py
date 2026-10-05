"""规划器：可行性、三级目标与无解定位。"""

import pytest

from app.models import CompileRequest
from app.planner import InfeasiblePlanError, compile_plan

from .conftest import brute_force_best, eight_slots, make_request, slot


def test_feasible_plan_shape_and_evolution():
    req = make_request(slots=eight_slots())
    result = compile_plan(CompileRequest.model_validate(req))

    assert result["feasible"] is True
    assert result["slot_count"] == 8
    assert len(result["selected"]) == 8
    assert len(result["momentums"]) == 8
    assert len(result["objectives"]["command_id_sequence"]) == 8

    # 逐隙动量必须等于“前态 + 扰动 + 修正量”。
    state = (0, 0)
    safety = req["safety_region"]
    for i, (sel, m) in enumerate(zip(result["selected"], result["momentums"]), start=1):
        d = req["slots"][i - 1]["disturbance"]
        state = (state[0] + d[0] + sel["correction"][0],
                 state[1] + d[1] + sel["correction"][1])
        assert tuple(m) == state
        assert safety["x_min"] <= m[0] <= safety["x_max"]
        assert safety["y_min"] <= m[1] <= safety["y_max"]

    # 末态在目标域。
    fm = tuple(result["final_momentum"])
    assert fm == tuple(result["momentums"][-1])


def test_minimum_energy_primary_objective():
    # 第 4 隙 B 更便宜会诱发逐隙贪心，检查整窗最优不越界且总能耗最小。
    req = make_request(slots=eight_slots())
    result = compile_plan(CompileRequest.model_validate(req))
    expected = brute_force_best(req)
    assert pytest.approx(result["objectives"]["total_energy"]) == expected[0]
    assert result["objectives"]["mode_switches"] == expected[1]
    assert tuple(result["objectives"]["command_id_sequence"]) == expected[2]


def test_mode_switch_is_secondary_tie_breaker():
    """同能耗下优先少切换：两条路能耗相同，应选全程同模式。"""
    slots = [
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
    ]
    req = make_request(slots=slots)
    result = compile_plan(CompileRequest.model_validate(req))
    assert result["objectives"]["mode_switches"] == 0
    # 全部 A：编号序列全 1（编号序列同为 8 个 1 或全 2，1 更小）。
    assert result["objectives"]["command_id_sequence"] == [1] * 8


def test_command_id_sequence_tertiary_tie_breaker():
    """能耗、切换数都并列时，编号序列字典序最小。"""
    slots = [
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "A", (1, 0), 1.0)),
    ] * 8
    req = make_request(slots=slots)
    result = compile_plan(CompileRequest.model_validate(req))
    # 同模式（零切换）、同能耗；id=1 字典序更小。
    assert result["objectives"]["command_id_sequence"] == [1] * 8


def test_energy_ties_within_float_epsilon():
    """数学等价但浮点表示相差 1e-16 的能耗应视为并列，再按切换次数裁决。

    0.1 + 4*0.05 与 0.3 之和的双精度值不同（0.30000000000000004）。
    """
    # 第一隙：id=1（模式 B，精确 0.3）/ id=2（模式 A，浮点噪声偏大）；
    # 后续隙：两条均为模式 A，id=1 精确 0.3、id=2 带噪声。
    noisy = 0.1 + 4 * 0.05  # 0.30000000000000004
    assert noisy != 0.3
    slots = [
        slot((0, 0), (1, "B", (0, 0), 0.3), (2, "A", (0, 0), noisy)),
    ] + [
        slot((0, 0), (1, "A", (0, 0), 0.3), (2, "A", (0, 0), noisy))
        for _ in range(7)
    ]
    req = make_request(slots=slots)
    result = compile_plan(CompileRequest.model_validate(req))
    ids = result["objectives"]["command_id_sequence"]
    # 能耗并列：优先零切换 -> 第一隙也选模式 A（id=2），其后选字典序最小的 id=1。
    assert result["objectives"]["mode_switches"] == 0
    assert ids == [2] + [1] * 7
    assert result["objectives"]["total_energy"] == pytest.approx(2.4, abs=1e-9)


def test_energy_dominates_switch_count():
    """能耗差一分钱也比切换次数重要。"""
    slots = [
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 0.5)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (1, 0), 1.0)),
    ]
    req = make_request(slots=slots)
    result = compile_plan(CompileRequest.model_validate(req))
    # 全局最省能耗方案为全程 B（7.5），同时零模式切换。
    assert result["objectives"]["total_energy"] == 7.5
    assert result["objectives"]["mode_switches"] == 0


def test_no_safe_path_reports_last_reachable_slot():
    """第一隙后即被安全域切断：last_reachable_slot=0。"""
    slots = eight_slots()
    req = make_request(
        safety={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        slots=slots,
    )
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.reason == "no_safe_path"
    assert exc.value.last_reachable_slot == 0
    assert exc.value.reachable_states == 1  # 初始状态


def test_mid_window_cutoff():
    """前几隙可达，之后安全域切断。"""
    # 每隙 x 固定 +1，安全域只允许走到第 3 隙。
    slots = [
        slot((1, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, 0), 2.0))
    ] * 8
    req = make_request(
        safety={"x_min": 0, "x_max": 3, "y_min": -1, "y_max": 1},
        slots=slots,
    )
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.reason == "no_safe_path"
    assert exc.value.last_reachable_slot == 3
    assert exc.value.reachable_states >= 1


def test_target_unreachable_keeps_full_window_reachable():
    """所有隙都在安全域内，但无人到达目标矩形。"""
    slots = [
        slot((1, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, 0), 1.0))
    ] * 8
    req = make_request(
        safety={"x_min": 0, "x_max": 50, "y_min": 0, "y_max": 50},
        target={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        slots=slots,
    )
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.reason == "target_unreachable"
    assert exc.value.last_reachable_slot == 8
    assert exc.value.reachable_states == 1  # 唯一动量 (8,0)


def test_reachable_state_count_dedupes_modes():
    """同一动量经不同模式到达，可达状态数按动量去重。"""
    slots = [
        slot((1, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, 0), 1.0)),
    ] * 8
    req = make_request(
        safety={"x_min": 0, "x_max": 3, "y_min": 0, "y_max": 1},
        slots=slots,
    )
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.last_reachable_slot == 3
    assert exc.value.reachable_states == 1
