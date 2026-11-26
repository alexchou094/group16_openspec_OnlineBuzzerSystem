# 第 4、5 組新版實作與驗證（2026-11-26）

本紀錄對應目前三態逐筆審閱規格，取代舊版 round-review-verification.md 與 round-review-verify-report.md 的適用性。範圍為 4.1–4.6、5.1–5.6。開始時 task4&5 工作目錄乾淨；文件置於本 change 目錄，不改設定、依賴或匯入/匯出實作。

## 需求、實作與測試對照

| 任務 / requirements 與 scenarios | 實作 | 驗證 |
| --- | --- | --- |
| 4.1 多題順序、題組完整、500/501 Unicode 碼點、原文、全空白 | ActivityService.submit，每格獨立驗證但整筆原子接受 | invalid_does_not_consume、unicode_limit_and_original_text、HTTP 輸入邊界、瀏覽器 Unicode 與原文 |
| 4.2 同組兩裝置競爭、回應遺失重送、下一輪機會 | 既有活動鎖與唯一鍵 | same_group_concurrency_and_retry（Barrier）、all_wrong_nonwinner_and_next_round、四 context 瀏覽器 |
| 4.3 截止前/恰等於/之後、舊輪次、同時戳、手動自動關輪 | 既有注入時鐘與 _expire/_close | exact_deadline、close_race_and_stale_round、manual_close_before_submission、第 2 組回歸 |
| 4.4 學生可見性與成功次數、老師接收先後不提供暫估順位、憑證 | state 投影，OPEN 的 results 永遠空；提交陣列按接受先後 | http_roles_versions_and_history、瀏覽器畫面/回應檢查 |
| 4.5 輪詢、原文換行、500 字提示、倒數、舊回應、離線/回應遺失 | activity.js + .answer-text 樣式 | 四 context 流程、Unicode 瀏覽器測試、第 2 組同步與倒數回歸 |
| 4.6 加入/憑證備援/離組/刪組說明、安全文字及公開結果 | 操作文件、textContent、既有角色憑證過濾 | 第 2 組回歸、HTTP 憑證範圍、瀏覽器 HTML 文字 |
| 5.1 固定題序、速度先後、第一候選不是勝者 | fixed_order、rank 三態結果 | ranking_review_and_finalize（速度）、fixed_order_is_reception_not_question_number |
| 5.2 公平逐題、歷史基準、題組一次、新組零、後題等待 | rank 僅累加 CORRECT；WAITING 不提供順位/當題次數 | ranking_review_and_finalize（公平）、nonopen_question_midround_group、速度/公平瀏覽器對照 |
| 5.3 當前候選、前兩錯第三對、尾段未審閱、禁止跳號/尾段判錯 | ReviewStatus、review 權限及游標檢查 | two_wrong_third_correct_tail_and_undo、HTTP、四組瀏覽器 |
| 5.4 撤銷錯/對、後題失效、重審、原始資料不變、不重複加分、過期版本 | review 撤銷重置該候選起及後題，從基準 rank | 同上、competing_reviews_and_finalize（Barrier）、跨題撤銷瀏覽器 |
| 5.5 未審完拒絕定案、尾段可定案、全錯/無提交、歷史鎖定 | finalize 審閱完整性 + 既有版本/冪等規則 | ranking_review_and_finalize、all_wrong、HTTP、雙請求定案競爭 |
| 5.6 待審閱/暫定/定案、操作權限、撤銷前提示、學生同步 | resultsPanel、撤銷確認、只顯示當前候選操作 | 四 context 端到端、四組尾段測試 |

## 修改理由與交接

