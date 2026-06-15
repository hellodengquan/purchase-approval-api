import os


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_purchase_order(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "办公设备采购"
    assert data["status"] == "draft"
    assert data["total_amount"] == 50000.0
    assert len(data["items"]) == 2
    assert data["idempotency_key"] is None


def test_create_order_with_idempotency_key(client, sample_order_payload):
    payload = {**sample_order_payload, "idempotency_key": "unique-key-001"}
    resp1 = client.post("/api/purchase-orders/", json=payload)
    assert resp1.status_code == 201

    resp2 = client.post("/api/purchase-orders/", json=payload)
    assert resp2.status_code == 201
    assert resp2.json()["id"] == resp1.json()["id"]


def test_idempotency_key_prevents_duplicate(client, sample_order_payload):
    payload = {**sample_order_payload, "idempotency_key": "key-dup"}
    r1 = client.post("/api/purchase-orders/", json=payload)
    r2 = client.post("/api/purchase-orders/", json=payload)
    assert r1.json()["id"] == r2.json()["id"]


def test_submit_purchase_order(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]

    resp = client.post(f"/api/purchase-orders/{order_id}/submit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert len(data["nodes"]) > 0
    assert data["nodes"][0]["status"] == "pending"
    assert data["current_node_id"] == data["nodes"][0]["id"]


def test_submit_non_draft_fails(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/submit")
    assert resp.status_code == 400


def test_full_sequential_approval_flow(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "李经理", "action": "approve", "comment": "同意"})
    assert resp.json()["status"] == "pending"

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "王总监", "action": "approve", "comment": "同意"})
    assert resp.json()["status"] == "pending"

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "赵副总裁", "action": "approve", "comment": "同意"})
    assert resp.json()["status"] == "approved"
    assert resp.json()["current_node_id"] is None


def test_reject_flow(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "李经理", "action": "reject", "comment": "不同意"})
    assert resp.json()["status"] == "rejected"


def test_countersign_flow(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/countersign",
                       json={"initiator": "李经理", "approvers": ["李经理", "陈副经理"], "comment": "需会签"})
    assert resp.status_code == 200
    node = [n for n in resp.json()["nodes"] if n["status"] == "pending"][0]
    assert node["mode"] == "countersign"

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "陈副经理", "action": "approve", "comment": "同意"})
    assert resp.json()["status"] == "pending"

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "李经理", "action": "approve", "comment": "同意"})
    assert resp.json()["nodes"][0]["status"] == "approved"


def test_add_sign_flow(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/add-sign",
                       json={"approver": "李经理", "added_approver": "周专家", "comment": "需专家意见"})
    assert resp.status_code == 200
    node = [n for n in resp.json()["nodes"] if n["status"] == "pending"][0]
    assert node["mode"] == "countersign"

    approver_names = [a["approver"] for a in node["approvers"]]
    assert "李经理" in approver_names
    assert "周专家" in approver_names

    client.post(f"/api/purchase-orders/{order_id}/approve",
                json={"approver": "李经理", "action": "approve"})
    client.post(f"/api/purchase-orders/{order_id}/approve",
                json={"approver": "周专家", "action": "approve"})

    resp = client.get(f"/api/purchase-orders/{order_id}")
    assert resp.json()["nodes"][0]["status"] == "approved"


def test_parallel_sign_flow(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/parallel-sign",
                       json={"initiator": "李经理", "approvers": ["李经理", "王副经理"], "comment": "并签"})
    assert resp.status_code == 200
    node = [n for n in resp.json()["nodes"] if n["status"] == "pending"][0]
    assert node["mode"] == "parallel"

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "王副经理", "action": "approve", "comment": "同意"})
    node = [n for n in resp.json()["nodes"] if n["id"] == node["id"]][0]
    assert node["status"] == "approved"


