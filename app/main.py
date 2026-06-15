from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import engine, Base
from app.routers.purchase import router, rule_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="采购审批管理系统 API",
    description="实现采购单的申请提交、多级审批流程（含会签/加签/并签/跳级/退回/撤回），金额阈值路由，审批规则版本管理",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(rule_router)


@app.get("/api/health")
def health_check():
    return {"status": "ok"}
