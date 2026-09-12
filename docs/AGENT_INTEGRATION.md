# Conclave 整合代理指引（AGENT_INTEGRATION）

> 本文件是寫給**整合 agent**（codex）看的執行手冊，不是給人讀的說明文件。
>
> **任務**：三份實作（AI 層 / 應用層 / 基礎設施層，各來自不同分支或 commit）已備齊。你要把它們整合成一套功能完整的 Conclave，解決接合衝突、跑通測試、回報結果。
>
> **執行模式**：**自動執行，關鍵決策點停下問人**。你能自己做的（grep 比對簽名、merge、改不一致的呼叫端、跑測試、修接縫）就做；碰到「規格書沒定義的歧義」或「兩份實作行為都對但互斥」或「需要產品判斷」時，停下問人——見 §6 的停止條件。
>
> **依據優先順序**（遇到爭議時查閱順序）：
> 1. `SPEC_INTEGRATION.md` §2 衝突仲裁規則（本份 §5 摘要）
> 2. `SPEC_UNIFIED.md` §7 跨層不變量（不可破的紅線）
> 3. 三份拆分規格書的對應章節
> 4. 問人
>
> 全程用繁體中文回報。

---

## 0. 啟動協定

開始前先讀這五份規格書（它們是你決策的依據）：
- `docs/SPEC_UNIFIED.md`（接合面定義 + 跨層不變量）
- `docs/SPEC_AI_LAYER.md`、`docs/SPEC_APP_SERVER.md`、`docs/SPEC_INFRA.md`（三塊實作細節）
- `docs/SPEC_INTEGRATION.md`（人的整合指引，你的 §5 仲裁規則來自這裡）

**不確定時的行為**：查規格書 → 查程式碼事實 → 還是不確定就停下問人（§6）。**不要用猜的**。

---

## 1. 輸入與產出

### 輸入（人會提供給你）

- 三份實作的位置（分支名或 commit hash 或目錄）：AI 層、應用層、基礎設施層
- 可連的 Postgres（`localhost:5432`，user/db 皆 `conclave`）或 Docker 可起
- LLM 端點與 API key（跑完整分析驗證用；若無則只跑到 mock 層級）

### 產出（你要交付的）

1. 一個 `integration` 分支，含合併後的完整 Conclave，三層接合、測試通過
2. 一份整合報告（見 §7 格式）：做了什麼、改了哪些接縫、哪些地方停下問過人、測試結果、剩餘風險
3. 若有停下問人後未解的決策，明確列出待決項

---

## 2. 執行迴圈

你以「檢查 → 合併 → 驗證 → 修正」的迴圈工作，每輪縮小不確定性。**每完成一個可驗證的步驟就 commit**，commit 訊息以 `integration:` 前綴。

```
每輪：
  1. 跑 §3 的某個檢查/驗證
  2. 若發現不一致 → 依 §5 仲裁規則修正（改呼叫端配合被依賴方）
  3. 跑驗證確認修正有效
  4. commit
  5. 遇 §6 停止條件 → 停下問人，等回覆再繼續
```

**不確定性優先**：先處理會阻塞最多後續工作的接合（目錄結構 > Settings 欄位 > db helper 簽名 > API > SSE > AI 工作流）。

---

## 3. 整合步驟（按順序執行，每步附可執行指令）

### 步驟 1：建立整合分支 + 放入基礎設施層

```
git checkout -b integration
# 放入 infra 實作：compose.yml, Dockerfiles, config.py, migrations/*, pytest.ini,
#   requirements.txt, package.json, vite.config.ts, .env.example, .gitignore,
#   backend/tests/conftest.py
git add -A && git commit -m "integration: infra layer"
```

**驗證**（infra 能獨立起來）：
```bash
cp .env.example .env  # 確認有 LLM_API_KEY 欄位；若缺，停下問人要 key
docker compose up -d db
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -m pytest tests/ --collect-only 2>&1 | tail -3
# 預期：能 collect 測試（不跑）。Settings 載入成功、migration 語法正確。
docker compose down
```

**失敗時**：
- Settings 載入失敗（缺欄位/型別錯）→ 這是 infra 自己的問題，記錄並停下問人「infra 的 Settings 與規格書不符，要 infra 那人修還是我改？」
- migration 語法錯 → 同上
- `DATABASE_URL` 連不上 → 確認 db 容器健康、DSN 正確

### 步驟 2：目錄結構對齊檢查（放應用層前）

放應用層前，先確認它的檔案會落到正確位置。對照標準結構（`SPEC_INTEGRATION §0.1`）：

