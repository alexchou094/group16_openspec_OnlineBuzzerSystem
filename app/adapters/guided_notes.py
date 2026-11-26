import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import requests

from app.domain.guided_notes import MergeRange, SheetSnapshot, address


class SheetAccessError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class SheetWriteUncertain(Exception):
    pass


@dataclass(frozen=True)
class SheetOption:
    id: int
    title: str


class WorksheetReader(Protocol):
    def list_sheets(self, spreadsheet_id: str) -> tuple[SheetOption, ...]: ...

    def read_sheet(self, spreadsheet_id: str, sheet_id: int) -> SheetSnapshot: ...


class WorksheetWriter(Protocol):
    def read_sheet(self, spreadsheet_id: str, sheet_id: int) -> SheetSnapshot: ...

    def write_answers(self, sheet: SheetSnapshot, answers: tuple[tuple[str, str], ...]) -> None: ...


class GoogleWorksheetReader:
    def __init__(self, token: str, expiry: datetime):
        self.token = token
        self.expiry = expiry

    def check_expiry(self):
        now = datetime.now(UTC)
        if self.expiry.tzinfo is None:
            now = now.replace(tzinfo=None)
        if self.expiry <= now:
            raise SheetAccessError(401, "Google 授權已過期，請重新授權。")

    def write_answers(self, sheet: SheetSnapshot, answers: tuple[tuple[str, str], ...]) -> None:
        self.check_expiry()
        title = sheet.title.replace("'", "''")
        try:
            response = requests.post(
                f"https://sheets.googleapis.com/v4/spreadsheets/{sheet.spreadsheet_id}/values:batchUpdate",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"valueInputOption": "RAW", "data": [
                    {"range": f"'{title}'!{target}", "values": [[answer]]}
                    for target, answer in answers
                ]},
                timeout=15, allow_redirects=False,
            )
        except requests.RequestException:
            raise SheetWriteUncertain("Google 寫回連線中斷，請重新核對。") from None
        if response.status_code in (401, 403, 404):
            raise SheetAccessError(response.status_code, "Google 拒絕寫回，請重新授權並確認檔案編輯權限。")
        if response.status_code == 400:
            raise SheetAccessError(422, "Google 拒絕寫回，請確認工作表仍存在且結構未變更。")
        if response.status_code == 429:
            raise SheetAccessError(429, "Google Sheets 暫時限制寫回請求，請稍後重試。")
        if response.status_code != 200:
            raise SheetWriteUncertain("Google 寫回結果未確認，請重新核對。")

    def _request(self, spreadsheet_id: str, fields: str, sheet_id: int | None = None) -> dict:
        self.check_expiry()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", spreadsheet_id):
            raise SheetAccessError(422, "請用 Google Picker 選擇 Google 試算表。")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        try:
            if sheet_id is None:
                response = requests.get(
                    url, headers={"Authorization": f"Bearer {self.token}"},
                    params={"fields": fields}, timeout=15, allow_redirects=False,
                )
            else:
                response = requests.post(
                    url + ":getByDataFilter", headers={"Authorization": f"Bearer {self.token}"},
                    params={"fields": fields}, json={"dataFilters": [{"gridRange": {"sheetId": sheet_id}}]},
                    timeout=15, allow_redirects=False,
                )
        except requests.RequestException:
            raise SheetAccessError(502, "無法連線至 Google Sheets，請稍後重試。") from None
        if response.status_code == 401:
            raise SheetAccessError(401, "Google 授權已過期或撤銷，請重新授權。")
        if response.status_code in (403, 404):
            raise SheetAccessError(403, "無法讀取所選試算表，請檢查 Google 帳號與檔案權限。")
        if response.status_code == 400:
            raise SheetAccessError(422, "無法讀取所選檔案或工作表，請重新選擇 Google 試算表。")
        if response.status_code != 200:
            raise SheetAccessError(502, "Google Sheets 暫時無法讀取，請稍後重試。")
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ValueError:
            raise SheetAccessError(502, "無法解析 Google Sheets 回應。") from None

    def list_sheets(self, spreadsheet_id: str) -> tuple[SheetOption, ...]:
        data = self._request(spreadsheet_id, "sheets(properties(sheetId,title,sheetType))")
        return tuple(SheetOption(p.get("sheetId", 0), p["title"]) for sheet in data.get("sheets", [])
                     if (p := sheet["properties"]).get("sheetType", "GRID") in ("GRID", "DATA_SOURCE"))

    def read_sheet(self, spreadsheet_id: str, sheet_id: int) -> SheetSnapshot:
        data = self._request(
            spreadsheet_id,
            "sheets(properties(sheetId,title,gridProperties(columnCount)),merges,"
            "data(startRow,startColumn,rowData(values(effectiveValue,userEnteredValue))))",
            sheet_id,
        )
        sheet = next((item for item in data.get("sheets", [])
                      if item["properties"].get("sheetId", 0) == sheet_id), None)
        if sheet is None:
            raise SheetAccessError(409, "找不到所選工作表，請重新選檔。")
        rows = []
        literal_text = set()
        for grid in sheet.get("data", []):
            start_row = grid.get("startRow", 0)
            start_column = grid.get("startColumn", 0)
            for row, data_row in enumerate(grid.get("rowData", []), start_row):
                while len(rows) <= row:
                    rows.append([])
                cells = data_row.get("values", [])
                rows[row].extend([None] * max(0, start_column + len(cells) - len(rows[row])))
                for column, cell in enumerate(cells, start_column):
                    effective = cell.get("effectiveValue", {})
                    rows[row][column] = next(iter(effective.values()), None)
                    if "errorValue" in effective:
                        rows[row][column] = "#ERROR!"
                    entered = cell.get("userEnteredValue", {})
                    if "stringValue" in entered:
                        literal_text.add((address(row, column), entered["stringValue"]))
        merges = tuple(MergeRange(
            m.get("startRowIndex", 0), m.get("startColumnIndex", 0),
            m["endRowIndex"], m["endColumnIndex"],
        ) for m in sheet.get("merges", []))
        properties = sheet["properties"]
        return SheetSnapshot(spreadsheet_id, sheet_id, properties["title"],
                             tuple(tuple(row) for row in rows), merges,
                             properties.get("gridProperties", {}).get("columnCount"), frozenset(literal_text))
