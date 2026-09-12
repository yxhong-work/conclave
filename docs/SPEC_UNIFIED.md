# Conclave 整體規格書（Unified Spec）

> 本文件是 Conclave 的**整體視角規格書**，與三份拆分規格書併用：
> - [`SPEC_AI_LAYER.md`](SPEC_AI_LAYER.md) — AI 智慧層（第一人）
> - [`SPEC_APP_SERVER.md`](SPEC_APP_SERVER.md) — 應用伺服器與前端（第二人）
> - [`SPEC_INFRA.md`](SPEC_INFRA.md) — 基礎設施與配置（第三人）
>
> **用途**：每位實作者拿自己的拆分規格書（細節）+ 本份整體規格書（全域視角）一起開發。
> 拆分規格書定義「你那塊怎麼做」；本份定義「整體怎麼運作、你那塊與另兩塊在哪裡接合、哪些跨層不變量不能破」。
> 本份**不重複**拆分規格書的實作細節，只編排整體工作流與接合面。
>
> **整合時**：三人實作完成後，由一人主導整合——見 [`SPEC_INTEGRATION.md`](SPEC_INTEGRATION.md)，那是「把三份程式碼接起來」的動作指南（合併順序、衝突仲裁、接合驗證）。本份是「接合面該長什麼樣」的設計；整合指引是「怎麼接」的執行。

---

## 0. 讀法

| 你負責 | 必讀順序 |
|---|---|
| AI 智慧層 | 本份 → SPEC_AI_LAYER.md（你的細節）→ §6 接合表對照 SPEC_APP_SERVER §3/§6/§8 與 SPEC_INFRA §4/§5 |
| 應用伺服器+前端 | 本份 → SPEC_APP_SERVER.md（你的細節）→ §6 接合表對照 SPEC_AI_LAYER §0/§2/§3 與 SPEC_INFRA §4/§5 |
| 基礎設施 | 本份 → SPEC_INFRA.md（你的細節）→ §6 接合表對照 SPEC_APP_SERVER §2/§9 與 SPEC_AI_LAYER §0 |

本份的 §1–§5 是「整體怎麼運作」；§6 是「三塊在哪裡接合」（最重要的整合地圖）；§7 是「跨層不變量」（任何人改動都不可破的紅線）；§8 是「整合驗證順序」。

---

## 1. 系統定位與整體架構

Conclave 是 **Privacy-Preserving Multi-Agent Coordination** 的 PoC：成員私下提交想法，後端收齊後由 LLM 整合成共識摘要，以 SSE 即時推播給全員。成員之間看不到彼此的原始想法，只看到彙整後的共識。核心隱私設計是「結構性隔離而非政策性規勸」——LLM 看不到的東西就無法洩漏。

```
瀏覽器 (React + Vite + TS)  ──HTTP/SSE──▶  FastAPI (uvicorn 單 worker)  ──SQL──▶  Postgres 16
                                                │
                                                ├─OpenAI SDK (async)─▶  LLM 端點（OpenAI 相容）
                                                └─MCP SDK (stdio/HTTP)─▶  外部研究工具
```

三個 Docker 容器（db / backend / frontend），後端單 worker（in-process SSE 廣播不跨 worker）。

### 三層職責

| 層 | 規格書 | 職責 | 一句話 |
|---|---|---|---|
| **AI 智慧層** | SPEC_AI_LAYER | LLM 共識管線 + MCP 研究 + 適應性個人化提問 | 「把成員想法變成共識、為每人生成探底問題、研究外部資訊」 |
| **應用伺服器+前端** | SPEC_APP_SERVER | FastAPI 路由 + 狀態機 + SSE + 資料存取 + 前端 SPA | 「收齊回覆、管控狀態、推播事件、呈現畫面」 |
| **基礎設施** | SPEC_INFRA | 容器編排 + 配置 + 資料綱要 + 測試基礎設施 | 「讓整體跑得起來、設定可調、綱要正確」 |

---

## 2. 端到端工作流（三層如何協作）

