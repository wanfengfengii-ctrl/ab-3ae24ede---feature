"""请求语义校验：把“输入矛盾”精确定位到字段。

Pydantic 负责类型与基数（8–20 时隙、每时隙 2–5 条指令、二维整数向量、
非负能耗等），本模块补充跨字段约束：矩形边界顺序、目标域包含于安全域、
初始动量位于安全域、时隙内指令编号唯一。
"""

from .models import CompileRequest


class SemanticError(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _rect_bounds_ok(rect) -> bool:
    return rect.x_min <= rect.x_max and rect.y_min <= rect.y_max


def validate_semantics(req: CompileRequest) -> None:
    safety = req.safety_region
    target = req.target_region

    if not _rect_bounds_ok(safety):
        raise SemanticError(
            "safety_region",
            "矩形边界矛盾：需要满足 x_min <= x_max 且 y_min <= y_max",
        )
    if not _rect_bounds_ok(target):
        raise SemanticError(
            "target_region",
            "矩形边界矛盾：需要满足 x_min <= x_max 且 y_min <= y_max",
        )

    # 末态必须同时位于安全域与目标域，因此目标域必须包含于安全域，
    # 否则属于输入自相矛盾，而非“真实无解”。
    if not (
        safety.x_min <= target.x_min
        and target.x_max <= safety.x_max
        and safety.y_min <= target.y_min
        and target.y_max <= safety.y_max
    ):
        raise SemanticError(
            "target_region",
            "目标矩形必须包含于统一安全域内，否则末态约束互相矛盾",
        )

    ix, iy = req.initial_momentum
    if not (
        safety.x_min <= ix <= safety.x_max and safety.y_min <= iy <= safety.y_max
    ):
        raise SemanticError(
            "initial_momentum",
            "初始动量必须位于安全域内",
        )

    for t, slot in enumerate(req.slots):
        seen: set[int] = set()
        for c, cmd in enumerate(slot.commands):
            if cmd.id in seen:
                raise SemanticError(
                    f"slots[{t}].commands[{c}].id",
                    f"时隙 {t}（0 基）内指令编号 {cmd.id} 重复，编号须在时隙内唯一",
                )
            seen.add(cmd.id)
