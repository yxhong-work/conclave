# Conclave — Privacy-Preserving Multi-Agent Coordination (PoC 規格書)

> 本文件整合 `docs/Private Multi-Agent Coordination.md`（產品／Hackathon 面向）與
> `docs/private_preference_multi_agent_negotiation_project.txt`（研究／形式化面向），
> 並依「可實作的 PoC」尺度取捨內容後，作為實作規格書。
> 所有標註 **(v2)** 的項目本版不實作，僅記錄設計方向。

---

## 0. TL;DR

每個人把對某個問題的真實想法**私下**告訴系統；後端收齊所有人的想法後，
丟給一個 LLM 整合出**單一共識摘要**，再即時推播到所有人的畫面。
過程中，成員之間**看不到彼此的原始想法**——只看得到彙整後的共識。
這是兩份來源文件「Private Multi-Agent Coordination / Privacy-Preserving Negotiation」
概念的最小可運作切片。

---

## 1. 專案定位與範圍

### 1.1 核心 Thesis（保留自來源文件）

> 今天的 AI 多在強化**個人**智能，但許多多人問題缺的不是更聰明的個人，
> 而是更好的**多人協調機制**。三個各自很強的 Smart Agent 不自動等於一個
> Good group decision——中間缺的是 Coordination。

本 PoC 要驗證的最小命題：

> 在不向群體成員彼此公開原始想法的前提下，能否透過一個 LLM 協調層，
> 產生一份多數人能接受的共識？

### 1.2 本版做什麼（In scope）

- 網頁應用：首頁兩個入口（建立群組 / 加入群組）。
- 建立群組產生 **5 碼、大小寫敏感、含數字** 的 PIN；他人憑 PIN + 暱稱加入。
- 群組內顯示建立者撰寫的「討論問題」；成員（**含建立者**）在輸入框寫下自己的想法並發送。
- 後端收齊回覆後，將**所有回覆整合**丟給 LLM，產出**單一共識摘要**（全體共用一份）。
- 摘要即時推播至所有成員畫面。
- 三種觸發條件（建立者手動開始 / 預期回覆數達標 / 倒數計時到點），OR 邏輯。
- LLM 失敗時進入 `error` 狀態，建立者可重試。
- 全部以 docker compose 打包（前端、後端、資料庫三服務）。

### 1.3 本版不做（Out of scope，標註 v2）

以下概念（部分來自研究文件、部分為評估後否決的設計替代）**本版不實作**，僅記錄以保留設計脈絡：

- **多輪談判 loop**（proposal / objection / counter-proposal / concession / 條件交換）**(v2)**
- **匿名讓步提案**（Blindspot 的 relaxation loop：「是否有人願意把步行距離放寬到 15 分鐘？」）**(v2)**
- **Private Agent 的策略性公開立場**（$M_i = f(P_i, H, A_i)$，把私有偏好轉成策略性公開訊息）**(v2)**
- **個人化分析**（每人看到針對自己的分析）——本版所有人看同一份摘要。
- **量化評測**（Social Welfare、Pareto Efficiency、Privacy Leakage、Exploitability 等 metrics）**(v2)**
- 實驗設定對照組（Full Transparency / Direct Voting / Centralized Planner）**(v2)**
- 密碼學隱私（MPC、ZKP、TEE、local agent execution）——見 §16 技術誠實。

> 取捨理由：PoC 的價值在於「單輪收齊 → LLM 整合 → 共識推播」這條核心 loop
> 端對端跑通且 UI 清楚。多輪談判與量化評測是真正的研究增量，但會讓主線模糊，
> 適合在 PoC 驗證「隱私邊界 + LLM 協調」可行後再展開。

---

## 2. 名詞與角色

| 名詞 | 定義 |
|---|---|
| **Group（群組）** | 一個討論題目與其所有參與者的集合，由唯一 PIN 識別。 |
| **Creator（建立者）** | 開群組的人；可設定預期人數/倒數、並可手動「開始分析」。 |
| **Participant（成員）** | 任何加入群組的人（含 Creator）。 |
| **Opinion（想法/回覆）** | 成員私下輸入的自由文字。**不對其他成員公開**。 |
| **Consensus（共識摘要）** | LLM 對所有 Opinion 整合出的單一結果，**對全體成員公開**。 |
| **PIN** | 5 碼大小寫敏感含數字字串，作為加入群組的鑰匙。 |

---

## 3. 使用者故事與端對端流程

### 3.1 主流程

1. **Alice 開群組**：在首頁按「建立群組」，填寫討論問題（例：「我們今晚去哪裡吃飯？」）、
   自己的暱稱，並可選擇設定「預期人數」與「倒數計時」。提交後系統回傳一組 **PIN**。
2. **其他人加入**：Bob、Carol 在首頁的 PIN 輸入框與暱稱輸入框填入資料，按「加入群組」加入。
3. **閱讀與輸入**：成員畫面顯示討論問題；閱讀後在輸入框寫下自己的想法，按「發送」送出。
   **建立者 Alice 也一樣**——建立後在 `/g/{pin}` 送出自己的想法（建立表單無 opinion 欄位）。
4. **收齊觸發**：後端依觸發條件（見 §4）判定開始分析，群組狀態轉為 `analyzing`。
5. **LLM 分析**：後端將所有已收到的 Opinion 整合丟給 LLM，產生單一共識摘要。
6. **推播結果**：摘要即時推播至所有在線成員畫面；群組狀態轉為 `done`。
7. **晚到者**：在 `analyzing` 或 `done` 才加入的成員，無法再送出想法；`done` 時可看到最終摘要。
8. **失敗重試**：若 LLM 失敗（`error` 狀態），建立者可按「重試分析」重新跑一次（見 §3.2、§7.6）。

### 3.2 狀態機

群組狀態唯一驅動前端畫面：