```bash
# 確認應用層實作的檔案路徑
ls backend/app/main.py backend/app/db.py backend/app/broadcast.py backend/app/tasks.py \
   backend/app/pin.py backend/app/models.py backend/app/routes/groups.py \
   backend/app/routes/responses.py backend/app/routes/events.py frontend/src/lib/api.ts
# 任何一個不在預期位置 → 停下問人：「應用層把 X 放到 Y，要改回標準位置還是調整結構？」
```

**仲裁規則**：路徑衝突一律以標準結構為準（§5.2）。但若應用層有正當理由偏離（例如拆了子模組），停下問人。

### 步驟 3：放入應用層

```
# 放入 app server + frontend 實作
git add -A && git commit -m "integration: app server + frontend"
```

**驗證**（應用層可獨立運作，AI 層用 stub）：
```bash
# 先確認 AI 層檔案至少能 import（若還沒放 AI 層，建空骨架 __init__.py + 4 個入口函式簽名）
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -c "from app.main import app; print('import ok')"
# 若炸：通常是 AI 層還沒放、late import 找不到 → 建 AI 層 stub（見步驟 4 前）

# 跑應用層單元測試（不碰 AI）
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -m pytest tests/test_pin.py tests/test_broadcast.py tests/test_groups_api.py -q 2>&1 | tail -3

# 起 server 驗健康
docker compose up -d
curl -s localhost:8000/api/health  # 應回 {"ok":true}
curl -s -X POST localhost:8000/api/groups -H "Content-Type: application/json" -d '{"question":"Q?","creator_nickname":"A"}' | python3 -m json.tool
# 應回 201 + pin + participant_id + creator_token
docker compose down
```

**失敗時**：
- import 炸在 AI 層 late import → 先放 AI 層 stub（空的 `start_analysis_task`/`generate_next_question`/`redact`/`shutdown_analysis_tasks`，簽名照 `SPEC_UNIFIED §6.1`），讓應用層能 import
- API 404 → 確認 router prefix 是 `/api`、main.py 註冊順序、vite proxy
- 建組失敗 → `generate_pin`、`try_enter_analyzing`、migration 的 groups 表

### 步驟 4：放入 AI 層 + 接縫簽名對齊（核心步驟）

```
# 放入 AI 層實作：llm.py, mcp.py, mcp_tools.py, mcp_config.yaml
git add -A && git commit -m "integration: AI layer"
```

**先跑接縫簽名三向對齊**（這是整合成敗關鍵）：

#### 4a. AI 層提供的 4 個入口 vs 應用層呼叫端

```bash
# 找 AI 層定義
grep -n "^def start_analysis_task\|^async def start_analysis_task" backend/app/llm.py
grep -n "^async def generate_next_question" backend/app/llm.py
grep -n "^def redact" backend/app/llm.py
grep -n "^async def shutdown_analysis_tasks" backend/app/llm.py

# 找應用層呼叫端
grep -rn "start_analysis_task\|generate_next_question\|shutdown_analysis_tasks" backend/app/routes/ backend/app/main.py backend/app/tasks.py
grep -rn "from .* import .*redact\|llm.redact\| redact(" backend/app/routes/ backend/app/mcp.py
```

**逐項核對**（對照 `SPEC_UNIFIED §6.1`）：

| 函式 | 簽名要求 | 核對動作 |
|---|---|---|
| `start_analysis_task` | `def (pool, group_id) -> asyncio.Task`（**非 async**） | 確認 AI 層定義是 `def` 非 `async def`；確認 3 個呼叫端**無 await**（`start_analysis_task(pool, gid)` 而非 `await ...`） |
| `generate_next_question` | `async (prev_consensus: str, prev_question: str) -> str` | 確認呼叫端傳兩個 str、用 `try/except` 接 `_seed_next_question` |
| `redact` | `def (value: object) -> str` | 確認呼叫端傳入可 str 化的值、取用 str |
| `shutdown_analysis_tasks` | `async (timeout: float = 10.0) -> None` | 確認 main.py lifespan 用 `await` + late import |

**不一致時**：依 §5.1，改**呼叫端**配合被依賴方（AI 層）。例外：若 AI 層簽名與 `SPEC_UNIFIED §6.1` 不符（AI 層自己寫錯），改 AI 層。

#### 4b. AI 層依賴的 7 個 db helper + broadcast vs 應用層定義

