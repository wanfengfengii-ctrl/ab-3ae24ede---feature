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


# ---------------------------------------------------------------------------
# 接续白名单（allowed_predecessor_ids）
# ---------------------------------------------------------------------------


def _uniform_slots(ids_a, ids_b, chains, disturbance=(0, 0)):
    """8 个同构时隙：两条零修正、零扰动指令，chains[t] 为第 t 隙（1 基）白名单。

    chains 是 dict: 1 基时隙号 -> {0: id 列表或 None（A 指令）, 1: ...}。
    """
    slots = []
    for t in range(1, 9):
        ca = (ids_a, "A", (0, 0), 1.0)
        cb = (ids_b, "B", (0, 0), 1.0)
        spec = chains.get(t, {})
        if spec.get(0) is not None:
            ca = (*ca, spec[0])
        if spec.get(1) is not None:
            cb = (*cb, spec[1])
        slots.append(slot(disturbance, ca, cb))
    return slots


def test_restricted_command_must_follow_listed_id():
    """白名单生效：受限指令只能跟在名单中已选编号之后。"""
    # B(id=2) 更省能，但第 2 隙起 B 只允许跟随 id=1（A），故两个 B 不得相邻。
    slots = []
    for t in range(1, 9):
        ca = (1, "A", (0, 0), 1.0)
        cb = (2, "B", (0, 0), 0.1)
        if t >= 2:
            cb = (*cb, [1])
        slots.append(slot((0, 0), ca, cb))
    result = compile_plan(CompileRequest.model_validate(make_request(slots=slots)))
    ids = result["objectives"]["command_id_sequence"]
    # 硬约束：任何 B(2) 的前驱都必须是 A(1)。
    for prev, cur in zip(ids, ids[1:]):
        if cur == 2:
            assert prev == 1
    # 8 隙中最多放 4 个不相邻的 B，四者能耗相同；切换数次之：可把唯一一对
    # 相邻 A 放到最前得到 6 次切换（[B,A,A,B,A,B,A,B]），严格优于交替的 7 次。
    assert ids == [2, 1, 1, 2, 1, 2, 1, 2]
    assert result["objectives"]["total_energy"] == pytest.approx(4 * 1.0 + 4 * 0.1)
    assert result["objectives"]["mode_switches"] == 6


def test_chain_constraint_changes_optimum_vs_unconstrained():
    """接续限制须参与全局最优：若无约束会贪心选 B，限制后被迫选 A。"""
    # B 更便宜，但第 2 隙 B 只允许跟随 id=1（A）；之后再无限制。
    slots = []
    for t in range(1, 9):
        ca = (1, "A", (0, 0), 1.0)
        cb = (2, "B", (0, 0), 0.1)
        if t == 2:
            cb = (*cb, [1])
        slots.append(slot((0, 0), ca, cb))
    result = compile_plan(CompileRequest.model_validate(make_request(slots=slots)))
    ids = result["objectives"]["command_id_sequence"]
    # 两种走法能耗并列（1.7）：
    #   第 1 隙 A(1.0) -> 第 2 隙 B(0.1) -> 全 B：切换 1 次；
    #   第 1 隙 B(0.1) -> 第 2 隙被迫 A(1.0) -> 全 B：切换 2 次。
    # 故接续限制迫使第 1 隙选 A，第 2 隙起全 B。
    assert ids == [1] + [2] * 7
    assert result["objectives"]["total_energy"] == pytest.approx(1.7)
    assert result["objectives"]["mode_switches"] == 1


def test_chain_feasible_sequence_respected():
    """名单可含多个编号，任一命中即可接续。"""
    slots = _uniform_slots(
        1,
        2,
        {
            2: {0: [2], 1: [1, 2]},  # 第 2 隙 A 只能跟 2；B 可跟 1 或 2
            3: {0: [2], 1: [1]},
        },
    )
    result = compile_plan(CompileRequest.model_validate(make_request(slots=slots)))
    ids = result["objectives"]["command_id_sequence"]
    assert ids[1] in (1, 2)  # 第 2 隙：跟 2 时 A(1)、跟 1/2 时 B(2)
    # 第 3 隙若选 A(1) 需要第 2 隙为 2；若选 B(2) 需要第 2 隙为 1。
    assert (ids[1], ids[2]) in {(2, 1), (1, 2), (2, 2)}


def test_broken_chain_is_infeasible_with_diagnostics():
    """接续断链：安全落点可达，但被白名单挡住 → reason=broken_predecessor_chain。

    第 1 隙 id=1 修正 (100,0) 被安全域排除，只有 id=2 可达；第 2 隙两条指令的
    白名单都只含编号 1（编号 1 在第 1 隙确实存在，故输入合法），所有安全落点断链。
    """
    slots = [
        slot((0, 0), (1, "A", (100, 0), 1.0), (2, "B", (0, 0), 1.0)),
        slot((0, 0), (1, "A", (0, 0), 1.0, [1]), (2, "B", (0, 0), 1.0, [1])),
    ] + [
        slot((0, 0), (1, "A", (0, 0), 1.0), (2, "B", (0, 0), 1.0))
        for _ in range(6)
    ]
    req = make_request(
        safety={"x_min": -10, "x_max": 10, "y_min": -10, "y_max": 10},
        slots=slots,
    )
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.reason == "broken_predecessor_chain"
    assert exc.value.last_reachable_slot == 1
    # 第 1 隙仅 id=2 落点 (0,0) 安全：该层唯一可达动量。
    assert exc.value.reachable_states == 1


def test_safety_cutoff_still_reported_as_no_safe_path_with_chains_present():
    """窗口中存在白名单字段，但切断原因是安全域时仍报 no_safe_path。"""
    slots = [
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (-1, 0), 1.0)),
        slot((0, 0), (1, "A", (1, 0), 1.0, [1, 2]), (2, "B", (-1, 0), 1.0, [1, 2])),
    ] + [
        slot((0, 0), (1, "A", (1, 0), 1.0), (2, "B", (-1, 0), 1.0))
        for _ in range(6)
    ]
    req = make_request(
        safety={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        target={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        slots=slots,
    )
    with pytest.raises(InfeasiblePlanError) as exc:
        compile_plan(CompileRequest.model_validate(req))
    assert exc.value.reason == "no_safe_path"
    assert exc.value.last_reachable_slot == 0


def test_chain_matches_brute_force():
    """固定含白名单场景与穷举参考对拍。"""
    slots = _uniform_slots(
        1,
        2,
        {
            2: {1: [1]},
            3: {0: [2], 1: [1]},
            5: {0: [1], 1: [2]},
            7: {1: [1, 2]},
        },
    )
    req = make_request(slots=slots)
    expected = brute_force_best(req)
    result = compile_plan(CompileRequest.model_validate(req))
    got = (
        result["objectives"]["total_energy"],
        result["objectives"]["mode_switches"],
        tuple(result["objectives"]["command_id_sequence"]),
    )
    assert got == expected