這是理解「三塊怎麼接起來跑一輪分析」的核心。每一步標註由哪層負責。

### 2.1 建立群組 → 加入 → 提交

1. **[前端]** Create 頁填問題/暱稱/選項（含 adaptive 核選框）→ `createGroup()` → `POST /api/groups`
2. **[應用]** `create_group`：`generate_pin()`[應用] + `secrets.token_urlsafe` 產 creator_token → INSERT groups+participants+rounds 種子列 → 回 `{pin, participant_id, creator_token}`
3. **[前端]** 寫 `sessionStorage`（per-tab 身分）→ 導向 `/g/:pin`
4. **[前端]** 其他人 Home 頁憑 PIN 加入 → `POST /api/groups/{pin}/join` → **[應用]** `join_group`：INSERT participants + `set_member_seq`（穩定編號）+ 廣播 `progress`
5. **[前端]** Group 頁 `useGroupSSE` 開 `EventSource` 連 `/api/events/{pin}` → **[應用]** `event_stream`：`subscribe(pin)` 取 Queue
6. **[前端]** 成員送出想法 → `POST /api/groups/{pin}/responses` → **[應用]** `submit_response`：INSERT responses → **收齊觸發**（`SELECT FOR UPDATE` + 條件 UPDATE `status='analyzing'`）→ 廣播 `phase analyzing` + `start_analysis_task`[AI]

### 2.2 分析階段（AI 層核心，三層交接處）

`start_analysis_task` 是**應用層 → AI 層的入口**（fire-and-forget，非 await）：

7. **[AI]** `run_analysis(pool, group_id)`：
   - **[應用 db]** `get_group_by_id` 讀群組 + `get_round_responses_ordered` 讀本輪回覆（含 member_seq + participant_id）
   - **[AI]** `probe_model`（失敗→**[應用 db] `set_group_status('error')`** + 廣播 error → return）
   - **A1 草案**：`build_consensus_prompt`（label-blind，意見經 `_shuffle` 洗序 + 臨時 A/B/C）→ `_chat_with_retry`（失敗→error→return）
   - **A2 研究**（僅 `mcp_enabled`）：
     - **[AI]** 跨輪成本控制（`_jaccard` >= 0.7 重用前輪 brief）否則 `research_phase`
     - **[AI mcp_tools]** `connect` + `register_tools`（MCP session）→ planner LLM → 逐一執行工具 → brief
     - 廣播 `research started/done`；`mcp_required` 降級→error
   - **A3 最終共識**：有 brief 則草案+brief 融合（失敗退回草案），否則用草案 → `_post_consensus`
   - **B 立場**：`build_stance_prompt`（labeled 成員N）→ `_chat_with_retry` → `_parse_stance_output` → `stance_shift_summary` 經 `_strip_attributable`（fail-open，失敗留 None）
   - **P 適應性**（`adaptive_on` 且非最後輪且有回覆）：`_run_p_stage` — 每位成員一次 P_X 呼叫（只見該成員自己資料 + 群體公開），`validate_member_question`（0-3 題/NONE 哨兵），`replace_member_questions`[應用 db] 單筆 upsert
   - **[應用 db]** `set_round_done`（事務：寫 rounds.consensus/stance_digest/stance_shift_summary/research_brief + groups.status=done）
   - 廣播 `consensus {content, round}` + `phase {status:done, round}`

8. **[前端]** SSE 收到 `consensus` → dispatch → phase=done，ReactMarkdown 渲染共識卡片

> **順序保證**：A1 → A2 → A3 → B → P → `set_round_done` → 廣播。P 在 `set_round_done` 之前——這是跨層不變量（§7），讓覆寫路徑無法被 P 的 late upsert 擊敗。

### 2.3 多輪與終局