```
collecting ──(觸發)──▶ analyzing ──(LLM 完成)──▶ done ──(建立者開下一輪)──▶ collecting (round+1)
                  analyzing ──(LLM 失敗)──▶ error ──(建立者重試)──▶ analyzing
                  done ──(建立者結束 / 達上限 / 逾時)──▶ closed
```

- `collecting`：收集中，可加入、可送出想法、可看進度（已送 N / 已加入 M）。
- `analyzing`：分析中，**不可送出**；畫面顯示「分析中…」。晚到者仍可 `POST /join` 加入，但加入後只能看到「分析中」狀態、無法送想法（見 §6.1「晚到者可加入」）。
- `done`：完成，顯示共識摘要；晚到者加入直接看到摘要（唯讀）。建立者可開啟下一輪或結束討論（見 `docs/MULTIROUND_SPEC.md`）。
- `error`：分析失敗；晚到者加入看到「分析失敗」狀態。建立者可重試（見 §7.6），一般成員顯示「分析失敗，請聯絡建立者」。
- `closed`：討論已結束（建立者關閉或逾時自動關閉）；輪次歷史唯讀，不再有新輪。

> **晚到者貫穿三狀態**：`POST /join` 在 `collecting`/`analyzing`/`done`/`error` 皆允許；
> 差異在加入後能做什麼。`collecting` 可送想法；其餘三狀態不可送、只能看當前狀態/
> 摘要。這讓「憑 PIN 加入看結果」的需求在任何時刻都成立（§3.1 step 7、§6.1）。

> `error` 是終端狀態的分支，不是回到 `collecting`——回退會讓已送出的想法
> 語意混亂（成員以為已鎖定卻又可改）。重試由建立者觸發，語意為「用同一批
> 已收想法重新跑一次 LLM」，故 `error → analyzing`（而非 `error → collecting`）。

### 3.3 隱私邊界（本版具體承諾）

| 誰 | 看得到什麼 |
|---|---|
| 成員 | 討論問題、群組進度（人數/已送數）、最終共識摘要。**看不到其他成員的原始想法**。 |
| 建立者 | 同上 + 「開始分析」按鈕。 |
| 平台/後端 | 所有原始想法（見 §16 技術誠實）。 |
| 群組外的人 | 什麼都看不到（需 PIN 才能加入）。 |

> 這是本版唯一、但具體的隱私性質：**成員彼此之間不交換原始想法，
> 只透過 LLM 的共識摘要間接協調**。
>
> **誠實註記**：這「不等於」來源文件「Coordination Layer 交換 structured signal
> 而非任意轉傳完整文字」的實作——本版後端與 LLM 實際讀取所有原始想法全文
> （見 §16.1）。來源文件主張的 structured-signal 交換在本版並未實踐；本版
> 只做到「成員之間」不交換原文。這個落差是 PoC 取捨，已在 §14 對應表說明。

---

## 4. 觸發條件邏輯（精確定義）

建立群組時可設定 `expected_count`（**預期「回覆」數**，可為空）與 `deadline`（倒數截止時間，可為空）。
三個觸發來源以 **OR** 組合：

| 設定情況 | 觸發時機 |
|---|---|
| 建立者按「開始分析」 | **立即觸發**（忽略人數與時間） |
| 只設 `expected_count` | 收到第 `expected_count` 份回覆時觸發 |
| 只設 `deadline` | 時間到時觸發（無論已收到幾份） |
| 兩者都設 | **任一達標即觸發**（人先到或時間先到） |
| 兩者都未設 | **僅等建立者按「開始分析」**（不自動觸發） |

實作約束：

- 觸發是單次的：一旦狀態離開 `collecting`，不再接受新回覆、不重新觸發。
- 觸發後才送到的回覆被忽略（不納入本次分析）。
- `deadline` 觸發由後端背景掃描任務保證（見 §7.4），不依賴剛好有人按按鈕。
- `expected_count` 觸發於每次收到回覆時檢查。
- **零回覆保護**：若 `submitted_count == 0`，不觸發分析（`POST /start` 回 `409`
  並附訊息「尚未收到任何想法」；`expected_count`/`deadline` 觸發同理——
  無想法可分析時，狀態留在 `collecting` 並廣播 `error` 事件說明原因）。
- **`expected_count` 語意為「回覆數」非「人數」**：觸發條件是「已收到 N 份回覆」，
  不是「已加入 N 人」。成員加入但未送出不算。預期上 `expected_count` 應等於
  預期參與人數（每位參與者送一份），但觸發嚴格看 `submitted_count`。

> **詮釋註記**：兩者都未設時「僅靠建立者手動開始」是依你描述推導的預設
> （你說三條件都保留；未設人數/時間時，唯一剩下的就是手動）。若你希望改成
> 「未設定時 = 所有已加入者都送出就觸發」，請告知，會在實作前調整。

---

## 5. 資料模型（Postgres）

三張表，最小且充分。

```sql
-- 群組
CREATE TABLE groups (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pin           CHAR(5) UNIQUE NOT NULL,          -- 大小寫敏感，UNIQUE 約束防撞
    question      TEXT NOT NULL,                    -- 建立者撰寫的討論問題（≤ 1000 字，應用層驗證）
    creator_token TEXT NOT NULL,                    -- 隨機 secrets，授權建立者動作
    expected_count INT NULL,                        -- 預期「回覆」數（可空）；≥ 1
    deadline      TIMESTAMPTZ NULL,                  -- 截止時間（可空）
    status        TEXT NOT NULL DEFAULT 'collecting', -- collecting|analyzing|done|error
    consensus     TEXT NULL,                        -- LLM 產出的共識摘要
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 成員（含建立者）
CREATE TABLE participants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    nickname    TEXT NOT NULL,                      -- ≤ 30 字，應用層驗證
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, nickname)                      -- 同群組暱稱不重複（PoC 簡化）
);

-- 想法/回覆
CREATE TABLE responses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id        UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    participant_id  UUID NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,                  -- ≤ 4000 字，應用層驗證
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, participant_id)               -- 每人只能送一份
);

CREATE INDEX ON responses (group_id);
```

