# Design

## Context

動機與能力範圍見 proposal.md；五份 specs 為可觀察行為的準據。專案目前僅有 OpenSpec 初始化，無應用碼、依賴清單或既有測試，因此下述模組與 API 都是規劃，不是現有功能。

需求來源是課程說明、SpecKit 組提案、單題及題組 Excel 範例，以及本次 Explore 的確認。最重要的修正是題組範例的題號縱向合併：空白續列不能直接跳過，純二維值陣列也不足以還原作答映射。範例座標只用來建立測試預期，正式解析不能寫死。

使用者指定 Python 伺服端／服務層及 HTML/CSS 頁面。本設計只規劃本機單服務，Google Sheets 存取仍需網路。跨組對齊與 Google 授權尚未完成實證，不把附件中的待辦當成已完成成果。

## Goals / Non-Goals

**Goals:**
- 把流程、排序、解析與外部 I/O 分離，能在不連 Google 的情況驗證核心行為。
- 以單一權威狀態及可重現排序處理並發提交、判錯、定案與匯出快照。
- 最少的前端互動程式完成同步、倒數及表單體驗，規則均由 Python 驗證。

**Non-Goals:**
- 不建 SPA、WebSocket、資料庫、分散式鎖或多 worker 部署。
- 不保證後端重啟恢復，亦不提供跨服務故障復原。
- 不設計定案後連鎖改判、既有學習單結構自動修復或外部同時編輯的交易隔離。
- 本次不建立應用程式、測試碼、依賴檔、憑證檔或部署檔。

## Decisions

### 1. Python Web 與服務分層

選擇 FastAPI 作為 Python HTTP 邊界、Jinja2 渲染 HTML、CSS 負責版面，原生 JavaScript 處理表單送出、每秒輪詢及倒數。相較 SPA，這符合本機課堂操作與指定頁面技術，減少前後端獨立狀態。相比 Django，全套帳號與 ORM 對本版非必要；框架不承擔業務排序規則。

預計目錄（尚未建立）：

| 位置 | 責任 |
| --- | --- |
| `app/main.py`、`app/web/` | 應用組裝、路由、身分驗證、輸入輸出與錯誤轉換 |
| `app/services/` | 活動、提交、審閱、匯入、匯出使用案例與鎖定界線 |
| `app/domain/` | 資料模型、狀態轉移、純解析器與純順位演算法 |
| `app/adapters/` | Google OAuth／Sheets 外部 I/O 與測試替身介面 |
| `app/repositories/` | 記憶體活動與 session 儲存、每活動鎖 |
| `app/templates/`、`app/static/` | HTML、CSS、必要 JavaScript |
| `tests/` | 領域、服務、HTTP、瀏覽器驗收與合成範例 |

正式服務單一 process、單一 worker，預設 loopback。所有 Google 請求在活動狀態鎖外執行，避免外部延遲阻塞搶答。測試使用 pytest、FastAPI TestClient 與獨立瀏覽器 context；依賴版本於 apply 鎖定並驗證，不在此猜測最新版本。

```text
HTML / CSS / JavaScript
          |
          v
FastAPI routes --> Session authorization
          |
          v
Python services --> In-memory repository
       |     |
       |     +--> Pure parser / Pure ranking
       v
Google adapter --> Google Sheets
```

### 2. 狀態模型與資料邊界

| 模型 | 主要資料與不變條件 |
| --- | --- |
| Activity | id、代碼、owner session、phase、組數上限、題目、組別、輪次、state_version、finalized_revision |
| Group | 穩定內部 id、活動、活動內唯一的整數組號、6 位數加入憑證 |
| SourceSnapshot | spreadsheet_id、sheet_id、顯示名稱、表頭、合併範圍、結構指紋 |
| Question / Blank | 穩定內部 id、數字題號、有序子空格、編號與目標格；不存題目敘述 |
| Round | id、題目集合、fairness_enabled、起始次數、opened_at、deadline、fixed_question_order、review_version、finalized |
| Submission | round_id、question_id、group_id、答案陣列、received_at、receive_sequence、判錯旗標 |
| RoundResult | 每題候選順位、勝者、處理當下成功次數、勝出理由、累計次數與版本 |
| ExportSnapshot / Attempt | 定案版本、目標答案集合、結構指紋、預覽識別、嘗試狀態與錯誤分類 |