```bash
# 找應用層定義
grep -n "^async def get_group_by_id\|^async def get_round_responses_ordered\|^async def get_round_consensus\|^async def set_round_done\|^async def set_group_status\|^async def get_prior_member_questions\|^async def replace_member_questions" backend/app/db.py
grep -n "^async def broadcast" backend/app/broadcast.py

# 找 AI 層呼叫端讀了哪些欄位
grep -n 'g\["pin"\]\|g\["question"\]\|g\["current_round"\]\|g\["max_rounds"\]\|g\["adaptive_questions"\]' backend/app/llm.py
grep -n 'r\["content"\]\|r\["member_seq"\]\|r\["participant_id"\]' backend/app/llm.py
grep -n 'prev\["consensus"\]\|prev\["stance_digest"\]\|prev\["stance_shift_summary"\]\|prev\["research_brief"\]\|prev\["question"\]' backend/app/llm.py
```

**逐項核對**（對照 `SPEC_UNIFIED §6.2`）：

| helper | 關鍵核對 | 不一致時 |
|---|---|---|
| `get_group_by_id` | 應用層 SELECT 清單含 AI 層讀的 5 欄（pin/question/current_round/max_rounds/adaptive_questions）+ 其餘 7 欄 | 改應用層 SELECT（被依賴方）—但若 AI 層讀了 DDL 沒有的欄位，改 AI 層 |
| `get_round_responses_ordered` | SELECT 含 content/member_seq/participant_id + JOIN participants + ORDER BY member_seq | 同上 |
| `get_round_consensus` | SELECT 含 consensus/stance_digest/stance_shift_summary/research_brief/question | 同上 |
| `set_round_done` | 參數順序與預設：`(pool, group_id, round_number, consensus, stance_digest, stance_shift_summary, research_brief=None)` | 改呼叫端配合 |
| `set_group_status` | `(pool, group_id, status, consensus=None)` | 改呼叫端配合 |
| `get_prior_member_questions` | `(pool, group_id, participant_id, up_to_round)` → `list[tuple[int,str]]` | 改呼叫端配合 |
| `replace_member_questions` | `(pool, group_id, round_number, items)` + `ON CONFLICT DO UPDATE` | 改呼叫端配合 |
| `broadcast` | `async (pin, event_type, data)` | 改呼叫端配合 |

#### 4c. Settings 欄位名對齊

```bash
# 找 infra 定義的欄位
grep -E "^\s+\w+:\s*(str|int|float|bool)" backend/app/config.py | head -30
# 找 AI/應用讀取的 settings.*
grep -rn "s\.\|settings\.\|get_settings()" backend/app/llm.py backend/app/mcp.py backend/app/routes/ backend/app/tasks.py backend/app/main.py | grep -oE "s\.\w+|settings\.\w+" | sort -u
```

**逐欄比對**：每個 `s.xxx` / `settings.xxx` 讀取必須在 config.py 有對應欄位。**常見錯誤**：AI 層寫 `s.adaptive_questions`（這是群組 Record 欄位，不是 Settings）——應該是 `s.adaptive_questions_enabled`（Settings）+ `g["adaptive_questions"]`（Record）。混淆就改 AI 層。

#### 4d. Migration schema vs db.py SELECT 對齊

```bash
# DDL 欄位
grep -E "^\s+\w+\s+(UUID|TEXT|INT|BOOLEAN|TIMESTAMPTZ|CHAR)" backend/migrations/*.sql
# db.py SELECT 欄位
grep -E "SELECT.*FROM (groups|rounds|responses|participants|member_questions)" backend/app/db.py
```

**關鍵不變量**（§5.3）：`get_rounds_history` 的 SELECT **絕不可含** `stance_digest` 或 `research_brief`。若含，直接改掉（這是隱私紅線，不需要問人）。

#### 4e. SSE 事件名對齊

```bash
# 廣播端
grep -rn "broadcast(pin," backend/app/llm.py backend/app/routes/ backend/app/tasks.py
# 前端監聽
grep -n "addEventListener" frontend/src/hooks/useGroupSSE.ts
```

對照 `SPEC_UNIFIED §6.4`，事件名與 data 鍵名必須一致。不一致改廣播端或前端皆可（兩邊都是應用層，§5.1 不適用——選改动較小的那邊）。

### 步驟 5：完整測試

```bash
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -m pytest tests/ -q 2>&1 | tail -5
```

**預期全綠**。失敗時：
- 看是哪個測試炸 → 對應哪個接合面（見 `SPEC_INTEGRATION §4` 的測試-接合面對照表）
- 修接縫 → 重跑該測試 → 全綠後再跑全套

### 步驟 6：接合驗證（依序，每步通過再下一步）

按 `SPEC_INTEGRATION §3` 的 10 步跑。這裡列出每步的可執行指令與停止條件：