- `gen_random_uuid()` 為 Postgres 13+ 內建（`postgres:16-alpine` 滿足），**無需 `pgcrypto` extension**。
- `groups.creator_token`：建立時產生，只在 `POST /groups` 回應中回傳一次，建立者瀏覽器保存，用於「開始分析」授權。
- PIN 碰撞：靠 `groups.pin` 的 UNIQUE 約束，產生時 `INSERT` 失敗則重試（見 §7.2）。
- 暱稱重複：PoC 簡化為同群組不重複；UNIQUE 違反時回 `409`（不預先 SELECT 檢查，靠 constraint race-free）。未來可改為顯示名可重複、以 participant_id 區分。
- **長度限制**：`question`/`nickname`/`content` 的上限在 pydantic schema（§6.1）以 `Field(max_length=...)` 強制；DB 的 `TEXT` 不加 `VARCHAR(n)` 以避免 migration 負擔，應用層為唯一把關點。
- **Schema 建立**：backend 啟動時（lifespan startup）執行 `migrations/001_init.sql` 建立三張表（`CREATE TABLE IF NOT EXISTS`）。PoC 不用 alembic；啟動冪等，重啟不破壞資料。正式 migration 工具為 v2。

### 5.1 資料保留與保存媒介

- **本版：永久保留，不做清理。** 群組完成後所有資料（question、participants、responses、consensus）持續留在 Postgres + docker compose 的 **named volume `pgdata`**（§9.1）。容器刪除或重建不影響資料；只有明確 `docker volume rm` 才會消失。
- **為何不刪**：PoC 階段優先讓端對端 loop 跑通；刪除機制需額外清理任務與策略決策，故列為 v2（見 §15）。
- **隱私含義**：原始想法長期堆積，暴露面隨時間增大（後端單一 backend 可讀到全部，見 §16）。此為已知取捨。
- **PIN 可重用性**：本版資料不刪除，`groups.pin` UNIQUE 且留存，故 **PIN 不會被釋放給新群組重用**。若 v2 加入刪除，需連帶決定 PIN 是否回收（見 §15）。

---

## 6. API 規格

Base path prefix `/api`。所有 JSON。時間為 ISO 8601。錯誤回 `{ "error": "..." }`。

### 6.1 REST

| 方法 | 路徑 | 說明 | Body / 回應 |
|---|---|---|---|
| `POST` | `/api/groups` | 建立群組 | req: `{question, creator_nickname, expected_count?, timeout_seconds?}` → `201 {pin, participant_id, creator_token, status:"collecting"}` |
| `POST` | `/api/groups/{pin}/join` | 加入群組 | req: `{nickname}` → `200 {participant_id, status}`；**任何狀態皆允許加入**（`collecting` 可後續送想法；`analyzing`/`done`/`error` 加入後只看當前狀態/摘要、不可送）。暱稱重複回 `409`。 |
| `GET` | `/api/groups/{pin}/state` | **真相來源** | `200 {pin, question, status, expected_count, participant_count, submitted_count, deadline, consensus, is_creator}`；`is_creator` 需帶 `?token=` 比對；`consensus` 在 `status=="done"` 時非空，其餘為 `null`。前端用此單一端點對齊真相。 |
| `POST` | `/api/groups/{pin}/responses` | 送出想法 | req: `{participant_id, content}` → `200 {ok}`；已送過則 `409`（靠 UNIQUE 約束，§6.1 要點）；`status!="collecting"` 回 `409`「已截止」。`participant_id` 為唯一憑證（PoC 無帳號，§16.8）。 |
| `POST` | `/api/groups/{pin}/start` | 建立者手動觸發 | req: `{creator_token}` → `202`；非建立者 `403`；已非 collecting 則 `409` |

設計要點：

- `GET /state` 是**前端重連/晚到時的單一事實來源**；SSE 只負責即時推播（見 §7.5）。
- `is_creator` 透過 query `?token=` 比對 `creator_token`，讓前端知道是否顯示「開始分析」按鈕。
  > **安全註記**：query string 會進入瀏覽器歷史與 server access log。PoC 接受此取捨，
  > 但 access log 應設為 WARN/INFO 以上不記錄完整 query；正式產品改為 `Authorization`
  > header 或短生命週期 session token（見 §16）。
- `POST /responses` 採「已送過即 409」：handler 直接 INSERT，靠 `UNIQUE (group_id, participant_id)`
  約束；捕到 `IntegrityError`（asyncpg `UniqueViolationError`）時回 `409`，**不預先 SELECT**
  檢查（避免 race）。前端送出後鎖定輸入框。若要支援「重新編輯」可改為 upsert，本版不做。
- **輸入驗證**（pydantic schema，回 `422`）：`question` `max_length=1000`、
  `nickname`/`creator_nickname` `max_length=30`、`content` `max_length=4000`；
  `expected_count` `ge=1`；`timeout_seconds` `gt=0` 且 `le=86400`（最長 1 天）。
  `timeout_seconds` 轉換為 `deadline = now() + timeout_seconds` 存入 §5 的 `deadline` 欄位。

### 6.2 SSE

| 路徑 | 說明 |
|---|---|
| `GET /api/events/{pin}` | Server-Sent Events 串流，即時推播該群組事件。 |

事件類型（`event:` 名稱 / `data:` JSON）：