活動 phase 為 PREPARING、OPEN、RESULTS、CANCELLED、FINISHED。定案是 RESULTS 中的輪次旗標。歷史成功次數從定案勝者推導；重算只用區域副本，避免暫算污染歷史。中途加入新組時，本輪基準以零補足。

```text
PREPARING --> OPEN --> RESULTS [unfinalized]
    |                    |         ^
    |                    v         |
    |                  REVIEW --> RECOMPUTE
    |                    |
    |                    v
    |              RESULTS [finalized] --> OPEN
    |                    |
    v                    v
CANCELLED             FINISHED
```

準備中可取消，取消後拒絕預覽、寫回及 CSV 下載。開輪後先關輪、審閱、定案再結束。取消或結束後，同一老師工作階段可再建立活動。開輪時獨立固定公平模式與倒數。兩種模式皆可關閉倒數，或啟用 1～3600 秒的整數倒數。已定案但無勝者的題仍屬未成功，可再次選取。每個成功題一旦定案便不再開放，確保每題至多一份定案勝者。來源從第一輪開始鎖定。

### 3. 身分、同步與 HTTP 合約

以隨機 session id 的 HttpOnly、SameSite cookie 綁定伺服端身分。建立活動的老師工作階段可跨同一瀏覽器工作階段的分頁使用。建立活動時，伺服端原子檢查該老師工作階段是否已有進行中活動。老師與學生使用獨立瀏覽器 context／profile 展示。

同一活動內的組號為 1 到組數上限的整數。新組建立時隨機產生 6 位數憑證，以可保留前導零的字串存於伺服端記憶體。後續加入驗證組號與憑證。角色過濾後的狀態資料僅向本組已加入裝置和活動老師提供該組憑證，供同組分享與老師備援。公開結果省略憑證。

離組只解除目前裝置與組別的關聯。刪除組別與接受提交共用活動鎖，刪除前檢查該組是否曾有有效提交。刪除時解除所有裝置關聯並使原憑證失效。同一組號重新建立時使用新的內部 id 與憑證。提交與成功次數依內部組別 id 歸屬。

首頁依伺服端工作階段與現有組別關聯顯示學生的返回入口。只有一個符合條件的活動時顯示單一入口，有多個時可分別選擇。返回入口查詢只提供辨識及前往活動所需的資料，活動頁仍依工作階段驗證權限。

頁面載入活動腳本時，網址帶有依檔案內容產生的版本。更新腳本會改變網址，讓已快取舊腳本的瀏覽器載入與當前頁面相符的版本。

敏感操作檢查活動 owner、CSRF token／Origin；Google 登入不授予活動 owner 身分。未授權回應不攜帶活動私有內容，答案以轉義文字顯示。工作階段、組別加入及 Google 憑證不進 log、repo 或錯誤頁。

路由草案（實作可調整 URL，但不得改變 specs 行為）：

| 操作 | 輸入重點 | 回應／限制 |
| --- | --- | --- |
| `POST /activities` | 建立者 session、組數上限 | 活動代碼、老師控制權；已有進行中活動時拒絕 |
| `POST /activities/join` | 代碼、組號、既有組加入憑證 | 學生 session、自己的組別 |
| `POST /activities/{id}/leave` | 學生 session | 僅準備中或結果階段，解除目前裝置的組別身分 |
| `DELETE /activities/{id}/groups/{group}` | 老師 session、組別識別 | 僅刪除從未提交的組別 |
| `GET /activities/{id}/state` | 身分 | 依角色投影、server_time、版本、phase、自己的提交或老師看板 |
| `POST /activities/{id}/import` | 來源檔案；多張工作表時提供選取的工作表識別 | 單張工作表自動選用；僅準備中，回傳完整映射摘要或位置錯誤 |
| `POST /activities/{id}/rounds` | 題目、模式、可選秒數、預期狀態版本 | 新輪次；條件不足回衝突 |
| `POST /activities/{id}/rounds/{round}/submissions` | question_id、答案陣列 | 不接受用戶指定組別／時間，已送出回既有自己組資料 |
| `POST .../rounds/{round}/close` | round_id | 冪等關輪結果 |
| `PATCH .../rounds/{round}/reviews/{submission}` | 判錯狀態、expected_review_version | 原子重算；舊版本回衝突 |
| `POST .../rounds/{round}/finalize` | expected_review_version | 冪等定案結果 |
| `POST /activities/{id}/cancel` | 預期活動版本 | 僅 PREPARING 可取消 |
| `POST /activities/{id}/finish` | 預期活動版本 | 僅已定案 RESULTS 可結束 |
| `POST .../exports/preview`、`POST .../exports/write` | 定案快照及確認識別 | 新定案版本使舊預覽失效 |
| `GET .../exports/{attempt}`、`POST .../exports/{attempt}/retry` | 寫入嘗試識別 | 結果核對或同快照重試 |
| `GET .../exports/results.csv` | 老師身分 | 所有已定案輪次的提交紀錄 |

