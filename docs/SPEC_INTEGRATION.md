# Conclave 整合指引（SPEC_INTEGRATION）

> 本文件是**整合階段的動作指南**，不是設計文件。
> 它服務的場景：三位隊員各拿自己的拆分規格書（`SPEC_AI_LAYER` / `SPEC_APP_SERVER` / `SPEC_INFRA`）+ `SPEC_UNIFIED` 用 agent 分頭實作後，由**一人主導整合**，把三份程式碼合併成一套功能完整的 Conclave。
>
> **與其他規格書的分工**：
> - `SPEC_UNIFIED.md` 定義「接合面應該長什麼樣」（設計視角）
> - 三份拆分規格書定義「每塊怎麼做」（實作視角）
> - **本份定義「怎麼把三塊接起來、碰到不一致怎麼辦、怎麼驗證接對了」**（執行視角）
>
> 本份不重複架構描述。遇到接合面的契約定義，引用 `SPEC_UNIFIED §6`；遇到單塊的實作細節，引用對應拆分規格書。
>
> **給 agent 整合**：若要讓 agent（codex）自主執行整合，見 [`AGENT_INTEGRATION.md`](AGENT_INTEGRATION.md)——本份的執行步驟改寫成 agent 可直接執行的指令、仲裁規則的決策樹、停止問人的觸發條件與報告格式。本份是給人讀的說明；AGENT 版是給 agent 跑的手冊。兩份內容對應，但 agent 版是可執行的。

---

## 0. 整合前提檢查（動工前先做）

整合者拿到三份實作後，**先別急著合併程式碼**。先做這幾項檢查，能在 5 分鐘內發現 80% 的接合問題：

### 0.1 目錄結構對齊

三份實作必須落到同一目錄結構，否則 import 路徑全部對不上。對照 `SPEC_UNIFIED §1` 的架構，確認：

```
conclave/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py          ← 應用層
│   │   ├── config.py        ← 基礎設施層
│   │   ├── db.py            ← 應用層
│   │   ├── broadcast.py     ← 應用層
│   │   ├── tasks.py         ← 應用層
│   │   ├── pin.py           ← 應用層
│   │   ├── models.py        ← 應用層
│   │   ├── llm.py           ← AI 層
│   │   ├── mcp.py           ← AI 層
│   │   ├── mcp_tools.py     ← AI 層
│   │   └── routes/
│   │       ├── __init__.py  ← 應用層
│   │       ├── groups.py    ← 應用層
│   │       ├── responses.py ← 應用層
│   │       └── events.py    ← 應用層
│   ├── migrations/
│   │   ├── 001_init.sql         ← 基礎設施層
│   │   ├── 002_rounds.sql       ← 基礎設施層
│   │   └── 003_adaptive_questions.sql ← 基礎設施層
│   ├── tests/
│   │   ├── conftest.py      ← 基礎設施層
│   │   └── test_*.py        ← 各層（見 §0.4）
│   ├── requirements.txt     ← 基礎設施層
│   ├── pytest.ini           ← 基礎設施層
│   └── Dockerfile           ← 基礎設施層
├── frontend/
│   ├── src/
│   │   ├── App.tsx, main.tsx        ← 應用層
│   │   ├── pages/ (Home, Create, Group) ← 應用層
│   │   ├── hooks/ (useGroupSSE, useTypewriter) ← 應用層
│   │   ├── lib/api.ts               ← 應用層
│   │   └── styles/index.css         ← 應用層
│   ├── package.json         ← 基礎設施層
│   ├── vite.config.ts      ← 基礎設施層
│   └── Dockerfile          ← 基礎設施層
├── compose.yml              ← 基礎設施層
├── mcp_config.yaml          ← AI 層
├── .env.example             ← 基礎設施層
└── .gitignore               ← 基礎設施層
```

> **檢查動作**：三份實作各自的檔案是否落到上面對應位置？若有人把 `config.py` 放到 `app/` 以外、或把 `mcp_tools.py` 放到非 `app/` 目錄，先修正位置——後續所有 import 路徑都依此結構。

### 0.2 接縫簽名三向對齊

打開 `SPEC_UNIFIED §6` 的接合地圖，逐項確認三份實作的簽名一致。這是整合成敗的關鍵——簽名不一致，接合就斷。