| 事件 | data | 觸發時機 |
|---|---|---|
| `phase` | `{status}` | 狀態轉換時（collecting→analyzing→done，或 analyzing→error） |
| `progress` | `{participant_count, submitted_count}` | 有人加入或送出時 |
| `consensus` | `{content}` | LLM 分析完成 |
| `error` | `{message}` | 分析失敗（LLM 端點錯誤、零回覆觸發等） |

- 保持連線用 SSE 內建 keepalive（`sse-starlette` `ping` 參數，預設 15s，對應 §11 `SSE_KEEPALIVE_S`）。
- **無 replay**：客戶端斷線重連後，應立刻 `GET /state` 同步真相，SSE 僅接續未來事件。
- **progress 事件來源**：即時推播 `participant_count`/`submitted_count` 超出你最初描述的
  「只廣播 LLM 結果」範圍，但對應來源 product doc demo 的「3/3 private preferences received」
  顯示，成本低、使用者體驗更清楚。若要嚴格極簡可省略 progress、只靠 `/state` 輪詢。

---

## 7. 後端規格（FastAPI）

### 7.1 技術與依賴

- Python 3.12+（conda `conclave` 環境實測為 3.13.15，相容）。
- **實測確認版本**（conda `conclave` 環境安裝通過）：`fastapi 0.141`、`uvicorn 0.52`、
  `sse-starlette 3.4`、`pydantic 2.13`、`pydantic-settings 2.15`、`asyncpg 0.31`、
  `psycopg 3.3`、`openai 3.8`、`httpx 0.28`。
- 套件安裝於 conda `conclave` 環境，**不污染全域**；Docker 內用獨立 venv / image。

### 7.2 PIN 產生

```python
import secrets, string
ALPHABET = string.ascii_letters + string.digits           # 62 字元，大小寫敏感 + 數字
def generate_pin() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(5))   # 62^5 ≈ 9.16 億種
```

- 使用 `secrets.choice`（密碼學強隨機），非 `random`。
- 產生後直接 `INSERT`；遇 `UNIQUE` 違約重試 1–2 次（不以 `SELECT` 預查，避免 race）。
- **可選**：若要口語可讀，排除易混淆字（`0/O`、`1/l/I`）。本版用完整 62 字元（你指定大小寫敏感+數字）。

### 7.3 狀態轉換與觸發

- 每次收到 `POST /responses` 後：若 `status=="collecting"` 且 `expected_count` 非空且 `submitted_count >= expected_count` → 觸發分析。
- `POST /start`：若 `status=="collecting"` 且 token 正確且 `submitted_count >= 1` → 觸發分析；`submitted_count == 0` 回 `409`「尚未收到任何想法」。
- `deadline` 觸發：見 §7.4。
- **零回覆保護**：任何觸發路徑在 `submitted_count == 0` 時都不進入 `analyzing`；`expected_count`/`deadline` 觸發若命中零回覆，狀態留 `collecting` 並廣播 `error` 事件「尚未收到任何想法」。
- **「觸發分析」= 臨界區（race-free）**：
  1. 以 DB 交易執行 `UPDATE groups SET status='analyzing' WHERE id=? AND status='collecting'`；**受影響列數為 1** 才真正進入（其他並發觸發者拿到 0，直接退出，避免重複觸發）。
  2. 臨界區成功（rowcount=1）後，**fire-and-forget** 啟動背景任務 `asyncio.create_task(run_analysis(group_id))` 執行 LLM 呼叫 + `consensus`/`error` 廣播。
     觸發請求本身（`POST /responses`/`/start`/deadline 掃描）**不 blocking 等待 LLM**——LLM 約 15–20s，blocking 會讓 HTTP 請求超時。
  3. `run_analysis` 讀取 `SELECT content FROM responses WHERE group_id=? ORDER BY submitted_at`（按提交時間排序，確保 prompt 可重現）。
- **回覆與觸發的排序保證**：`POST /responses` 的 INSERT 與後續觸發檢查必須在同一交易內，先 INSERT 再以 `SELECT status FROM groups WHERE id=? FOR UPDATE`（或同等原子操作）檢查 `status` 與 `submitted_count`，避免「INSERT 時 status 為 collecting、檢查時已被另一觸發翻成 analyzing」的 race。若 status 已非 collecting，該回覆仍留存但不納入本次分析（§4「觸發後才送到的回覆被忽略」）。

### 7.4 截止時間掃描

- 啟動時建立一個背景 `asyncio.Task`（lifespan startup），每 5 秒掃描：
  `SELECT id FROM groups WHERE status='collecting' AND deadline IS NOT NULL AND deadline < now()`，逐一觸發分析。
- 此設計**對重啟具容忍力**：`deadline` 存在 DB，後端重啟後掃描任務重建，仍會抓到已過截止的群組。

### 7.5 SSE 廣播

- 廣播封裝在單一函式 `broadcast(pin, event_type, data)`，背後是 in-process 註冊表：
  `dict[pin, set[asyncio.Queue]]`。每個 SSE 連線配一個 `asyncio.Queue(maxsize=16)`，`await queue.get()` 迴圈推播；斷線時從 set 移除。
- **Backpressure 政策**：廣播時用 `queue.put_nowait(item)`；捕到 `asyncio.QueueFull`
  表示該客戶端消費過慢或斷線未偵測，**從 set 移除該 queue 並關閉其 SSE 連線**
  （客戶端 `EventSource` 會自動重連，重連後 `GET /state` 對齊真相）。
  **不可用 `await queue.put(...)`**——它會 block 整個廣播協程，讓一個慢客戶拖垮所有人。
- **只用單一 uvicorn worker**：in-process 註冊表不跨行程共享。dev 用 `--reload`（1 worker，但
  reloader 重啟會清空註冊表——開發期可接受；SSE 會自動重連）。prod **必須** `--workers 1`
  且**不可** `--reload`（見 §9.2）。一旦改用 `--workers N` 或多後端容器，必須換成 Redis pub/sub
  ——`broadcast()` 是唯一置換點（見 §16）。
