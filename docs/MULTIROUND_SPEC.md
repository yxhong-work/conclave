# Conclave — 多輪審議規格書(Deliberation Rounds)

> 本文件規劃把 Conclave 從「單輪收齊 → 共識」延伸為「多輪審議」:
> 共識產出後,成員可基於結果開啟下一輪(回應未解分歧、補充新資訊、
> 在候選間表決),而不是被丟回首頁。設計取自多方案評審後的勝出提案
> 「多輪審議 Deliberation Rounds」,並嫁接其他方案的防禦性設計
> (見 §13 決策記錄)。所有標註 **(v2)** 的項目本版不實作。
>
> 閱讀前提:`docs/SPEC.md`(主規格書)與 `docs/MCP_SPEC.md`(研究階段)。
> 本文件只描述「多輪」及其對既有管線的修改。

---

## 0. TL;DR

把 `done` 從「硬終態」重新詮釋為「第 N 輪完成,可開啟第 N+1 輪」。
done 畫面成為審議中樞:顯示當前共識 + 輪次歷史 + 建立者專屬的「開啟下一輪」。
下一輪是同一群組的新一輪收集(成員與 creator_token 沿用,不必重新加入)。

**跨輪脈絡採雙軌**:
- **公開軌(全體可見)**——前輪共識,作為柵欄化共享脈絡進入第 N+1 輪 prompt。
- **私有軌(系統內,絕不公開)**——由獨立的 stance LLM 呼叫產出「每位成員的立場摘要」
  (「成員1: 偏素食; 成員2: 重視預算」),存 DB,跨輪對齊用。讓主持人(LLM)能確認
  「誰的立場在 N→N+1 變了」,產出更精準的演化共識——就像真實會議裡主持人私下追蹤
  每人立場、只向全體報告綜合結論。

MCP 研究僅在問題實質變更時重跑(Jaccard 相似度閾值)。反疲勞由 `max_rounds`、
冷卻 30s、`closed` 終態三重把關。既有單輪群組與舊前端持續可用。

核心不變量:

1. **成員對成員的隱私邊界不變(結構性保證)**:成員之間仍只看到共識,看不到彼此的原始想法或立場摘要。
   共識結果不含可跨輪歸因到個人的身分——由**結構性機制**保證(§5.3):生成共識的 LLM 呼叫
   其輸入**物理上不含**穩定成員標籤、立場摘要、或任何 per-member 資料,模型無法洩漏它看不到的東西。
   這是與主 SPEC §3.3 一致的承諾。**系統內部**為跨輪對齊而私有持有立場摘要(由獨立 LLM 呼叫產出,
   只進私有 DB),不動成員對成員的邊界(主 SPEC §16.1 早已承認後端可讀原文);共識只呈現**群體級**演化
   (「整體在預算上更彈性」),不呈現個人級轉變。
2. **狀態機單層**:群組狀態(`collecting/analyzing/done/error/closed`)仍是唯一驅動,
   不引入 per-round 狀態欄,避免雙寫不同步。`current_round` 只是計數,不另成狀態機。
3. **向下相容**:既有單輪群組行為不變;SSE 新欄位與新事件皆 additive;
   `run_analysis(pool, group_id)` 簽名**不變**(round 由內部讀取),既有測試 seam 不破。
4. **prompt 有界**:無論跑幾輪,每輪 prompt 大小等同第 2 輪(系統+問題+前輪共識+前輪群體演化摘要+研究簡報+當輪想法)。

---

## 1. 定位與範圍

### 1.1 In scope(本版)

- 資料模型加 round 維度:`responses.round_number`、`groups.current_round`/`max_rounds`、`participants.member_seq`(穩定成員編號)、新 `rounds` 表(含 `stance_digest` 私有欄)。
- 三個新 API:`POST /rounds/next`、`POST /rounds/close`、`GET /rounds`。
- 狀態機兩個新弧:`done → collecting`(開下一輪)、`done → closed`(結束討論)。
- `build_prompt` / `run_analysis` 支援雙軌跨輪脈絡(前輪共識 + 前輪立場摘要;簽名向下相容)。
- 獨立 stance LLM 呼叫產出 `stance_digest`(私有立場摘要)+ `stance_shift_summary`(群體演化摘要)+ `_strip_attributable` 共識去歸因後處理。
- MCP 研究的跨輪成本控制(Jaccard 問題相似度 → 重跑或重用簡報)。
- 前端 done 畫面重設計:輪次指示、歷史摘要時間軸、開下一輪表單、結束按鈕、冷卻倒數。
- 反疲勵:`max_rounds` 上限、30s 冷卻、24h 自動關閉。

### 1.2 Out of scope(v2)

- **多輪談判協定**(ACCEPT/REJECT/COUNTER/CONCEDE、匿名讓步提案)——本版仍是「收齊 → 共識」的輪迴,只是多輪;協定式談判為 v2。
- **成員帳號 / 跨裝置身分**——`participant_id` 仍存 sessionStorage;跨輪斷線重連的身分恢復為 v2。
- **rolling meta-summary**(第 1..N-2 輪的累縮)——本版視窗為 1 已足夠;深脈絡為 v2。
- **每輪獨立的 expected_count / deadline**——本版每輪沿用群組設定;v2 再做 per-round 參數。
- **跨輪原始想法的可控揭露**(策略性公開立場)——與主 SPEC §1.3 同列 v2。

---

## 2. 現況盤點:為什麼不能直接加下一輪

| # | 限制 | 位置 | 本版處置 |
|---|---|---|---|
| 1 | `responses` 有 `UNIQUE (group_id, participant_id)`——每人只能發言一次 | `001_init.sql:25` | 加 `round_number`,UNIQUE 拓寬為 3 欄(§3) |
| 2 | `groups.consensus` 單欄——只存一份共識 | `001_init.sql:9`,`db.py:50` | 新 `rounds` 表存每輪共識;`groups.consensus` 保留最新輪(向下相容) |
| 3 | 狀態機單向,`done` 是硬終態,`try_enter_analyzing` 只認 `('collecting','error')` | `db.py:31` | `done` 重詮釋為「可續輪」;新增 `done→collecting`/`done→closed` 兩弧(§4) |
| 4 | 觸發計數無輪次概念,跨輪會把所有回覆算在一起 | `responses.py:30-37` | 計數加 `WHERE round_number=current_round`(§6) |
| 5 | `get_responses_ordered` 取全部回覆,無輪過濾 | `db.py:45` | 新 `get_round_responses_ordered`(round 參數) |
| 6 | `build_prompt` / `run_analysis` 無輪次與前輪脈絡參數 | `llm.py:133,229` | 加 `prev_consensus`/`round_number`(向下相容預設) |
| 7 | MCP 研究每輪重跑,成本線性成長 | `mcp.py`,`llm.py:268` | Jaccard 相似度:問題實質變更才重跑(§7) |
| 8 | `init_db` 讀寫死的 `001_init.sql`,無 migration 框架 | `db.py:15` | 改為 glob `migrations/*.sql` 排序執行(§3) |
| 9 | `leave` 在 `status!='collecting'` 時回 409——done 狀態成員無法退出 | `groups.py:111` | 退出閘拓寬為 `IN ('collecting','done')`(§6) |