**AI 層提供的 4 個入口**（`SPEC_UNIFIED §6.1`）——整合者核對 AI 層實作與應用層呼叫端：

| 函式 | 簽名 | 來自 | 呼叫端 | 核對動作 |
|---|---|---|---|---|
| `start_analysis_task` | `def (pool, group_id) -> asyncio.Task`（**非 async**） | AI 層 | 應用（groups/responses/tasks） | grep 三個呼叫端，確認**無 await**、參數順序一致 |
| `generate_next_question` | `async (prev_consensus: str, prev_question: str) -> str` | AI 層 | 應用（groups open_next_round） | 確認呼叫傳兩個 str、用 try/except 接 fallback |
| `redact` | `def (value: object) -> str` | AI 層 | 應用（groups 日誌） | grep `redact(` 確認呼叫端傳入與取用 str |
| `shutdown_analysis_tasks` | `async (timeout: float = 10.0) -> None` | AI 層 | 應用（main lifespan） | 確認 lifespan shutdown 用 `await` + late import |

**AI 層依賴應用層的 7 個 db helper + broadcast**（`SPEC_UNIFIED §6.2`）——核對應用層實作與 AI 層呼叫端：

| 函式 | 核對動作 |
|---|---|
| `get_group_by_id` | AI 層讀 `g["pin"]/g["question"]/g["current_round"]/g["max_rounds"]/g["adaptive_questions"]`——應用層 SELECT 清單必須含這 5 個（外加其他 7 個） |
| `get_round_responses_ordered` | AI 層讀 `r["content"]/r["member_seq"]/r["participant_id"]`——應用層 SELECT 必須含這 3 欄 + JOIN participants |
| `get_round_consensus` | AI 層讀 `prev["consensus"]/["stance_digest"]/["stance_shift_summary"]/["research_brief"]/["question"]`——應用層 SELECT 這 5 欄 |
| `set_round_done` | 參數順序 `(pool, group_id, round_number, consensus, stance_digest, stance_shift_summary, research_brief=None)` |
| `set_group_status` | 參數 `(pool, group_id, status, consensus=None)` |
| `get_prior_member_questions` | 參數 `(pool, group_id, participant_id, up_to_round)`、回傳 `list[tuple[int,str]]` |
| `replace_member_questions` | 參數 `(pool, group_id, round_number, items: list[tuple[uuid,str]])`、`ON CONFLICT DO UPDATE` |
| `broadcast` | `async (pin, event_type, data)`——AI 層呼叫 6 種事件名（見 §0.3） |

> **檢查動作**：用 `grep -rn "def start_analysis_task\|def generate_next_question\|def redact\|def shutdown_analysis_tasks" backend/app/` 找 AI 層定義；再 `grep -rn "start_analysis_task\|generate_next_question\|redact\|shutdown_analysis_tasks" backend/app/routes/ backend/app/main.py backend/app/tasks.py` 找呼叫端，逐對核對簽名。

### 0.3 SSE 事件名對齊

AI 層廣播的事件名與 data 欄位，必須與前端 `useGroupSSE` 監聽的事件名 + 解析的欄位一致（`SPEC_UNIFIED §6.4`）：

| event | AI/應用廣播的 data | 前端解析的欄位 | 核對 |
|---|---|---|---|
| `phase` | `{status, round?}` | `d.status, d.round` | status 值：`analyzing`/`done`/`error` |
| `progress` | `{participant_count, submitted_count, round?}` | `d.participant_count, d.submitted_count, d.round` | |
| `consensus` | `{content, round}` | `d.content, d.round` | |
| `round` | `{round, question, status, adaptive?}` | `d.round, d.question, d.status, d.adaptive` | status 值：`opened`/`closed` |
| `research` | `{status, providers}` | 前端未監聽 | 不影響接合 |
| `error` | `{message}` | 見 `SPEC_UNIFIED §7` 死碼說明 | **不可移除** `onDisconnect→fetchState` 兜底 |

> **檢查動作**：`grep -rn "broadcast(pin," backend/app/llm.py backend/app/routes/ backend/app/tasks.py` 找廣播端，與 `frontend/src/hooks/useGroupSSE.ts` 的 `addEventListener` 比對事件名與 data 鍵名。