前端每秒查詢一份完整狀態，前景正常連線目標兩秒內同步。對外 state_version 是依角色／組別可見投影產生的遞增版本，不直接暴露內部全域版本；他組提交不得使學生版本、ETag 或快取差異洩漏他組提交次數。學生當輪回應亦不包含全域 receive_sequence，對外提交識別使用不含流水順序的不透明 id，避免從自己的兩筆提交推算他組活動。用投影版本捨棄舊回應；未變更可減少渲染，但不依賴前端判斷權限。學生投影先過濾當輪他組資料，不能只在 HTML 隱藏。歷史公開結果仍可查，活動當輪資料分開投影。暫估順位只對老師可見，依同一排序器計算但明確標為暫估。

倒數以 server_time 與 deadline 校正顯示，前端到零停用提交只是提示。斷線顯示錯誤並重試查詢；重新整理以 session 取得全量快照，不推測提交是否成功。

### 4. 並發、接收時間與截止

每活動使用同一把鎖序列化提交、關輪、判錯、定案及開輪。服務進入提交臨界區時為本系統的「後端接收判定點」，此時取單調時鐘、UTC 時間與接收序號，先檢查 phase、round id、截止，再驗證答案及唯一鍵。未通過不占用提交機會。這一定義不宣稱量測封包到達網卡的時間，也不採用用戶端時間。

截止用單調時鐘比較；以服務啟動的 UTC 錨點加單調經過時間產生不倒退的 received_at，供排序、顯示及匯出，避免系統校時改變先後。接收序號消除同時間戳歧義。由排程觸發自動關輪，所有提交／狀態讀取也檢查是否到期，避免排程延遲讓晚到答案被接受。手動關輪先取得鎖便先封存；之後提交拒絕。關輪保存提交集合及固定題序一次，重複 close 不另建結果。

相較資料庫唯一鍵與分散式交易，單 process 的活動鎖足以符合本機存續範圍。禁止多 worker 是部署限制，不能只當效能建議。

### 5. 解析器與結構指紋

純解析器輸入 SheetSnapshot（稀疏值補成二維資料、合併範圍、工作表識別），輸出 Question／Blank 映射及結構摘要，不直接呼叫網路。Google adapter 必須取得合併 metadata，不能只讀 values。

1. 掃描唯一合法表頭列，判定題號欄及作答表頭範圍。
2. 題號正規化成正整數，從 1 連續；僅在題號欄的縱向合併範圍內補上續列題號。合併資料不擴散至一般間隔列。
3. 相鄰相同題號或合法合併續列形成一題；題組左欄必須具連續子編號，右側為答案格。單題作答合併範圍使用左上角，非合併單格亦可辨識。
4. 題號空白且沒有子空格的間隔列忽略；有子編號卻無合法題號歸屬、重疊目標、重複分離題號或模糊版型即整份失敗。
5. 返回題數、空格數及映射。表頭移位、工作表更名、正常間隔列不影響初次解析能力；匯入之後的結構變更則禁止寫回。

結構指紋包含來源 ids、表頭位置與文字、題號／子編號／目標座標及題目區相關合併範圍；不含答案文字、格式色彩與題目敘述。工作表名稱用 sheet_id 重新解析；更名若不影響座標與身份不必拒絕。每次寫入前重讀並重新解析比較映射，不憑表頭還在就認定安全。

範例測試基準：單題範例有 6 題，作答合併格左上角為 G5、G7、G9、G11、G13、G15；混合範例題號 B5:B6、B8:B9 縱向合併，兩題組答案在 H5、H6、H8、H9，單題答案在 G11、G13。這些座標只出現在測試預期，且須另以整體平移的同等結構測試，證明未硬編碼。

### 6. 純順位計算、版本與定案