---

## 3. 資料模型

新檔 `backend/migrations/002_rounds.sql`,由 `init_db` glob 排序執行(001 先、002 後)。
所有陳述皆 guard,重跑為 no-op;`postgres:16-alpine` 支援 `ADD COLUMN IF NOT EXISTS`。

### 3.1 `responses` 加 round 維度

```sql
ALTER TABLE responses ADD COLUMN IF NOT EXISTS round_number INT NOT NULL DEFAULT 1;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conname = 'responses_group_id_round_number_participant_id_key') THEN
    ALTER TABLE responses DROP CONSTRAINT IF EXISTS responses_group_id_participant_id_key;
    ALTER TABLE responses ADD CONSTRAINT responses_group_id_round_number_participant_id_key
      UNIQUE (group_id, round_number, participant_id);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS responses_group_round_idx ON responses (group_id, round_number);
```

既有列 `round_number=1`,且原 `UNIQUE(group_id, participant_id)` 已滿足 → 新 3 欄鍵亦滿足,無資料衝突。

### 3.2 `groups` 加輪次追蹤

```sql
ALTER TABLE groups ADD COLUMN IF NOT EXISTS current_round INT NOT NULL DEFAULT 1;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS max_rounds   INT NOT NULL DEFAULT 3;
```

`groups.status` 維持自由 TEXT(無 CHECK,與現況一致),新增一個值 `closed`。
`groups.consensus` 持續存**最新輪**共識(向下相容 GET /state 與舊前端)。
`groups.question` 在開新輪時更新為當輪問題(舊前端讀 `question` 恆見當前問題)。

### 3.2a `participants` 加穩定成員編號(跨輪身分對齊)

跨輪立場摘要要求「成員1」在第 1 輪與第 2 輪指同一個人。目前 `build_prompt` 的 A/B/C
標籤**每輪從 A 重新編**,無跨輪連續性——若沿用,A 輪的「成員1」會對不上 B 輪的「A」。
故 `participants` 加一個依**加入順序**的穩定編號:

```sql
ALTER TABLE participants ADD COLUMN IF NOT EXISTS member_seq INT NULL;
-- 回填:依 joined_at 排序,每個 group 內從 1 起
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='participants' AND column_name='member_seq')
     AND EXISTS (SELECT 1 FROM participants WHERE member_seq IS NULL) THEN
    WITH ranked AS (
      SELECT id, row_number() OVER (PARTITION BY group_id ORDER BY joined_at, id) AS rn
      FROM participants
    )
    UPDATE participants p SET member_seq = ranked.rn
    FROM ranked WHERE p.id = ranked.id;
  END IF;
END $$;
-- 新成員:加入時由應用層取 MAX(member_seq)+1 寫入(NOT NULL 由應用層保證;
-- 既有列回填後即非空;為向下相容保留 NULLABLE 直到下次啟動回填完成)
```

`member_seq` **只用於 stance 呼叫(私有軌,§7.2)**,讓 stance_digest 的「成員1」跨輪指同一人。
**共識呼叫(公開軌)絕不使用 member_seq**——它仍用臨時 A/B/C(`chr(65+i)`,每輪重置),
故共識 LLM 的輸入永遠沒有穩定身分 token 可洩漏(§5.3 結構性保證)。
**隱私影響**:member_seq 是系統內部編號,不經任何公開端點傳遞(同 stance_digest);
成員之間仍只看到共識,看不到誰是「成員1」。共識裡就算出現「A」也只是當輪臨時標籤,跨輪無意義。

### 3.3 新 `rounds` 表(輪次歷史 = 摘要時間軸來源)

```sql
CREATE TABLE IF NOT EXISTS rounds (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id       UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    round_number   INT NOT NULL,
    question       TEXT NOT NULL,
    consensus      TEXT NULL,            -- 公開:全體可見的共識(群體級,無個人歸因)
    stance_digest  TEXT NULL,            -- 私有:每位成員的立場摘要,僅 stance 呼叫讀取,絕不公開(§5.3)
    stance_shift_summary TEXT NULL,      -- 公開-安全:群體級演化摘要(「整體在預算上更彈性」),無成員標籤,
                                         --   由 stance 呼叫一併產出,供下一輪共識呼叫當演化訊號(§5.1)
    research_brief TEXT NULL,            -- 快取 MCP 簡報供跨輪成本控制重用(MCP_SPEC §17 的延伸)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    analyzed_at    TIMESTAMPTZ NULL,
    UNIQUE (group_id, round_number)
);
CREATE INDEX IF NOT EXISTS rounds_group_id_idx ON rounds (group_id);
```

無 per-round `status` 欄——輪狀態由 `groups.status` + `current_round` 推導
(輪 < current_round → done;輪 == current_round → group.status),避免雙層狀態機。

`stance_shift_summary` 雖存於 `rounds` 表,但它是**群體級、無成員標籤**的公開安全資料
(可經 GET /rounds 回傳,亦可供下一輪共識呼叫讀取)。`stance_digest` 才是必須嚴格保護的秘密。

`stance_digest` 是本版相對於「只帶共識」設計的關鍵新增,由 LLM 於產出共識時一併生成,
格式為「成員1: <立場>; 成員2: <立場>; …」。它**只**在 `build_prompt` 跨輪脈絡中
讀取(供第 N+1 輪 LLM 對齊立場演化),**絕不**經任何公開端點、SSE 事件、或前端傳遞
(§5.3 存取控制)。`GET /rounds` 的回應模型**不包含**此欄。

### 3.4 既有單輪群組回填(冪等)

```sql
INSERT INTO rounds (group_id, round_number, question, consensus)
SELECT id, 1, question, consensus FROM groups g
WHERE NOT EXISTS (SELECT 1 FROM rounds r WHERE r.group_id = g.id AND r.round_number = 1)
ON CONFLICT (group_id, round_number) DO NOTHING;
```

`done`/`error`/`collecting` 群組皆建一筆 round 1;`done` 的 `consensus` 複製過去,
`collecting` 的為 NULL。

### 3.5 `init_db` 改動(`db.py`)

```python
migrations = sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql"))
for sql in migrations:
    await pool.execute(sql.read_text(encoding="utf-8"))
```

只有 `001_init.sql` 時 glob 回傳單檔,等同現況;加了 `002` 則排序後先 001 後 002。

---

## 4. 狀態機

