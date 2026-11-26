import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.guided_notes import SheetAccessError, SheetOption
from app.domain.guided_notes import MergeRange, SheetSnapshot
from app.web.google_oauth import Flow


def make_sheet(sheet_id, title, cells, merges, *, spreadsheet_id="test_sheet", columns=12):
    rows = [[] for _ in range(max(row for row, _ in cells) + 1)]
    for (row, column), value in cells.items():
        rows[row].extend([None] * max(0, column + 1 - len(rows[row])))
        rows[row][column] = value
    return SheetSnapshot(spreadsheet_id, sheet_id, title, tuple(map(tuple, rows)),
                         tuple(MergeRange(*merge) for merge in merges), columns)


@pytest.fixture
def single_sheet():
    rows = range(4, 15, 2)
    cells = {(3, 1): "題號", (3, 2): "題目", (3, 6): "作答"}
    cells.update({(row, 1): number for number, row in enumerate(rows, 1)})
    merges = [(3, 2, 4, 6), (3, 6, 4, 8)]
    merges += [(row, 2, row + 1, 6) for row in rows]
    merges += [(row, 6, row + 1, 8) for row in rows]
    merges += [(row, 1, row + 1, 8) for row in range(5, 14, 2)]
    return make_sheet(1, "單題", cells, merges)


@pytest.fixture
def mixed_sheet():
    cells = {(2, 1): " 題號 ", (2, 6): "作答", (4, 1): 1.0, (7, 1): " 2 ",
             (10, 1): 3, (12, 1): "4", (4, 6): "（1）", (5, 6): "（2）",
             (7, 6): "(1)", (8, 6): "(2)"}
    return make_sheet(0, "混合", cells, [
        (2, 6, 3, 8), (4, 1, 6, 2), (7, 1, 9, 2), (10, 6, 11, 8), (12, 6, 13, 8),
    ])


@pytest.fixture
def update_cell():
    def update(sheet, row, column, value):
        rows = [list(values) for values in sheet.values]
        rows.extend([] for _ in range(row + 1 - len(rows)))
        rows[row].extend([None] * (column + 1 - len(rows[row])))
        rows[row][column] = value
        return replace(sheet, values=tuple(map(tuple, rows)))

    return update


class FakeWorksheetReader:
    def __init__(self, *sheets):
        self.sheets = {sheet.sheet_id: sheet for sheet in sheets}
        self.reads = []

    def list_sheets(self, spreadsheet_id):
        if not any(sheet.spreadsheet_id == spreadsheet_id for sheet in self.sheets.values()):
            raise SheetAccessError(403, "無法讀取所選試算表，請檢查 Google 帳號與檔案權限。")
        return tuple(SheetOption(sheet.sheet_id, sheet.title) for sheet in self.sheets.values()
                     if sheet.spreadsheet_id == spreadsheet_id)

    def read_sheet(self, spreadsheet_id, sheet_id):
        self.reads.append((spreadsheet_id, sheet_id))
        return self.sheets[sheet_id]


@pytest.fixture
def fake_worksheet_reader():
    return FakeWorksheetReader


@pytest.fixture
def google_settings(tmp_path, monkeypatch):
    client_file = tmp_path / "client.json"
    client_file.write_text(json.dumps({"web": {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}), encoding="utf-8")
    monkeypatch.setenv("BUZZER_GOOGLE_OAUTH_CLIENT_FILE", str(client_file))
    monkeypatch.setenv("BUZZER_GOOGLE_PICKER_KEY", "test-picker-key")
    monkeypatch.setenv("BUZZER_GOOGLE_PROJECT_NUMBER", "123")

    def fetch_token(flow, *, code, timeout):
        assert code in ("test-code", "expired-code") and timeout == 15
        seconds = -60 if code == "expired-code" else 3600
        flow.oauth2session.token = {
            "access_token": "test-token",
            "expires_at": (datetime.now(UTC) + timedelta(seconds=seconds)).timestamp(),
        }

    monkeypatch.setattr(Flow, "fetch_token", fetch_token)