- **啟動斷言**：`lifespan startup` 時檢查 `os.environ.get("WEB_CONCURRENCY", "1")` 與 uvicorn
  worker 數；若 `> 1` 則 log warning「in-process SSE 廣播不跨 worker，請改 Redis pub/sub 或限為單 worker」。

### 7.6 LLM 整合

- 使用 OpenAI Python SDK，指向自訂端點：
  ```python
  from openai import OpenAI
  client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                  timeout=httpx.Timeout(60.0, connect=5.0))  # 每次呼叫最長 60s
  ```
- **模型名稱自動探測**：啟動時 `GET {llm_base_url}/models`，取 `data[0].id` 快取；可用環境變數 `LLM_MODEL` 覆蓋。
  > 實測 `GET /v1/models` 回報 `GLM-5.2-NVFP4-GB200`（`max_model_len=1048576`），chat completions 接受此名稱。自動探測可避免寫死名稱踩到版本差異。若 `LLM_MODEL` 非空則優先使用；探測失敗且 `LLM_MODEL` 空 → startup 失敗並 log 明確錯誤。
- 呼叫 `client.chat.completions.create(...)`（非 Responses API；sglang 走 Chat Completions）。
- **推理模型 token 預算（關鍵，實測確認）**：此模型為推理模型，`reasoning_tokens` **計入 `completion_tokens`**——即 `completion_tokens` 已含 reasoning + 實際輸出（實測：`completion=1081 = reasoning 897 + 輸出 ~184`）。又實測 `max_tokens=64` 時 reasoning 吃光額度 → **`content` 為空字串、`finish_reason='length'`**。故：
  - 預設 `max_tokens=8192`（環境變數 `LLM_MAX_TOKENS` 可調），確保 reasoning + 輸出皆有餘裕。**太小會截斷成空輸出**。
  - 實測 `max_completion_tokens` 亦被 sglang 接受；兩者皆可，優先 `max_tokens`（相容性更廣）。
  - `temperature=0.3`（環境變數 `LLM_TEMPERATURE`）。
  - 不串流（PoC 簡化；使用者已在等待收齊回覆，blocking 呼叫即可）。**可選 v1.1**：串流 + 「分析中…」進度。
- **`usage` 結構（實測確認）**：`reasoning_tokens` 位於 **`usage.reasoning_tokens`（top-level）**，非 `usage.completion_tokens_details`。assistant message 另含 `reasoning_content` 欄位（推理過程文字，不顯示給使用者）。防禦性讀取仍以 `getattr(usage, "reasoning_tokens", None)` 並 fallback `usage.get("completion_tokens_details", {}).get("reasoning_tokens")`，首次請求記錄完整 `usage`。
  ```
  {"prompt_tokens":144, "completion_tokens":1081, "reasoning_tokens":897, "total_tokens":1225}
  ```
- **失敗處理（與 §3.2 `error` 狀態一致）**：LLM 例外或 `finish_reason=='length'`（空輸出）時，
  設 `status='error'`（**不退回 `collecting`**）、廣播 `error` 事件含訊息、不寫 `consensus`。
  建立者可經 `POST /start` 重試（`error → analyzing`，用同一批已收想法重跑 LLM；見 §3.2 註記）。
- **延遲**：實測真實共識摘要呼叫約 8–15s（視輸入長度），前端 `analyzing` 畫面應容忍 60s（見 §8.2 watchdog）。

### 7.7 Prompt 設計（共識摘要）

System：

> You are a neutral facilitator. Given a shared question and several
> participants' private opinions, synthesize ONE consensus summary that
> best accommodates everyone. Write in Traditional Chinese. Use clear
> markdown. Do not attribute individual opinions to specific labels in a
> way that embarrasses anyone; focus on the agreed-upon direction and any
> key conditions.

User：

```
【討論問題】
{question}

【成員想法】（匿名編號，僅供整合參考，不得在摘要中指名）
A：{opinion_1}
B：{opinion_2}
C：{opinion_3}
...

請產出一份大家盡可能都能接受的共識摘要，包含：
1. 共識方向
2. 關鍵條件 / 限制
3. 若仍有未解分歧，簡述並給出建議
```

- 成員在 prompt 中以 `A/B/C...` 匿名編號；摘要不指名。
- **「不亮底牌」的結構性滿足**：來源文件的「不亮底牌」指的是成員之間不交換原始偏好；
  本版結構上即滿足——成員彼此看不到原文，只看到 LLM 整合後的共識（見 §3.3）。
  「匿名編號」是對 LLM 的去識別化，讓摘要不指名；這不等於來源文件的策略性公開立場
  （$M_i = f(P_i, H, A_i)$，把私有偏好轉成策略性公開訊息），後者為 v2。
- **建立者也是參與者**（§2）：建立者與一般成員一樣，必須在群組室送出自己的想法才納入分析；
  `/create` 表單沒有 opinion 欄位，建立者送出後在 `/g/{pin}` 輸入。`expected_count` 應把
  建立者自己算進去（見 §4 語意註記）。
- 這是「單一共識摘要」產出形態的具體實作（呼應你 §1 選擇）。

---

## 8. 前端規格（React + Vite + TypeScript）

### 8.1 頁面與路由

| 路由 | 內容 |
|---|---|
| `/` | 首頁，兩個按鈕：「建立群組」「加入群組」。加入按鈕旁有 **PIN 輸入框 + 暱稱輸入框**（按下「加入群組」時帶這兩者導向 `/g/{pin}`）。 |
| `/create` | 建立群組表單：問題、建立者暱稱、預期人數（選填）、倒數秒數（選填）。提交後導向 `/g/{pin}` 並保存 `creator_token`。**無 opinion 欄位**——建立者送出想法在 `/g/{pin}`（見 §7.7）。 |
| `/g/{pin}` | 群組室：顯示問題、進度、輸入框、發送鍵；依狀態切換畫面。 |