群組狀態仍是唯一驅動。五個狀態、兩個新弧:

```
第 1 輪:  collecting ──觸發──▶ analyzing ──LLM ok──▶ done
                                 analyzing ──LLM fail──▶ error ──重試──▶ analyzing

第 N>1 輪(自 done):  done ──建立者開下一輪──▶ collecting (current_round++)
                      done ──建立者結束 / 達上限 / 逾時──▶ closed
```

**狀態:**
- `collecting` — 第 N 輪收集中(N = current_round)。可加入、發送、退出、建立者可開始。
- `analyzing` — 第 N 輪整合中,不可發送。(同現況)
- `done` — 第 N 輪共識就緒。成員檢視;建立者可開 N+1 或關閉。(由硬終態重詮釋為「可續輪」)
- `error` — 第 N 輪分析失敗。建立者經 `POST /start` 重試(重跑 current_round)。(同現況)
- `closed` — 最終終態,不再有新輪;輪次歷史唯讀。

**轉換與觸發者:**

| From | To | 觸發 | 閘 |
|---|---|---|---|
| collecting | analyzing | 觸發(expected/deadline/建立者開始) | `try_enter_analyzing`:`status IN ('collecting','error')`——**不變** |
| analyzing | done | `run_analysis` 成功 | 寫 `rounds` + `groups` |
| analyzing | error | `run_analysis` 失敗 | **不變** |
| error | analyzing | `POST /start`(建立者) | **不變**;重跑 current_round |
| done | collecting | `POST /rounds/next`(建立者) | **新**:`try_open_next_round` = `UPDATE groups SET status='collecting', current_round=current_round+1 WHERE id=$1 AND status='done' AND current_round < max_rounds`;rowcount=1(原子、race-free,同 `try_enter_analyzing` 模式) |
| done | closed | `POST /rounds/close`(建立者)或自動關閉 | **新**:`UPDATE groups SET status='closed' WHERE id=$1 AND status='done'` |

**反無限輪:**
1. `max_rounds`(預設 3,建立時設定,1–10)——`try_open_next_round` 的 `current_round < max_rounds` 實體阻擋;前端在 `current_round >= max_rounds` 時隱藏「開啟下一輪」。
2. **冷卻**:`POST /rounds/next` 檢查 `now() - rounds.analyzed_at >= 30s`(前一輪);未到回 409「冷卻中,請稍候」。
3. **自動關閉**:`deadline_scan_loop` 新分支,把 `done` 且上一輪 `analyzed_at` 超過 24h 的群組翻成 `closed`。
4. **並發輪不可能**:群組狀態單值,同一時刻只有一輪在 collecting/analyzing;兩個原子 UPDATE-rowcount 閘保證。

**Startup sweep**(`main.py`):**不變**——`UPDATE groups SET status='error' WHERE status='analyzing'` 仍對(analyzing 是 round-scoped,current_round 標示哪輪在跑)。

**退出/解散**(`groups.py`):閘由 `status=='collecting'` 拓寬為 `status IN ('collecting','done')`。
非建立者於 done 退出僅移除 participant(下輪可重加);建立者於 done 解散則設 `closed` + 解散訊息
(輪次歷史在 `rounds` 表保留,只覆寫 `groups.consensus`)。`analyzing`/`closed` 期間:409。

---

## 5. 跨輪脈絡與隱私(雙呼叫隔離)

### 5.1 兩個獨立 LLM 呼叫(結構性隔離)

本版的核心隱私機制是**結構性**(資訊理論保證),不是政策性(prompt/regex/存取控制)。
每輪的 LLM 工作拆成**兩個獨立的 API 請求**,輸入嚴格區隔:

**呼叫 A — 共識呼叫(公開軌,標籤盲)**:
生成共識(draft + final)。它的輸入**只有**:
- system prompt(中立主持人 + 「不得在共識歸因到個人」)
- 當輪問題
- 前輪共識(柵欄化,群體級合成,§5.2)
- **前輪群體演化摘要** `stance_shift_summary`(群體級、無成員標籤,如「相較前輪,整體在預算上更彈性」)
- 研究簡報(若有,MCP 階段產出)
- 當輪想法,以**臨時 A/B/C** 標籤(`chr(65+i)`,每輪重置)呈現,**且標籤前先隨機打亂順序**(§5.4)

它**物理上不含**:穩定 `成員N` 標籤、`stance_digest`、`prev_stance_digest`、任何 per-member 立場資料。
→ 模型無法洩漏它看不到的東西。共識裡就算出現「成員2」也是模型幻覺的匿名標籤——
公開方從不看 `member_seq→真人` 映射,「成員2」在共識裡跟「A」「參與者2」一樣無跨輪意義。

**呼叫 B — stance 呼叫(私有軌,看標籤)**:
產出 `stance_digest` 與 `stance_shift_summary`。它的輸入有:
- 當輪想法,以**穩定 `成員{member_seq}`** 標籤(跨輪一致)呈現
- 前輪 `stance_digest`(跨輪對齊用)
- 前輪共識(脈絡)
- 當輪問題

它的輸出**只進私有 DB**(`rounds.stance_digest` 與 `rounds.stance_shift_summary`),
**永不**進任何共識呼叫的輸入(只有 `stance_shift_summary` 進下一輪呼叫 A)。

**呼叫 C — 問題生成(建立者留空時)**:`generate_next_question` 從前輪共識萃取未解分歧,
產出 3–5 題下一輪引導問題(條列)。輸入**只有**前輪共識(公開、已去標籤+連結化)+ 前輪問題,
**不見**想法 / `stance_digest` / `member_seq`——較呼叫 A 更窄(連 `stance_shift_summary` 都不帶)。
為第三個獨立 LLM 呼叫;其隱私保護為政策性(呼叫端只傳公開資料,程式碼層保證不傳私有欄),
非 DCIA 結構性保證(不像呼叫 A 那樣靠「輸入物理上無標籤」)。失敗 fallback 至既有 regex
`_seed_next_question`,再 fallback 沿用前輪問題。

**為何這是結構性而非政策性**:跨輪持久假名「成員2」這個 token,**作為穩定身分參照**,
從來不在呼叫 A 的輸入 token 流裡。呼叫 A 是一個完全獨立的 HTTP 請求,其 prompt 物理上不含
穩定身分與 per-member 立場資料。這與 MCP 研究階段(mcp.py)planner 呼叫與共識呼叫分離、
原文永不進 planner 的模式完全一致。政策性風險(共享 API key 的 provider 端關聯)是系統級,
主 SPEC §16.1 已承認,非成員對成員洩漏。

### 5.2 演化感知如何保留(群體級,不靠 per-member)

共識的演化感知需求是「**整體**在預算上更彈性」,不是「**成員2** 轉變了」。
後者正是要消除的個人級歸因風險。群體級演化只需:
- 前輪共識(群體級合成)
- 當輪無標籤想法
- 前輪 `stance_shift_summary`(群體級演化摘要)

