"""FastAPI 应用入口。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import CompileRequest, CompileResponse
from .planner import InfeasiblePlanError, compile_plan
from .validation import SemanticError, validate_semantics

app = FastAPI(
    title="动量卸载整窗计划编译服务",
    version="1.0.0",
)


@app.exception_handler(SemanticError)
async def _semantic_error_handler(_request: Request, exc: SemanticError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_request",
            "message": exc.message,
            "field": exc.field,
        },
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    # 定位到具体字段（如 slots[3].commands[0].energy），方便值班员区分参数错误。
    details = []
    for err in exc.errors():
        loc = [str(part) for part in err["loc"] if part != "body"]
        details.append(
            {
                "field": ".".join(loc) if loc else "body",
                "type": err["type"],
                "message": err["msg"],
            }
        )
    return JSONResponse(
        status_code=422,
        content={"error": "invalid_request", "details": details},
    )


@app.exception_handler(InfeasiblePlanError)
async def _infeasible_handler(
    _request: Request, exc: InfeasiblePlanError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": "no_feasible_plan",
            "reason": exc.reason,
            "message": "不存在满足安全域与末态目标的完整指令序列",
            "last_reachable_slot": exc.last_reachable_slot,
            "reachable_states": exc.reachable_states,
        },
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/momentum-plans/compile", response_model=CompileResponse)
async def compile_momentum_plan(request: CompileRequest) -> dict:
    validate_semantics(request)
    return compile_plan(request)