### 0.4 Settings 欄位名對齊

基礎設施層的 `Settings` 欄位名必須與 AI 層、應用層讀取的 `settings.*` 分毫不差（`SPEC_UNIFIED §7 #10`）：

> **檢查動作**：`grep -rn "s\.\|settings\.\|get_settings()" backend/app/llm.py backend/app/mcp.py backend/app/routes/ backend/app/tasks.py backend/app/main.py` 找所有 `settings.*` 讀取，與 `backend/app/config.py` 的 Settings 類別欄位逐一比對。**常見錯誤**：AI 層寫 `s.adaptive_questions`（群組欄位，非 Settings）混淆 `s.adaptive_questions_enabled`（Settings）——前者從 `get_group_by_id` 的 Record 讀、後者從 Settings 讀，不可混。

### 0.5 Migration schema vs db.py SELECT 欄位對齊

基礎設施層的 DDL 欄位必須與應用層 `db.py` 的 SELECT 清單一致（`SPEC_UNIFIED §7 #4`）：

> **檢查動作**：對照 `SPEC_INFRA §5` 的 DDL，確認 `get_group_by_id` SELECT 12 欄位全在 `groups` 表、`get_round_consensus` SELECT 5 欄位全在 `rounds` 表、`member_questions` 表欄位與 `replace_member_questions` INSERT 欄位一致。**關鍵不變量**：`get_rounds_history` 的 SELECT 清單**絕不可含** `stance_digest` 或 `research_brief`。

### 0.6 前置檢查做完的判定

- 全部通過 → 進 §1，開始合併
- 有不一致 → 先別合併，依 §2 的仲裁規則修正該份實作，再回來重跑 §0

---

## 1. 合併順序（一人主導的線性步驟）

三份實作合併有依賴順序——基礎設施是地基，應用層疊在上面，AI 層最後接。順序錯了會卡在中間反覆修 import。

### 步驟 1：建立整合分支 + 放入基礎設施層

```
git checkout -b integration
# 把第三人的 infra 實作放進來：
#   compose.yml, backend/Dockerfile, frontend/Dockerfile,
#   backend/app/config.py, backend/migrations/*, backend/pytest.ini,
#   backend/requirements.txt, frontend/package.json, frontend/vite.config.ts,
#   .env.example, .gitignore, backend/tests/conftest.py
git add -A && git commit -m "integration: infra layer"
```

**驗證**：
```bash
cp .env.example .env  # 填入 LLM_API_KEY
docker compose up -d db
# 確認 migration 可跑：用 infra 的 conftest.py + 一個空測試
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -m pytest tests/ --collect-only
docker compose down
```
若 migration 語法錯或 Settings 載入失敗，這裡就會炸——修到能跑再下一步。

### 步驟 2：放入應用伺服器層

```
# 把第二人的 app server 實作放進來：
#   backend/app/main.py, db.py, broadcast.py, tasks.py, pin.py, models.py,
#   backend/app/routes/*, frontend/src/*（全部前端）
git add -A && git commit -m "integration: app server + frontend"
```

**驗證**（應用層可獨立運作，AI 層用 stub）：
```python
# 在 backend/tests/ 寫一個臨時 stub 測試，monkeypatch run_analysis 為 fake
# 確認 app 能 import、API 能回應、SSE 能連
```
```bash
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -m pytest tests/test_groups_api.py tests/test_broadcast.py tests/test_pin.py -q
docker compose up -d
curl localhost:8000/api/health  # 應回 {"ok":true}
curl -X POST localhost:8000/api/groups -H "Content-Type: application/json" -d '{"question":"Q?","creator_nickname":"A"}'
# 應回 201 + pin
docker compose down
```
這步**不碰 AI 層**——若 `start_analysis_task` 被 import 失敗會炸，所以先 monkeypatch 或確認 AI 層檔案至少能 import（可放 AI 層的空骨架）。

### 步驟 3：放入 AI 層

```
# 把第一人的 AI 實作放進來：
#   backend/app/llm.py, mcp.py, mcp_tools.py, mcp_config.yaml
git add -A && git commit -m "integration: AI layer"
```

