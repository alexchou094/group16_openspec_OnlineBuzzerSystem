# Verification Report: build-online-buzzer-system（第 4、5 組）

> 此為改版前的歷史報告，不代表目前三態審閱規格的完成情形。新版驗證請見 [round-review-v2-verification.md](round-review-v2-verification.md)。

使用 openspec-verify-change，讀取 status、apply context、proposal、design、tasks 與五份 delta specs，並比對實作及測試。以下區分本次範圍與完整 change；不執行 archive。

## Summary

| Dimension | Status |
| --- | --- |
| Completeness | 本次 11/11 任務完成；完整 change 19/37。round-submission 6 與 ranking-review 7 個 requirements 已對應實作及 scenarios 測試。其餘 18 任務屬範圍外未完成 |
| Correctness | 本次 13/13 requirements 有通過證據；第 2 組回歸通過。共 58 個案例在整合執行及受影響重跑後通過。Google/匯入解析/匯出/非實作者人工驗收 Not verified（第 1、3、6、7 組尚未交付或執行） |
| Coherence | 本次沿用每活動鎖、可注入單調時鐘、原生 JS、角色投影版本及既有 session；純排序不修改歷史基準，定案後服務拒絕改判。完整 change 外部 adapter/匯出設計 Not verified（範圍外未實作） |

## 本次實作與驗證定位

| 規格責任 | 程式位置 | 測試依據 |
| --- | --- | --- |
| 完整題組、去重、session 組別、截止 | app/services/activities.py:161；app/main.py:47、178 | tests/test_round_review.py 的 invalid、same_group、exact_deadline、close_race、manual_close、HTTP 測試 |
| 搶答私有資料、角色可見版本及歷史 | app/services/activities.py:243 | test_http_roles_versions_and_history；第 2 組 HTTP 回歸 |
| 題序、速度、公平、候選理由及計數 | app/domain/ranking.py:11、18；app/domain/activity.py:56 | ranking_review_and_finalize（速度/公平）、fixed_order、nonopen_question_midround_group |
| 判錯、撤銷、跨題重算與過期版本 | app/services/activities.py:182 | ranking_review_and_finalize、all_wrong_nonwinner、HTTP 改判授權與版本 |
| 定案冪等、成功題不重開、全錯重開 | app/services/activities.py:210 及既有 open_round | 同上、第 2 組生命週期與本次四 context 流程 |
| UI、文字安全、倒數、斷線、遺失回應及舊回應 | app/static/activity.js:156、189、326；app/static/activity.css | tests/test_round_review_browser.py 全部；tests/test_activity_browser.py 全部 |

完整「任務/requirement/scenario → 實作 → 驗證」及實際命令、失敗原因、修正後重跑結果見 round-review-verification.md。操作與資料交接見 round-review-operations.md。

## 本次範圍內缺陷

沒有未解決的 CRITICAL 或 WARNING。

檢查過程修正了刪除從未提交的空組後，歷史 cumulative_counts 投影可能索引已刪組的問題，並加入回歸。既有第 2 組結果斷言調整為檢查原欄位，因本次 API 合法擴充 candidates/reason；未移除原業務斷言。兩次瀏覽器測試失敗來自測試等待條件，修正後受影響流程重跑通過，未將失敗執行寫為成功。

## 範圍外 CRITICAL（完整 change 尚未完成）

下列每項仍未勾選；依使用者分工，交回相應負責人，不在本次擴張實作。

| 任務 | 缺口與下一步 |
| --- | --- |
| 1.1 | A 完成及驗證專案基礎、鎖定依賴與乾淨環境啟動 |
| 1.2 | A 完成無敏感值設定範例與操作驗證 |
| 1.3 | A 完成跨組需求對齊及確認紀錄 |
| 1.4 | A 使用指定 Google 測試複本完成 OAuth/Picker 實證 |
| 3.1 | 建立 SheetSnapshot adapter 契約與範例 fixture |
| 3.2 | 實作動態表頭/題組解析與位置映射 |
| 3.3 | 驗證完整性及失敗原子性 |
| 3.4 | 整合 Google 讀取與原子匯入；使用既有 replace_questions 交接 |
| 3.5 | 建立真正匯入頁與摘要；本次測試 fixture 不算完成 |
| 6.1 | 依本次定案模型建立匯出快照、預覽與外部 I/O 鎖界線 |
| 6.2 | 重讀並驗證來源結構指紋 |
| 6.3 | RAW 精確寫入及指定測試複本讀回核對 |
| 6.4 | 寫回狀態、待確認與相同快照重試 |
| 6.5 | 已定案紀錄九欄 CSV 與文字安全 |
| 6.6 | 匯出頁及老師授權驗證 |
| 7.1 | 完整 Google 匯入到寫回/CSV 的跨模組瀏覽器驗收 |
| 7.2 | 非主要實作者人工核對多份原表與非目標內容 |
| 7.3 | 統一 demo、敏感資訊檢查、組員驗收及課程交付清單 |

## Final Assessment

本次第 4、5 組可交付整合，無已知未解決範圍內缺陷。完整 change 有 18 個範圍外未完成任務，不宣稱完整 change 驗證通過或可封存。未驗證的外部整合及人工驗收，須於第 1、3、6、7 組交付後依其任務重新執行。`openspec validate --strict` 通過僅表示 artifacts 結構有效。
