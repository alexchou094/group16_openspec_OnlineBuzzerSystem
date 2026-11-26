# 第 1 節人工測試

## 1.1–1.3 本機環境與需求對齊

在專案根目錄用 PowerShell 執行 [README 的本機啟動步驟](../../../README.md#本機啟動)。使用新的虛擬環境可以重現乾淨安裝。

| 案例 | 操作 | 預期結果 |
| --- | --- | --- |
| A1 安裝與啟動 | 在新的 Python 3.14 虛擬環境安裝 `requirements-dev.txt`。執行 `python -m pip check`，再依 README 啟動單 worker。 | 依賴檢查無衝突，`http://127.0.0.1:8000` 顯示搶答首頁。 |
| A2 身分與重啟 | 老師建立上限為 2 的活動。另兩個瀏覽器設定檔分別加入第 1、2 組。在老師的同設定檔開第二個活動頁，然後停止並重啟服務。 | 老師分頁共用控制權，學生各自看到本組身分。重啟後舊活動頁失效。 |
| A3 設定與忽略規則 | 執行 `git check-ignore .env credentials/client_secret.json token-test.json`，打開 `.env.example`。 | 指令列出三個假路徑，設定範例只有空值。 |
| A4 對齊 | 開啟 [需求對齊檢核表](requirements-alignment.md)，依兩組規格核對編號、11/22 決議及其他規則。 | C1–C9／E1–E6 皆有驗收位置。組員可在表中記錄確認日期、結論及待確認事項。 |

## 1.4 Google OAuth／Picker

### 準備

1. 在同一個 Google Cloud 專案啟用 Google Picker API、Google Drive API 及 Google Sheets API。若 OAuth 應用處於測試中，將使用的 Google 帳號加為測試使用者。
2. 建立「網頁應用程式」OAuth 用戶端，登記 JavaScript 來源 `http://127.0.0.1:8001` 及重新導向 URI `http://127.0.0.1:8001/oauth/callback`。將下載的 JSON 放在私有目錄。準備同一專案的 Picker API key，設定網站限制為 `http://127.0.0.1:8001/*` 與 `https://docs.google.com/*`，並限制使用 Google Picker API。另記下數字專案編號。
3. 在指定 Google Sheets 測試複本新增「OAuth 驗證」分頁。A1 放入 `BUZZER_OAUTH_TEST_COPY`，B1 留空，合併 C1:D1 並輸入 `合併格示範`。測試期間請保留這些位置，並讓正常案例使用具有編輯權的測試帳號。

將下列範例值換成個人設定，在已安裝依賴的專案根目錄執行。

```powershell
$env:BUZZER_GOOGLE_OAUTH_CLIENT_FILE = 'C:/私有目錄/client_secret.json'
$env:BUZZER_GOOGLE_PICKER_KEY = '填入 Picker API key'
$env:BUZZER_GOOGLE_PROJECT_NUMBER = '填入數字專案編號'
$env:BUZZER_GOOGLE_TEST_SPREADSHEET_ID = '填入測試複本網址中 /d/ 和下一個 / 之間的 ID'
.\.venv\Scripts\python.exe -m uvicorn app.web.google_probe:app --host 127.0.0.1 --port 8001 --workers 1 --no-access-log
```

開啟 `http://127.0.0.1:8001`。測試紀錄只留案例結果或遮蔽後的錯誤文字。

### 案例

| 案例 | 操作 | 預期結果 | 實測 |
| --- | --- | --- | --- |
| G1 選檔與讀取 | 按「授權 Google」並同意。再按「選擇測試複本」，選取指定試算表。 | 顯示 `sheet_id`、A1 測試標記、C1 文字，以及 `merges` 中 C1:D1 的範圍。Sheets 合併座標從零開始，結束欄不含在內，C1:D1 的起訖欄索引為 2、4。 | 通過。使用者提供的讀取回應含 A1、C1 與起訖欄索引 2、4。 |
| G2 RAW 寫入讀回 | B1 留空時按「RAW 寫入並讀回」。到試算表檢查 B1，再按「讀取值和合併範圍」。 | 頁面與試算表 B1 顯示文字 `=1+2`。A1 與 C1:D1 的文字及合併範圍維持原狀。 | 通過（使用者回報） |
| G3 拒絕與取消 | 在獨立瀏覽器設定檔發起授權，在 Google 同意頁拒絕或取消。重新授權成功後，另測關閉 Picker。 | 回到驗證頁時提示已拒絕或取消授權。關閉 Picker 顯示已取消選檔。 | 依使用者指示跳過實測 |
| G4 過期或撤銷 | 完成 G1，頁面和服務保持開啟。等待短效 access token 到期，或在 Google 帳號設定撤銷應用程式的存取權，再按「讀取值和合併範圍」。 | 頁面提示重新授權。記錄本次測到的是到期或撤銷。 | 依使用者指示跳過實測 |
| G5 權限不足 | 使用只有檢視權限的另一測試帳號，在獨立瀏覽器設定檔選取同一測試複本。確認可讀取後按 RAW 寫入。 | Google 拒絕寫入，頁面提示檢查權限，B1 維持原值。 | 通過（使用者回報） |
| G6 其他檔案／非空目標 | 選取其他試算表。再選回指定複本，在 B1 輸入 `保留我` 後嘗試 RAW 寫入。 | 其他檔案被拒絕。指定複本的 B1 非空時顯示安全檢查提示並保留原值。 | 通過（使用者回報） |

Google Cloud 設定及 API 行為可參考 [Google Web Server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)、[Web Picker](https://developers.google.com/workspace/drive/picker/guides/web-picker) 及 [Google Sheets 讀寫值](https://developers.google.com/workspace/sheets/api/guides/values)。

## 本機驗證紀錄

- Windows／Python 3.14.7 的全新虛擬環境已安裝鎖定依賴，`pip check` 未發現依賴衝突。搶答首頁和 Google 驗證頁以各自的單 worker 在 loopback 啟動，兩頁均回傳 HTTP 200。
- 啟用 Chrome 的完整重跑共 40 項通過，包含老師與兩組同步，以及模擬 Google 回應的授權拒絕、過期、測試複本限制、合併範圍、RAW 讀回及權限不足。另確認 Google 驗證頁缺少設定時會顯示設定提示。
- 2026-11-26 在 Google Cloud 專案 `software-515902` 的指定複本完成 G1。使用者回報 G2、G5、G6 通過，並指定跳過 G3、G4 的實測。