9. **[前端]** done 畫面，建立者按「開啟下一輪」 → `POST /api/groups/{pin}/rounds/next`
10. **[應用]** `open_next_round`：覆寫分支（`req.question` 非空 > member_questions > Call Q 階梯[AI `generate_next_question`]）→ 事務內 INSERT rounds + 條件 UPDATE gate + override 時 `clear_member_questions` → `prune_delivered_member_questions` → 廣播 `round {adaptive}`
11. **[前端]** SSE `round` 事件 → `round_opened`，adaptive 群組 `getMyQuestion()` 取個人化題 → 顯示「你的這一輪問題」卡片
12. 終局：建立者 `POST /rounds/close` 或 24h auto-close[應用 tasks] 或建立者解散 `/leave` → `try_close_group` + `purge_future_member_questions` → 廣播 `round closed`

---

## 3. 群組狀態機（跨層共有，不可破）

狀態機由**應用層**的 `db.py` 閘門函式守護，但**所有層**都必須尊重它的轉移規則：

```
collecting ──► analyzing ──► done ──►（open_next_round）──► collecting（下一輪）
    │              │           │
    │              │           └──► closed（close / auto-close / dissolve）
    │              └──► error ──┐
    │                            └──►（POST /start retry）──► analyzing
    └──（deadline 到 + sc==0：廣播 error，保持 collecting）
```

| 轉移 | 觸發 | 閘門（條件式 UPDATE + rowcount=="UPDATE 1"） |
|---|---|---|
| collecting→analyzing | 收齊 / 手動 start / deadline | `try_enter_analyzing`（`status IN ('collecting','error')`） |
| analyzing→done | AI 層 `set_round_done` | — |
| analyzing→error | AI 層 `set_group_status('error')` | — |
| error→analyzing | 建立者 retry | `try_enter_analyzing` |
| done→collecting | 開下一輪 | `try_open_next_round`（`status='done' AND current_round<max_rounds`） |
| done→closed | close / auto-close / dissolve | `try_close_group` |

**三條觸發路徑（收齊 / 手動 start / deadline）全部收斂到同一 `try_enter_analyzing` 原子閘門**——這是應用層的核心設計，AI 層的 `start_analysis_task` 永遠在閘門通過後才被呼叫。

---

## 4. 隱私模型（貫穿三層的紅線）

隱私不是某一層的事，是三層共同維護的不變量：

| 隱私保證 | 由哪層實作 | 機制 |
|---|---|---|
| **成員間看不到彼此原文** | 應用（API 不回傳他人 content）+ 前端（只顯示共識） | `GET /rounds` 不含 content；`GET /state` 只回計數 |
| **共識 LLM 看不到穩定身分** | AI 層 | Call A label-blind：意見 `_shuffle` 洗序 + 臨時 A/B/C（每輪重設） |
| **立場資料不回流公開軌** | AI 層 + 應用 db | Call B 輸出只寫 `rounds.stance_digest`（私有）；`stance_shift_summary` 進下輪共識前先 `_strip_attributable`；`get_rounds_history` 永不 SELECT stance_digest/research_brief |
| **個人化問題只給收件人** | 應用（`/my-question` 鑑權）+ AI 層（P_X 結構隔離）+ 前端（只顯示自己的） | `member_questions` 永不進 SSE 廣播、永不進 list 端點；P_X 輸入只含 X 自己資料 |
| **raw opinions 不進 planner/tools** | AI 層 | MCP planner 只見問題+草案；工具參數只從問題與草案推導 |
| **機密不進 log** | AI 層 | `redact()` 遮環境變數；P 階段日誌只記 error class，不記 prompt/opinion/question |
| **access log 不洩 participant_id** | 基礎設施 | `--no-access-log`（participant_id 是 bearer token，走 query string） |

**敏感度光譜**：`consensus`（全員）< `member_questions`（僅收件人）< `stance_digest`/`research_brief`（僅系統）。

---

## 5. 資料模型概觀（詳細 DDL 見 SPEC_INFRA §5）

四張表，跨層共用：