三者呼叫 A 都有,且都不含 per-member 可歸因資料。`stance_shift_summary` 是「摘要的摘要」
(原文 → stance_digest → 群體摘要的雙重壓縮),即使 stance 呼叫違規寫入 per-member 片語,
呼叫 A 也沒有標籤可對其歸因,`_strip_attributable`(§5.5)再抓「成員N:」模式。

### 5.3 隱私保證(結構性 vs 政策性,誠實分層)

**結構性(資訊理論)保證——本版核心**:
- **標籤隔離**:共識呼叫 A 輸入用臨時 A/B/C(每輪重置 + 隨機打亂),無穩定身分 token。
- **雙呼叫分離**:stance_digest 由獨立 API 請求產出,只進私有 DB,永不進共識 prompt。
- **群體級 by construction**:前輪共識與 `stance_shift_summary` 都是群體級資料,不含 per-member 欄位。

這三層把共識軌的隱私從「政策性」升回「結構性」——共識 LLM 無 per-member 資料可洩。

**防禦縱深(政策性,但便宜且降低殘餘風險)**:
- **想法隨機打亂**:標籤前 `secrets.SystemRandom().shuffle`,消除「A=最先送出者」的排序 side channel(§5.4)。
- **錯誤訊息通用化**:成員面向的 error 廣播改用通用文案(「分析失敗,請重試」),完整錯誤只留伺服器 log。
  現況 `redact()` 只遮環境變數(TOKEN|KEY|SECRET|PASSWORD),不遮想法內容——OpenRouter 錯誤可能含原文。
- **明確欄位列**:DB 查詢改用明確欄位(非 `SELECT *`),防未來欄位(如 stance_digest)洩進應用記憶體。
- **`_strip_attributable` 後處理**:降級為化妝品(抓模型幻覺的標籤),不再是負重牆(§5.5)。
- **stance_digest 存取控制**(§5.6):與 `responses.content` 同保護層級(API 排除、SSE/log 不傳、DB 隔離)——
  同為已接受的儲存風險(主 SPEC §16.1)。

### 5.4 想法隨機打亂(消排序 side channel)

`get_responses_ordered` 現況 `ORDER BY submitted_at`,使 A=最先送出者;成員觀察進度計數即可關聯。
`build_prompt` 標籤前先隨機打亂:

```python
import secrets
shuffled = list(opinions)
secrets.SystemRandom().shuffle(shuffled)
labels = [chr(65 + i) for i in range(len(shuffled))]  # 臨時 A/B/C,每輪重置
```

一行,零 LLM 成本,消除排序→標籤的結構性 side channel。stance 呼叫(呼叫 B)用穩定 `member_seq`
標籤,排序由 `member_seq` 決定(加入順序),不洩漏送出順序。

### 5.5 共識去歸因後處理(`_strip_attributable`,化妝品層)

共識呼叫 A 結構上無 per-member 資料,但模型可能幻覺出「成員N:」標籤。寫入 DB 前後處理移除:

```python
_ATTRIBUTABLE = re.compile(r"(成員\s?\d+[：:]\s*[^\n]+|成員[A-E][：:]|第[一二三]位成員|編號\d|(?<!\w)A[：:]\s*\S)")
def _strip_attributable(text: str) -> str:
    cleaned = _ATTRIBUTABLE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()
```

這是化妝品——結構性層(§5.3)已防住真實標籤;此處只清幻覺。套用點:共識寫入 DB 前
(與 `_linkify_bare_urls` 同後處理鏈)。

### 5.6 `stance_digest` 存取控制(與原文同層級)

`stance_digest` 是 `responses.content` 的 relabel/壓縮,敏感度等同(皆已存、皆後端可讀、§16.1 已接受)。
保護層級與原文一致:
1. **DB 欄限縮**:只在 stance 呼叫內讀取;無 SQL 路徑寫進 `groups`/`rounds.consensus`。
2. **API 不暴露**:`GET /rounds` 回應模型**明確排除** `stance_digest` 與 `research_brief`
   (含 `stance_shift_summary`,因其為公開安全);**嚴格 response_model**。
3. **SSE 不廣播**:無事件攜帶 stance_digest。
4. **log 不記**:不入任何 log 層級;`redact()` 涵蓋(防除錯傾印)。
5. **明確欄位列**:DB 查詢不用 `SELECT *`(§5.3)。

### 5.7 滑動視窗 = 1(prompt 有界)

只有**緊鄰前一輪**的共識、`stance_shift_summary`、`stance_digest` 進第 N 輪對應呼叫(prompt 有界)。
深脈絡(第 1..N-2 輪累縮 meta-summary,含立場演化軌跡)為 v2。

### 5.8 無論如何消不掉的剩餘風險(誠實面對)

1. **小群組內容再識別(結構性、不可消)**:3–5 人 PoC 裡,獨特立場(唯一素食者)光看共識內容就能被認出——
   有用的共識必然引用具體立場。要消只能讓共識不提任何立場,但那共識就沒用了。PoC 接受(產品方已確認)。
2. **prev_consensus 內容遞迴**:第 N 輪共識若用內容(非標籤)描述獨特立場,會跨輪帶入;
   第 N+1 輪 LLM 可憑內容(非標籤)做跨輪關聯。標籤盲設計防「標籤關聯」不防「內容關聯」;
   `stance_shift_summary` 降低 LLM 去 mine prev_consensus 的動機,但非完全消除。v2 可加遞迴 scrubber。
3. **`stance_shift_summary` 語意歸因**:小群組裡群體語句(「素食立場增強」)仍可能洩誰是素食者。
   群體聚合嚴格安全於 per-member stance_digest,但 2–3 人群組無法完美匿名。
4. **stance_digest 儲存的存取控制**:政策性(非結構性),但與 responses.content 同風險(§16.1 已接受)。
5. **provider 端關聯**:兩呼叫同 OpenRouter key,provider 可跨呼叫關聯。系統級風險(§16.1),非成員對成員。
   v2 可讓 stance 呼叫換不同 provider 結構性隔離。

只有**緊鄰前一輪**的共識與立場摘要進第 N 輪 prompt,不是全部歷史。prompt 維持 O(1)。
深脈絡(第 1..N-2 輪的累縮 meta-summary,含立場演化軌跡)為 v2。

---

## 6. API 與 SSE

### 6.1 新端點

