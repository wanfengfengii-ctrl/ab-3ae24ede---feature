"""HTTP 层：成功、参数错误定位、无解诊断。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

from .conftest import eight_slots, make_request, slot

client = TestClient(app)


def _compile(body):
    return client.post("/api/momentum-plans/compile", json=body)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_compile_success_payload():
    body = make_request(slots=eight_slots())
    r = _compile(body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["feasible"] is True
    assert set(data) >= {
        "selected",
        "momentums",
        "final_momentum",
        "objectives",
        "slot_count",
    }
    obj = data["objectives"]
    assert set(obj) == {"total_energy", "mode_switches", "command_id_sequence"}
    sel = data["selected"][0]
    assert set(sel) == {"slot", "command_id", "mode", "correction", "energy"}


def test_tie_breaking_feasible_case():
    """并列裁决：完全同构的两条指令，选编号更小的且零切换。"""
    body = make_request(
        slots=[
            slot((0, 0), (7, "A", (0, 0), 1.0), (3, "A", (0, 0), 1.0))
        ]
        * 8
    )
    r = _compile(body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["objectives"]["command_id_sequence"] == [3] * 8
    assert data["objectives"]["mode_switches"] == 0
    assert data["objectives"]["total_energy"] == 8.0


def test_infeasible_returns_last_reachable_slot():
    # 第一隙结果为 (±1, 0)，不可能落在仅含原点的安全域。
    body = make_request(
        safety={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        target={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        slots=eight_slots(),
    )
    r = _compile(body)
    assert r.status_code == 409
    data = r.json()
    assert data["error"] == "no_feasible_plan"
    assert "last_reachable_slot" in data
    assert "reachable_states" in data
    assert isinstance(data["last_reachable_slot"], int)
    assert isinstance(data["reachable_states"], int)


def test_missing_field_is_located():
    body = make_request(slots=eight_slots())
    del body["slots"][0]["commands"][0]["energy"]
    r = _compile(body)
    assert r.status_code == 422
    data = r.json()
    assert data["error"] == "invalid_request"
    fields = [d["field"] for d in data["details"]]
    assert any(f.startswith("slots.0.commands.0") for f in fields)


def test_wrong_type_is_located():
    body = make_request(slots=eight_slots())
    body["initial_momentum"] = [0, "x"]
    r = _compile(body)
    assert r.status_code == 422
    fields = [d["field"] for d in r.json()["details"]]
    assert "initial_momentum.1" in fields


def test_2d_vector_must_have_length_two():
    body = make_request(slots=eight_slots())
    body["initial_momentum"] = [0, 0, 0]
    r = _compile(body)
    assert r.status_code == 422


def test_slot_count_bounds():
    body = make_request(slots=eight_slots()[:7])
    r = _compile(body)
    assert r.status_code == 422
    assert any("slots" in d["field"] for d in r.json()["details"])


def test_commands_per_slot_bounds():
    too_few = eight_slots()
    too_few[0]["commands"] = too_few[0]["commands"][:1]
    r = _compile(make_request(slots=too_few))
    assert r.status_code == 422

    too_many = eight_slots()
    base = too_many[0]["commands"][0]
    too_many[0]["commands"] = [
        {**base, "id": 100 + i} for i in range(6)
    ]
    r = _compile(make_request(slots=too_many))
    assert r.status_code == 422


def test_negative_energy_rejected():
    body = make_request(slots=eight_slots())
    body["slots"][2]["commands"][1]["energy"] = -0.01
    r = _compile(body)
    assert r.status_code == 422


def test_non_finite_energy_rejected():
    body = make_request(slots=eight_slots())
    import json
    body["slots"][2]["commands"][1]["energy"] = "NaN"
    raw = json.dumps(body).replace('"NaN"', "NaN")
    r = client.post(
        "/api/momentum-plans/compile",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert any("energy" in d["field"] for d in r.json()["details"])


def test_malformed_json_is_client_error():
    r = client.post(
        "/api/momentum-plans/compile",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert 400 <= r.status_code < 500


def test_duplicate_command_id_is_located():
    body = make_request(slots=eight_slots())
    body["slots"][1]["commands"][1]["id"] = body["slots"][1]["commands"][0]["id"]
    r = _compile(body)
    assert r.status_code == 422
    data = r.json()
    assert data["error"] == "invalid_request"
    assert data["field"].startswith("slots[1].commands[1].id")


def test_target_outside_safety_is_contradiction():
    body = make_request(
        safety={"x_min": -5, "x_max": 5, "y_min": -5, "y_max": 5},
        target={"x_min": 6, "x_max": 10, "y_min": 6, "y_max": 10},
        slots=eight_slots(),
    )
    r = _compile(body)
    assert r.status_code == 422
    assert r.json()["field"] == "target_region"


def test_initial_outside_safety_is_contradiction():
    body = make_request(initial=(101, 0), slots=eight_slots())
    r = _compile(body)
    assert r.status_code == 422
    assert r.json()["field"] == "initial_momentum"


def test_bad_rectangle_bounds_are_located():
    body = make_request(slots=eight_slots())
    body["safety_region"] = {"x_min": 5, "x_max": -5, "y_min": -5, "y_max": 5}
    r = _compile(body)
    assert r.status_code == 422
    assert r.json()["field"] == "safety_region"


def test_unknown_field_rejected():
    body = make_request(slots=eight_slots())
    body["slots"][0]["nonsense"] = 1
    r = _compile(body)
    assert r.status_code == 422


def test_parameter_error_differs_from_real_infeasibility():
    """值班员视角：422 参数错误 vs 409 真实无解。"""
    bad_param = make_request(slots=eight_slots())
    bad_param["initial_momentum"] = ["?", 0]
    assert _compile(bad_param).status_code == 422

    infeasible = make_request(
        safety={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        target={"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0},
        slots=eight_slots(),
    )
    assert _compile(infeasible).status_code == 409
