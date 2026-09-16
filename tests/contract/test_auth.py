from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agico_kb.config import Settings
from agico_kb.main import create_app


@pytest.mark.parametrize("path", ["/v1/catalog", "/v1/documents", "/v1/submissions"])
def test_anonymous_cannot_access_business_routes(path):
    # A missing auth guard would expose business records; DB is unnecessary for rejection.
    app = create_app(Settings("postgresql://unused", Path("unused")))
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 401
