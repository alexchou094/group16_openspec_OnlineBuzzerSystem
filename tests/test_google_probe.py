import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.adapters import google_probe as adapter
from app.web import google_probe as web


@pytest.fixture
def client(tmp_path):
    client_file = tmp_path / "client.json"
    client_file.write_text(json.dumps({"web": {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}), encoding="utf-8")
    app = web.create_app(web.Config(str(client_file), "test-picker-key", "123", "test-copy"))
    with TestClient(app, base_url=web.ORIGIN) as client:
        index = client.get("/")
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', index.text).group(1)
        client.headers.update({"X-CSRF-Token": csrf, "Origin": web.ORIGIN})
        yield client


def start(client):
    response = client.post("/oauth/start")
    assert response.status_code == 200
    query = parse_qs(urlsplit(response.json()["url"]).query)
    assert query["scope"] == [adapter.SCOPE]
    assert query["redirect_uri"] == [web.CALLBACK]
    assert query["code_challenge_method"] == ["S256"]
    return query["state"][0]


def authorize(client, monkeypatch, *, expired=False):
    def fetch_token(flow, *, code, timeout):
        assert code == "test-code" and timeout == 15
        delta = timedelta(seconds=-30 if expired else 3600)
        flow.oauth2session.token = {
            "access_token": "test-token",
            "expires_at": (datetime.now(UTC) + delta).timestamp(),
        }

    monkeypatch.setattr(web.Flow, "fetch_token", fetch_token)
    response = client.get("/oauth/callback", params={"state": start(client), "code": "test-code"})
    assert "Google 授權成功" in response.text


def test_denied_oauth_consumes_valid_state_only(client):
    assert client.post("/oauth/start", headers={"Origin": "http://localhost:8001"}).status_code == 403
    state = start(client)
    assert client.get("/oauth/callback", params={"state": "wrong", "error": "access_denied"}).status_code == 400
    query = {"state": state, "error": "access_denied"}
    with TestClient(client.app, base_url=web.ORIGIN) as other:
        assert other.get("/oauth/callback", params=query).status_code == 400
    assert "Google 授權已拒絕或取消" in client.get("/oauth/callback", params=query).text
    assert client.get("/oauth/callback", params=query).status_code == 400
    assert client.post("/picker").status_code == 401


def test_expired_token_never_reaches_google(client, monkeypatch):
    authorize(client, monkeypatch, expired=True)
    monkeypatch.setattr(adapter.requests, "request", lambda *args, **kwargs: pytest.fail("unexpected request"))
    result = client.post("/select", json={"spreadsheet_id": "test-copy"})
    assert result.status_code == 401
    assert "已過期" in result.json()["detail"]


@pytest.mark.parametrize("write_status, original, expected", [
    (200, "", 200),
    (403, "", 403),
    (200, "keep me", 409),
])
def test_picker_allowlist_and_raw_write(client, monkeypatch, write_status, original, expected):
    authorize(client, monkeypatch)
    assert client.post("/write").status_code == 409
    assert client.post("/picker").json()["token"] == "test-token"
    calls = []
    merges = [{"startColumnIndex": 2, "endColumnIndex": 4, "endRowIndex": 1}]

    class GoogleResponse:
        def __init__(self, status_code, result):
            self.status_code = status_code
            self.result = result

        def json(self):
            return self.result

    def request(method, url, *, headers, params, json, timeout, allow_redirects):
        assert "/spreadsheets/test-copy" in url and timeout == 15 and not allow_redirects
        assert headers["Authorization"] == "Bearer test-token"
        calls.append((method, url, params))
        if "fields" in params:
            assert "merges" in params["fields"]
            return GoogleResponse(200, {"sheets": [{"properties": {
                "title": adapter.PROBE_SHEET, "sheetId": 123,
            }, "merges": merges}]})
        if url.endswith("A1%3AD1"):
            return GoogleResponse(200, {"values": [[adapter.MARKER, original, "合併格示範"]]})
        assert url.endswith("%21B1")
        if method == "PUT":
            assert params == {"valueInputOption": "RAW"} and json == {"values": [["=1+2"]]}
            return GoogleResponse(write_status, {})
        assert params == {"valueRenderOption": "UNFORMATTED_VALUE"}
        return GoogleResponse(200, {"values": [["=1+2"]]})

    monkeypatch.setattr(adapter.requests, "request", request)
    assert client.post("/select", json={"spreadsheet_id": "another-file"}).status_code == 403
    assert not calls
    selected = client.post("/select", json={"spreadsheet_id": "test-copy"})
    assert selected.status_code == 200 and selected.json()["merges"] == merges
    result = client.post("/write")
    assert result.status_code == expected
    if expected == 200:
        assert result.json()["B1"] == "=1+2"
        assert calls[-1][0] == "GET" and calls[-1][1].endswith("%21B1")
    elif expected == 403:
        assert "權限" in result.json()["detail"]
        assert calls[-1][0] == "PUT"
    else:
        assert all(method == "GET" for method, _, _ in calls)
