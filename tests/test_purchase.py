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
        "min_skip_amount": 100000,
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
    assert data["min_skip_amount"] == 100000
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
    assert resp.json()["version"]["is_active"] is True
    assert resp.json()["affected_pending_orders"] == []
    assert "已回滚" in resp.json()["message"]

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


# ===== 边界测试用例 =====

def test_skip_threshold_validation(client, sample_order_payload):
    payload = {
        "title": "小额采购（低金额）",
        "applicant": "张三",
        "items": [{"name": "文具", "quantity": 20, "unit_price": 100}],
    }
    resp = client.post("/api/purchase-orders/", json=payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/skip",
                       json={"approver": "管理员", "comment": "跳过"})
    assert resp.status_code == 400
    assert "跳级审批仅适用于金额大于等于" in resp.json()["detail"]


def test_skip_high_amount_allowed(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/skip",
                       json={"approver": "管理员", "comment": "跳过经理审批"})
    assert resp.status_code == 200
    assert resp.json()["nodes"][0]["status"] == "skipped"


def test_add_sign_with_backup_approver(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp = client.post(f"/api/purchase-orders/{order_id}/add-sign",
                       json={"approver": "李经理",
                             "added_approver": "周专家",
                             "backup_approver": "赵替补",
                             "comment": "需专家意见"})
    assert resp.status_code == 200
    node = [n for n in resp.json()["nodes"] if n["status"] == "pending"][0]
    approver_data = [a for a in node["approvers"] if a["approver"] == "周专家"][0]
    assert approver_data["backup_approver"] == "赵替补"
    assert approver_data["is_absent"] is False


def test_approve_action_idempotency(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp1 = client.post(f"/api/purchase-orders/{order_id}/approve",
                        json={"approver": "李经理",
                              "action": "approve",
                              "idempotency_key": "approve-key-001"})
    assert resp1.status_code == 200
    initial_approvals_count = len(resp1.json()["approvals"])

    resp2 = client.post(f"/api/purchase-orders/{order_id}/approve",
                        json={"approver": "李经理",
                              "action": "approve",
                              "idempotency_key": "approve-key-001"})
    assert resp2.status_code == 200
    assert len(resp2.json()["approvals"]) == initial_approvals_count


def test_return_action_idempotency(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    client.post(f"/api/purchase-orders/{order_id}/approve",
                json={"approver": "李经理", "action": "approve"})

    resp1 = client.post(f"/api/purchase-orders/{order_id}/return",
                        json={"approver": "王总监",
                              "idempotency_key": "return-key-001"})
    assert resp1.status_code == 200
    initial_approvals_count = len(resp1.json()["approvals"])

    resp2 = client.post(f"/api/purchase-orders/{order_id}/return",
                        json={"approver": "王总监",
                              "idempotency_key": "return-key-001"})
    assert resp2.status_code == 200
    assert len(resp2.json()["approvals"]) == initial_approvals_count


def test_countersign_action_idempotency(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp1 = client.post(f"/api/purchase-orders/{order_id}/countersign",
                        json={"initiator": "李经理",
                              "approvers": ["李经理", "陈副经理"],
                              "idempotency_key": "cs-key-001"})
    assert resp1.status_code == 200
    initial_approvals_count = len(resp1.json()["approvals"])

    resp2 = client.post(f"/api/purchase-orders/{order_id}/countersign",
                        json={"initiator": "李经理",
                              "approvers": ["李经理", "陈副经理"],
                              "idempotency_key": "cs-key-001"})
    assert resp2.status_code == 200
    assert len(resp2.json()["approvals"]) == initial_approvals_count


def test_withdraw_action_idempotency(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp1 = client.post(f"/api/purchase-orders/{order_id}/withdraw",
                        json={"applicant": "张三",
                              "idempotency_key": "withdraw-key-001"})
    assert resp1.status_code == 200
    initial_approvals_count = len(resp1.json()["approvals"])

    resp2 = client.post(f"/api/purchase-orders/{order_id}/withdraw",
                        json={"applicant": "张三",
                              "idempotency_key": "withdraw-key-001"})
    assert resp2.status_code == 200
    assert len(resp2.json()["approvals"]) == initial_approvals_count


def test_rollback_with_pending_orders_cleanup(client, sample_order_payload):
    client.get("/api/purchase-orders/approval-route/preview?amount=1000")
    resp1 = client.get("/api/approval-rules/active")
    v1_id = resp1.json()["id"]

    resp2 = client.post("/api/approval-rules/versions", json={
        "description": "v2临时规则",
        "rules": [
            {"level": "manager", "min_amount": 0, "max_amount": 10000},
            {"level": "director", "min_amount": 10000, "max_amount": None},
        ]
    })
    v2_id = resp2.json()["id"]

    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    order_before = client.get(f"/api/purchase-orders/{order_id}").json()
    assert order_before["status"] == "pending"
    assert order_before["rule_version_id"] == v2_id

    resp = client.post(f"/api/approval-rules/versions/{v1_id}/rollback")
    assert resp.status_code == 200
    assert resp.json()["version"]["is_active"] is True
    assert order_id in resp.json()["affected_pending_orders"]
    assert "进行中的采购单已重置为草稿" in resp.json()["message"]

    order_after = client.get(f"/api/purchase-orders/{order_id}").json()
    assert order_after["status"] == "draft"
    assert order_after["rule_version_id"] is None
    assert order_after["current_node_id"] is None
    assert len(order_after["nodes"]) == 0


def test_rollback_without_pending_orders(client):
    client.get("/api/purchase-orders/approval-route/preview?amount=1000")
    resp1 = client.get("/api/approval-rules/active")
    v1_id = resp1.json()["id"]

    client.post("/api/approval-rules/versions", json={
        "description": "v2临时规则",
        "rules": [
            {"level": "manager", "min_amount": 0, "max_amount": 10000},
        ]
    })

    resp = client.post(f"/api/approval-rules/versions/{v1_id}/rollback")
    assert resp.status_code == 200
    assert resp.json()["affected_pending_orders"] == []
    assert "无进行中的采购单受影响" in resp.json()["message"]


def test_rollback_with_mixed_order_statuses(client, sample_order_payload):
    client.get("/api/purchase-orders/approval-route/preview?amount=1000")
    resp1 = client.get("/api/approval-rules/active")
    v1_id = resp1.json()["id"]

    resp2 = client.post("/api/approval-rules/versions", json={
        "description": "v2临时规则",
        "rules": [
            {"level": "manager", "min_amount": 0, "max_amount": 10000},
            {"level": "director", "min_amount": 10000, "max_amount": None},
        ]
    })
    v2_id = resp2.json()["id"]

    resp_a = client.post("/api/purchase-orders/", json=sample_order_payload)
    pending_id = resp_a.json()["id"]
    client.post(f"/api/purchase-orders/{pending_id}/submit")

    resp_b = client.post("/api/purchase-orders/", json=sample_order_payload)
    draft_id = resp_b.json()["id"]

    resp = client.post(f"/api/purchase-orders/{pending_id}/approve",
                       json={"approver": "李经理", "action": "approve"})
    resp = client.post(f"/api/purchase-orders/{pending_id}/approve",
                       json={"approver": "王总监", "action": "approve"})
    approved_id = pending_id

    resp_c = client.post("/api/purchase-orders/", json=sample_order_payload)
    pending2_id = resp_c.json()["id"]
    client.post(f"/api/purchase-orders/{pending2_id}/submit")

    resp = client.post(f"/api/approval-rules/versions/{v1_id}/rollback")
    assert resp.status_code == 200
    affected = resp.json()["affected_pending_orders"]
    assert pending2_id in affected
    assert approved_id not in affected
    assert draft_id not in affected


def test_add_sign_with_absent_approver_escalation(client, sample_order_payload, db_session):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    client.post(f"/api/purchase-orders/{order_id}/add-sign",
                json={"approver": "李经理",
                      "added_approver": "周专家",
                      "backup_approver": "赵替补",
                      "comment": "需专家意见"})

    from app.models import ApprovalNodeApprover
    approver_entry = db_session.query(ApprovalNodeApprover).filter(
        ApprovalNodeApprover.approver == "周专家"
    ).first()
    assert approver_entry is not None
    approver_entry.is_absent = True
    db_session.commit()

    resp = client.post(f"/api/purchase-orders/{order_id}/approve",
                       json={"approver": "周专家", "action": "approve"})
    assert resp.status_code == 200

    order_data = resp.json()
    node = [n for n in order_data["nodes"] if n["status"] == "pending"][0]
    approver_names = [a["approver"] for a in node["approvers"]]
    assert "赵替补" in approver_names

    expert_entry = [a for a in node["approvers"] if a["approver"] == "周专家"][0]
    assert expert_entry["is_absent"] is True


def test_countersign_concurrent_approval_completes_node(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    client.post(f"/api/purchase-orders/{order_id}/countersign",
                json={"initiator": "李经理",
                      "approvers": ["李经理", "陈副经理", "张委员"],
                      "comment": "三人会签"})

    order_before = client.get(f"/api/purchase-orders/{order_id}").json()
    node_before = [n for n in order_before["nodes"] if n["mode"] == "countersign"][0]
    pending_before = [a for a in node_before["approvers"] if not a["acted"] and not a["is_absent"]]
    assert len(pending_before) == 2

    resp1 = client.post(f"/api/purchase-orders/{order_id}/approve",
                        json={"approver": "陈副经理", "action": "approve"})
    assert resp1.status_code == 200
    order_mid = resp1.json()
    node_mid = [n for n in order_mid["nodes"] if n["mode"] == "countersign"][0]
    assert node_mid["status"] == "pending"

    resp2 = client.post(f"/api/purchase-orders/{order_id}/approve",
                        json={"approver": "张委员", "action": "approve"})
    assert resp2.status_code == 200
    order_after = resp2.json()
    node_after = [n for n in order_after["nodes"] if n["mode"] == "countersign"][0]
    assert node_after["status"] == "approved"


def test_idempotency_key_conflict_same_order(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id1 = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id1}/submit")

    payload2 = {**sample_order_payload, "title": "另一张采购单"}
    resp2 = client.post("/api/purchase-orders/", json=payload2)
    order_id2 = resp2.json()["id"]
    client.post(f"/api/purchase-orders/{order_id2}/submit")

    resp_a = client.post(f"/api/purchase-orders/{order_id1}/approve",
                         json={"approver": "李经理",
                               "idempotency_key": "shared-key-001"})
    assert resp_a.status_code == 200

    resp_b = client.post(f"/api/purchase-orders/{order_id2}/approve",
                         json={"approver": "李经理",
                               "idempotency_key": "shared-key-001"})
    assert resp_b.status_code == 409
    assert "幂等键已被其他请求使用" in resp_b.json()["detail"]


def test_countersign_audit_record_unique(client, sample_order_payload):
    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp1 = client.post(f"/api/purchase-orders/{order_id}/countersign",
                        json={"initiator": "李经理",
                              "approvers": ["李经理", "陈副经理"],
                              "idempotency_key": "audit-unique-001"})
    assert resp1.status_code == 200
    records1 = resp1.json()["approvals"]
    countersign_records1 = [r for r in records1 if r["action_type"] == "countersign"]
    assert len(countersign_records1) == 1

    resp2 = client.post(f"/api/purchase-orders/{order_id}/countersign",
                        json={"initiator": "李经理",
                              "approvers": ["李经理", "陈副经理"],
                              "idempotency_key": "audit-unique-001"})
    assert resp2.status_code == 200
    records2 = resp2.json()["approvals"]
    countersign_records2 = [r for r in records2 if r["action_type"] == "countersign"]
    assert len(countersign_records2) == 1


def test_alembic_downgrade_trap():
    import os
    import re

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for version_file in ["001_initial_v2", "002_add_boundary_fields"]:
        script_path = os.path.join(project_dir, "alembic", "versions", f"{version_file}.py")

        with open(script_path) as f:
            content = f.read()

        downgrade_match = re.search(r"def downgrade\(\) -> None:\s*([\s\S]*?)(?=\ndef |\Z)", content)
        assert downgrade_match, f"Could not find downgrade function in {version_file}"

        downgrade_body = downgrade_match.group(1)
        assert "raise RuntimeError" in downgrade_body, \
            f"Expected RuntimeError in {version_file} downgrade"
        assert "IRREVERSIBLE" in downgrade_body, \
            f"Expected IRREVERSIBLE warning in {version_file} downgrade"


def test_rollback_mid_operation_creates_new_version(client, sample_order_payload):
    client.get("/api/purchase-orders/approval-route/preview?amount=1000")
    resp1 = client.get("/api/approval-rules/active")
    v1_id = resp1.json()["id"]

    resp = client.post("/api/purchase-orders/", json=sample_order_payload)
    order_id = resp.json()["id"]
    client.post(f"/api/purchase-orders/{order_id}/submit")

    resp2 = client.post(f"/api/approval-rules/versions/{v1_id}/rollback")
    assert resp2.status_code == 200

    resp3 = client.post("/api/approval-rules/versions", json={
        "description": "v3中间规则",
        "rules": [
            {"level": "manager", "min_amount": 0, "max_amount": 10000},
            {"level": "ceo", "min_amount": 10000, "max_amount": None},
        ]
    })
    assert resp3.status_code == 201
    assert resp3.json()["is_active"] is True
    assert resp3.json()["version_number"] > v1_id

    active = client.get("/api/approval-rules/active").json()
    assert active["id"] == resp3.json()["id"]