| 方法 | 路徑 | Body | 回應 | 閘 |
|---|---|---|---|---|
| POST | `/api/groups/{pin}/rounds/next` | `{creator_token, question?, timeout_seconds?}` | `202 {round, question}` | creator_token 符;`status='done'`;`current_round < max_rounds`;冷卻已過(上一輪 analyzed_at 起 30s) |
| POST | `/api/groups/{pin}/rounds/close` | `{creator_token}` | `200 {ok}` | creator_token 符;`status='done'` |
| GET | `/api/groups/{pin}/rounds` | — | `200 [{round_number, question, consensus, stance_shift_summary, created_at, analyzed_at}, ...]` | 任一參與者(輪次歷史共享,不需 token)。**回應不含 `stance_digest` 與 `research_brief`**(§5.6);含 `stance_shift_summary`(群體級,公開安全)。**嚴格 response_model** 強制排除私有欄 |

`POST /rounds/next`:insert 新 `rounds` 列(round_number=current_round+1,
question=提供或 LLM 生成(自前輪共識萃取未解分歧,3–5 題條列;失敗 fallback 至 regex `_seed_next_question`,再 fallback 沿用前輪問題);LLM 呼叫 inline(blocking,8–15s),僅在建立者留空且前輪有共識時觸發),原子遞增 current_round 並設 `status='collecting'`(`try_open_next_round`),
可選設新 deadline(省略 timeout_seconds 則清掉舊 deadline——後續輪預設開放、僅建立者觸發)。
廣播 `round` 事件。

`POST /rounds/close`:設 `status='closed'`(`try_close_group`)。廣播 `round` 事件(status='closed')。

### 6.2 變更端點

| 方法 | 路徑 | 改動 |
|---|---|---|
| POST | `/api/groups/{pin}/responses` | 路由從群組列讀 `current_round` 存入 `responses.round_number`(客戶端無需改——輪次由伺服端決定)。觸發計數 round-scoped:`WHERE group_id=$1 AND round_number=$2`。狀態閘 `status!='collecting'` 不變。新 UNIQUE 允許同一 participant 每輪一份(第 2 輪不再 409)。 |
| POST | `/api/groups/{pin}/start` | 閘不變(status IN collecting/error,creator_token)。**`run_analysis` 簽名不變**,round 由內部讀 current_round。 |
| GET | `/api/groups/{pin}/state` | **加欄**:`current_round: int`、`max_rounds: int`。`consensus` 回最新輪(向下相容)。`question` 回當前輪問題。全 additive。 |
| POST | `/api/groups` | `CreateGroupRequest` 加 `max_rounds: int = Field(default=3, ge=1, le=10)`。 |
| POST | `/api/groups/{pin}/leave` | 閘拓寬為 `status IN ('collecting','done')`。建立者於 done 解散設 `closed`(非 done+解散訊息,防誤開)。 |

### 6.3 SSE 事件(向下相容——皆 additive 欄或新事件名)

| 事件 | data | 改動 |
|---|---|---|
| `phase` | `{status, round?}` | 加選填 `round`。舊前端只讀 `status` 不破。第 2 輪的 analyzing 對舊前端與第 1 輪無異(它就是又看到 analyzing,其 done 畫面已由 GET /state 顯示最新共識)。 |
| `consensus` | `{content, round?}` | 加選填 `round`。舊前端只讀 `content` 進 done。 |
| `progress` | `{participant_count, submitted_count, round?}` | 加選填 `round`。 |
| `round`(新) | `{round, question, status: "opened"\|"closed"}` | 新事件。舊前端忽略未註冊事件(EventSource 行為)。新前端用以重置輸入與顯示新輪橫幅。每輪轉換最多 1 次,遠低於 queue maxsize=16。 |
| `error` / `research` | — | 不變。 |

### 6.4 重連/resync 語義

- 無 SSE replay(不變——客戶端以 GET /state 對齊)。
- 重連時 GET /state 回 `current_round`,客戶端比對本地輪次;不符則 GET /rounds 取完整歷史,
  重置 UI 到當前輪的 collecting/done。沿用既有「GET /state 是真相來源」模式。

---

## 7. LLM 與 MCP 研究(每輪 4 個呼叫)

### 7.1 兩個 prompt 建構函式(標籤盲 vs 看標籤)

本版的核心是兩個**輸入嚴格區隔**的 prompt 建構:

**`build_consensus_prompt`(呼叫 A 用,標籤盲)**:
```python
build_consensus_prompt(question, opinions, research=None,
                       prev_consensus=None, prev_shift_summary=None) -> tuple[str, str]
```
- 想法先 `secrets.SystemRandom().shuffle`,再貼**臨時 A/B/C**(`chr(65+i)`,每輪重置)。**無 member_seq**。
- user prompt(輪 > 1):前輪共識柵欄 + 前輪 `stance_shift_summary` 柵欄(群體級,無標籤)+ 研究簡報柵欄 + 當輪想法(A/B/C)。
- system prompt:中立主持人 + 「不得在共識歸因到個人;共識只呈現群體方向與條件」。
- **此函式的輸出 prompt 物理上不含穩定身分或 per-member 立場資料**(§5.3 結構性保證)。

**`build_stance_prompt`(呼叫 B 用,看標籤)**:
```python
build_stance_prompt(question, opinions_with_seq, prev_stance_digest=None, prev_consensus=None) -> tuple[str, str]
```
- 想法以**穩定 `成員{member_seq}`** 標籤(跨輪一致),排序按 `member_seq`。
- user prompt:前輪 `stance_digest` 柵欄 + 前輪共識 + 當輪想法(成員N)。
- system prompt:產出兩段——`stance_digest`(每成員一行)與 `stance_shift_summary`(群體級,無標籤);
  「stance_shift_summary 不得含成員編號或可歸因到個人的描述」。
- **此函式的輸出只進私有 DB,永不進任何共識呼叫**。

### 7.2 `run_analysis` 改動(**簽名不變**,4 個 LLM 呼叫)

`run_analysis(pool, group_id)` —— 簽名不變(保既有 `fake_run(pool, group_id)` 測試 seam)。內部讀 `current_round`:

1. `SELECT current_round, question FROM groups WHERE id=$1`(明確欄位,非 `SELECT *`,§5.3)。
2. 取當輪回覆 + member_seq 映射:`get_round_responses_ordered(pool, group_id, current_round)`
   (回 `(content, member_seq)`)。
3. 若 current_round > 1:取前一輪 `SELECT consensus, stance_digest, stance_shift_summary FROM rounds WHERE ...`(current_round-1)。
4. **呼叫 A1 draft**:`build_consensus_prompt(question, opinions, None, prev_consensus, prev_shift_summary)` → draft(標籤盲)。
5. **呼叫 A2 研究**(MCP,若啟用):見 §7.3 成本控制;planner 看 draft,不看想法(既有結構隔離不變)。
6. **呼叫 A3 final**:`build_consensus_prompt(..., research, prev_consensus, prev_shift_summary)` + draft → final(標籤盲)。
   後處理:`_linkify_bare_urls` + `_strip_attributable`(§5.5,化妝品)。
