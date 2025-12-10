# ComfyuiDataLabel

# 🔥 Software Requirements Specification (SRS)

### Human-in-the-loop Data Curation Platform

### Version 3.0 

### Date: 2025-12-10

---

# **1. Introduction**

## **1.1 Purpose**

本文件定義「人類回饋資料整備平台（Human-in-the-loop Data Curation Platform）」之完整功能需求、非功能需求、資料結構、流程規範以及與 ComfyUI 的 API 整合方式。

此平台的目標：

* 高效產生可用於 **DPO / RLHF** 的圖像資料
* 保證資料一致性（Same Prompt, Diff Seed）
* 支援 **大規模生成（Mass Generation）**
* 提供 **行動優先（Mobile-First）的標註介面**
* 在後端以 **智慧批次調度（Smart Orchestrator）** 指揮 GPU Worker（ComfyUI）

本 SRS 亦將後端與 ComfyUI 之間的整合模式明確化，避免後續開發者或 AI coding agent 猜測。
* reference https://docs.comfy.org/development/comfyui-server/comms_routes https://docs.comfy.org/
---

## **1.2 Scope**

系統由四個主要模組構成：

1. **Admin Module**
2. **Task Module（User Facing）**
3. **Smart Orchestrator（Backend）**
4. **Annotation Workbench（Mobile-First）**

ComfyUI **不屬於系統本體**，而是由本系統 orchestrate 的 **外部推論引擎**。

---

## **1.3 Definitions**

**Static Parameter**
必須在整個 workflow 中保持不變（模型、LoRA、VAE 等）

**Dynamic Parameter**
可以逐 Request 改變（seed、steps、CFG、prompt variables）

**Pilot Run**
大批量生成前的小規模運算，用於檢查 OOM 與參數正確性

**Freeze (Snapshot)**
試產通過後，將所有 Prompt、Seeds、Workflow 參數固定，不再允許修改

**Native Batch**
ComfyUI 能一次運算 N 張圖（同 Prompt，不同 seed）

---

# **2. System Overview**

## **2.1 Product Perspective**

平台前端提供 Admin 和 User 操作介面。
後端 Smart Orchestrator 與多台 **ComfyUI Worker（獨立 VM / 容器 / GPU 節點）** 溝通。

後端使用資料庫、Redis Queue、Storage（S3 / NAS）來管理所有生成流程。

---

## **2.2 User Types**

### **Admin**

* 上傳 Workflow（workflow_api.json）
* 設定 ComfyUI Worker endpoint
* 設定最大 batch size、安全配置
* 管理變數池（Variable Pool）

### **User (Annotator / Operator)**

* 設定任務（多少組 prompt）
* 執行 Pilot Run
* Freeze 後啟動 Mass Generation
* 標註圖片（A/B/N 用戶偏好）

---

# **3. Functional Requirements**

# **3.1 Admin Module**

---

## **3.1.1 Workflow Import**

Admin 必須上傳：

* workflow_api.json
* workflow metadata（哪些 nodes 為 Prompt / Seed） # gui support

系統需：

1. 驗證 workflow 結構
2. 偵測不支援節點（如需要 API key 的 ComfyOrg nodes）
3. 確認節點 Schema 是否為 v3（若為舊版需提示）

Admin 需設定：

* `Max_Workflow_Batch_Size`（例如：SDXL=4, SD1.5=8）
* 是否允許 ControlNet、是否動態調整解析度

---

## **3.1.2 Variable Pools**

Admin 建立多類 Variable Pool(user can view)：

* 服飾（dress）
* 光影（lighting）
* 姿勢（pose）
* 標籤（style）
* 等等

支援兩種 sampling mode：

* **No-replacement Random Sampling（無放回抽樣）**
* **Permutation（排列組合）**

系統必須確保 Prompt 生成功能不會產生重複組合。

---

## **3.1.3 ComfyUI Worker Registry（核心新增）**

