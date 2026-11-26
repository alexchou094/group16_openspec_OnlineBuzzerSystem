from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.adapters import guided_notes as google
from app.domain.guided_notes import MergeRange
from app.main import create_app
from app.web.guided_notes import CALLBACK, OAUTH_COOKIE


ORIGIN = "http://testserver"


def identity(client):
    return {"Origin": ORIGIN, "X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}


def test_oauth_state_role_and_expiry(google_settings):
    app = create_app(hosts=("testserver",))
    with TestClient(app) as teacher, TestClient(app) as student:
        teacher_headers, student_headers = identity(teacher), identity(student)
        activity = teacher.post("/activities", headers=teacher_headers, json={"group_limit": 1}).json()
        path = f"/activities/{activity['id']}/import"
        student.post("/activities/join", headers=student_headers,
                     json={"code": activity["code"], "group_number": 1}).raise_for_status()
        assert student.get(path).status_code == 403
        for route, body in [("oauth", None), ("picker", None), ("select", {"spreadsheet_id": "test_sheet"})]:
            assert student.post(f"{path}/{route}", headers=student_headers, json=body).status_code == 403
        assert teacher.post(path + "/oauth").status_code == 403

        def start():
            response = teacher.post(path + "/oauth", headers=teacher_headers)
            query = parse_qs(urlsplit(response.json()["url"]).query)
            assert query["redirect_uri"] == [ORIGIN + CALLBACK]
            assert query["scope"] == ["https://www.googleapis.com/auth/drive.file"]
            assert query["code_challenge_method"] == ["S256"]
            cookie = response.headers["set-cookie"].lower()
            assert OAUTH_COOKIE in cookie and "httponly" in cookie and "samesite=lax" in cookie
            return query["state"][0]

        state = start()
        assert teacher.get(CALLBACK, params={"state": "wrong"}).status_code == 400
        assert student.get(CALLBACK, params={"state": state}).status_code == 400
        denied = teacher.get(CALLBACK, params={"state": state, "error": "access_denied"})
        assert denied.status_code == 200 and denied.headers["referrer-policy"] == "no-referrer"
        assert "已拒絕或取消" in teacher.get(path).text
        assert teacher.get(CALLBACK, params={"state": state}).status_code == 400
        assert teacher.post(path + "/picker", headers=teacher_headers).status_code == 401

        teacher.get(CALLBACK, params={"state": start(), "code": "expired-code"}).raise_for_status()
        expired = teacher.post(path + "/picker", headers=teacher_headers)
        assert expired.status_code == 401 and "已過期" in expired.json()["detail"]
        teacher.get(CALLBACK, params={"state": start(), "code": "test-code"}).raise_for_status()
        assert "test-token" not in teacher.get(path).text
        picker = teacher.post(path + "/picker", headers=teacher_headers)
        assert picker.json()["token"] == "test-token" and picker.headers["cache-control"] == "no-store"


def test_google_adapter_reads_sparse_values_and_merges_by_sheet_id(monkeypatch):
    def reply(data, status=200):
        return SimpleNamespace(status_code=status, json=lambda: data)

    def list_request(url, **options):
        assert url.endswith("/spreadsheets/file_id")
        assert options["headers"] == {"Authorization": "Bearer test-token"}
        assert not options["allow_redirects"]
        return reply({"sheets": [{"properties": {"sheetId": 0, "title": "重新命名", "sheetType": "GRID"}}]})

    def read_request(url, **options):
        assert url.endswith("/spreadsheets/file_id:getByDataFilter")
        assert options["json"] == {"dataFilters": [{"gridRange": {"sheetId": 0}}]}
        assert options["params"]["fields"] == (
            "sheets(properties(sheetId,title,gridProperties(columnCount)),merges,"
            "data(startRow,startColumn,rowData(values(effectiveValue,userEnteredValue))))"
        )
        return reply({"sheets": [{
            "properties": {"sheetId": 0, "title": "重新命名", "gridProperties": {"columnCount": 8}},
            "merges": [{"endRowIndex": 2, "endColumnIndex": 1}],
            "data": [{"startRow": 1, "startColumn": 2, "rowData": [{"values": [
                {"effectiveValue": {"numberValue": 1.0}}, {}, {"effectiveValue": {"stringValue": "題號"}},
            ]}]}],
        }]})

    monkeypatch.setattr(google.requests, "get", list_request)
    monkeypatch.setattr(google.requests, "post", read_request)
    reader = google.GoogleWorksheetReader("test-token", datetime.now(UTC) + timedelta(hours=1))
    assert reader.list_sheets("file_id") == (google.SheetOption(0, "重新命名"),)
    sheet = reader.read_sheet("file_id", 0)
    assert sheet.values == ((), (None, None, 1.0, None, "題號"))
    assert sheet.merges == (MergeRange(0, 0, 2, 1),)
    assert sheet.column_count == 8
    with pytest.raises(google.SheetAccessError, match="Google Picker"):
        reader.list_sheets("file/id")

    monkeypatch.setattr(google.requests, "get", lambda *args, **kwargs: reply({"secret": "raw-error"}, 403))
    with pytest.raises(google.SheetAccessError, match="檔案權限") as denied:
        reader.list_sheets("file_id")
    assert "raw-error" not in str(denied.value)
