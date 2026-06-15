import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app


SQLALCHEMY_TEST_URL = "sqlite:///./test_purchase_approval.db"

engine = create_engine(SQLALCHEMY_TEST_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def sample_order_payload():
    return {
        "title": "办公设备采购",
        "description": "采购笔记本电脑",
        "applicant": "张三",
        "department": "技术部",
        "items": [
            {"name": "笔记本电脑", "quantity": 5, "unit_price": 8000, "specification": "ThinkPad"},
            {"name": "显示器", "quantity": 5, "unit_price": 2000, "specification": "27寸"},
        ],
    }


@pytest.fixture
def create_and_submit_order(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")
    return order_id