**6.1 啟動接合**
```bash
docker compose up --build -d
sleep 8
curl -s localhost:8000/api/health  # {"ok":true}
docker exec conclave-db psql -U conclave -d conclave -c "\dt"  # 見 5 張表
```
失敗 → 查 docker logs conclave-backend。常見：Settings 欄位名、migration、DATABASE_URL。

**6.2 基本 API 接合**
```bash
curl -s -X POST localhost:8000/api/groups -H "Content-Type: application/json" \
  -d '{"question":"晚餐？","creator_nickname":"A","expected_count":2}' | python3 -m json.tool
# 應回 pin + participant_id + creator_token
```
失敗 → vite proxy、API prefix、generate_pin、建組事務。

**6.3 收齊觸發接合**（需要 mock，因為還沒接真 LLM）
```bash
# 用 test_integration.py 的 fake_run 模式，或 monkeypatch app.llm.run_analysis
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -m pytest tests/test_integration.py tests/test_trigger.py -q
```

**6.4 完整分析接合**（需要真實 LLM 或 mock AsyncOpenAI + probe_model）
```bash
# 若有 LLM_API_KEY：跑真實分析
# 若無：跑 test_adaptive.py（用 mock）
cd backend && DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave python3 -m pytest tests/test_adaptive.py -q
```

**6.5 P 階段接合**（同 6.4，用 adaptive=true 群組）
**6.6 多輪接合**、**6.7 終局清理**、**6.8 隱私不變量**、**6.9 並發**、**6.10 MCP**：見 `SPEC_INTEGRATION §3`，每步都有對應測試檔。

### 步驟 7：隱私不變量驗證（不可跳過）

這些是「功能跑通但隱私破了」的防線——**必須逐一驗**，不能用「測試全綠」代替：

```bash
# 1. get_rounds_history 不含 stance_digest/research_brief
grep -A20 "async def get_rounds_history" backend/app/db.py | grep "stance_digest\|research_brief"
# 應無輸出。若有 → 直接改掉（隱私紅線，§5.3，不問人）

# 2. /my-question 無效 pid 回 anchor 非 404
# （用 curl 測，見 SPEC_INTEGRATION §3.8）

# 3. P_X prompt 不含他人意見與成員標籤
# （用 test_adaptive.py 的 test_run_analysis_p_isolation_no_other_opinions）

# 4. stance_digest 不進 groups 表
docker exec conclave-db psql -U conclave -d conclave -c "SELECT stance_digest FROM groups" 2>&1
# 應報欄位不存在（groups 表無此欄）

# 5. member_questions 不進 SSE 廣播
grep -n "broadcast" backend/app/llm.py backend/app/routes/ | grep -i "member_question"
# 應無輸出

# 6. access log 關閉
docker logs conclave-backend 2>&1 | grep -E "GET /|POST /" | head -3
# 應無 uvicorn access log 行（因 --no-access-log）
```

**任何一條破 → 這是 bug，直接修**（隱私不變量不可談判，§5.3）。

---

## 4. 工具使用守則

- **改接縫時雙向看**：改 A 層接縫，grep 確認 B 層呼叫端是否要跟著改。接縫是雙向契約。
- **每個可驗證步驟 commit**：訊息 `integration: <做了什麼>`。炸了能回退。
- **grep優先於read**：找接縫用 grep 精準定位，不要整檔讀。
- **改碼最小化**：只改不一致的接縫，不重構、不优化、不改風格。
- **測試是驗證不是目標**：跑測試是確認接合對了，不是為了讓測試綠而改測試。**測試炸時先懷疑接縫，不是測試**——除非測試本身與規格書不符（此時停下問人）。

---

## 5. 衝突仲裁規則（摘要，詳見 SPEC_INTEGRATION §2）

遇到不一致時，依以下規則決定改哪份——**不開會、不問人，直接改**（除非觸發 §6 停止條件）：

| 衝突類型 | 以誰為準 | 改誰 |
|---|---|---|
| 函式簽名 | **被依賴方**（被呼叫的那層） | 改呼叫端 |
| 目錄/路徑 | 標準結構（SPEC_INTEGRATION §0.1） | 改偏離的那份 |
| 行為（P 在 done 前後、redact 範圍等） | SPEC_UNIFIED §7 跨層不變量 | 改違反不變量的那份 |
| 測試 fixture | infra 的 conftest | 改其他層自訂 fixture 的測試 |
| 依賴套件 | infra 的 requirements.txt/package.json | 加到 infra，不在別處另建 |
| Settings 欄位名 | infra 的 config.py | 改讀取端配合 |
| Migration DDL | infra 的 migrations | 改 db.py SELECT 配合 |

