# 課堂搶答系統

本專案包含活動與組別身分、Google Sheets 學習單匯入、多題搶答、逐筆審閱、公平排序、定案寫回與 CSV。整體驗收狀態、已知缺口及五分鐘 demo 見[交付檢核](openspec/changes/build-online-buzzer-system/delivery-checklist.md)，非主要實作者請使用[整合人工驗收表](openspec/changes/build-online-buzzer-system/integration-manual-acceptance.md)。

## 每次啟動

Google Cloud 已設定、依賴已安裝後，每次開啟 PowerShell 都在同一個視窗執行下列指令。將 OAuth JSON 路徑、Picker API key 和數字專案編號換成本機的實際值。

若先前只完成第 1 節的 Google 驗證，請在同一個 OAuth 用戶端一次性新增正式匯入的回呼網址 `http://127.0.0.1:8000/google/oauth/callback`。原驗證工具使用 `http://127.0.0.1:8001/oauth/callback`，兩個網址可以同時保留。其他欄位見[首次設定 Google Cloud 憑證](#首次設定-google-cloud-憑證)。

```powershell
Set-Location 'C:\github repo\group16_openspec_OnlineBuzzerSystem'

$env:BUZZER_GOOGLE_OAUTH_CLIENT_FILE = '本機\client_secret.json'
$env:BUZZER_GOOGLE_PICKER_KEY = 'PICKER_KEY'
$env:BUZZER_GOOGLE_PROJECT_NUMBER = '專案編號'

# 需要載入示範題時，取消下一行的註解。
# $env:BUZZER_DEMO = '1'

.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

啟動後開啟 [搶答系統首頁](http://127.0.0.1:8000)。要匯入學習單，建立活動後按「匯入學習單」→「授權 Google」→「選擇 Google 試算表」。要停止服務，在 PowerShell 按 `Ctrl+C`。

`BUZZER_GOOGLE_TEST_SPREADSHEET_ID` 只供第 1 節的獨立驗證工具使用。若只用示範題，可略過三個 Google 環境變數。

## 首次安裝

開發與測試使用 Python 3.14。在專案根目錄建立虛擬環境並安裝依賴。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

活動和瀏覽器工作階段保存在單一伺服器程序的記憶體。重啟會清空活動，舊活動頁會顯示失效。請使用單 worker，並用不同瀏覽器設定檔或獨立瀏覽器 context 模擬老師、第一組和第二組。同一設定檔的多個分頁會共用身分。伺服器持續運作時，使用保留網站 Cookie 的同一設定檔重開瀏覽器，開啟原活動網址即可恢復老師或學生身分。

獨立驗證工具的啟動與人工測試見 [第 1 節人工測試](openspec/changes/build-online-buzzer-system/part1-manual-tests.md)。

## 示範輪次

在[每次啟動](#每次啟動)的指令中啟用示範模式，即可在老師看板載入三道示範題。

1. 老師輸入組數上限並建立活動，將活動代碼交給學生。
2. 各組首台裝置輸入代碼與組號。後續裝置需填同組裝置或老師看板上的六位數憑證。學生可在準備中或結果階段離組，老師可刪除從未有效提交的組別。
3. 老師載入示範題，選題、選排序模式並開輪。倒數可留空，或輸入 1 到 3600 的整數秒。
4. 老師手動關輪，或等待倒數到期。定案後可開下一輪或結束活動。結束頁會保留結果並列出未成功題目。

## 匯入 Google Sheets 學習單

### 首次設定 Google Cloud 憑證

1. 開啟 [Google Cloud Console](https://console.cloud.google.com/)，建立或選用同一個專案，在「API 和服務 → 程式庫」啟用 **Google Picker API**、**Google Drive API** 和 **Google Sheets API**。首次使用在「Google Auth Platform」填寫應用程式名稱與聯絡信箱。個人 Google 帳號選外部目標對象，保持測試狀態，將實際操作試算表的 Google 帳號加入測試使用者。資料存取範圍使用 `https://www.googleapis.com/auth/drive.file`，應用程式只要求所選檔案的存取。
2. 在「Google Auth Platform → 用戶端」建立 **網頁應用程式** OAuth 用戶端。將 `http://127.0.0.1:8000` 加入「已授權的 JavaScript 來源」，將 `http://127.0.0.1:8000/google/oauth/callback` 加入「已授權的重新導向 URI」。下載含 `client_secret` 的 JSON，存放在專案外的私有目錄，或專案內已由 Git 忽略的 `credentials/`。 [Google OAuth Web 用戶端設定](https://developers.google.com/identity/protocols/oauth2/web-server)
3. 在「API 和服務 → 憑證」建立 **Picker API key**。應用程式限制選「網站」，加入 `http://127.0.0.1:8000/*` 與 `https://docs.google.com/*`。API 限制選「限制金鑰」，選 **Google Picker API** 與 **Google Drive API**。Picker 在瀏覽器使用這把 key，由網站及 API 限制控制使用範圍。 [Google Picker 金鑰設定](https://developers.google.com/workspace/drive/picker/guides/web-picker-sample)
4. 從 Google Cloud 主控台的歡迎頁複製同一專案的 **專案編號（Project number）**，欄位值為純數字。 [查詢 Google Cloud 專案編號](https://docs.cloud.google.com/resource-manager/docs/view-update-projects)

完成後在啟動 PowerShell 設定三個環境變數，見「每次啟動」。`BUZZER_GOOGLE_PROJECT_NUMBER` 是純數字編號，不是專案 ID 或 OAuth client ID。請勿把 JSON 內容或金鑰貼到聊天／提交 Git。正式驗收先執行 `Remove-Item Env:BUZZER_DEMO -ErrorAction SilentlyContinue`，並使用 `http://127.0.0.1:8000`，避免與 `localhost` 混用造成回呼不一致。系統直接透過 Sheets API 讀寫雲端表格，無須下載 XLSX；請登入擁有指定副本編輯權的帳號。首次安裝時若 `python` 指令不可用，可用 Windows 的 `py` 建立虛擬環境。

### 選擇學習單

遇到 `redirect_uri_mismatch` 時，核對網址的主機名稱、連接埠與回呼路徑是否與 Google Cloud 登記相符。若 Picker 顯示 `API developer key is invalid`，核對 API key 的網站和 API 限制。

1. 老師建立活動後，按「匯入學習單」→「授權 Google」→「選擇 Google 試算表」。單張工作表直接匯入。多張時要明確選一張並按「匯入選取的工作表」。成功後會看到總題數、總空格數及各題空格數。第一輪前可整份替換。
2. 在同一列放置唯一一組「題號」與「作答」表頭，題號從 1 連續排列。單題使用單格或橫向合併格。題組以相鄰重複題號或縱向合併題號表示，每個子格左欄填 `（1）`、`（2）` 等連續編號，右欄為答案格。兩題間可留空列。
3. 格式錯誤會指出座標，例如 `G6：子編號須連續`。修正該格或合併範圍後重選試算表。權限錯誤時確認 Google 帳號與檔案共用權限。授權到期時重新授權。取消選擇或匯入失敗時，原有匯入結果仍可使用。

## 測試

### 乾淨環境啟動驗收

在專案根目錄執行以下指令。若已在連接埠 8000 啟動本專案，先按 `Ctrl+C` 停止服務。

```powershell
python -m venv .verify-venv/manual-part1
$testPython = ".\.verify-venv\manual-part1\Scripts\python.exe"
& $testPython -m pip install -r requirements-dev.txt
& $testPython -m pip check
$env:BUZZER_DEMO = '1'
& $testPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

確認 `pip check` 顯示 `No broken requirements found.`，再開啟 [搶答系統首頁](http://127.0.0.1:8000)。

### 自動化測試

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

瀏覽器測試可使用已安裝的 Chrome 或 Edge。將 `BUZZER_BROWSER_CHANNEL` 設為 `chrome` 或 `msedge`。也可用 `python -m playwright install chromium` 安裝測試瀏覽器，並省略 channel 設定。

```powershell
$env:BUZZER_BROWSER_TEST = '1'
$env:BUZZER_BROWSER_CHANNEL = 'chrome'
.\.venv\Scripts\python.exe -m pytest -q -s tests
```
