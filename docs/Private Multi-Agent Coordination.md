---
title: Private Multi-Agent Coordination

---

# Private Multi-Agent Coordination
## 讓多個人的 AI 在不必公開所有私人條件的情況下，協助群體達成共識

> **一句話版本：**  
> 每個人把真正的限制與偏好告訴自己的 Private Agent；多個 Agent 再透過一套協調機制尋找所有人都能接受的方案，而不需要把每個人的私人理由全部攤在群組裡。

---

## 1. 對應 Hackathon 的哪個方向？

Sea × OpenAI Regional Codex Hackathon 公開的三個 focus areas 為：

1. **Autonomous and Adaptive AI**：能以更高自主性與適應性運作的 AI agents。
2. **AI-Native Products and Operations**：把 AI 放在產品與工作流程核心的解決方案。
3. **Deep Domain AI**：針對特定產業問題打造的專門 AI 應用。

### 建議主分類：AI-Native Products and Operations

Private Multi-Agent Coordination 最適合放在 **AI-Native Products and Operations**。

原因是它不是把「AI 助手」附加到既有多人決策流程，而是重新定義多人協作本身：

**傳統流程**

```text
每個人公開意見
      ↓
群組討論 / 投票
      ↓
妥協
      ↓
決定
```

**AI-native 流程**

```text
Participant A ─ Private Agent A ─┐
Participant B ─ Private Agent B ─┼─ Coordination Protocol ─ Agreement
Participant C ─ Private Agent C ─┘
```

AI 不只是聊天介面，而是承擔：

- 理解每個人的自然語言條件；
- 區分 hard constraints 與 soft preferences；
- 代表個別 participant 評估候選方案；
- 發現衝突；
- 尋找 Pareto-improving / feasible alternatives；
- 在無解時提出最小幅度的 constraint relaxation；
- 控制哪些資訊可以公開、哪些應維持私有。

如果將來把它專門做成「採購議價」、「排班協調」或「醫療 shared decision-making」，則也可能進一步落入 **Deep Domain AI**。但對 hackathon MVP 而言，建議維持通用的多人協調產品，主打 **AI-Native Products and Operations**。

官方來源：  
Sea, *Inaugural Sea × OpenAI Regional Codex Hackathon Series Kicks Off in Singapore*  
https://security.sea.com/news/407

---

# 2. 問題：為什麼多人有了 ChatGPT，還是很難做決策？

很多多人決策問題並不是缺乏資訊，而是每個人都存在：

- 不方便公開的限制；
- 不想解釋的私人原因；
- 可以妥協但不願意一開始就公開的底線；
- 不容易量化的偏好；
- 彼此衝突、而且會隨其他方案改變的條件。

例如三個朋友決定晚餐：

### Alice 的真實條件

- 素食。
- 預算最多 NT$400。
- 預算限制不希望被其他人知道。

### Bob 的真實條件

- 很想吃日式料理。
- NT$500 以下都可以。
- 距離不是問題。

### Carol 的真實條件

- 不吃生食。
- 希望走路 10 分鐘內。
- 可以接受日式，但不是必要。

如果大家直接在群組裡討論，Alice 可能只說：

> 「我今天不太想吃太貴的。」

於是其他人得到的其實不是 Alice 的真正 constraint。

另一種方式是大家把所有秘密交給同一個 ChatGPT，但這代表所有 participant 必須把私人資訊交給同一個共享 context。

**Private Multi-Agent Coordination 想解決的是：**

> 能不能讓每個人對自己的 Agent 說真話，再讓這些 Agent 共同尋找可接受方案，而不要求所有人先向彼此揭露完整底牌？

---

# 3. 核心產品概念

暫時可以把產品稱為：

# Blindspot

**Tagline：**

> **AI helps groups reach agreements without exposing every private constraint.**

系統中有三種角色：

```text
┌─────────────────┐
│ Participant A   │
└────────┬────────┘
         │ private input
         ▼
┌─────────────────┐
│ Private Agent A │
└────────┬────────┘
         │
         │ structured response / feasibility signal
         │
         ▼
┌───────────────────────────┐
│ Coordination Layer        │
│                           │
│ - candidate generation    │
│ - conflict detection      │
│ - consensus tracking      │
│ - relaxation proposal     │
└─────────────┬─────────────┘
              │
      ┌───────┴────────┐
      ▼                ▼
Private Agent B    Private Agent C
      │                │
Participant B      Participant C
```

重要的是：**不是建立一個知道所有秘密的「超級 GPT」然後替大家決定。**

產品真正有趣的部分是 participant 之間存在明確的 information boundary。