def test_skip_flow(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/skip",
                       json={"approver": "管理员", "comment": "跳过经理审批"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"][0]["status"] == "skipped"
    assert data["status"] == "pending"


def test_return_flow(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    client.post(f"/api/purchase-orders/{order_id}/approve",
                json={"approver": "李经理", "action": "approve"})

    resp = client.post(f"/api/purchase-orders/{order_id}/return",
                       json={"approver": "王总监", "comment": "退回修改"})
    assert resp.status_code == 200
    data = resp.json()

    manager_node = [n for n in data["nodes"] if n["level"] == "manager"][0]
    assert manager_node["status"] == "pending"


def test_return_first_level_fails(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/return",
                       json={"approver": "李经理", "comment": "退回"})
    assert resp.status_code == 400


def test_withdraw_flow(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/withdraw",
                       json={"applicant": "张三", "comment": "需要修改"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"
    assert data["current_node_id"] is None
    assert len(data["nodes"]) == 0


def test_withdraw_wrong_applicant_fails(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/withdraw",
                       json={"applicant": "李四", "comment": "冒充"})
    assert resp.status_code == 400


def test_withdraw_then_resubmit(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    client.post(f"/api/purchase-orders/{order_id}/withdraw",
                json={"applicant": "张三", "comment": "撤回"})

    resp = client.post(f"/api/purchase-orders/{order_id}/submit")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_cancel_order(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]

    resp = client.post(f"/api/purchase-orders/{order_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_get_approval_route(client):
    resp = client.get("/api/purchase-orders/approval-route/preview?amount=3000")
    assert resp.status_code == 200
    data = resp.json()
    assert data["required_levels"] == ["manager"]

    resp = client.get("/api/purchase-orders/approval-route/preview?amount=20000")
    assert resp.json()["required_levels"] == ["manager", "director"]

    resp = client.get("/api/purchase-orders/approval-route/preview?amount=100000")
    assert resp.json()["required_levels"] == ["manager", "director", "vp"]

    resp = client.get("/api/purchase-orders/approval-route/preview?amount=500000")
    assert resp.json()["required_levels"] == ["manager", "director", "vp", "ceo"]


def test_list_purchase_orders(client, sample_order_payload):
    client.post("/api/purchase-orders/", json=sample_order_payload)
    resp = client.get("/api/purchase-orders/")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    resp = client.get("/api/purchase-orders/?status=draft")
    assert len(resp.json()) >= 1


def test_update_draft_order(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]

    resp = client.put(f"/api/purchase-orders/{order_id}",
                      json={"title": "更新后的标题"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "更新后的标题"


def test_update_non_draft_fails(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.put(f"/api/purchase-orders/{order_id}", json={"title": "x"})
    assert resp.status_code == 400


def test_rule_version_create_and_list(client):
    client.get("/api/purchase-orders/approval-route/preview?amount=1000")
    resp = client.post("/api/approval-rules/versions", json={
        "description": "测试规则v2",
        "rules": [
            {"level": "manager", "min_amount": 0, "max_amount": 8000},
            {"level": "director", "min_amount": 8000, "max_amount": 50000},
            {"level": "vp", "min_amount": 50000, "max_amount": 200000},
            {"level": "ceo", "min_amount": 200000, "max_amount": None},
        ]
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_active"] is True
    assert len(data["rules"]) == 4

    resp = client.get("/api/approval-rules/versions")
    assert len(resp.json()) >= 2


def test_rule_version_activate_deactivate(client):
    client.get("/api/purchase-orders/approval-route/preview?amount=1000")
    resp = client.post("/api/approval-rules/versions", json={
        "description": "v3",
        "rules": [
            {"level": "manager", "min_amount": 0, "max_amount": 10000},
            {"level": "director", "min_amount": 10000, "max_amount": None},
        ]
    })
    v3_id = resp.json()["id"]

    resp = client.post(f"/api/approval-rules/versions/{v3_id}/deactivate")
    assert resp.json()["is_active"] is False

    resp = client.post(f"/api/approval-rules/versions/{v3_id}/activate")
    assert resp.json()["is_active"] is True


def test_rule_version_rollback(client):
    client.get("/api/purchase-orders/approval-route/preview?amount=1000")
    resp1 = client.get("/api/approval-rules/active")
    v1_id = resp1.json()["id"]

    resp2 = client.post("/api/approval-rules/versions", json={
        "description": "v2新规则",
        "rules": [
            {"level": "manager", "min_amount": 0, "max_amount": 10000},
            {"level": "director", "min_amount": 10000, "max_amount": None},
        ]
    })
    v2_id = resp2.json()["id"]

    resp = client.post(f"/api/approval-rules/versions/{v1_id}/rollback")
    assert resp.json()["is_active"] is True

    resp = client.get("/api/approval-rules/active")
    assert resp.json()["id"] == v1_id


def test_rule_version_affects_route(client):
    client.get("/api/purchase-orders/approval-route/preview?amount=1000")
    client.post("/api/approval-rules/versions", json={
        "description": "高额阈值",
        "rules": [
            {"level": "manager", "min_amount": 0, "max_amount": 100000},
            {"level": "ceo", "min_amount": 100000, "max_amount": None},
        ]
    })

    resp = client.get("/api/purchase-orders/approval-route/preview?amount=50000")
    assert resp.json()["required_levels"] == ["manager"]


def test_small_amount_single_level_approval(client):
    payload = {
        "title": "小额采购",
        "applicant": "张三",
        "items": [{"name": "文具", "quantity": 10, "unit_price": 50}],
    }
    resp = client.post("/api/purchase-orders/", json=payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "李经理", "action": "approve"})
    assert resp.json()["status"] == "approved"