7. **呼叫 B stance**(可與 A2 並行,看標籤):`build_stance_prompt(question, opinions_with_seq, prev_stance_digest, prev_consensus)`
   → `stance_digest` + `stance_shift_summary`。失敗 → 兩者 NULL,分析仍成功(fail-open)。
8. 寫:`UPDATE rounds SET consensus=$3, stance_digest=$4, stance_shift_summary=$5, analyzed_at=now() WHERE ...`
   **且** `UPDATE groups SET status='done', consensus=$3 WHERE id=$1`(**groups 不寫 stance_digest/stance_shift_summary**)。
9. 廣播:`consensus {content, round}` + `phase {status:'done', round}`(廣播去歸因後共識,絕非 stance_digest)。

終態契約不變:成功 → `status='done'` + 共識廣播;失敗 → `status='error'` + error 廣播(通用文案,§5.3)。
既有 `fake_run` 不必改(簽名不變)。stance 呼叫失敗 → stance_digest/shift NULL,第 N+1 輪退回「只帶共識」(fail-open)。

### 7.3 MCP 研究跨輪成本控制

每輪重跑完整研究階段(planner LLM + 最多 `mcp_max_tool_calls` 工具呼叫)**僅當當輪問題實質不同於前輪**。
啟發式:**Jaccard token 相似度**(嫁接自 Design B,取代 exact hash-skip):

- 把 `question_N` 與 `question_{N-1}` 做 token 集合比對;**相似度 < 0.7(即變更 > 30%)才重跑**。
- 「素食選項?」→「要加素食選項嗎?」這類改寫不會觸發無謂重跑(exact hash 會)。
- 仍無 LLM 呼叫成本(只做 token 集合比較)。
- 問題不變:重用 `rounds.research_brief`(round N-1),零外部成本。
- 問題變:重跑 `research_phase`,新簡報存入當輪 `rounds.research_brief` 供後續重用。

這是 MCP_SPEC §17(「研究簡報落 DB」原列 v2)的刻意延伸:跨輪 hash/Jaccard-skip 的成本正當性強——
R 輪全跑成本 3×R LLM + 3×R 工具(線性);skip 後「問題沒改的輪」零 MCP 成本。
UX-first 鼓勵每輪改問題(自動播種自未解分歧),故問題**通常**會變;但 operator 設 max_rounds=10 不改問題時,
只付第 1 輪的 MCP 成本。

### 7.4 prompt 跨多輪有界

**共識呼叫 A** prompt 恆 O(1):系統(~300 tok)+ 問題(~100)+ 前輪共識柵欄(~400 tok)
+ 前輪 stance_shift_summary 柵欄(~150 tok,群體級)+ 研究簡報柵欄(≤ `MCP_TOTAL_RESULT_CHARS`)
+ 當輪想法(每人 ≤4000 字,受 participant 數上限,臨時 A/B/C)。無 stance_digest、無穩定標籤、無更早輪累積。

**stance 呼叫 B** prompt 亦 O(1):系統 + 問題 + 前輪 stance_digest 柵欄(~200 tok)
+ 前輪共識 + 當輪想法(成員N,穩定)。兩呼叫皆第 10 輪與第 2 輪同大小;`rounds` 表線性成長(儲存),prompt 不成長。

---

## 8. 前端

### 8.1 `Group.tsx` 狀態機改動

State 加:
```typescript
interface State { /* 既有... */ current_round; max_rounds; rounds: RoundInfo[]; next_round_cooldown: number | null; }
interface RoundInfo { round_number; question; consensus: string | null; }
```

新 actions:
- `round_opened` — phase 重置為 waiting、清輸入、更新 current_round + question + rounds 歷史。由 SSE `round`(status='opened')或 GET /state 偵測 current_round 遞增觸發。
- `round_closed` — 設「討論已結束」顯示。由 SSE `round`(status='closed')或 GET /state 顯示 status='closed' 觸發。

`mapPhase` 既有狀態不變;`closed` 在 done 分支內以橫幅呈現(非新 Phase)。

### 8.2 SSE(`useGroupSSE.ts`)

- 新 `onRound`:`(round, question, status: 'opened'|'closed') => void`。
- `onPhase`/`onConsensus` 把 `round` 欄傳給 reducer(對齊事件與當前輪)。
- 舊簽名向下相容(round 選填)。

### 8.3 done 畫面重設計(UX-first 核心)

`phase === 'done'` 且 `status !== 'closed'` 時:
1. **輪次指示**(共識卡頂):「第 {current_round} 輪 / 最多 {max_rounds} 輪」。
2. **當前共識**(同現況——ReactMarkdown + remark-gfm)。
3. **輪次歷史摘要時間軸**(共識下方,可摺疊):縱列過往輪次,每項「第 N 輪」+ 一行共識預覽(~100 字),
   點擊展開全文。done 畫面 fetch GET /rounds。讓「對話有可見的弧」而非單一結果。
4. **「開啟下一輪」按鈕**(僅建立者,且 `current_round < max_rounds` 且 `status === 'done'`):
   開一內聯表單,可編輯問題欄(預填 LLM 生成的未解問題清單)+ 選填 timeout。送出 POST /rounds/next。
   30s 冷卻期間按鈕顯示倒數且禁用。
5. **「結束討論」按鈕**(僅建立者,`status === 'done'`):POST /rounds/close,先確認。
6. **非建立者等待提示**(非建立者,`status === 'done'`,且 `current_round < max_rounds`):
   顯示「建立者正在考慮是否開啟下一輪…」附脈動圓點,避免非建立者誤以為結束而提前退出。
   `current_round >= max_rounds` 時不顯示(已達上限,無下一輪可開)。
7. **「回到首頁」按鈕**(全員——保留向下相容與非建立者離開用)。

`status === 'closed'` 時:
- 顯示「討論已結束」橫幅蓋於最終共識上。
- 輪次歷史時間軸仍可檢視(唯讀)。
- 僅「回到首頁」(無開下一輪)。

### 8.4 新輪開啟時(SSE `round` 或 GET /state poll)

- 轉 `waiting` phase(輸入框重啟)。
- 顯示「第 {N} 輪開始」橫幅(5s 自動消失)。
- 顯示新問題(可能與第 1 輪不同)。
- 輸入框上方顯示可摺疊「前輪共識」(預設收合,避免疲勞;可展開)。
- 輸入框清空,備新輪想法。

### 8.5 身分/session

- `participant_id` 與 `creator_token` 在 sessionStorage 跨輪沿用(同 participant、同群組、同分頁),不必重新加入。
- `UNIQUE(group_id, round_number, participant_id)` 允許每人每輪一份——「發送→鎖輸入→等他人」UX 每輪重複。
- 成員清掉 sessionStorage 再訪 `/g/{pin}` 需重加新暱稱(舊暱稱已佔→409)。已知 PoC 限制(同現況,跨輪才相關);完整重認證為 v2。

