# 整合交付檢核與 demo（7.3）

2026-11-26，核對基準為 Git `5ac1511` 加上本次程式、測試與文件變更。專案根目錄已確認為 `C:\github repo\group16_openspec_OnlineBuzzerSystem`。本文件及人工驗收表皆位於 `openspec/changes/build-online-buzzer-system/`，通用操作入口為根目錄 README.md。

狀態：7.2、7.3 均完成，交付文件供使用者審閱。周敬恩的非主要實作者角色、三份線上學習單及其餘人工測試全數通過、跨組／助教對齊均由使用者確認。未自行發布、提交課程資料或封存 change。

## 程式與規格核對

| 規格／設計 | 程式與介面 | 檢查結果 |
| --- | --- | --- |
| activity-session；design 2–4 | main.py、services/activities.py、repositories/memory.py、activity.js | 建立／加入、角色、恢復 Cookie、生命週期有測試；本次補齊空組與失效身分行為，見下表 |
| guided-notes-import；design 5、7 | domain/guided_notes.py、services/guided_notes.py、web/guided_notes.py、adapters/guided_notes.py、guided_notes_import.html/js | 原版與平移解析測試、HTTP 及瀏覽器匯入通過；三份人工驗收全數通過（使用者回報） |
| round-submission；design 3–4 | main.py、services/activities.py、activity.js/css、index.html | 每格 500 碼點、完整題組、唯一提交、截止、角色過濾、文字顯示與同步有回歸 |
| ranking-review；design 6 | domain/ranking.py、services/activities.py、activity.js | 三態、固定題序、公平依已確認勝者、撤銷後題重審及定案鎖定有回歸 |
| result-export；design 8 | services/result_export.py、web/result_export.py、result_export.html/js、adapters/guided_notes.py | 定案快照、鎖外 I/O、RAW／讀回、重試與十欄 CSV 有回歸；既有混合表 Google 實測見 result-export-operations.md |

路由實作以 `app/main.py`、`app/web/` 為準，design 的路由表是草案。例如寫回核對／重試實際為 `/activities/{id}/exports/attempts/{attempt_id}/verify` 與 `/retry`，不是另建全域匯出 API。審閱使用 PATCH 與 `review_status`、`expected_review_version`。HTTP 授權與 CSRF 由後端檢查，前端隱藏按鈕不能替代授權。

## 差異修正與人工完成門檻

| 項目 | 目前觀察 | 規格預期與後續 |
| --- | --- | --- |
| 空組自動釋放 | 已修正 `ActivityService.leave`：最後裝置離開未提交組時，在活動鎖內移除組與投影 | 保留曾提交組及仍有裝置的組；憑證歷史確保重建不重用舊值。服務／HTTP／多 context 回歸 |
| OPEN 刪空組 | 已修正 `delete_group` 與 can_delete 投影及前端按鈕 | 僅 PREPARING／RESULTS 允許；OPEN API 回 409 且不撤銷裝置資格。HTTP／瀏覽器回歸 |
| 刪組後學生畫面 | 403 自動返回首頁加入表單並提示身分失效 | 401／404 保留失效提示；連線錯誤維持重連，不誤導至加入。同身分其他分頁離組與老師刪組皆有瀏覽器回歸 |
| 7.2 人工與外部確認 | 使用者回報三表及其餘人工測試全數通過，確認周敬恩為非主要實作者且外部對齊已完成 | 見 integration-manual-acceptance.md；結果來源為使用者回報 |

既有 `round-review-v2-verification.md` 的第 2 組缺口已在本次修正；其中第 6 組「尚未實作」與舊測試數量均為歷史時點描述，現況以本文件及 result-export-operations.md 為準。7.1 的既有勾選不替代 7.2 人工驗收。

## 啟動與測試

從根目錄依 README 建立 `.venv`、安裝 requirements-dev.txt，先執行 `pip check`。正式 Google 流程使用 README 的三個環境變數，並移除示範旗標：