**驗證**（AI 層接上、完整分析跑通）：
```bash
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -m pytest tests/test_adaptive.py tests/test_llm.py tests/test_mcp_loop.py -q
# 確認 run_analysis 能被觸發（用 fake AsyncOpenAI mock）
```

### 步驟 4：整合測試

依 §3 的整合驗證順序跑完整接合測試。

---

## 2. 衝突仲裁規則（碰到不一致時以誰為準）

三份實作即使都對著規格書寫，仍會有接合不一致。以下是仲裁規則——**整合者照此決定改哪份，不開會討論**：

### 2.1 簽名衝突：以「被依賴方」為準

當 A 層的函式簽名與 B 層的呼叫端不一致時，**改呼叫端配合被依賴方**，不改被依賴方。

| 衝突類型 | 以誰為準 | 理由 |
|---|---|---|
| AI 層的 `start_analysis_task` 簽名 vs 應用層呼叫 | **AI 層**（被依賴方） | 應用層是呼叫端，改呼叫端配合 |
| 應用層的 `db.py` helper 簽名 vs AI 層呼叫 | **應用層**（被依賴方） | AI 層是呼叫端 |
| infra 的 `Settings` 欄位名 vs 兩層讀取 | **infra**（被依賴方） | 兩層改讀取名配合 |
| infra 的 migration DDL vs 應用層 SELECT 欄位 | **infra DDL** | 應用層改 SELECT 清單配合 |

### 2.2 目錄/路徑衝突：以 §0.1 的標準結構為準

任何偏離 §0.1 目錄結構的實作，**改回標準結構**。這是硬規則——import 路徑全依此結構，不容妥協。

### 2.3 行為衝突：以 SPEC_UNIFIED §7 跨層不變量為準

當兩份實作對同一行為有不同做法（例如 P 在 set_round_done 前或後、redact 是否含 LLM_API_KEY、error 事件前端是否接）：

1. 先查 `SPEC_UNIFIED §7` 是否列為不變量——若是，**以不變量為準**，改違反的那份
2. 若不在 §7，查對應拆分規格書的章節——以拆分規格書的明確定義為準
3. 仍無定義——整合者依 `SPEC_UNIFIED §2` 的端到端工作流判斷哪個做法符合整體運作

### 2.4 測試 fixture 衝突：以 infra 的 conftest 為準

`conftest.py` 的 `TEST_DSN`、`TRUNCATE` 清單、`_reset_global_pool` fixture 由 infra 層定義，其他層的測試必須用它，不各自重定義。`TRUNCATE` 清單必須是 `groups, participants, responses, rounds, member_questions CASCADE`（五表）——缺一個就會有測試殘留資料。

### 2.5 依賴衝突：以 infra 的 requirements.txt 為準

`requirements.txt` 與 `package.json` 由 infra 層定義。AI 層或應用層若用了新套件，**加到 infra 的 requirements.txt**，不在自己的檔案裡另建。版本以 infra 為準。

---

## 3. 接合驗證順序（整合後照此跑）

整合完三層後，依此順序驗證接合面，**每步通過再下一步**——前面的接合是後面的前提：

### 3.1 啟動接合（infra → 應用）

```bash
docker compose up --build
curl localhost:8000/api/health  # {"ok":true}
# 確認 migration 自動跑、四張表存在
docker exec conclave-db psql -U conclave -d conclave -c "\dt"
# 應見 groups, participants, responses, rounds, member_questions
```
**失敗時**：查 `docker logs conclave-backend`——常見是 Settings 載入失敗（欄位名不一致）、migration 語法錯、`DATABASE_URL` 不通。

### 3.2 基本 API 接合（應用 → 前端）

```bash
# 建組
curl -X POST localhost:8000/api/groups -H "Content-Type: application/json" \
  -d '{"question":"晚餐？","creator_nickname":"A","expected_count":2}' | jq
# 應回 pin + participant_id + creator_token

# 瀏覽器開 localhost:5173，用 PIN 加入、提交——確認前端能連後端
```
**失敗時**：查 vite proxy（`/api` → `backend:8000`）、API 路徑前綴（`/api`）、CORS。

### 3.3 收齊觸發接合（應用 → AI）