| 表 | 跨層角色 |
|---|---|
| `groups` | 應用讀寫狀態機欄位（status/current_round/max_rounds/adaptive_questions）；AI 層讀 question/current_round/max_rounds/adaptive_questions |
| `participants` | 應用讀 member_seq（穩定立場標籤）；`ON DELETE CASCADE` 連帶刪 responses + member_questions |
| `responses` | 應用 INSERT（提交）；AI 層讀 content+member_seq+participant_id（P 階段枚舉） |
| `rounds` | AI 層寫 consensus+stance_digest+stance_shift_summary+research_brief（`set_round_done`）；應用讀 consensus/question（公開）+ stance_digest/research_brief（私有，跨輪 context） |
| `member_questions` | AI 層寫（P 階段 upsert）；應用讀（`/my-question`）+ 清理（override/close/prune） |

**隱私分級在 schema 層就分開**：`stance_digest`/`research_brief` 只存在 `rounds` 表，`get_rounds_history` 的 SELECT 清單**永不包含**這兩欄（應用層不變量）。

---

## 6. 三層接合地圖（整合時看這裡）

這是分頭實作後能否接合的關鍵。每個接合點標註「提供方 → 消費方」與精確契約。

### 6.1 應用層 → AI 層（應用層呼叫 AI 層的 4 個入口）

| 接合點 | 提供方 | 消費方 | 契約 |
|---|---|---|---|
| 啟動分析 | AI 層 | 應用（groups/responses/tasks） | `def start_analysis_task(pool, group_id) -> asyncio.Task`（**非 async**，內部 `create_task`，不 await） |
| 下一輪問題 | AI 層 | 應用（groups open_next_round） | `async def generate_next_question(prev_consensus: str, prev_question: str) -> str`（失敗拋出，呼叫端 fallback `_seed_next_question`） |
| 機密遮蔽 | AI 層 | 應用（groups 錯誤日誌） | `def redact(value: object) -> str` |
| 關機清理 | AI 層 | 應用（main lifespan） | `async def shutdown_analysis_tasks(timeout: float = 10.0) -> None` |

> 這 4 個全是 **late import**（應用層在函式內 `from ..llm import ...`），避免循環依賴與測試 monkeypatch 失效。

### 6.2 AI 層 → 應用層（AI 層透過 db.py 與 broadcast 讀寫）

| 接合點 | 提供方 | 消費方 | 契約 |
|---|---|---|---|
| 讀群組 | 應用 db | AI 層 | `async get_group_by_id(pool, group_id) -> Record|None`（回傳含 `pin, question, current_round, max_rounds, adaptive_questions` 等 12 欄） |
| 讀本輪回覆 | 應用 db | AI 層 | `async get_round_responses_ordered(pool, group_id, round_number) -> list[Record]`（含 `content, member_seq, participant_id`，ORDER BY member_seq） |
| 讀前輪 context | 應用 db | AI 層 | `async get_round_consensus(pool, group_id, round_number) -> Record|None`（含 `consensus, stance_digest, stance_shift_summary, research_brief, question`） |
| 寫回合結果 | 應用 db | AI 層 | `async set_round_done(pool, group_id, round_number, consensus, stance_digest, stance_shift_summary, research_brief=None) -> None`（事務） |
| 設群組狀態 | 應用 db | AI 層 | `async set_group_status(pool, group_id, status, consensus=None) -> None` |
| P 階段讀前題 | 應用 db | AI 層 | `async get_prior_member_questions(pool, group_id, participant_id, up_to_round) -> list[tuple[int,str]]` |
| P 階段寫題 | 應用 db | AI 層 | `async replace_member_questions(pool, group_id, round_number, items: list[tuple[uuid,str]]) -> None`（單筆 upsert ON CONFLICT DO UPDATE） |
| 推播事件 | 應用 broadcast | AI 層 | `async broadcast(pin: str, event_type: str, data: dict) -> None` |

### 6.3 基礎設施 → 兩層（Settings + schema + 環境）

