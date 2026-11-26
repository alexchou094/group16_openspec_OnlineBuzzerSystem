from datetime import UTC, datetime
from urllib.parse import quote

import requests


SCOPE = "https://www.googleapis.com/auth/drive.file"
PROBE_SHEET = "OAuth 驗證"
MARKER = "BUZZER_OAUTH_TEST_COPY"
RAW_VALUE = "=1+2"


class ProbeError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


class GoogleSheetsProbe:
    def __init__(self, spreadsheet_id: str, token: str, expiry: datetime):
        self.base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        self.token = token
        self.expiry = expiry

    def check_expiry(self):
        now = datetime.now(UTC)
        if self.expiry.tzinfo is None:
            now = now.replace(tzinfo=None)
        if self.expiry <= now:
            raise ProbeError(401, "Google 授權已過期，請重新授權並選檔。")

    def request(self, path: str = "", *, params: dict, values: list | None = None) -> dict:
        self.check_expiry()
        try:
            response = requests.request(
                "PUT" if values is not None else "GET",
                self.base_url + path,
                headers={"Authorization": f"Bearer {self.token}"},
                params=params,
                json={"values": values} if values is not None else None,
                timeout=15,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise ProbeError(502, "無法確認 Google Sheets 回應，請重新讀取驗證格。") from None
        if response.status_code == 401:
            raise ProbeError(401, "Google 授權已過期或已撤銷，請重新授權並選檔。")
        if response.status_code in (403, 404):
            raise ProbeError(403, "Google 拒絕存取，請確認測試帳號及分頁編輯權限。")
        if response.status_code != 200:
            raise ProbeError(502, "Google Sheets 回應失敗，請重新讀取驗證格。")
        try:
            return response.json()
        except ValueError:
            raise ProbeError(502, "無法解析 Google Sheets 回應。") from None

    def value_path(self, cells: str) -> str:
        return "/values/" + quote(f"'{PROBE_SHEET}'!{cells}", safe="")

    def read(self) -> dict:
        sheets = self.request(params={"fields": "sheets(properties(sheetId,title),merges)"})
        sheet = next((item for item in sheets.get("sheets", [])
                      if item["properties"]["title"] == PROBE_SHEET), None)
        if sheet is None:
            raise ProbeError(409, f"測試複本缺少「{PROBE_SHEET}」分頁。")
        values = self.request(self.value_path("A1:D1"),
                              params={"valueRenderOption": "UNFORMATTED_VALUE"}).get("values", [])
        return {"sheet_id": sheet["properties"]["sheetId"], "values": values,
                "merges": sheet.get("merges", [])}

    def write_and_verify(self) -> dict:
        before = self.read()
        row = next(iter(before["values"]), [])
        target = row[1] if len(row) > 1 else ""
        target_merged = any(
            merge.get("startRowIndex", 0) == 0
            and merge.get("startColumnIndex", 0) <= 1 < merge["endColumnIndex"]
            for merge in before["merges"]
        )
        if not row or row[0] != MARKER or target not in ("", RAW_VALUE) or target_merged:
            raise ProbeError(409, "請確認 A1 測試標記，並保持 B1 未合併且為空白或原測試值。")
        path = self.value_path("B1")
        self.request(path, params={"valueInputOption": "RAW"}, values=[[RAW_VALUE]])
        result = self.request(path, params={"valueRenderOption": "UNFORMATTED_VALUE"})
        if result.get("values") != [[RAW_VALUE]]:
            raise ProbeError(502, "B1 讀回與預期文字不符。")
        return {"B1": RAW_VALUE, "核對": "RAW 寫入並讀回成功"}