後台提供頁面維護 **多個 ComfyUI Worker**：
（每台 Worker 通常是一個 GPU VM）

Worker 欄位：

| 欄位                  | 說明                                                |
| ------------------- | ------------------------------------------------- |
| name                | 例如 A100-01                                        |
| base_url            | 例如 [http://10.0.1.33:8188](http://10.0.1.33:8188) |
| api_key (optional)  | 給 ComfyOrg API nodes                              |
| enabled             | 布林值                                               |
| status              | HEALTHY / UNHEALTHY                               |
| max_concurrent_jobs | 預設 1                                              |
| tags                | sdxl / flux / low-vram                            |

### **Worker Test Connection**

Admin 按下「測試連線」按鈕時：

系統需對此 base_url 執行：

1. `GET /system_stats`
2. `GET /queue`

若成功回應 → 標記 Worker 為 HEALTHY
若失敗 → 標記 Worker 為 UNHEALTHY

### **健康檢查（Health Check）**

Smart Orchestrator 每 30 秒：

* 自動 ping 所有 `enabled = true` 的 worker
* 更新其 `status`

### **必要條件**

若無任何 HEALTHY Worker：

* 禁止 Pilot Run / Mass Generation
* UI 顯示錯誤：「無可用 GPU Worker」

---

# **3.2 Task Configuration & Freeze Protocol**

---

## **3.2.1 Task Setup**

User 設定：

* 選 workflow
* 選 Variable Pools
* 指定目標組數 K（如 1000）
* 指定每組 seed 數量（如 2、4、8）

系統會：

* 從 Variable Pools 生成 K 個獨立 Prompt
* 為每組生成 N 個 seed（random 32-bit integer）
* 保存 Prompt + Seed 到 DB（未 Freeze 時允許修改）

---

## **3.2.2 Pilot Run**

系統需執行：

* 選出最消耗資源的 prompt 組合（最高解析度、最多 ControlNet）
* 對任務進行一次 Pilot 請求：

  * `/prompt`（batch_size=1）
* 若 OOM：

  * 降解析度或 batch size
  * 最多 retry 3 次

Pilot 成功後：

* 產生 10 組樣本供 User 審核

---

## **3.2.3 Freeze Protocol**

User 按下 Freeze 之後：

* 所有 Prompt、Seeds、Workflow Snapshot **完全不可修改**
* 任務狀態變為 `FROZEN`
* Mass Generation 才允許開始

---

# **3.3 Smart Orchestrator（Backend）**

此模組負責所有調度行為：

---

## **3.3.1 Worker Selection（硬規則，禁止 AI 自行推斷）**

選擇 Worker 的固定策略：

1. 從 `workers` 篩選出：

   * `enabled = true`
   * `status = HEALTHY`
2. 根據以下排序：

   1. `priority`（Admin 設定）
   2. `current_queue_len`（越少越優先）
3. 取排序後的第一個 Worker

AI coding agent **不得**自創 weighting 或 load balancing 方法。

---

## **3.3.2 Dual-Loop Batch Scheduling**

Smart Orchestrator 必須實作兩層迴圈：

### 外層迴圈（Prompt-level scheduling）

* 每組 Prompt 分為一個獨立任務
* 依序提交到指定 ComfyUI Worker

### 內層迴圈（Seed batch generation）

* workflow.batch_size = N
* 使用 `LatentBatchSeedBehavior` 注入 seeds
* 一次生出 N 張圖

此策略確保：

* Prompt 之間充分利用 GPU
* Seed 區分符合 DPO 要求（Same Prompt, Diff Seed）

---

## **3.3.3 API Integration（所有 URL 必須使用 worker.base_url）**

禁止硬編碼 `http://localhost:8188`！

每次呼叫 API：

```
POST {worker.base_url}/prompt
GET  {worker.base_url}/queue
GET  {worker.base_url}/history/{prompt_id}
POST {worker.base_url}/interrupt
POST {worker.base_url}/free
```

---

## **3.3.4 Queue Depth Control**

為確保高效運作：

* Orchestrator 在送出任務之前必須確認：

  * `pending + running < worker.max_concurrent_jobs`
* 若超過 → 等待（0.5 秒輪詢）

---

## **3.3.5 Failure Isolation / Retry**

每個 Prompt 任務：

* retry <= 3 次
* 若永遠失敗 → 設為 FAILED，不影響其他 Prompt

---

# **3.4 Annotation Workbench（Mobile-First）**

功能：

* N 圖 A/B/N 比較
* 單圖模式（手機）
* Pinch-to-zoom
* 左右滑動切換 seed 變體
* 顯示縮圖列（thumbnails）

標註選項：

* `chosen_index`
* `rejected_index`（可選）
* `spam = true`（全部爆炸）

---

# **4. Data Requirements**

---

## **4.1 DPO JSONL Format**

```json
{
  "prompt": "...",
  "chosen": "images/task123/prompt045_seed991.png",
  "rejected": "images/task123/prompt045_seed552.png",
  "metadata": {
    "workflow": "sdxl_workflow_v5",
    "seeds": [991, 552],
    "model": "SDXL_1.0",
    "variable_pool_version": "dress_pool_v2"
  }
}
```

---

## **4.2 Storage Rules**

* 所有圖片從 ComfyUI 產生後須立即搬移到永久 Storage
* 禁止依賴 `/output` 或 `/temp`

---

# **5. Non-functional Requirements**

---

## **5.1 Reliability**

* 若 ComfyUI Worker crash → Orchestrator 標記為 UNHEALTHY，自動切換其他 Worker
* Mass Generation 不得依賴單一 Worker

---

## **5.2 Performance**

* Worker queue depth 必須保持 ≤ 1（或依設定）
* 任務提交前必須檢查 queue 長度

---

## **5.3 UX**

* 標註 UI 必須在手機上 60 FPS 操作順暢
* 圖片預先載入下一組

---

# **6. Developer Guidelines（強制規範給 AI agent）**

這一節是**為了確保 AI Coding Agent 行為一致、不能亂猜**。

---

## **6.1 ALL ComfyUI calls must use Worker Registry**

### 禁止：

```python
requests.post("http://localhost:8188/prompt")
```

### 必須：

```python
requests.post(f"{worker.base_url}/prompt")
```

---

## **6.2 Workflow injection must obey these constraints**

1. 必須使用 `LatentBatchSeedBehavior` 來處理 N 個 seeds
2. 若 workflow 無法使用此 node → batch_size=1、以 loop 方式生成
3. 禁止 AI 自行修改 Prompt 或 Seed（由 DB 提供）

---

## **6.3 Forbidden Behaviors（明確禁止 AI 做的事）**

* 不得自動調整 prompt 文本
* 不得自動裁切圖片
* 不得創造新的 workflow node
* 不得修改 Admin 設定的 batch size
* 不得使用「猜測」判斷 ComfyUI 版本

---

# **7. Architecture Diagram（文字版）**

```
+------------------+        +------------------+
|     Admin UI     |        |     User UI      |
| - upload WF       |        | - configure task |
| - manage workers  |        | - pilot / freeze |
+---------+--------+        +---------+--------+
          |                           |
          v                           v
     +----+-----------------------------+
     |        Smart Orchestrator        |
     | - worker selection               |
     | - queue depth control            |
     | - pilot run                      |
     | - mass generation scheduling     |
     +----+-----------------------------+
          |
   (for each prompt)
          |
          v
+---------+---------+
|  ComfyUI Worker   |  (N 台 GPU VM)
| - run workflow     |
| - /prompt /queue   |
+---------+---------+
          |
          v
+--------------------+
|   Storage / DB     |
+--------------------+
          |
          v
+--------------------+
| Annotation Workbench|
+--------------------+
```

---