| 接合點 | 提供方 | 消費方 | 契約 |
|---|---|---|---|
| 配置 | infra config | AI + 應用 | `get_settings() -> Settings`（`@lru_cache` 單例）；欄位名見 SPEC_INFRA §4，兩層讀取的欄位名必須與之一致 |
| 資料綱要 | infra migrations | 應用 db | 四張表欄位/約束/索引見 SPEC_INFRA §5；`db.py` 的 SELECT 欄位清單必須與 DDL 一致 |
| 執行環境 | infra compose | 全部 | 三容器編排；**backend 必須掛 `mcp_config.yaml` 與 `.env`**（否則 AI 層 `load_config()` 失敗） |
| 測試環境 | infra pytest/conftest | 應用測試 | `TEST_DSN`、`TRUNCATE groups,participants,responses,rounds,member_questions CASCADE` |

### 6.4 SSE 事件契約（AI 廣播 ↔ 前端監聽）

| event | data | 廣播方（AI/應用） | 前端 handler |
|---|---|---|---|
| `phase` | `{status, round?}` | 應用（start/收齊/deadline）+ AI（done/error） | `onPhase` → dispatch `phase` |
| `progress` | `{participant_count, submitted_count, round?}` | 應用（join/leave/submit） | `onProgress` |
| `consensus` | `{content, round}` | AI（分析完成） | `onConsensus` → phase=done |
| `round` | `{round, question, status, adaptive?}` | 應用（open_next/close/dissolve/auto-close） | `onRound?` → `round_opened`/`round_closed` |
| `research` | `{status, providers}` | AI（research started/done） | 前端未監聽 |
| `error` | `{message}` | 應用（deadline 零回覆）+ AI（分析失敗） | **見 §7 死碼說明** |

### 6.5 API 契約（應用 ↔ 前端）

12 個 endpoint，路徑/request model/response model 見 SPEC_APP_SERVER §6。前端的 `api.ts` 函式簽名必須與之逐欄對齊（已在跨份稽核驗證一致）。

---

## 7. 跨層不變量（任何人改動都不可破的紅線）

這些是「破了一個，三層都接不起來」的硬約束：

1. **P 在 set_round_done 之前**（AI 層 → 應用 db）：run_analysis 的步驟順序 A1→A2→A3→B→P→set_round_done→broadcast。若 P 在 done 之後，覆寫路徑會被 P 的 late upsert 擊敗，成員會在輪中由 anchor 被切成個人題。
2. **狀態轉移只用條件式 UPDATE + rowcount**（應用層）：不靠先 SELECT 後判斷。三條觸發路徑收斂到同一閘門，確保 `start_analysis_task` 每輪只啟動一次。
3. **Call A label-blind，Call B labeled，兩軌不回流**（AI 層 + 應用 db）：共識呼叫輸入物理上不含 member_seq/stance_digest；立場呼叫輸出只寫 rounds.stance_digest，stance_shift_summary 進下輪共識前先 _strip_attributable。
4. **stance_digest/research_brief 永不經 API 回傳**（應用層）：`get_rounds_history` 的 SELECT 清單永不包含這兩欄；`RoundInfo` model 結構上不能攜帶它們。
5. **member_questions 永不進 SSE 廣播與 list 端點**（應用 + AI + 前端）：只經 `/my-question` 單筆鑑權讀取。`broadcast()` 是 pin→全員 fan-out，任何 per-member 資料進 broadcast 即全員洩漏。
6. **raw opinions 永不進 MCP planner/tools**（AI 層）：planner 只見問題+草案；工具參數只從問題與草案推導。
7. **P 階段日誌紅線**（AI 層）：P_X 失敗的 log 只記 `group_id/round/participant_id` 與 error class，**絕不記** prompt/opinion/stance/question 內容（`redact()` 是 secret 紅線不是內容紅線）。
8. **`--no-access-log`**（基礎設施）：participant_id 是 bearer token 走 query string，access log 會洩漏。若重啟 access log 必須同時加 query-string-stripping filter。
9. **compose 必須掛 mcp_config.yaml + .env**（基礎設施）：AI 層 `mcp_tools.py` 的 `CONFIG_PATH` 在容器內解析為 `/mcp_config.yaml`，未掛則 MCP 啟用時 `load_config()` 拋 `RuntimeError`。
10. **Settings 欄位名跨層一致**（infra 定義，AI/應用消費）：三方讀取的 `settings.*` 欄位名必須與 SPEC_INFRA §4 的定義分毫不差（已驗證一致）。