- Submission.review_status 為 UNREVIEWED / INCORRECT / CORRECT；rejected 改為唯讀衍生屬性，不能由 rejected=False 推論正確。
- QuestionResult.status 為 WAITING / PENDING / COMPLETE，current_submission_id 指向唯一可操作候選。無提交直接 COMPLETE，首筆 CORRECT 或全錯也 COMPLETE。
- 候選陣列保留處理順序；INCORRECT 的 rank 為 null，其餘依相對順序連續編號。WAITING 的 rank 與 successes_before 皆為 null。
- 審閱 API body 改為 review_status、expected_review_version，舊 rejected body 回 422，避免舊頁面悄悄以舊語意改判。
- 第 2 組兩個直接建立提交的測試 fixture 改用 review_status；原本活動隔離、離組不轉移成功、不可刪已提交組等斷言保留。
- 第 3 組 replace_questions、Question/Blank/SourceSnapshot 介面不變。第 6 組須以 CORRECT 勝者匯出，CSV 十欄末欄採審閱狀態，尾段 UNREVIEWED 不得視為正確；原 answers 不修剪。

## 執行結果

測試沿用先前安裝於暫存的鎖定依賴與 bundled Python 3.12，沒有修改 requirements 或全域環境。實際命令：

```powershell
$env:BUZZER_BROWSER_TEST='1'
$env:BUZZER_BROWSER_CHANNEL='chrome'
& C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -X utf8 -c "import sys,runpy,os; sys.path[:0]=['C:/Users/user/AppData/Local/Temp/buzzer-review-deps',os.getcwd()]; sys.argv=['pytest','tests','-q','-s']; runpy.run_module('pytest',run_name='__main__')"
```

| 執行 | 結果 |
| --- | --- |
| 更新核心後 `tests -q`，未設瀏覽器旗標 | 77 passed、6 skipped，4.60 秒；不把 skipped 計為通過 |
| 新增服務情境與更新瀏覽器後，`tests/test_round_review.py tests/test_round_review_browser.py -q -s` | 27 passed，34.69 秒；四 context 關輪同步 0.891 秒 |
| 最終 `tests -q -s`，啟用 Chrome | **90 passed，83.46 秒，無跳過**；83 項非瀏覽器、7 項瀏覽器 |
| `node --check app/static/activity.js` | 通過 |
| `git diff --check` | 通過 |
| `openspec validate build-online-buzzer-system --strict` | valid；僅規劃結構驗證，不代替執行測試 |

本次無測試失敗需要重跑。既有布林判錯/自動勝者測試已依新版需求改為先檢查待審閱，再經顯式正確確認；並保留第 2 組原有生命週期斷言。

瀏覽器實證：四 contexts（老師、兩組、同組第二裝置）驗證唯一提交、回應遺失、離線恢復、HTML 安全、角色隔離、舊 OPEN 回應晚到不覆蓋 RESULTS、公平跨題撤銷重審、全錯重開與鎖定。最終關輪同步 0.906 秒。另以三 contexts 對照速度/公平，以五 contexts（老師與四組）驗證 emoji 的 500/501 字、原文空白換行、錯錯對後尾段仍未審閱，以及撤銷提示取消與確認兩條路徑。第 2、3 組瀏覽器回歸亦全部通過。

結果區截圖不含憑證，測試輸出位置：`C:/Users/user/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/AC/#!sandbox.{49c78646-2ae6-4889-b107-23ecfc99f305}/Temp/pytest-of-user/pytest-0/test_four_contexts_review_reco0/teacher-results.png`。暫存可被清理，測試再次執行會產生新圖。沒有宣稱已由其他組員人工驗收。

## 範圍外與限制

本次不包含第 6、7 組及真實 Google 端到端、非主要實作者人工驗收。activity-session 新增的空組自動釋放、OPEN 禁止刪空組與自動返回加入畫面，現有第 2 組尚未實作完整；此次保留原行為，列為範圍外缺口，不以第 2 組既有測試通過聲稱新版全部符合。

## OpenSpec verify 結果

使用 openspec-verify-change 核對同一 `build-online-buzzer-system`，重新讀取 status/apply，對照五份 specs、design、任務與目前程式。`state: ready` 與 planning artifacts 完成不代表完整實作完成。

