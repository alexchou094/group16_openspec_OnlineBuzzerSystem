# 課堂搶答系統

`spec01` 對應 OpenSpec 的 `activity-session`，支援老師建立及控制活動、學生依組號加入、組別身分恢復，以及輪次與結果同步。

## 本機啟動

在專案根目錄使用 PowerShell。開發與測試使用 Python 3.14。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

開啟 `http://127.0.0.1:8000`。活動和瀏覽器工作階段保存在單一伺服器程序的記憶體。重啟會清空活動，舊活動頁會顯示失效。請使用單 worker，並用不同瀏覽器設定檔或獨立瀏覽器 context 模擬老師、第一組和第二組。同一設定檔的多個分頁會共用身分。伺服器持續運作時，使用保留網站 Cookie 的同一設定檔重開瀏覽器，開啟原活動網址即可恢復老師或學生身分。

一般活動示範不需要 Google 憑證。環境變數欄位見 [.env.example](.env.example)，請在啟動服務的 PowerShell 視窗設定。Google OAuth Web 用戶端 JSON 放在專案外的私有目錄，或專案內已忽略的 `credentials/`。Google Cloud 設定、驗證工具啟動與人工測試見 [第 1 節人工測試](openspec/changes/build-online-buzzer-system/part1-manual-tests.md)。

## 示範輪次

啟動前在同一個 PowerShell 視窗設定 `$env:BUZZER_DEMO = '1'`，即可在老師看板載入三道示範題。示範題只供驗證活動流程。後續學習單模組可呼叫 `ActivityService.replace_questions`，將已驗證的來源和題目映射交給活動服務。

1. 老師輸入組數上限並建立活動，將活動代碼交給學生。
2. 各組首台裝置輸入代碼與組號。後續裝置需填同組裝置或老師看板上的六位數憑證。學生可在準備中或結果階段離組，老師可刪除從未有效提交的組別。
3. 老師載入示範題，選題、選排序模式並開輪。倒數可留空，或輸入 1 到 3600 的整數秒。
4. 老師手動關輪，或等待倒數到期。定案後可開下一輪或結束活動。結束頁會保留結果並列出未成功題目。

## 測試

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

瀏覽器測試可使用已安裝的 Chrome 或 Edge。將 `BUZZER_BROWSER_CHANNEL` 設為 `chrome` 或 `msedge`。也可用 `python -m playwright install chromium` 安裝測試瀏覽器，並省略 channel 設定。

```powershell
$env:BUZZER_BROWSER_TEST = '1'
$env:BUZZER_BROWSER_CHANNEL = 'chrome'
.\.venv\Scripts\python.exe -m pytest -q tests/test_activity_browser.py
```