---

# 4. 基本 Workflow

## Step 1：建立共同目標

其中一人建立 room：

> 「我們今天晚上要決定去哪裡吃飯。」

這是所有人都能看到的 public objective。

---

## Step 2：每個人私下輸入自己的條件

每個 participant 在自己的 private view 裡輸入自然語言。

例如：

> 我吃素。  
> 最多 400 元，但不要跟其他人說預算是我的限制。  
> 地點都可以。

Private Agent 將內容整理為內部結構：

```json
{
  "hard_constraints": [
    "vegetarian_available",
    "price_per_person <= 400"
  ],
  "soft_preferences": [],
  "disclosure": {
    "budget": "private"
  }
}
```

LLM 的價值在這裡是將模糊自然語言轉成可協調的 constraint / preference，而不是單純聊天。

---

## Step 3：系統提出候選方案

Coordinator 可以透過搜尋、資料庫或預先準備的候選資料取得：

```text
Restaurant A
Restaurant B
Restaurant C
Restaurant D
...
```

接著將候選方案交給每個 Private Agent 評估。

Private Agent 不一定要回傳自己的完整 constraint，只需要回傳例如：

```text
Restaurant A → reject
Restaurant B → acceptable
Restaurant C → acceptable
```

或：

```json
{
  "candidate": "Restaurant B",
  "feasible": true,
  "utility": 0.82
}
```

---

## Step 4：找到共識

Coordinator 找到：

```text
                  Alice    Bob    Carol

Restaurant A       ❌       ✅      ✅
Restaurant B       ✅       ✅      ✅
Restaurant C       ✅       ❌      ✅
```

因此：

# Restaurant B — Consensus Found

公開畫面只需要呈現：

> 所有 participant 的 hard constraints 都滿足。

不需要顯示：

> 「Alice 因為沒錢，所以 A 不行。」

---

# 5. 最有趣的情境：沒有解

真正能讓 demo 變有趣的不是「AI 找到餐廳」，而是：

# NO FEASIBLE AGREEMENT

假設：

```text
A：素食 + <= 400
B：一定要日式
C：一定要 10 分鐘內
```

沒有任何候選同時符合。

傳統系統只能說：

> 找不到結果。

Private Multi-Agent Coordination 可以進一步找出：

> **只要放寬一項 constraint，就可以產生解。**

但它不一定要公開這個 constraint 是誰提出的。

中央畫面：

```text
NO CONSENSUS

One constraint prevents an agreement.

Anonymous relaxation proposal:

「是否有人願意將步行距離
 由 10 分鐘放寬到 15 分鐘？」

[ Keep my constraint ]
[ I can accept this ]
```

Carol 在自己的 private view 按：

> I can accept this.

中央重新計算：

```text
Recomputing...
      ↓
CONSENSUS FOUND
```

這比單純「GPT 推薦餐廳」更接近真正的 **AI-mediated coordination**。

---

# 6. Hackathon Demo 範例

## Demo：三個隊員決定晚上去哪裡吃飯

三位隊員各拿自己的手機。

大螢幕只顯示：

```text
BLINDSPOT

Decision:
Where should we have dinner?

Participants
────────────
A    🔒 Ready
B    🔒 Ready
C    🔒 Ready
```

### Participant A 私下輸入

> 我吃素，最多 400 元。  
> 預算不要公開。

### Participant B 私下輸入

> 我今天非常想吃日式。  
> 500 元以下都可以。

### Participant C 私下輸入

> 我不吃生食，而且最好走路十分鐘內。

大螢幕：

```text
3/3 private preferences received.

Searching for agreement...
```

接著：

```text
Candidate 1  ✗
Candidate 2  ✗
Candidate 3  ✗

No feasible agreement.
```

系統提出匿名 relaxation：

```text
Would anyone accept
a 15-minute walking distance?

1 participant can relax this constraint.
```

C 私下同意。

畫面立即變成：

# 🤝 CONSENSUS FOUND

並顯示最終餐廳及「為什麼是合理共識」的公開資訊，但不暴露 A 的 private budget constraint。

---

# 7. 第二個 Demo Case：直接拿 Hackathon 選題當例子

這甚至可以成為產品故事的一部分。

三個隊員正在決定：

> 「我們到底要做哪一個 Hackathon 題目？」

### Member A

> 我想做很酷的 demo。  
> 我不想做 infra。  
> 但不要直接跟其他人說我其實很擔心來不及完成。

### Member B

> 我希望技術深度高。  
> 我非常排斥純 wrapper。

### Member C

> 我希望 product sense 強。  
> 最好能在台上立即看懂。