```powershell
Set-Location 'C:\github repo\group16_openspec_OnlineBuzzerSystem'
Remove-Item Env:BUZZER_DEMO -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

在另一個 PowerShell 於根目錄執行完整測試：

```powershell
$env:BUZZER_BROWSER_TEST = '1'
$env:BUZZER_BROWSER_CHANNEL = 'chrome'
.\.venv\Scripts\python.exe -m pytest -q -s tests
Get-ChildItem app/static/*.js | ForEach-Object { node --check $_.FullName }
openspec validate build-online-buzzer-system --strict
git diff --check
```

沒有 Chrome 時可選已安裝 Edge（`msedge`），或依 README 安裝 Playwright Chromium 並移除 channel。未啟用 BUZZER_BROWSER_TEST 會跳過瀏覽器測試，不可當成全部通過。

本次環境沒有 `.venv` 或 PATH 上的 Python，使用既有 bundled Python 與先前安裝的測試依賴，未改依賴或系統環境。實際重現命令：

```powershell
$env:BUZZER_BROWSER_TEST='1'
$env:BUZZER_BROWSER_CHANNEL='chrome'
& C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -X utf8 -c "import sys,runpy,os; sys.path[:0]=['C:/Users/user/AppData/Local/Temp/buzzer-review-deps',os.getcwd()]; sys.argv=['pytest','tests','-q','-s']; runpy.run_module('pytest',run_name='__main__')"
```

| 2026-11-26 本次執行 | 實際結果 |
| --- | --- |
| 修正前全套 pytest，啟用 Chrome | 107 passed，72.83 秒，零跳過；四 context 測試輸出關輪同步 0.047 秒 |
| 新增回歸首次執行 | 110 passed、1 failed；新增瀏覽器案例使用錯誤按鈕名稱「離開本組」，已改為實際「離開組別」，未放寬行為斷言 |
| 修正後最終全套 pytest，啟用 Chrome | **111 passed，105.63 秒，零跳過**；四 context 關輪同步 0.796 秒 |
| 全部 app/static/*.js 的 node --check | exit 0 |
| OpenSpec strict validate | valid |
| git diff --check | exit 0 |
| HEAD 版本控制敏感資料檢查 | 75 個追蹤檔案，未發現敏感檔名或私鑰、Google key/access token、GitHub token、AWS access key 特徵；測試中的 test-secret/test-token 為合成資料 |
| 目前工作檔案與新文件 | 同一組敏感特徵掃描無命中；Markdown 本地連結無缺失 |

以上瀏覽器測試使用模擬 Google 回應；並非本次真實 Google 驗收。敏感資料檢查是 HEAD 與目前工作檔案的已列特徵掃描，不保證完整歷史及所有未知格式皆無敏感值。`.gitignore` 已排除 .env、credentials、client_secret*.json、token*.json，公開範例維持占位值。7.3 的程式／規格／操作文件核對與交付整理已完成；7.2 外部與人工條件已依使用者回報及確認完成。

## 五分鐘 demo 腳本

目標 4 分 50 秒；尚未由組員計時演練。演示前啟動服務、完成 Google 授權、備妥指定混合複本與三個獨立設定檔，先填好測試文字供貼入。Google 授權若需現場完整展示，須先計時調整；網路等待不可冒稱成功。此腳本不是取代 7.2 的完整驗收。

| 時間 | 畫面與操作 | 解說／預期 |
| --- | --- | --- |
| 0:00–0:35 | 建立 2 組活動、Picker 選混合複本 | 4 題／6 格，題組是一題；學生 A/B 依組號加入 |
| 0:35–1:15 | 速度模式選第 3、4 題，無倒數。A 先送兩題，B 後送兩題；關輪並確認 A 兩題正確，定案 | 老師看提交先後，學生只看本組；速度可由同組贏兩題，尾段未審閱 |
| 1:15–2:05 | 公平模式選第 1、2 題題組。A 先送兩題，B 後送兩題，關輪 | A 已成功 2 次，B 為 0，先審 B。題組一次提交，整題只計一次 |
| 2:05–2:55 | 題一 B 正確；題二先審 B 並確認。撤銷題一 B 正確，改判 B 錯 A 對，再審題二 B 正確 | 後題重審，沒有假定勝者；原答案與固定題序不變 |
| 2:55–3:25 | 定案第二輪並結束 | 定案後鎖定，累計 A=3、B=1；學生同步結果 |
| 3:25–4:20 | 匯出預覽，核對六個答案格，確認覆寫並等待讀回 | 僅定案勝者；展示事先基準與寫後子編號、格式及非目標內容 |
| 4:20–4:50 | 下載 CSV、展示十欄與三態 | 未審閱不等於答對；說明重啟清空、單 worker、真實寫回依網路 |

7.2 已依使用者確認完成；若寫回待確認，現場如實展示待確認與讀回核對，不直接宣稱完成。全錯重開、空組釋放、四組錯錯對尾段等完整情境由人工驗收表另行記錄。

## 限制與課程交付清單

單 process／單 worker、loopback 使用；資料與 session 只存記憶體，重啟清空。相同瀏覽器設定檔的分頁共享身分。Google 需要真實授權及網路，寫回期間不可並行修改來源結構；外部檢查與寫入不具原子交易保證。無自動批改、正式跨裝置部署、資料庫、重啟恢復或 XLSX 產品備援。

- [x] 原始碼、HTML/CSS/JavaScript、鎖定依賴及測試存在於既定目錄。
- [x] 啟動／測試指令、規格／模組對照及已知差異整理。
- [x] 五分鐘內 demo 時間配置及人工驗收表備妥供審閱。
- [x] 本次完整測試與 HEAD 敏感資料掃描已記錄。
- [x] 已知程式差異修正、測試補齊並重新驗證。
- [x] 非主要實作者周敬恩完成三份學習單驗收（使用者於對話確認，未另提供簽名附件）。
- [x] 跨組、助教與 Google 驗收已有明確結論（使用者確認；G3/G4 保留既有跳過紀錄）。
- [ ] 組員實際演練 demo，記錄不超過五分鐘及證據。
- [ ] 使用者審閱交付內容，確認課程要求的報告／影片／分工／提交格式（目前未提供完整課程交付規則）。
- [ ] 使用者自行發布或另行明確授權提交；本次未發布、commit、push 或 archive。

## 人工驗收回報更新

2026-11-26 使用者回報「人工測驗完了，全數通過」。三份線上學習單、非目標內容與 C1–C9／E1–E6 已在人工驗收表記錄為通過（使用者回報），不冒稱本次由代理實測。前述 Google 設定阻塞已解除；使用者後續已確認非主要實作者角色與外部對齊，7.2 完成，不將此回報延伸成 demo 計時、課程提交或 G3/G4 重測的證據。
