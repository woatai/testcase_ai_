"""验证 HTTP 存活检查与文本需求标准化接口。"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import testcase_ai.api as api_module


class FakeApplication:
    settings = SimpleNamespace(max_upload_bytes=1024)

    def initialize(self) -> None:
        pass


def test_health_and_text_requirement_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "get_application", lambda: FakeApplication())
    with TestClient(api_module.api) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post(
            "/api/v1/requirements",
            json={"title": "退款需求", "content": "订单退款后返还优惠券"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "退款需求"