候選：

```text
Self-Healing Runtime
Agent Wallet
Private Coordination
AI Game
Product Passport
```

系統不是用一個 GPT 武斷地「選最好的一個」，而是讓每個人的 Private Agent 對候選方案評估：

```text
                       A      B      C
Self-Healing           ✗      ✓      ✗
Agent Wallet           ✓      ✗      ✓
Private Coordination   ✓      ✓      ✓
AI Game                ✓      ~      ✓
```

最終：

```text
Private Coordination

3/3 acceptable
Highest aggregate preference
0 hard constraints violated
```

這會讓「我們為什麼做這個產品」本身成為 demo story。

---

# 8. 為什麼不能直接用一個 GPT / Codex 取代？

這是這個題目最重要的 defence。

## 「大家把條件全部交給 ChatGPT 不就好了？」

可以，但那改變了問題：

> 所有人必須把自己的 private information 交給同一個共享 session / operator。

Blindspot 的核心不是「有一個模型很會找答案」，而是建立：

- participant identity；
- private contexts；
- disclosure rules；
- multi-party agreement state；
- negotiation protocol；
- consensus / conflict semantics。

這些都是產品層與協調層，而不是單一模型 prompt。

---

## 「每個人各自用 ChatGPT 再自己討論呢？」

也可以，但人必須自己完成：

```text
Private reasoning
      ↓
決定公開多少
      ↓
交換意見
      ↓
理解衝突
      ↓
重新談判
      ↓
判斷是否已有共識
```

Private Multi-Agent Coordination 的產品價值，就是把這個 coordination loop 變成 AI-native workflow。

Codex 可以協助我們**開發**這套產品，但不等於這套 multi-party coordination service 本身。

---

# 9. 一個重要的技術誠實：MVP 不應宣稱「密碼學隱私」

Hackathon 版本需要特別避免過度宣稱。

如果三個 Private Agent 最終都跑在同一個 backend，而且 backend 可以讀到所有原始輸入，那麼：

> **平台營運者仍可能看到私人資訊。**

所以 MVP 正確的說法應該是：

> **Selective disclosure / privacy between participants**

而不是：

> 「連系統本身都無法知道你的秘密。」

一天版本可以做到：

- Participant A 看不到 B/C 的原始 private context；
- 公開畫面不顯示 private constraint；
- Coordinator 優先交換 structured feasibility signal，而非任意轉傳完整文字；
- 明確記錄哪些資訊可公開。

未來如果真的要做強 privacy，才可研究：

- local agent execution；
- trusted execution environments；
- secure multi-party computation；
- zero-knowledge proofs；
- encrypted constraint evaluation。

**Hackathon 不需要做到這些，也不應假裝已經做到。**

---

# 10. 一天 MVP 應該做到什麼？

不要做一個完整的「AI 世界和平協商平台」。

只做完整核心 loop：

```text
Create room
    ↓
3 users join
    ↓
Private natural-language preferences
    ↓
LLM → hard / soft constraints
    ↓
Evaluate 5–10 candidates
    ↓
Detect consensus / conflict
    ↓
Generate one anonymous relaxation proposal
    ↓
Private acceptance
    ↓
Final consensus
```

只要這條 loop 完整、UI 清楚、三支手機真的能互動，demo 就已經成立。

---

# 11. 什麼東西不要做？

一天內應刻意避免：

- 複雜 multi-agent framework；
- agent 自己無限對話幾十輪；
- cryptographic MPC；
- 通用議價語言；
- 長期 memory；
- 真正的經濟機制設計；
- 任意人數的大規模 consensus；
- 很多 SaaS integration；
- 太多 domain。

這些都會讓產品主線模糊。

---

# 12. 最核心的產品 Thesis

Private Multi-Agent Coordination 並不是：

> **「讓 GPT 更會幫大家做決定。」**

而是：

> **今天的 AI 大多在強化 individual intelligence；但許多現實問題缺的不是更聰明的個人，而是更好的多人協調機制。**

每個人都可以有很強的 AI。

但三個 AI 各自很強：

```text
Smart Agent
Smart Agent
Smart Agent
```

並不自動等於：

```text
Good group decision
```

中間缺少的是：

# Coordination

因此這個產品真正想探索的是：

> **What does collaboration look like when every person has a private AI representative?**

這是我們認為 Private Multi-Agent Coordination 最有意思的地方。

---

## Hackathon Pitch 一句話

> **Blindspot lets everyone's AI know the things they don't want to say out loud — and helps the group reach an agreement without exposing every private constraint.**

或者更短：

> **Private agents. Shared decisions.**