> 首頁「加入」流程一致性：§3.1 step 2 說「輸入 PIN 與暱稱加入」，§8.1 首頁據此提供 PIN +
> 暱稱兩個輸入框。加入送出後直接以 `POST /api/groups/{pin}/join` body `{nickname}` 加入，
> 再導向 `/g/{pin}`。暱稱在加入時即收集，不在群組室再問一次。

### 8.2 群組室畫面狀態機（`useReducer`）

```
joining → waiting → submitted → analyzing → done
                                  ↘ error（建立者可重試 → analyzing）
```

- `waiting`：已加入、未送出；顯示問題、進度（已送 N / 已加入 M）、輸入框 + 發送鍵。
- `submitted`：已送出；輸入框鎖定，顯示「已送出，等待其他人」+ 進度。
- `analyzing`：顯示「分析中…」（可加 spinner；推理模型約 8–15s，見 §7.6）。
- `done`：顯示共識摘要（markdown 渲染）。
- `error`：顯示錯誤訊息；**建立者**額外顯示「重試分析」按鈕 → `POST /start`（`error → analyzing`）；一般成員顯示「分析失敗，請聯絡建立者」。
- **`analyzing` watchdog**：進入 `analyzing` 後啟動 60s 計時器；期間若未收到任何 `phase`/`consensus`/`error` SSE 事件且 `GET /state` 仍為 `analyzing`，顯示「分析較久，請稍候」並可繼續等。逾 60s 仍無變化則提示「連線可能中斷，正在重連」並觸發 `GET /state` 重新同步。

### 8.3 SSE 消費

- 用原生 `EventSource` API（`new EventSource('/api/events/${pin}')`）。
- 在 `useEffect` 中開啟、註冊 `onmessage` / 具名事件、回傳 cleanup 呼叫 `eventSource.close()`（stale 連線是最常見 bug）。
- **重連後同步**：`EventSource` 內建重連但不重播事件；重連後立刻 `GET /api/groups/{pin}/state` 對齊真相。
- 進度與狀態集中在單一 `useReducer`，避免多個獨立 `useState` 散落。

### 8.4 建立者 UI

- `creator_token` 存 localStorage；`GET /state?token=` 回傳 `is_creator`。
- `is_creator && status=="collecting"` 時顯示「開始分析」按鈕 → `POST /start`。

### 8.5 樣式

- PoC 採簡潔中性風格，單一主題；以可讀與清晰狀態回饋為先。可選 Tailwind 或純 CSS modules。

---

## 9. 系統架構與容器

### 9.1 開發環境（docker compose，三服務）

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: conclave
      POSTGRES_PASSWORD: conclave
      POSTGRES_DB: conclave
    volumes: [pgdata:/var/lib/postgresql/data]
    ports: ["5432:5432"]          # 開發期暴露方便除錯；正式環境移除此 mapping（backend 經 compose 網路存取 db:5432，不需暴露到主機）
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U conclave -d conclave"]
      interval: 5s
      timeout: 3s
      retries: 10

  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://conclave:conclave@db:5432/conclave
      LLM_BASE_URL: http://10.241.77.188:8000/v1
      LLM_API_KEY: probe
      LLM_MODEL: ""                # 空 = 自動探測
      LLM_MAX_TOKENS: "8192"
      LLM_TEMPERATURE: "0.3"
    volumes: ["./backend:/app"]
    ports: ["8000:8000"]
    command: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    depends_on:
      db:
        condition: service_healthy   # 等 db 就緒再啟動，避免 migration 連線失敗

  frontend:
    build: ./frontend
    volumes: ["./frontend:/app", "/app/node_modules"]
    ports: ["5173:5173"]
    command: npm run dev -- --host 0.0.0.0
    depends_on: [backend]

volumes:
  pgdata:
```

- 開發期前端用 Vite dev server（HMR），`vite.config.ts` 中 `server.proxy` 把 `/api` 與 `/events` 代理到 `http://backend:8000`，讓瀏覽器單一來源、免 CORS、SSE 透過 proxy。
- **SSE 透過 Vite proxy 的關鍵設定**：`/events` 代理規則必須關閉 response buffering，否則 `http-proxy` 會緩衝事件、破壞即時串流。`vite.config.ts`：
  ```ts
  server: {
    proxy: {
      '/api': { target: 'http://backend:8000', changeOrigin: true },
      '/events': { target: 'http://backend:8000', changeOrigin: true, ws: false },
    },
  }
  ```
  Vite 的 proxy 預設對 SSE（`text/event-stream`）不緩衝，但若自行加 `selfHandleResponse` 會破壞串流——勿加。
- 後端 `uvicorn --reload`（1 worker）；注意開發期 SSE 長連線偶爾影響 reload 偵測，可接受。reloader 重啟會清空 in-process SSE 註冊表，客戶端 `EventSource` 會自動重連。

### 9.2 正式打包變體（PoC 簡化）

- **`compose.prod.yml` 為兩服務變體（`db` + `backend`，前端靜態檔由 backend 容器提供）**，
  偏離你「前端+後端+資料庫三容器」的要求。**§9.1 的 `compose.yml`（三服務）才是你
  `docker compose up` 實測時會跑的 canonical deliverable**；§9.2 標為 v1.1 可選優化。
- 結 React build（`vite build` → `dist/`）由 FastAPI 提供：
  - API 路由（`/api/*`）掛在**最前**。
  - **SPA fallback**：`StaticFiles(directory="dist", html=True)` 只會對存在的檔案回 200、
    不存在的路徑回 404——**不會 fallback 到 `index.html`**。故需另加 catch-all 路由
    `@app.get("/{path:path}")` 回傳 `dist/index.html`，讓 client-side routes（`/create`、
    `/g/{pin}`）在直接訪問/重新整理時正確返回 SPA（已實測驗證：`StaticFiles(html=True)`
    對 `/g/AAAAA` 回 404；catch-all 修法回 200）。