### 關於 `error` 事件的前端 handler 死碼

後端 `broadcast(pin, "error", {"message":...})` 廣播 SSE 自訂事件 `event: error`，但前端只有 `es.onerror`（原生連線錯誤）而**沒有** `addEventListener("error", ...)`。所以 `SSEHandlers.onError` 介面存在但實際上**不會被 server 廣播的 error 事件觸發**——server 端 error 廣播的效果是斷線後 `onDisconnect → fetchState()`，讀到 `status: "error"` 經 `mapPhase` 切到 error phase。這是現況（規格書反映現況），實作時可選擇維持或補 `addEventListener("error")` 讓 onError 生效——但**不可**移除 `onDisconnect → fetchState` 的兜底路徑。

---

## 8. 整合驗證順序

三份分頭實作完成後，依此順序接合驗證（每步對應一個接合面）：

1. **infra 起得來**：`docker compose up --build` → `/api/health` 回 `{"ok":true}` → migration 自動跑（四張表存在）
2. **應用層基本 API**：`POST /groups` → `GET /state` → `POST /join` → `POST /responses`（不觸發分析，expected_count=None）
3. **收齊觸發接 AI 層**：設 `expected_count=2`，兩人提交 → 狀態翻 `analyzing` → `start_analysis_task` 被呼叫（用 fake `run_analysis` mock 驗證被觸發）
4. **AI 層 run_analysis 完整跑**：mock AsyncOpenAI + probe_model → A1/A2/A3/B 跑完 → `set_round_done` 寫回 → 廣播 `consensus`+`phase done` → `GET /state` 看到 done
5. **P 階段（adaptive 群組）**：`adaptive_questions=true` 群組跑分析 → `member_questions` 有 row → `/my-question?round=N+1` 回 `is_personal=true`
6. **多輪**：done → `POST /rounds/next`（帶 question=override 測 clear；不帶測 Call Q）→ 狀態回 collecting → `round` 事件 → 前端 `round_opened`
7. **終局清理**：close / auto-close / dissolve → `purge_future_member_questions` → 未來輪 row 消失
8. **隱私不變量**：`GET /rounds` 不含 stance_digest/research_brief；`/my-question` 無效 pid 回 anchor 非 404；P_X prompt 不含他人意見與成員標籤（用 prompt capture 測試）
9. **並發**：兩人同時交最後兩票 → `start_analysis_task` 只被呼叫一次
10. **MCP（選用）**：`MCP_ENABLED=true` + `MCP_PROVIDERS=maps` + `GOOGLE_MAPS_API_KEY` → A2 研究跑通 → brief 進共識

每步對應 SPEC_APP_SERVER §11 / SPEC_AI_LAYER §9 / SPEC_INFRA §11 的整合檢核表項目。

---

## 9. 三份規格書的對應索引

| 本份章節 | 對應的拆分規格書章節 |
|---|---|
| §2 端到端工作流 | AI: §2 run_analysis / 應用: §2 lifespan + §6 API / 前端: §10 |
| §3 狀態機 | 應用: §3 db helpers + §7 狀態機表 |
| §4 隱私模型 | AI: §7 隱私 / 應用: §3.4 get_rounds_history + §6.10 my-question / infra: §5 隱私分級 + §2 --no-access-log |
| §5 資料模型 | infra: §5 DDL / 應用: §3 db helpers |
| §6 接合地圖 | AI: §0 整合接縫 / 應用: §0 整合接縫 / infra: §0 整合接縫 |
| §7 跨層不變量 | AI: §7+§8+§9 / 應用: §11 / infra: §11 |
| §8 整合驗證 | AI: §9.5 / 應用: §11 / infra: §11 |

當你在自己的拆分規格書裡看到「見整體規格書 §X」時，回來這裡看全域視角；當你在本份看到「見 SPEC_*.md §Y」時，去拆分規格書看實作細節。
