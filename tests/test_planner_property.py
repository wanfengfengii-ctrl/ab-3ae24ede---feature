"""随机对拍：DP 结果必须与独立穷举参考完全一致。"""

import random

import pytest

from app.models import CompileRequest
from app.planner import InfeasiblePlanError, compile_plan

from .conftest import brute_force_best, make_request


def _random_request(rng: random.Random, n_slots: int, n_cmds: int) -> dict:
    modes = ["A", "B", "C"]
    slots = []
    for _ in range(n_slots):
        ids = rng.sample(range(1, 100), n_cmds)
        commands = []
        for cid in ids:
            commands.append(
                {
                    "id": cid,
                    "mode": rng.choice(modes),
                    "correction": [rng.randint(-3, 3), rng.randint(-3, 3)],
                    "energy": rng.choice([0.0, 0.5, 1.0, 2.0, 5.0]),
                }
            )
        slots.append(
            {
                "disturbance": [rng.randint(-2, 2), rng.randint(-2, 2)],
                "commands": commands,
            }
        )
    bound = rng.randint(4, 12)
    return make_request(
        initial=(rng.randint(-2, 2), rng.randint(-2, 2)),
        safety={
            "x_min": -bound,
            "x_max": bound,
            "y_min": -bound,
            "y_max": bound,
        },
        target={
            "x_min": rng.randint(-bound, 0),
            "x_max": rng.randint(0, bound),
            "y_min": rng.randint(-bound, 0),
            "y_max": rng.randint(0, bound),
        },
        slots=slots,
    )


@pytest.mark.parametrize("seed", range(60))
def test_dp_matches_brute_force(seed):
    rng = random.Random(seed)
    # 固定 8 隙（穷举规模 2^8~3^8），指令数随机 2~3。
    n_slots = 8
    n_cmds = rng.randint(2, 3)
    req = _random_request(rng, n_slots, n_cmds)
    # 若目标域不包含于安全域，语义校验会拒绝；对拍仅关心规划器，直接缩窄。
    s, t = req["safety_region"], req["target_region"]
    t["x_min"] = max(t["x_min"], s["x_min"])
    t["x_max"] = min(t["x_max"], s["x_max"])
    t["y_min"] = max(t["y_min"], s["y_min"])
    t["y_max"] = min(t["y_max"], s["y_max"])
    req["initial_momentum"] = [
        min(max(req["initial_momentum"][0], s["x_min"]), s["x_max"]),
        min(max(req["initial_momentum"][1], s["y_min"]), s["y_max"]),
    ]

    expected = brute_force_best(req)
    try:
        result = compile_plan(CompileRequest.model_validate(req))
    except InfeasiblePlanError:
        assert expected is None
        return

    assert expected is not None
    got = (
        result["objectives"]["total_energy"],
        result["objectives"]["mode_switches"],
        tuple(result["objectives"]["command_id_sequence"]),
    )
    assert got == expected