```python
# monkeypatch app.llm.run_analysis 為 fake（寫 done + 共識），確認被觸發
# 測試：兩人提交達 expected_count → status 翻 analyzing → fake_run 被呼叫一次
# 用 tests/test_integration.py 的 test_full_loop_create_join_submit_consensus 改
```
**失敗時**：確認 `start_analysis_task` 是非 async、`try_enter_analyzing` 閘門回 `UPDATE 1`、late import 路徑正確。

### 3.4 完整分析接合（AI → 應用 db → 廣播 → 前端）

```bash
# 設 LLM_API_KEY 指向真實端點（或 mock AsyncOpenAI + probe_model）
# adaptive=false 群組跑一輪：提交 → 分析 → done → 前端收到 consensus 事件 → 顯示共識
# 確認 set_round_done 寫回 rounds.consensus + groups.status=done
docker exec conclave-db psql -U conclave -d conclave -c \
  "SELECT status, consensus FROM groups WHERE pin='...'"
```
**失敗時**：查 `run_analysis` 的 A1/A2/A3/B 步驟、`set_round_done` 的事務、廣播事件名（`consensus`/`phase`）。

### 3.5 P 階段接合（AI → 應用 db → /my-question → 前端）

```bash
# adaptive=true 群組跑分析
# 確認 member_questions 有 row
docker exec conclave-db psql -U conclave -d conclave -c \
  "SELECT participant_id, question FROM member_questions WHERE group_id=... AND round_number=2"
# 確認 /my-question 回 is_personal=true
curl "localhost:8000/api/groups/{pin}/my-question?participant_id={pid}&round=2" | jq
# 確認前端顯示「你的這一輪問題」卡片、共同問題 hero 隱藏
```
**失敗時**：查 `_run_p_stage` 的 adaptive_on 條件、`replace_member_questions` 的 upsert、`/my-question` 的 effective flag。

### 3.6 多輪接合

```bash
# done → POST /rounds/next（不帶 question 測 Call Q；帶 question 測 override clear）
# 確認狀態回 collecting、round 事件廣播、前端 round_opened
# override 時確認 member_questions 被清空
docker exec conclave-db psql -U conclave -d conclave -c \
  "SELECT count(*) FROM member_questions WHERE group_id=... AND round_number=2"
```

### 3.7 終局清理接合

```bash
# POST /rounds/close → 確認 future member_questions 被 purge
# 建立者 /leave 解散 → 確認 purge + round closed 事件
# 等 24h auto-close（或調 AUTO_CLOSE_HOURS 測試）→ 確認 purge
```

### 3.8 隱私不變量驗證（不可跳過）

這些是「功能跑通了但隱私破了」的防線——必須逐一驗：

| 不變量 | 驗證方式 |
|---|---|
| `get_rounds_history` 不含 stance_digest/research_brief | `SELECT stance_digest FROM rounds` 有值，但 `GET /rounds` 回傳的 RoundInfo 無此欄 |
| `/my-question` 無效 pid 回 anchor 非 404 | 用亂數 UUID 查 → 200 + `is_personal:false` |
| P_X prompt 不含他人意見與成員標籤 | 在 `_run_p_stage.run_one` 加 prompt capture，斷言不含其他成員的 opinion 子字串、不含 `成員N` |
| stance_digest 不進 groups 表 | `SELECT stance_digest FROM groups` 恆空 |
| member_questions 不進 SSE 廣播 | grep `broadcast(pin,` 確認無任何 member_questions content 進 data |
| access log 關閉 | `docker logs conclave-backend` 無 uvicorn access log 行 |

### 3.9 並發接合

```python
# 兩人「同時」交最後兩票（用 asyncio.gather 或 threading）
# 確認 start_analysis_task 只被呼叫一次（用計數 mock）
# 用 tests/test_trigger.py 的觸發真值表
```

### 3.10 MCP 接合（選用）

```bash
# MCP_ENABLED=true, MCP_PROVIDERS=maps, GOOGLE_MAPS_API_KEY=...
# 確認 mcp_config.yaml 在容器內可讀
docker exec conclave-backend ls /mcp_config.yaml  # 應存在
# adaptive 群組跑分析 → A2 研究跑通 → brief 進共識
# 若 mcp_required=false，研究失敗時降級、分析仍 done
```

---

## 4. 整合測試計畫（跑完即視為整合成功）