排序器輸入固定題序、完整原始提交、判錯集合、模式及歷史基準，輸出整輪結果，不改動輸入。公平模式每題先按該時點次數排序全部候選，產出連續候選順位，再僅把勝者次數加一。速度模式只用接收時間及序號排序，但也累積勝者次數供後續公平輪使用。判錯者留在公開紀錄、順位為空。

判錯更新要求 expected_review_version，持鎖設定旗標、從基準完整重算、比較舊新結果列出變動題目，最後發布新版本。設為既有旗標是無變更，不額外累加。撤銷判錯恢復原時間；題序始終不改。所有候選都排除的題無勝者，沒有提交的題同樣未成功。

定案需要最新 review_version；鎖內保存不變快照並提升 finalized_revision。歷史次數由定案結果推導，重複確認不多算。相較只遞補當題，完整重算才能維持跨題公平語意。相較支援歷史改判，鎖定定案結果避免牽連後續輪及已匯出資料。

### 7. Google 授權與外部存取

規劃 Google OAuth Web 流程、Picker 選取原 Google Sheets、後端 Sheets API 讀寫，採 `drive.file`，不要求存取所有 Drive 檔案。使用伺服端 Google client 程式庫／HTTP adapter，將授權和讀寫包在介面內以便 fake adapter 測試。服務帳號是可能的後續設計變更，並非目前默認自動退路；若 OAuth 驗證失敗，回報阻塞並另行決定，不能悄悄換產品流程。

重要限制：官方 Web Picker 需要 OAuth token 傳給瀏覽器 PickerBuilder。因此不能照抄參考提案「所有 token 永不離開後端」的絕對說法。client secret 與 refresh token 留在伺服端；Picker 僅在已授權老師頁面短暫持有必要 access token，不放 URL、localStorage、檔案或 log，選取結束後釋放頁面引用；後端保留本機活動所需 token 於記憶體。這不能令已發到瀏覽器的 token 立即失效，仍須依短效 token 與最小權限限制曝露。學生端不取得 Google token。

OAuth client secret 從環境指定的私有檔案載入，client id／Picker developer key 屬瀏覽器設定，須限制允許來源及啟用 API；提供給 repo 的只能是空值設定範例。OAuth callback 檢查 state，測試帳號、redirect URI、授權拒絕與逾期皆納入驗證。實作前以使用者指定的測試複本完成選檔、讀取 merge metadata、文字寫回與權限錯誤驗證，禁止用真實課堂原件做破壞性試驗。