### 8.6 `api.ts` 新增

```typescript
export interface RoundInfo { round_number; question; consensus: string|null; created_at; analyzed_at: string|null; }
export const openNextRound = (pin, creator_token, question?, timeout_seconds?) =>
  post(`/groups/${pin}/rounds/next`, { creator_token, question, timeout_seconds });
export const closeGroup = (pin, creator_token) => post(`/groups/${pin}/rounds/close`, { creator_token });
export async function getRounds(pin): Promise<RoundInfo[]> { /* fetch */ }
```
`GroupStateResp` 加 `current_round`、`max_rounds`(additive)。

---

## 9. 向下相容與遷移安全

### 9.1 既有部署資料

- `002_rounds.sql` 於每次啟動由 init_db glob 執行。ALTER 皆 guard(ADD COLUMN IF NOT EXISTS、DO block 換約束)。
  既有 responses 得 `round_number=1`(DEFAULT),既有 groups 得 `current_round=1` + `max_rounds=3`。
  換約束安全:既有列在 `(group_id, participant_id)` 唯一且全 `round_number=1` → `(group_id, round_number=1, participant_id)` 亦唯一。無資料衝突。
- backfill INSERT(round 1)為 ON CONFLICT DO NOTHING——冪等。`done` 群組共識複製到 `rounds.consensus`;`collecting` 為 NULL。

### 9.2 既有單輪群組持續可用

- 舊 `done` 群組:GET /state 仍回 `status='done'`、consensus(最新)、question(原)。舊前端見「回到首頁」如昔。
  新前端另見 `current_round=1`、`max_rounds=3`、(若 sessionStorage 還有 token)「開啟下一輪」按鈕。
- 舊 `collecting` 群組:如昔(round 1 collecting);觸發邏輯不變(輪次計數 = 全部,當 round_number 全 1)。
- 舊 `error` 群組:建立者 POST /start 重跑 round 1(current_round=1)。不變。

### 9.3 舊前端不破

- SSE 加選填 `round`——只讀 `status`/`content` 的客戶端不受影響;新 `round` 事件被未註冊 handler 忽略。
- GET /state 加 `current_round`/`max_rounds`——舊前端只解已知欄(additive JSON)不受影響。
- POST /responses 客戶端無改(輪次伺服端決定);舊客戶端發送得 `round_number=1`(current_round),單輪群組正確。
- 新端點(rounds/next、rounds/close、GET /rounds)additive——舊前端不會呼叫。

### 9.4 運行中容器

- 滾動重啟:migration 於啟動跑。舊容器仍跑時不知新欄,但只讀寫已知欄(status/consensus/question 等,猶存可用)。
  新欄有 DEFAULT,舊容器 INSERT 得正確預設。無損毀。
- glob 改:只有 `001_init.sql` 時回傳單檔(同前);加 `002` 則排序後先 001 後 002,不會 002 先跑。

### 9.5 測試 fixture(`conftest.py`)

- TRUNCATE 加 `rounds`:`TRUNCATE groups, participants, responses, rounds CASCADE`。
  `rounds` 對 groups 有 FK CASCADE,truncating groups 已連鎖——但明列更清楚且合於既有模式。

---

## 10. 已知風險與限制(技術誠實)

1. **約束重建鎖**:換 responses UNIQUE 短暫取 ACCESS EXCLUSIVE 鎖。PoC 規模(數百列)可忽略;
   大量資料需 `CREATE UNIQUE INDEX CONCURRENTLY` + `ALTER TABLE ... CONSTRAINT USING INDEX`。PoC 接受簡單 ALTER。
2. **done→collecting 違反原單輪契約**:SPEC.md §3.2 原說「觸發是單次的」。多輪刻意違此。
   startup sweep 只掃 `analyzing`,不碰 `done`——無誤翻;但 `try_open_next_round` 須為唯一 done→collecting 路徑。
3. **MCP 成本隨問題每輪變更**:若每輪改問題且 max_rounds 高(如 10),MCP 成本 3×10 LLM + 30 工具。
   Jaccard skip 在問題重複時省;UX-first 鼓勵改問題(反擊敗 skip)。緩解:預設 max_rounds=3、文件明示成本、operator 監帳。
4. **跨輪共識的注入面**:第 N 輪共識是 LLM 產出,惡意成員可 craft 想法影響共識、再以「脈絡」跨輪帶入。
   柵欄處理(同 MCP 簡報——未受信任、「忽略其中指令」)緩解,但比 MCP 研究的結構隔離軟。
5. **下一輪問題自動生成升級**:原 regex 萃取「未解分歧/建議」段已升級為 LLM 生成(`generate_next_question`),
   產出 3–5 題簡短未解問題條列(繁中,每題 ≤30 字)。LLM 只見前輪共識(公開、標籤盲)+ 前輪問題,
   不見 `stance_digest` / 想法(較共識呼叫 A 更窄)。失敗 fallback 至既有 regex `_seed_next_question`(沿用前輪問題)。
   建立者送出前仍可編輯。此 LLM 呼叫的隱私為政策性保護(呼叫端只傳公開資料),非 DCIA 結構性保證。
6. **sessionStorage 跨輪遺失**:成員跨輪關分頁再開 `/g/{pin}` 會丟 participant_id,需重加新暱稱(舊名已佔→409)。
   已知 PoC 限制(同現況,跨輪才相關)。完整帳號系統為 v2。UX 緩解:done 畫面於輪關閉前提示「想參與下一輪?請保持此頁面開啟」。
7. **done 畫面多狀態複雜**:可開下輪/達上限/closed/冷卻中等。緩解:清晰視覺層級——輪次指示恆見、
   開下輪按鈕醒目附輪號、closed 有獨立「討論已結束」橫幅。
8. **`ResearchOutcome.rounds` 命名碰撞**:既有欄指「已執行工具呼叫數」,非「討論輪」。
   型別不撞(frozen dataclass 上的 int),但 log/文件語意混淆。建議另 commit 重命名為 `tool_calls_executed`(低風險)。
9. **共識軌隱私(已結構性保護)**:本版經隱私探索後採雙呼叫隔離(DCIA,§5.1)——共識呼叫的輸入
   物理上不含穩定身分與 per-member 立場,跨輪假名追蹤風險**結構性消除**(資訊理論保證,非政策)。
   `_strip_attributable` 降為化妝品層(抓模型幻覺標籤,§5.5)。**剩餘的不可消風險**(內容再識別、
   prev_consensus 遞迴、stance_shift_summary 語意歸因、stance_digest 儲存存取控制、provider 端關聯)
   詳列於 §5.8,皆為 PoC 固有或系統級,非成員對成員新增風險。