整合者跑完 §3 的 10 步後，跑這套整合測試做最終驗收：

```bash
cd backend
export DATABASE_URL="postgresql://conclave:conclave@localhost:5432/conclave"
python3 -m pytest tests/ -v
```

**預期**：所有測試通過（含三份各自帶來的測試 + 跨層整合測試）。若三份各自的測試在整合後炸，通常是接合不一致——回 §0 重查接縫。

**重點測試檔**（對應接合面）：

| 測試檔 | 驗的接合面 |
|---|---|
| `test_pin.py` | 應用層基本（PIN 生成） |
| `test_broadcast.py` | 應用層 SSE 廣播 |
| `test_groups_api.py` | 應用 API + db + models |
| `test_trigger.py` | 應用狀態機閘門 + 並發 |
| `test_integration.py` | 應用 → AI 觸發接合 |
| `test_rounds.py` | 應用多輪 + db |
| `test_llm.py` | AI 層 prompt + probe |
| `test_adaptive.py` | AI P 階段 + 應用 db helpers + /my-question |
| `test_mcp_loop.py` | AI MCP 研究層 |
| `test_mcp_failure.py` | AI MCP fail-open |

---

## 5. 整合者的工作守則

1. **不寫新功能**——整合者只接合、修接縫、改不一致，不為三份實作加功能。發現功能缺失，回報對應負責人補。
2. **改動要雙向看**——改 A 層的接縫，同時確認 B 層的呼叫端是否要跟著改。接縫是雙向契約，改一邊不改另一邊就是新 bug。
3. **每步 commit**——§1 的每個步驟單獨 commit，炸了能回退到上個接合點，不會卡在中間動彈不得。
4. **不變量優先**——§3.8 的隱私不變量驗證不可跳過。「功能跑通但隱私破了」比「功能沒跑通」更危險——前者會上線洩漏，後者只是不能用。
5. **以規格書為仲裁來源**——任何爭議先查 `SPEC_UNIFIED §7`，再查拆分規格書對應章節，最後才用整合者判斷。規格書是三人共識過的，整合者個人意見次之。
6. **整合後更新規格書**——若整合過程發現規格書本身有誤（接縫定義錯），修正規格書並通知三人，避免下次重蹈。

---

## 6. 整合失敗的常見原因速查

| 症狀 | 可能原因 | 查哪 |
|---|---|---|
| backend 啟動 crash | Settings 欄位名不一致 / migration 語法錯 / DATABASE_URL 不通 | §0.4、§0.5、docker logs |
| API 404 | 路由沒註冊 / prefix 不對 / vite proxy 沒設 | §0.1、vite.config.ts、main.py router 註冊 |
| 分析不觸發 | `start_analysis_task` 被 await / 閘門沒過 / late import 路徑錯 | §3.3、`try_enter_analyzing` |
| 分析卡 analyzing | `run_analysis` 拋例外 / `set_round_done` 失敗 / probe_model 連不上 | llm.py run_analysis try/except、probe_model |
| P 階段沒寫 row | `adaptive_on` 條件錯 / `replace_member_questions` upsert 失敗 / `validate_member_question` 全拒 | §3.5、adaptive_on 計算、validate |
| /my-question 永遠 anchor | effective flag 計算錯 / row 真的沒寫 / round 範圍判斷錯 | §3.5、effective = settings.adaptive_questions_enabled AND g.adaptive_questions |
| 前端看不到個人化題 | `getMyQuestion` 沒呼叫 / my_question state 沒 dispatch / hero 隱藏守衛錯 | §3.5、Group.tsx useEffect + onRound |
| 共識含成員標籤 | Call A 沒 label-blind / `_strip_attributable` 沒套 / stance_shift_summary 沒洗 | §3.8、build_consensus_prompt 的 _shuffle |
| 多輪開不了 | 冷卻沒過 / `try_open_next_round` 閘門沒過 / Call Q 拋例外 | §3.6、NEXT_ROUND_COOLDOWN_S、generate_next_question fallback |
| MCP 啟用就 crash | `mcp_config.yaml` 沒掛進容器 / `MCP_RESEARCH_MODEL` 沒設 / provider 連不上 | §3.10、compose volumes、_validate_mcp |