技術依據（2026-11-17 查閱，未實際連接使用者帳號）：
- [Google Sheets 權限範圍](https://developers.google.com/workspace/sheets/api/scopes)：`drive.file` 限制至應用使用的檔案。
- [Web Picker 整合](https://developers.google.com/workspace/drive/picker/guides/web-picker)：PickerBuilder 需要 OAuth token、developer key 與 callback。
- [Sheets 工作表結構](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets)：讀取工作表合併範圍 metadata。
- [批次更新儲存格值](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate)：同一請求提供多個目標範圍。
- [值輸入模式](https://developers.google.com/workspace/sheets/api/guides/migration)：RAW 模式不將文字解析成公式。
- [FastAPI 模板整合](https://fastapi.tiangolo.com/advanced/templates/)：伺服端模板與靜態頁面資源整合。

### 8. 寫回快照、外部失敗與 CSV

預覽在活動鎖內取得當下 finalized_revision 與所有成功題，建立不可變 ExportSnapshot；未定案輪不列入。確認時檢查定案版本未改變，再固定本次 payload，外部 I/O 不持有活動鎖。開始外部寫入之後才出現的新定案結果不擴張本次 payload，需下一次預覽。每活動同時最多一個寫入嘗試。

將目標值以單一 values.batchUpdate、RAW 文字模式送出，僅指定答案位置、不傳格式更新。重讀驗證所有目標內容後才顯示已驗證成功；收到明確拒絕為失敗；逾時、回應遺失或核對失敗則為待確認／需重試。讀回內容可證明目前答案符合預期，不能證明是誰寫入。重試使用原 snapshot，重查結構並覆寫相同值；若有較新定案結果，清楚顯示本次仍是舊快照，不暗中追加。

Google 外部修改不受本機鎖約束。結構檢查與寫入之間仍存在競爭窗口，本版要求寫回期間不並行修改學習單結構；這是操作限制，不宣稱具備外部原子比較交換。寫入後結構核對不符時報待確認並停止自動重試，不盲目清空或回復儲存格。原有答案覆寫屬老師預覽確認的效果，原件的備份由測試複本及人工作業處理。

CSV 使用 Python 標準 csv 模組與 UTF-8 BOM，依輪次、題號、候選順位（判錯最後）、接收序號及子空格穩定排序。每個提交子格一列；無提交不造資料列，組別欄填數字組號。時間為 ISO 8601 UTC、保留微秒；同時間先後可由順位理解，判錯順位留空。對答案中可觸發公式的前綴（含前導空白／控制字元後的 =、+、-、@）加單引號文字保護，並在下載說明中告知。CSV 安全輸出不回寫至原答案。

### 9. 驗收與需求追溯

| 需求群 | 規格 | 主要驗證 |
| --- | --- | --- |
| C1、E1 | guided-notes-import | 兩份範例、平移版型、縱向合併、缺表頭／題號／子格錯誤、整份失敗 |
| C2、C9 | activity-session | 老師＋兩組同步、非法狀態轉移、倒數與手動關輪競爭、重連／重啟 |
| C3、C5、C6、C7、E2、E6 | round-submission | 多題順序、題組完整、並發去重、截止邊界、HTTP 層可見性、看板 |
| C8、E3、E4 | ranking-review | 速度、公平、同時間戳、跨題重算、撤銷、全錯重開、版本衝突及定案冪等 |
| C4、E5 | result-export | 精確寫回、結構漂移、文字模式、預覽失效、逾時核對、重試、完整 CSV |

自動測試分為純函式、帶可控時鐘／repository 的服務測試、HTTP 權限與角色投影、獨立瀏覽器 context 的端到端流程。使用可控時鐘模擬截止邊界，不用真實等待或單純 sleep 證明先後。外部 Sheets 以 fake adapter 驗證失敗類型，再另行做真實測試複本的人工整合驗證；假 adapter 通過不能宣稱 Google OAuth 已通過。

人工驗收至少包含老師與兩組、速度／公平差異、判錯使兩題勝者互換、定案鎖定、全錯下一輪、題組寫回及 CSV。由非主要負責者執行，記錄實際結果、版本與限制，並製作五分鐘內完整流程 demo。不得在驗證紀錄或 AI 對話輸出中放憑證。

## Risks / Trade-offs

- [與另一小組功能不同] → 在整合前比對 C1–C9、E1–E6、固定題序、定案、CSV 欄位與題組計數；若對方規則不同，另行修訂規格，不能把外部回覆當作已獲確認。
- [助教對題數口徑不同] → 本版明確顯示題數與空格數並以題組為一次勝負；若官方要求改變，走需求變更，不暗改實作。
- [OAuth／Picker 設定未完成] → 最先安排具體整合驗證；失敗時可繼續領域及 fake adapter 測試，但真實匯入匯出驗收保持未完成。
- [記憶體遺失與多 worker 不一致] → 限單 process 並明示重啟清空；本版不把這項限制包裝成資料持久化。
- [網路延遲影響勝者] → 定義後端接收順序為權威；不承諾衡量學生實際按鍵先後。
- [公平模式非直覺遞補] → 顯示勝出理由、暫定標記與跨題變動，定案才鎖定。
- [外部結構並行修改] → 寫前／寫後比對、操作期間避免編輯，遇到不明結果停止而不自動回滾。
- [支援版型有限] → 明列單題／合併題號／相鄰重複題號契約，模糊格式清楚拒絕；不承諾任意試算表都能解析。

## Migration Plan

本專案無既有資料或服務，沒有資料搬遷。後續 apply 先建立依賴與本機啟動方式，再依 tasks 分階段交付、驗證後整合。首次 Google 測試使用明確指定的學習單複本，所有驗收成功後才以實際課堂檔操作。

停止服務即結束記憶體活動，不提供應用狀態回滾；部署回退可回到前一程式版本，但不能復原已覆寫的 Google Sheets 內容。外部資料復原需另行人工處理，不在重試邏輯內偷偷修改。這次 propose 只交付規劃，沒有部署或寫入外部資料。

## Open Questions

以下是可於實作配置時填入、且不改變本版行為的事項：Google Cloud 專案由哪位組員管理、是否共用專案、測試 Google 帳號、允許的本機 port／redirect URI，以及指定測試學習單複本。跨組規則對齊、助教口徑與 OAuth 驗證列為上方風險及 tasks 檢查點；如結果不符，本版不能宣稱已通過驗收。