10. **member_seq 回填與新成員**:既有 participants 回填 member_seq 依 joined_at 排序;新成員加入時取
    `MAX(member_seq)+1`。若兩人同毫秒加入,race 可能短暫重號——由 UNIQUE(group_id, member_seq) 約束
    (本版可加)或應用層重試吸收。PoC 規模可接受。
11. **LLM 立場摘要品質**: stance_digest 是 LLM 觀察的濃縮,可能失準或漏抓立場。跨輪對齊因此只是「最佳努力」,
    不是真實身分追蹤。fail-open:產出失敗或無法解析 → NULL,第 N+1 輪退回「只帶共識」,不讓分析失敗。

---

## 11. 實作順序(建議)

1. **Commit 1(資料層)**:`002_rounds.sql`;`db.py` glob + 新 helpers(`try_open_next_round`、
   `try_close_group`、`get_round_responses_ordered`、`get_round_consensus`、`set_round_done`、
   `get_rounds_history`、`insert_round`);`conftest.py` TRUNCATE 加 rounds;既有測試確認綠。
2. **Commit 2(狀態機 + API)**:`groups.py` 新端點(next/close/GET rounds)、get_state 加欄、
   leave 閘拓寬;`responses.py` round-scoped 計數;`models.py` 新 schema;`tasks.py` auto-close 分支;
   `test_rounds.py` 新檔。
3. **Commit 3(LLM 雙呼叫隔離 + 立場摘要)**:拆 `build_consensus_prompt`(標籤盲 + `secrets` shuffle)
   與 `build_stance_prompt`(看 member_seq);`run_analysis` 4 呼叫流程(共識 draft/final + stance 呼叫);
   產出 `stance_digest` + `stance_shift_summary` 寫 rounds;`_strip_attributable` 化妝品後處理;
   錯誤廣播改通用文案;DB 查詢改明確欄位列;Jaccard skip + `rounds.research_brief`;
   隱私回歸測試(共識呼叫 prompt 不含 member_seq/stance_digest、共識去歸因、GET /rounds 排除 stance_digest、shuffle 打亂排序)。
4. **Commit 4(前端)**:`Group.tsx` done 重設計 + round_opened/closed actions;
   `useGroupSSE.ts` onRound;`api.ts` 三新函式;輪次歷史時間軸;冷卻倒數。
5. **Commit 5(文件)**:本文件修訂;`SPEC.md` §3.2 狀態機圖加 done→collecting 與 closed、§15 v2 移除多輪。

---

## 12. v2 方向(記錄,不實作)

- 多輪談判協定(ACCEPT/REJECT/COUNTER/CONCEDE、匿名讓步提案、條件交換)。
- 成員帳號 / 跨裝置身分 / 斷線重連身分恢復。
- rolling meta-summary(第 1..N-2 輪累縮)供深脈絡。
- 每輪獨立 expected_count / deadline / 觸發條件。
- 跨輪原始想法的可控揭露(策略性公開立場)。
- 並行多輪(同一群組不同子集同時討論不同子問題)。

---

## 13. 決策記錄

| 決策 | 選擇 | 理由 |
|---|---|---|
| 整體架構 | 勝出:單層狀態機 + `rounds` 表 + `current_round` 計數 | 兩個 runner-up 各有硬傷:Design A(同列 round 計數)UX 弱(固定問題、無 closed、leave 閘沒拓寬);Design B(rounds 一級實體)雙寫 fragility、破測試 seam、rolling summary 多一個 LLM 失敗點。勝出方案的單層狀態機 + 自動播種 + 冷卻/close + leave 閘拓寬最均衡。 |
| `run_analysis` 簽名 | **不變**(round 內部讀)——嫁接自 Design A | 保既有 `fake_run(pool, group_id)` 測試 seam 不破;消除 round_number 呼叫端/DB 不符的失敗模式;縮小 diff。 |
| MCP 跨輪成本 | Jaccard token 相似度(< 0.7 重跑)——嫁接自 Design B | exact hash-skip 被一詞改寫擊敗(「素食選項?」→「要加素食選項嗎?」無謂重跑);Jaccard 仍無 LLM 成本但容許小改寫。 |
| 跨輪脈絡與隱私 | **雙呼叫隔離**(DCIA):共識呼叫標籤盲 + stance 呼叫看標籤,兩獨立 API 請求——經隱私探索 workflow 評審推薦 | 原「雙軌餵 stance_digest 進共識呼叫」是政策性防護(prompt+regex),產品方質疑不穩定。雙呼叫隔離讓共識呼叫的輸入 token 流**物理上不含**穩定身分與 per-member 立場,把共識軌隱私升回**結構性**(資訊理論保證)。演化感知改由群體級 `stance_shift_summary` 承載(群體演化才是有用的 facilitation;per-member 轉變正是要消除的風險)。 |
| 穩定成員編號用途縮限 | `participants.member_seq` **只用於 stance 呼叫**(私有軌) | 共識呼叫絕不用 member_seq(維持臨時 A/B/C),故穩定身分 token 永不進共識 LLM 輸入。member_seq 是系統內部編號,不經公開端點。 |
| 想法隨機打亂 | 標籤前 `secrets.SystemRandom().shuffle` | 消除「A=最先送出者」的排序 side channel(`ORDER BY submitted_at` 結構性洩漏);零 LLM 成本。 |
| 共識去歸因 | `_strip_attributable` **降級為化妝品** | 結構性層(標籤盲+雙呼叫)已防住真實標籤;後處理只清模型幻覺的標籤,不再是負重牆。 |
| stance_digest 儲存 | 存 `rounds.stance_digest`,與 `responses.content` 同保護層級 | 產品方確認存。stance_digest 是原文的 relabel/壓縮,敏感度等同(皆已存、§16.1 已接受);存取控制 5 層(§5.6)。 |
| 小群內容再識別 | 接受為 PoC 固有(產品方確認) | 3–5 人群裡獨特立場(唯一素食者)光看共識內容就能被認出;有用的共識必然引用具體立場。消掉只能讓共識不提立場,但那共識沒用。 |
| 研究簡報落 DB | `rounds.research_brief` | MCP_SPEC §17 原列 v2 的刻意延伸;跨輪重用的成本正當性強(問題不變的輪零 MCP 成本)。 |
| 下一輪問題來源 | 自動播種自前輪「未解分歧」段,可編輯 | UX-first:下一輪自然自對話已識別的未解點生長,非空白「再討論一次」;fallback 沿用前輪問題。 |
| 反疲勞 | max_rounds(預設 3) + 30s 冷卻 + 24h auto-close + closed 終態 | 多重把關防無限輪與棄置群組;冷卻給成員讀共識的時間。 |
| 退出閘 | 拓寬至 IN (collecting, done) | 勝出方案獨有;另兩案漏掉——done 狀態成員否則卡死無法退出。 |
