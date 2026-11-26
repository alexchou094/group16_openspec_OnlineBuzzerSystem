# 第 4、5 組需求與驗證紀錄

範圍：4.1–4.6、5.1–5.5。文件位置為本 change 目錄；操作說明見同目錄 round-review-operations.md。起始 Git 工作目錄乾淨，未找到適用 AGENTS.md。沿用第 2 組模型、服務、HTTP 身分與投影；共用檔修改僅為提交、順位、審閱及畫面交接。不修改依賴、設定或第 1 組基礎。

## 需求 → 實作 → 驗證對照

| 任務 / requirement 與 scenarios | 實作 | 驗證（均已通過，下列執行紀錄為依據） |
| --- | --- | --- |
| 4.1 多題任意順序、題組缺格/空白/多格/未知題、偽造身分 | ActivityService.submit / SubmissionBody | test_round_review.py：驗證失敗不占機會、HTTP 拒絕額外欄位 |
| 4.2 每組每題每輪一次、兩裝置競爭、遺失重送、下輪重答 | 活動鎖、record_accepted | barrier 並發、瀏覽器雙裝置、全錯重開 |
| 4.3 截止前/等於/之後、舊輪次、同時間戳、手動自動競爭 | clock / _expire / _match_round | 可控時鐘與 barrier；第 2 組回歸 |
| 4.4 搶答角色隔離、老師看板、憑證、歷史結果 | state 過濾後版本 / 不透明提交 id | HTTP 比較版本與完整投影、無 ETag、歷史及新輪隔離 |
| 4.5 關輪兩秒同步、舊回應、無倒數、斷線、提交遺失 | activity.js | 獨立 browser contexts、自動化網路攔截 |
| 4.6 HTML 文字安全、操作與憑證 | textContent / 操作文件 | 瀏覽器特殊文字、HTTP 憑證隔離 |
| 5.1 固定題序、無提交置後、時間序號、速度兩題同組 | domain/ranking.py | 純計算及服務斷言 |
| 5.2 歷史基準、公平兩題、同時序、題組一次、新組零 | rank / _success_counts | 速度到公平、題組、新組 |
| 5.3 非勝者與題組判錯、撤銷、整輪跨題重算、全錯 | review / rank | 原始資料保留、固定題序、重算冪等 |
| 5.4 過期版本、定案冪等、歷史鎖定、全錯重開 | finalize / open_round | HTTP 及服務鎖定測試 |
| 5.5 結果暫定/定案、理由、受影響題、角色操作 | resultsPanel | 老師與學生共同結果、定案前無下一輪/結束 |

## 資料交接及範圍外

第 3 組以 replace_questions 傳入 SourceSnapshot 與有序 Question/Blank；不新增正式 fixture 匯入端點。第 6 組使用已定案 Round.results、submissions、cumulative_counts、finalized_revision，依 Question.blanks 的 target 對應原始答案。Submission 保留原文、單調 received_at 與接收序號；UTC 文字用 service.timestamp 轉換。候選 rank=None 表示判錯。不可將暫估或未定案結果匯出。

第 1、3、6、7 組與 Google 真實匯入/寫回、非實作者人工驗收不屬本次完成聲明。

## 執行紀錄

- `python -m pytest ...`：無 python PATH；`py -m pytest ...`：Windows Store 安裝無法啟動。改尋既有 bundled runtime，不更動依賴。
- 使用 `C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`（Python 3.12）及既有 requirements-dev.txt 鎖定版本。`-m pip install --target "$env:TEMP/buzzer-review-deps" -r requirements-dev.txt` 在 sandbox DNS 失敗後，以網路權限重試成功。實際安裝目錄為 `C:/Users/user/AppData/Local/Temp/buzzer-review-deps`，未修改依賴檔或全域環境。
- bundled Python 採 isolated path，PYTHONPATH 不生效，實際測試透過下列 sys.path + runpy 啟動。

```powershell
$env:BUZZER_BROWSER_TEST='1'
$env:BUZZER_BROWSER_CHANNEL='chrome'
& C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -c "import sys,runpy,os; sys.path[:0]=['C:/Users/user/AppData/Local/Temp/buzzer-review-deps',os.getcwd()]; sys.argv=['pytest','tests','-q','-s']; runpy.run_module('pytest',run_name='__main__')"
```

實際執行與修正：

| 執行範圍 | 實際結果 |
| --- | --- |
| test_activity_service.py、test_activity_http.py、test_round_review.py 首輪 | 49 passed / 1 failed；既有第 2 組測試對舊結果物件作完全相等比較，不接受新增欄位 |
| 同上重跑（新增刪空組及中途加入測試） | 52 passed，4.14 秒。舊測試保留題號與勝者原斷言，只允許新增候選/理由欄位 |
| test_activity_browser.py + 初版本次瀏覽器流程 | 第 2 組兩項通過；本次流程 1 failed：測試未等前一題完成就注入遺失回應，提示被前一題成功回應覆蓋。加入可觀察完成條件後重跑通過 |
| 本次完整流程單獨重跑 | 1 passed，19.96 秒，正常輪詢同步 0.984 秒 |
| 本次全部瀏覽器測試（完整流程、速度、公平） | 3 passed，36.98 秒，正常輪詢同步 0.985 秒 |
| `tests -q -s` 最終整合執行 | 57 passed / 1 failed，63.15 秒；含 53 項領域/服務/HTTP 及 4 項瀏覽器通過。新增延遲請求測試的攔截 callback 尚未完成就斷言，修正為等待明確的 oldHeld 訊號 |
| 上項唯一失敗受影響測試重跑 | 1 passed，20.46 秒；確實保留 OPEN 回應、取得新 RESULTS、再釋放舊回應，畫面不回退。同步 0.906 秒 |
| `node --check app/static/activity.js`、`git diff --check` | 通過 |
| `openspec validate build-online-buzzer-system --strict` | valid（規劃結構檢查，非執行測試替代） |

受影響測試重跑實際命令為上列 Python 啟動形式加 `-X utf8`，sys.argv 改為 `['pytest','tests/test_round_review_browser.py::test_four_contexts_review_recovery_and_isolation','-q','-s']`。最終 58 個測試案例皆有通過證據（53 項非瀏覽器、5 項瀏覽器），不是宣稱最後一次整批命令零失敗。

瀏覽器證據：使用已安裝 Chrome；老師、兩組學生、第一組第二裝置，共四個獨立 contexts。fixture 經正式模型/replace_questions 注入測試伺服器，正式 create_app 未開 demo。驗證題組 HTML 文字不執行、學生畫面/網路隔離、同組請求只留一筆、遺失回應待確認、離線恢復、兩秒同步、速度/公平差異、跨題判錯勝者互換、撤銷、全錯、定案不可改判與未成功題重開。第 2 組瀏覽器回歸亦驗證憑證/刪組/身分持續性。

最後結果區截圖（不含憑證）位於本機測試暫存目錄：`C:/Users/user/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/AC/#!sandbox.{764beaf5-7b12-4bbe-9e83-b5d752fd93c5}/Temp/pytest-of-user/pytest-0/test_four_contexts_review_reco0/teacher-results.png`。此檔可能隨暫存清理移除；測試會重新產生。不把隨機加入憑證寫入驗收文件。

未執行：真實 Google OAuth、Sheets 匯入/寫回、CSV、非主要實作者人工驗收、Python 3.14 乾淨環境驗證。分別屬第 1/3/6/7 組，不能以 fixture 通過代替。