| Dimension | 結果 |
| --- | --- |
| Completeness | 本次 4.1–4.6、5.1–5.6 共 12/12 完成；完整 change 為 31/40，仍有第 6、7 組 9 項未完成。round-submission 6 與 ranking-review 7 個 requirements 已映射 |
| Correctness | 本次 13 個 requirements 及相關 scenarios 有程式與測試依據，90 項測試通過。第 2 組新版規則有下述範圍外缺口；真實 Google 及第 6、7 組完整情境 Not verified（本次未執行/尚未實作） |
| Coherence | 本次遵守單 process、每活動鎖、可控時鐘、純排序、角色投影、版本衝突、三態及撤銷失效設計；命名/目錄沿用既有模式。第 6 組外部 I/O/匯出設計 Not verified（未實作） |

程式定位：

- `app/domain/activity.py:16` 定義 ReviewStatus；提交保存明確三態，rejected 僅為衍生值。
- `app/domain/ranking.py:11` 固定題序，`:18` 從基準逐題重算，只有確認正確才加分；後題 WAITING 無假定順位。
- `app/services/activities.py:161` 原文/Unicode 上限/完整提交，`:186` 原子審閱與撤銷，`:230` 定案完成條件及冪等，`:265` 角色狀態投影。
- `app/main.py:54` 審閱 body 採 ReviewStatus 且禁止未知欄位；`:186` 沿用 session/Origin/CSRF 保護。
- `app/static/activity.js:157` 待審閱、當前操作、撤銷前提示及尾段只顯示失敗；`:205` 安全文字原樣呈現；`:214` 多行提交及碼點字數提示。

### 本次範圍內

無未解決 CRITICAL、WARNING 或 SUGGESTION。舊 API 的 rejected body 已明確拒絕；新腳本以內容版本載入。所有新版任務勾選均在相關測試通過後完成，並逐項重新查詢 apply 確認進度提升。

### 範圍外未完成事項

以下為完整 change 的 CRITICAL 完成缺口，未在本次偷偷勾選或擴張實作：

| 任務 | 後續處理 |
| --- | --- |
| 6.1 | 實作定案匯出快照/預覽，外部 I/O 移出鎖 |
| 6.2 | 實作寫前重新解析與結構一致性檢查 |
| 6.3 | 實作 RAW 精確寫回、原文保留及真實複本核對 |
| 6.4 | 實作失敗/待確認/成功與相同快照重試 |
| 6.5 | 實作十欄 CSV，區分 CORRECT/INCORRECT/UNREVIEWED |
| 6.6 | 實作匯出畫面與授權驗證 |
| 7.1 | Google 匯入至寫回/CSV 的完整跨模組瀏覽器驗收 |
| 7.2 | 非主要實作者人工驗證多份學習單與原表非目標內容 |
| 7.3 | 核對整體交付/demo/敏感資料與組員驗收紀錄 |

另有兩項既存 requirement 實作缺口：

- `activity-session / 學生離開本組`：`app/services/activities.py:98` 目前只移除 session membership，最後一裝置離開且無提交時不會自動釋放組號。應由第 2 組補齊活動鎖內釋放、舊憑證失效與回歸案例。
- `activity-session / 老師刪除空組`：`app/services/activities.py:105` 目前只拒絕終態，未拒絕 OPEN；`activity.js` 遇身分失效仍顯示錯誤和返回首頁入口，未自動返回加入畫面。應由第 2 組補齊 phase 驗證與身分失效 UI。既有第 2 組回歸通過不能掩蓋這兩項新版差異。

### Final Assessment

第 4、5 組新版需求已實作且驗證通過；完整 change 尚有第 6、7 組未完成任務及第 2 組新版差異，不能宣稱整個 change 完成或可封存。真實外部服務及其他組員人工驗收未執行，不算通過。未 commit、push 或 archive。