**被依賴方判斷**：看 `SPEC_UNIFIED §6` 的接合地圖「提供方 → 消費方」——提供方是被依賴方。

---

## 6. 停止條件（何時停下問人）

**碰到以下情況，停下問人，不要自行裁決**：

1. **規格書沒定義的歧義**：兩份實作行為不同，查完 `SPEC_UNIFIED §7` + 三份拆分規格書都沒定義該行為。
   - 問人格式：「[接合點] 規格書未定義 X 的行為。AI 層做法 A，應用層做法 B。建議：[你判斷哪個符合整體]。要採哪個？」

2. **兩份都對但互斥**：兩種實作都符合規格書，但只能選一（例如兩份用了不同的內部資料結構，接口相同但內部不兼容）。
   - 問人格式：「[接合點] 兩份都符合規格，但內部實作互斥。選 A 會需要改 B 的 [具體處]。選 B 會需要改 A 的 [具體處]。建議：[改動較小的]。要選哪個？」

3. **需要產品判斷**：行為歧義涉及產品意圖而非技術（例如 error 事件前端要不要接、adaptive 題數上限要幾題）。
   - 問人格式：「[接合點] 這是產品判斷：[描述歧義]。選項：A/B/C。建議：[你的判斷]。」

4. **規格書本身有誤**：整合過程發現規格書的接縫定義自相矛盾或與現況不符。
   - 問人格式：「[接合點] 規格書 SPEC_X §Y 定義 Z，但與 SPEC_W §V 矛盾/與現況不符。需要修規格書。建議：[修正方向]。」

5. **infra 無法獨立起來**：步驟 1 的 infra 驗證失敗，且是 infra 自己的問題（Settings/migration/Dockerfile）。
   - 問人格式：「infra 層 [具體問題]，無法起。要 infra 那人修，還是我改？」

6. **缺少必要的輸入**：缺 LLM_API_KEY、缺 Postgres、缺三份實作的某一份。
   - 問人格式：「缺 [輸入]。請提供。」

7. **測試與規格書不符**：某個測試炸了，但測試本身與規格書的定義矛盾（不是接縫問題）。
   - 問人格式：「測試 test_X 期望 Y，但 SPEC_Z §W 定義相反。是測試錯還是規格錯？」

**停下時**：清楚描述 (a) 你在哪一步、(b) 碰到什麼、(c) 你查了哪些規格書章節、(d) 你的建議、(e) 需要人決定什麼。**不要只說「炸了怎辦」**——給人足夠資訊一次決定。

**等回覆時**：可以繼續做其他不衝突的步驟（若有），但不要對該歧義點假設答案繼續改。

---

## 7. 整合報告格式（完成後交付）

整合完成（或停下問人後）產出報告，包含：

```markdown
# 整合報告

## 狀態
[完成 / 部分完成（待決項 N 個） / 停下問人]

## 做了什麼
- 步驟 1-7 各自結果
- 修正的接縫清單（每項：接合點、原不一致、依哪條仲裁規則改了誰、改動摘要）

## 接縫修正清單
| 接合點 | 不一致 | 仲裁規則 | 改誰 | commit |
|---|---|---|---|---|
| ... | ... | §5.1 被依賴方 | 應用層呼叫端 | abc123 |

## 測試結果
- 全套：X passed, Y failed, Z skipped
- 失敗的測試與原因（若有）

## 隱私不變量驗證
- §7 的 6 項各項結果

## 停下問人的決策點
| # | 接合點 | 歧義 | 我的建議 | 待人決定 |
|---|---|---|---|---|
| 1 | ... | ... | ... | ... |

## 剩餘風險
- 任何你注意到但不在這次整合範圍的問題

## commit 歷史
- integration: infra layer (hash)
- integration: app server + frontend (hash)
- integration: AI layer (hash)
- integration: fix <接縫> (hash)
- ...
```

---

## 8. 邊界：不要做這些

- **不要寫新功能**：只接合、修接縫、改不一致。發現功能缺失，記錄並問人，不自己補。
- **不要重構**：即使看到「可以寫更好」的程式碼，不動——你的任務是接合不是優化。
- **不要改規格書**：除非觸發 §6 #4（規格書有誤），且改規格書前要問人。
- **不要為了測試綠而改測試**：測試炸時先懷疑接縫。改測試只在測試本身與規格書矛盾時（§6 #7）。
- **不要跨過隱私紅線**：§7 的 6 項不變量不可談判、不可跳過、不可「先這樣之後再改」。
- **不要並行改多個接縫不 commit**：每個接縫修正後 commit + 驗證，再下一個。否則炸了不知道是哪個改動。