- prod backend 命令：`uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`（**無 `--reload`**；
  `--workers 1` 維持 in-process SSE 廣播有效，見 §7.5）。
- 生產級（多容器 nginx + 獨立前端 CDN + 水平後端 + Redis pub/sub）為 v2。

---

## 10. 專案結構

```
conclave/
├── docs/                       # 來源文件 + 本規格書
│   ├── Private Multi-Agent Coordination.md
│   ├── private_preference_multi_agent_negotiation_project.txt
│   └── SPEC.md                 # 本文件
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app, lifespan, StaticFiles 掛載
│   │   ├── config.py           # pydantic-settings 環境變數
│   │   ├── db.py               # asyncpg 連線池 / migration
│   │   ├── models.py           # pydantic request/response schema
│   │   ├── pin.py             # PIN 產生
│   │   ├── broadcast.py        # in-process SSE 廣播註冊表
│   │   ├── llm.py              # OpenAI SDK 包裝 + 模型探測 + prompt
│   │   ├── routes/
│   │   │   ├── groups.py       # POST /groups, /join, /state, /start
│   │   │   ├── responses.py   # POST /responses
│   │   │   └── events.py      # GET /events/{pin} (SSE)
│   │   └── tasks.py           # deadline 掃描背景任務
│   ├── migrations/             # SQL（或 alembic）
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx             # 路由
│   │   ├── pages/
│   │   │   ├── Home.tsx
│   │   │   ├── Create.tsx
│   │   │   └── Group.tsx       # 群組室 + useReducer 狀態機 + SSE
│   │   ├── hooks/
│   │   │   └── useGroupSSE.ts
│   │   ├── lib/
│   │   │   └── api.ts          # REST 客戶端
│   │   └── styles/
│   ├── vite.config.ts          # proxy /api, /events
│   ├── package.json
│   └── Dockerfile
├── compose.yml                 # 開發
└── compose.prod.yml            # 正式（可選）
```

---

## 11. 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` | — | Postgres 連線字串 |
| `LLM_BASE_URL` | `http://10.241.77.188:8000/v1` | OpenAI 相容端點（實測活著） |
| `LLM_API_KEY` | `probe` | sglang 開放端點接受任意非空值 |
| `LLM_MODEL` | (空=自動探測) | 覆蓋模型名稱；空時 `GET /v1/models` 取 `data[0].id` |
| `LLM_MAX_TOKENS` | `8192` | 推理模型 token 上限；太小 → 空輸出（見 §7.6） |
| `LLM_TEMPERATURE` | `0.3` | 取樣溫度 |
| `LLM_TIMEOUT` | `60` | 單次 LLM 呼叫最長秒數（connect 5s） |
| `SSE_KEEPALIVE_S` | `15` | SSE keepalive 間隔（`sse-starlette` `ping`） |
| `DEADLINE_SCAN_S` | `5` | 截止掃描間隔 |

---

## 12. 測試策略

- **單元**：`pin.py`（格式、字元集、無撞）、`broadcast.py`（訂閱/推播/移除/queue-full 丟連線）、觸發邏輯（OR 三條件的真值表）、`llm.py` 模型探測與 `usage.reasoning_tokens` 防禦讀取。
- **整合**：用 `httpx` + test client 跑完整 loop：建立 → 加入多人 → 各自送出 → 觸發 → mock LLM → 廣播 → 各端收到 `consensus`。LLM 以 `pytest` monkeypatch 假回應，避免依賴外部端點。
- **觸發邏輯真值表**（重點測試）：
  | expected | deadline | 事件 | 預期 |
  |---|---|---|---|
  | 3 | – | 第 3 份回覆 | 觸發 |
  | – | T | 時間到 | 觸發 |
  | 3 | T | 第 3 份回覆（時間未到） | 觸發 |
  | 3 | T | 時間到（僅 2 份） | 觸發 |
  | – | – | 任何回覆 | 不觸發（等手動） |
  | – | – | 建立者按開始 | 觸發 |
  | – | – | 建立者按開始（0 份回覆） | **409，不觸發** |
  | – | T | 時間到（0 份回覆） | **不觸發，留 collecting，廣播 error** |
- **錯誤路徑測試**：LLM 失敗 → `status='error'` + `error` 事件；建立者 `POST /start` 從 `error` 重試 → `analyzing`；`max_tokens` 太小導致空輸出視為失敗。
- **SPA fallback 測試**（prod 變體）：`GET /create`、`GET /g/AAAAA` 回 200 + `index.html`；`GET /api/health` 回 JSON；`GET /assets/*` 回靜態檔。
- **手動/E2E**：三個瀏覽器加入同一 PIN、各自送出（含建立者）、確認只看到共識摘要（看不到彼此原始想法）、晚到者看到摘要、建立者重試 error。
- **覆蓋率目標 80%+**（依專案測試規範）。

---

## 13. 實作順序（建議）

1. DB schema + migrations + `db.py` 連線。
2. `pin.py` + `POST /groups` + `POST /join` + `GET /state`。
3. `broadcast.py` + `GET /events/{pin}` SSE（先推假事件驗證連線）。
4. `POST /responses` + 進度推播。
5. 觸發邏輯（expected_count + start）+ `deadline` 掃描。
6. `llm.py` + 真實端點整合 + `consensus` 廣播。
7. 前端首頁 + 建立 + 群組室 + SSE 消費 + 狀態機。
8. docker compose 打包，端對端跑通。
9. 測試 + 修正。

---

## 14. 與來源文件的對應（取捨透明化）

| 來源文件概念 | 本版處置 |
|---|---|
| Participant → Private Agent → Coordination Layer 三層 | **採用並簡化**：Participant → （無獨立 Private Agent，後端統一扮演協調層）→ LLM 協調層。Private Agent 策略性行為為 v2。 |
| Hard / soft constraint 結構化 | **不做**：成員直接自由文字；結構化為 v2。 |
| 候選方案生成 + per-agent 可行性評估 + 共識偵測 | **不做**：來源 product doc §10 的「評估 5–10 個候選、偵測共識/衝突」一日 MVP loop，本版以 LLM 直接整合自由文字產出摘要取代，無候選方案、無 per-agent 評估、無結構化共識偵測。 |
| Coordination Layer 交換 structured signal 而非完整文字 | **未實踐**：本版後端與 LLM 讀取所有原始想法全文（§3.3 誠實註記、§16.1）。structured-signal 交換為 v2。 |
| 匿名讓步提案（relaxation loop） | **v2**：本版無「無解→匿名讓步」流程。 |
| 單一共識摘要 vs 個人化分析 | 採「單一共識摘要」。 |
| 單輪 vs 多輪 | 單輪；多輪記錄為 v2。 |
| 三觸發條件 OR | **採用**（你指定）。 |
| Privacy Leakage / Exploitability 評測 | **v2**。 |
| 「不應宣稱密碼學隱私」技術誠實 | **採用**，見 §16。 |
| 首頁兩按鈕、PIN 加入 | **採用**（你指定）。 |

---

## 15. v2 與未來方向（記錄，不實作）

> 多輪審議已於 `docs/MULTIROUND_SPEC.md` 落地（done→collecting、closed、輪次歷史、雙呼叫隔離隱私）。
> 下列項目仍未實作。

- **多輪談判 loop**：ACCEPT/REJECT/COUNTER/CONCEDE、提案/反提案、匿名讓步。多輪審議為其前置。
- **Private Agent 策略層**：每位成員配一個會把私有偏好轉成策略性公開訊息的 Agent。
- **結構化偏好**：自然語言 → hard/soft constraints + disclosure 規則。
- **量化評測 benchmark**：Social Welfare、Pareto、Privacy Leakage（adversarial observer 反推 bottom line）、Exploitability、Agreement Rate；多部門預算情境為優先 benchmark 場景。
- **情境擴充**：薪資/商業合作/房屋議價等「公開底牌即吃虧」場景。
- **生產化**：Redis pub/sub 廣播、多 worker、nginx + 獨立前端、帳號系統、串流 LLM。
- **資料保留 / 清理機制（v2）**：本版永久保留所有群組資料；v2 加入刪除策略，候選方向：
  - TTL 自動刪除已完成（`done`且超過 N 天的群組（含 responses）；
  - 分析完成並推播後立即刪除原始 `responses`，只留 `question` 與 `consensus`（最貼近隱私主題，但無法事後除錯）；
  - 建立 `POST /api/groups/{pin}/purge`（需 `creator_token`）讓建立者主動清除該群組；
  - 連帶決定 PIN 是否回收（本版因不刪除故 PIN 不釋放）。
  採何者或組合，於 v2 時再定。

---

## 16. 風險與已知限制（技術誠實）

1. **非密碼學隱私**：後端單一 backend 可讀到所有原始想法。本版正確說法是
   **「成員之間的 selective disclosure」**，**不是**「連系統都看不到」。
   真正密碼學隱私（MPC / ZKP / TEE / local agent）為未來研究，不假裝已做到。
   ——此條直接採自來源文件 §9 的「技術誠實」。
2. **單 worker 廣播**：in-process 註冊表只在不開多 worker 時有效；水平擴展需換 Redis pub/sub（§7.5 有啟動斷言）。
3. **推理模型延遲與 token（實測確認）**：每次分析約 8–15s；`completion_tokens` **已含** `reasoning_tokens`（非額外）；`LLM_MAX_TOKENS` 需夠大（預設 8192），**過小會因 reasoning 吃光額度而空輸出**（實測 `max_tokens=64` → `content=''`、`finish_reason='length'`）。
4. **sglang 相容性（實測）**：`max_completion_tokens` 與 `max_tokens` 皆被接受；`reasoning_tokens` 位於 `usage.reasoning_tokens`（top-level）。`reasoning_effort` 是否被轉發未驗證，不使用。
5. **SSE 無 replay**：靠 `GET /state` 補救；重連邏輯須在前端實作（§8.3）；Vite proxy 勿緩衝 SSE（§9.1）。
6. **deadline 掃描精度**：5s 掃描間隔內的觸發最多延遲 5s，PoC 可接受。
7. **PIN 安全性**：5 碼空間 ~9.16 億，PoC 足夠；但 PIN 可被猜測或轉發，非安全邊界。正式產品需改為較長 token / 邀請連結。
8. **無帳號**：`participant_id` 存 localStorage；換瀏覽器/清快取即失身分，PoC 接受。
9. **資料永久保留**：本版不做清理，原始想法持續堆積於 Postgres + `pgdata` volume。
   暴露面隨時間增大；刪除/清理機制為 v2（見 §5.1、§15）。
10. **`creator_token` 出現於 URL**：`GET /state?token=` 會進入瀏覽器歷史與 access log（§6.1）。PoC 接受；access log 設為 WARN 以上不記 query；正式產品改 header/session token。
11. **dev compose 暴露 db port**：`5432:5432` 方便除錯但將 Postgres 暴露到主機（§9.1）。正式環境移除該 mapping；開發期勿在不可信網路執行。

---

## 17. 一句話 Pitch

> **Conclave：每個人把真實想法私下交給系統，AI 整合出一份大家都能接受的共識——
> 過程中沒有人需要向彼此攤開底牌。**

對應來源文件最短 pitch：*Private agents. Shared decisions.*
