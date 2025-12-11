人類回饋資料整備平台 Version 2– 系統需求與開發建議
專案概述
本專案旨在建立一套符合 IEEE 830 標準的 人類回饋資料整備平台（Human‑in‑the‑loop Data‑Curation Platform），目標是高效生成並標註用於直接偏好最佳化（DPO）與 RLHF 的圖像資料集。系統整合 Stable Diffusion 生圖流程（透過 ComfyUI 伺服器）與標註工作台，提供從「批量生成」到「人工標註」的一站式工作流程。
在整理用戶提供的 v2.1 版 SRS 時，發現若干問題與潛在風險。這些問題主要涉及 ComfyUI API 的相容性、批次生成的種子控制、佇列管理以及用戶體驗。以下先對這些問題作出分析，並引用官方文件來說明原因，再給出修訂後的需求與開發指引。
潛在問題分析
1 ComfyUI API 與佇列管理
    • 佇列 API 行為變更。 官方文件指出，ComfyUI 伺服器的核心 API 包含 /prompt（提交 Prompt 到執行佇列）與 /queue（讀取或管理佇列）等端點[1]。您的 SRS 中假設後端可以將數百個請求同時塞入 ComfyUI 佇列，並只依靠 ComfyUI 自身的效能來排列工作。但新版 ComfyUI 已限制單次佇列新增數量，並提供 batch count limit 設定以避免一次排入過多作業。若在短時間內提交大量請求，可能導致伺服器拒絕請求或 UI 無法回應。因而必須在外層實作佇列管理，定期透過 /queue 監視佇列長度並控制提交速率。
    • 狀態同步。 /prompt 端點返回 prompt_id 及在佇列中的位置[2]。舊版 API 直接以回傳順序即為執行順序，新版 ComfyUI 支援取消佇列 (/queue POST) 和中斷執行 (/interrupt)[3]。因此後端必須處理取消與重試，並實現狀態機以應對 OOM 或其他錯誤。
2 批次生成與種子控制
    • 種子不唯一。 根據 ComfyUI 使用者分享，當在同一個批次中生成多張圖片時，所有圖像的隨機性均來自同一個基準 seed；每張圖像的 seed 為基準 seed 的遞增值[4]。這與 SRS 中「內層迴圈批次生成 N 張圖，每張圖只種子不同」的假設不同：ComfyUI API 目前無法直接指定多個 seed，除非借助自訂節點（例如 LatentBatchSeedBehavior）調整種子行為。若不處理，可能導致產生的 N 張圖之間相關性過高，影響 DPO 標註效果。
    • 種子紀錄。 為了保證試產與 Freeze 之後的可重現性，必須知道每一張圖的 seed。然而當使用批次生圖時，ComfyUI 只回報起始 seed，後續 seed 需要推算 start_seed + index。若流程中任何節點動態改變 seed（例如 KSamplerAdvanced 的 control_after_generate），就無法準確推算。因此需明確規範在 workflow 中僅使用固定 seed 行為，或者透過 LatentBatchSeedBehavior 將 batch 隨機種子寫回 metadata。
3 變數池採樣與資料一致性
    • 組合爆炸與重複。 SRS 中建議由系統隨機從「服裝」、「光影」等變數池組合出 1000 個 Prompt。若變數池很大但任務量有限，純隨機採樣可能出現重複組合。建議提供「排列組合模式」與「無放回隨機抽樣模式」，並在 Freeze 時將實際抽樣結果保存，避免後續重複。
    • Prompt 整合時的語法問題。 若變數池中包含中英文混雜或特殊符號，拼接 Prompt 時需注意逗號、空格等分隔符，避免生成語法錯誤的 Prompt。
4 標註介面與使用者體驗
    • 行動優先但資訊量大。 您希望在 6 吋手機上提供 A/B/N 張對比標註。若一次需比較 8 張圖，手機版逐一切換會降低效率。此外，Zoom 功能若實作不佳可能產生延遲。建議在手機端提供「最佳快照縮圖列」與「雙指放大」結合的標註介面，並支援在桌機版一次顯示多張圖以提高效率。
    • 社群標籤管理。 公開任務池允許依標籤挑選任務，但標籤來源不明且易失真。建議預先定義標籤集合，並讓任務創建者選擇合適標籤，以利標註者根據興趣篩選。
5 彈性拓展與升級
    • V3 Schema 遷移。 ComfyUI 官方正推行 V3 Schema，許多節點已逐步過渡；若 workflow 使用較舊的節點定義，未來升級可能導致相容性問題。平台應提供節點版本檢測工具，在匯入 workflow 時提醒使用者升級至 V3。
    • 新模型支援。 需持續關注 ComfyUI 的版本更新與新模型支援狀態，採用可插拔的模型管理設計，避免僅局限於 SD1.5 或 SDXL。
修訂後的系統需求
以下根據上述分析對原 SRS 進行修訂與補充。若未提及即沿用原有描述。
1 引言
平台旨在建立從批量生成、試產檢測到人工標註的完整管線，主要解決 GPU 資源管理、資料一致性、種子追蹤及標註效率問題。
2 整體描述
    1. 架構概觀： 平台包含三層：
    2. 前端（Admin／User UI） – 管理 workflow 上傳與配置；提交生成任務；標註圖片。
    3. 後端調度層（Smart Orchestrator） – 管理任務生命週期，與 ComfyUI API 通訊，控制佇列長度及重試；處理 GPU OOM 和種子管理。
    4. 生成引擎（ComfyUI Worker） – 負責載入模型，執行 workflow，回傳生成圖片與 metadata。
    5. 佇列與執行模型： 後端調度層使用 /prompt POST 將 prompt 提交至 ComfyUI 佇列[7]；透過 /prompt GET 或 /queue GET 監控執行狀態[3]。調度器維持 ComfyUI 佇列長度≤2，透過 /queue POST 清除 queue_pending/queue_running 或 /interrupt POST 中斷當前工作以處理高優先度任務。
    6. 種子控制： Workflow 應加入 LatentBatchSeedBehavior 節點（或等效自訂節點），允許在批次中指定每張圖的 seed 或設定為「隨機種子」模式，避免批次生成的圖片種子遞增問題[4]。後端在試產及 Freeze 後把實際使用的 seed 寫入資料庫以便重現。
3 功能需求
3.1 Admin 模組 – Workflow 與變數池管理
    1. Workflow 上傳與限制設定： Admin 上傳 workflow_api.json 時，需要：
    2. 指定 Max_Workflow_Batch_Size（根據模型與 VRAM，建議 SDXL 為 4，SD1.5 為 8）。
    3. 標記哪些 Input Node 對應系統變數（prompt、seed 等）。
    4. 提供 workflow 所用節點的 Schema 版本，若檢測到舊版 V2 節點，提示升級。
    5. 變數池管理：
    6. 支援建立多種類別變數池（服裝、光影、角色等），並記錄其內容與版本。
    7. 提供「無放回隨機抽樣」與「排列組合」模式，避免生成任務中出現重複組合。
    8. 抽樣時生成對應的 Prompt 字串，需自動處理分隔符，避免 Prompt 語法錯誤。
3.2 User 模組 – 任務配置、試產與鎖定
    1. 任務配置： User 選擇 Workflow 和變數池後輸入目標組數 K（例如 1000 組），系統根據抽樣模式生成 K 個獨特的 Prompt 並為每組設定批次中圖片數 N（預設 2, 4 或 8）。後端利用 LatentBatchSeedBehavior 或類似機制為每張圖分配獨立 seed。
    2. 試產階段： 提交批次任務前，調度器選擇最可能觸發 OOM 的參數組合（最大解析度、最多 ControlNet 等），利用 /prompt 提交一個測試 batch 並監測是否 OOM。如失敗自動降低解析度或 batch size 重試（最大重試 3 次）。試產成功後產出一小部分樣本（例如 10 組）供 User 確認。
    3. 鎖定（Freeze）機制：
    4. User 審核試產樣本後點擊「鎖定」，系統將實際抽樣到的 Prompt 列表、每張圖的 seed、用於 workflow 的參數快照等寫入資料庫，任務狀態變為 FROZEN。
    5. 再執行批次生圖時禁止更改任何動態參數；若需修改需重新開始新的任務與試產流程。
3.3 後端模組 – 智慧調度與錯誤恢復
    1. 雙層調度策略：
    2. 外層迴圈（Prompt 層）： 針對每個 Prompt 建立一個任務物件，依序使用 /prompt 提交給 ComfyUI。調度器在提交前檢查 /queue 是否低於安全閾值，否則等待；每次提交後記錄 prompt_id 用於追蹤。
    3. 內層迴圈（Batch 層）： 在 workflow 中設定 batch_size = N，並透過 LatentBatchSeedBehavior 讓每張圖使用獨立 seed。利用 GPU SIMD 一次生成 N 張圖，可選擇 batch_count > 1 以重複運行多次。
    4. 錯誤處理與隔離：
    5. 若某個 Prompt 生成失敗或遭安全過濾器攔截，調度器將該 batch 標記為 FAILED 並記錄錯誤，不影響其他任務。
    6. 若 ComfyUI 出現 OOM 或進程崩潰，調度器可呼叫 /interrupt 中斷執行，再使用 /free 卸載模型後重新提交失敗的任務（重試上限 3 次）。
3.4 標註工作台 – Mobile First
    1. 資料呈現： 標註頁面在手機版中採用單圖模式，提供左右滑動快速切換，並在底部顯示小縮圖列方便跳轉；桌機版可提供網格模式一次檢視多張圖。
    2. 標註邏輯：
    3. 選擇最佳（Chosen）： 用戶在 N 張圖中選出一張最符合偏好的圖。
    4. 標記次差（Rejected）： 可選，選出一張最差的圖。
    5. 垃圾標記（Spam）： 若 N 張圖全數無法使用，點擊垃圾按鈕，系統標記整組為 spam，對應的圖像不入資料集。
    6. 系統需保證選出的 chosen 與 rejected 的 Prompt 完全一致，僅 seed 不同。
    7. 社群任務池： 為避免任務來源不明，僅允許標註者根據預定義標籤篩選公開任務；新增任務時須選擇標籤。
4 資料需求
    1. DPO 輸出格式（JSONL）： 每一行包含 prompt、chosen（圖片路徑）、rejected（圖片路徑或 null）、seeds（包含 chosen/rejected 的種子）以及 metadata（模型名、batch_size、變數池版本等）。
    2. 圖片儲存： 生成後立即將圖片從 ComfyUI 的暫存目錄轉存至永久儲存（如 S3 或 NAS），並將路徑寫入資料庫。禁止依賴 /temp 或 /output 目錄持久保存。
5 非功能需求
    1. 可靠性：
    2. 後端須實作監控程序，若 ComfyUI 進程異常終止，能自動重啟並恢復佇列。
    3. 每個 Prompt 任務重試次數不得超過 3 次；若多次失敗需人工介入。
    4. 效能：
    5. 調度器維持 ComfyUI 佇列中最多 1 至 2 個待執行工作，利用 /queue GET 監控[3]。
    6. 支持多 GPU 集群，可根據 GPU 空閒狀態動態分配任務。
    7. 使用者體驗：
    8. 標註介面支援離線瀏覽與預取，降低載入時間。
    9. 在手機端使用固定底部工具列，確保拇指操作友好；在桌機端顯示快捷鍵提示。
6 開發指引與實作建議
    1. ComfyUI API 封裝： 建立一層與 ComfyUI 通訊的服務，封裝 /prompt、/queue、/history、/interrupt 等 API[3]。此服務應支持：
    2. 佇列長度檢查與排隊等待。
    3. 自動重連 WebSocket (/ws) 以接收進度與錯誤[8]。
    4. 請求超時與錯誤重試。
    5. 種子管理： 在 workflow 中加入 LatentBatchSeedBehavior 或同等自訂節點，用於控制批次內種子分配（隨機或固定）並在輸出元資料中回傳 seeds。若無法使用，自行在後端生成每個圖像的 seed，並將 batch_size 設為 1，改由 batch_count 控制生圖數量（效能較低但可確保每張圖 seed 可控）。
    6. 佇列調度策略：
    7. 後端調度器維護內部任務佇列，每次從佇列取出一個 Prompt 任務，檢查 ComfyUI 佇列長度（透過 /queue GET），若小於設定值則提交；否則等待。這樣可防止瞬間排入大量任務導致 UI 卡死。
    8. 實作優先級隊列，高優任務（試產、快速迭代）可插隊。
    9. 試產與 OOM 預測： 透過分析 workflow 中的解析度、 ControlNet 數量、所載模型大小等指標預估 VRAM 使用；試產階段先生成資源佔用最大的組合。根據 ComfyUI 回傳的錯誤資訊調整 batch_size 或關閉部分功能重試。
    10. 持續整合與升級：
    11. 定期關注 ComfyUI 更新日誌，特別是重大版本（v0.4.0 之後）對 API 或節點的變更[5]。
    12. 在開發與部署時鎖定 ComfyUI 版本，並在升級前於測試環境驗證所有 workflow。
7 整體架構流程圖
以下以簡化的文字流程圖描述系統工作流程。方括號表示模組，箭頭表示資料或控制流。
┌─────────────┐      ┌─────────────────────┐      ┌───────────────────┐
│ Admin UI   │      │ Smart Orchestrator │      │ ComfyUI Worker    │
│ (workflow  │      │ (Backend)          │      │ (SD generation)   │
└──────┬──────┘      └──────────┬──────────┘      └─────────┬──────────┘
       │ Upload workflow & variables        │                    │
       │ set Max_Workflow_Batch_Size        │                    │
       │ define variable pools              │                    │
       ▼                                     │                    │
┌─────────────┐                               │                    │
│ User UI    │                               │                    │
│ (task cfg  │                               │                    │
└──────┬──────┘                               │                    │
       │ Select workflow & pools             │                    │
       │ Input number of prompt combos (K)   │                    │
       ▼                                     │                    │
  generate K prompts with seeds              │                    │
       │                                     ▼                    │
       │                            [Pilot Run & OOM check]      │
       │                             ├─ run heavy params → /prompt
       │                             ├─ monitor /prompt & /queue
       │                             └─ produce sample images
       │                                     │                    │
       │<───── present samples to user ──────┘                    │
       │ User approves & Freeze             │                    │
       │ (store prompts & seeds)            │                    │
       ▼                                     │                    │
 submit each prompt to orchestrator queue    │                    │
       │                                     ▼                    │
       │                         Outer loop: iterate prompts      │
       │                         ├─ wait until ComfyUI queue      │
       │                         │    length < limit               │
       │                         ├─ set batch_size = N            │
       │                         ├─ attach seeds via node         │
       │                         └─ POST /prompt                  │
       ▼                                     │                    ▼
  save returned images & metadata             │        ComfyUI loads model
       │                                     │        executes workflow,
       │                                     │        returns images
       ▼                                     │                    │
┌─────────────────────┐                       │                    │
│ Storage & Metadata │◄──────────────────────┘                    │
└─────────┬──────────┘                                             │
          │                                                      ▼
          │                                    ┌─────────────────┐
          │                                    │ Annotation      │
          │                                    │ Workbench (UI)  │
          │                                    └──────┬──────────┘
          │                                           │
          └───────────────── present N images per prompt
                                              │
                         User selects chosen/rejected/spam
                                              │
                                Generate DPO JSONL & export
上述流程圖概述了整個管線：Admin 與 User 透過 Web UI 配置任務；後端調度器負責試產、凍結及發送請求給 ComfyUI；生成結果經儲存後進入標註階段，產生最終的 DPO 資料集。
結論
原始 SRS 已整合許多創新的設計（試產與鎖定協議、變數池抽樣、智慧調度及行動優先標註介面），但仍需注意 ComfyUI API 行為與 seed 控制方面的細節。透過本修訂，新增種子管理機制、佇列監控策略以及升級兼容性考量，可以降低執行風險並提升用戶體驗。開發團隊需根據最新的 ComfyUI 文檔進行實作與測試，並在系統設計中保持模組化與可擴充性，以便適應 Stable Diffusion 模型和 ComfyUI 生態的不斷演進。
附錄：供 AI Coding Agent 使用的詳細指南
為了減少後續由 AI 程式開發代理做決策的自由度，以下提供更具體的技術細節、範例流程和資料庫模式。這些內容可直接用於編程實作，幾乎不需要進一步推測。
A ComfyUI API 操作
    1. 核心端點一覽：
    2. POST /prompt – 送出 workflow JSON 以排入執行佇列，回傳 prompt_id 與佇列位置[7]。
    3. GET /prompt – 查詢當前佇列與執行狀態，包含當前執行的 prompt 及錯誤訊息[3]。
    4. GET /queue – 查看待處理和執行中的任務清單，用於控制提交節奏[3]。
    5. POST /queue – 管理佇列，可清除 queue_pending/queue_running 任務；可用於插隊或取消。
    6. POST /interrupt – 立即停止目前正在執行的 workflow[3]。
    7. GET /history – 查詢已完成或失敗的 prompt 歷史。
    8. WebSocket /ws – 實時取得進度與節點執行狀態更新[8]。
    9. 提交 workflow 的程式範例：
以下 Python 函數將修改後的 workflow JSON 送到 /prompt，並回傳 prompt_id 與排隊位置。
import requests
import json

BASE_URL = "http://localhost:8188"

def submit_prompt(workflow_data: dict, client_id: str = "hil-agent") -> tuple[str,int]:
    payload = {
        "prompt": workflow_data,
        "client_id": client_id
    }
    resp = requests.post(f"{BASE_URL}/prompt", json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data.get("prompt_id"), data.get("number", -1)

# 用法示例：
prompt_id, position = submit_prompt(modified_workflow)
print(f"Submitted prompt {prompt_id} at queue position {position}")
    1. 佇列監控與等待：
AI 代理需在提交每個新任務前確認 ComfyUI 佇列深度不超過安全閾值。下方函式透過輪詢 /queue 來監控當前 queue_pending/queue_running 任務數，直到低於指定閾值後才返回：
import time

def wait_until_queue_below(limit: int) -> None:
    while True:
        q = requests.get(f"{BASE_URL}/queue").json()
        pending = q.get("queue_pending", [])
        running = q.get("queue_running", [])
        if len(pending) + len(running) < limit:
            break
        time.sleep(0.5)

# 範例：確保佇列中少於兩個任務再提交
wait_until_queue_below(limit=2)
    1. WebSocket 監聽進度：
以下示例展示如何建立 WebSocket 連線以監聽 prompt 執行進度與節點狀態。實際環境中應使用非同步程式庫（如 websockets 或 aiohttp）實作，再根據收到的訊息更新資料庫或採取重試。這裏僅示意流程：
import websockets
import asyncio
import json

async def monitor_progress(prompt_id: str):
    async with websockets.connect(f"ws://localhost:8188/ws") as ws:
        async for message in ws:
            data = json.loads(message)
            if data.get("prompt_id") != prompt_id:
                continue  # 只處理目標 prompt
            # 根據 message["type"] 處理不同類型事件
            if data["type"] == "executed" and data.get("node_id") == "end":
                print(f"Prompt {prompt_id} finished")
                break
            elif data["type"] == "error":
                print(f"Error executing {prompt_id}: {data.get('error')}" )
                break

# 使用範例：
asyncio.run(monitor_progress(prompt_id))
B 種子管理與 workflow 注入
    1. 生成可控制的種子列表：
    2. 對於每個 prompt，按 batch_size = N 產生 N 個隨機整數 seed。例如使用 random.randint(0, 2**32-1)。
    3. 把這些 seed 保存到 task_prompts.seed_list 中，以便後續追蹤。
    4. 修改 workflow 中的 seed 與 batch_size：
    5. 遍歷 workflow JSON 的 nodes 陣列，找到類型為 KSampler 或 KSamplerAdvanced 的節點。
    6. 將其 inputs.seed 設為第一個 seed，inputs.batch_size 設為 seeds 數量。
    7. 若使用 LatentBatchSeedBehavior 節點，將 seed_behavior 設為 random 或 fixed 並將 seed 列表填入該節點的輸入。
    8. 更新 workflow 完成後再提交。
    9. 示例函式
def inject_seeds_and_batch(workflow: dict, seeds: list[int]) -> dict:
    for node in workflow["nodes"]:
        if node.get("class_type") == "KSampler":
            node["inputs"]["seed"] = seeds[0]  # 批次起始 seed
            node["inputs"]["batch_size"] = len(seeds)
        elif node.get("class_type") == "LatentBatchSeedBehavior":
            node["inputs"]["seed_behavior"] = "random"
            node["inputs"]["seed_list"] = seeds
    return workflow
C 試產與 OOM 管理詳述
    1. VRAM 預估策略：
    2. 在管理端建立一張表格或模型，記錄常用模型（如 SD1.5、SDXL、Flux）在不同解析度與 ControlNet 設定下的平均 VRAM 使用量。
    3. 每次建立任務時，根據 workflow 中的輸入解析度、模型、ControlNet 數量初步預估 VRAM。若預估結果接近 GPU VRAM 上限，可限制 batch_size 或降解析度。
    4. 試產流程：
    5. 從所有 Prompt 中選擇最耗資源的組合（例如解析度最高且控制網最多），構建 workflow，batch_size 設為 1。
    6. 調用 /prompt 執行試產，若返回 OOM 錯誤（通常可從 WebSocket error 訊息或 /prompt 的 error 欄位判斷），則減半解析度或關閉部分 ControlNet，直至能夠成功生成。
    7. 試產成功後再用相同參數生成 10 組樣本，供使用者檢視並決定是否鎖定。
D 佇列調度器詳細流程
    1. 初始化內部佇列
    2. 從資料庫讀取所有 status = FROZEN 的 task，為每個 Prompt 生成 seed_list 與 workflow。
    3. 對每個 Prompt 建立一個內部任務物件，包括 prompt text、seed_list、重試次數等。
    4. 執行循環
    5. 步驟 1： 呼叫 wait_until_queue_below(limit) 確保 ComfyUI 佇列長度少於設定值（例如 2）。
    6. 步驟 2： 從內部佇列取出下一個未提交的任務，通過 inject_seeds_and_batch 將 seed、batch_size 注入工作流。
    7. 步驟 3： 調用 submit_prompt，取得 prompt_id 及位置，記錄在資料庫。
    8. 步驟 4： 啟動進度監聽（WebSocket 或輪詢）。如在執行期間收到 error 或超過超時限制，立即調用 /interrupt 中止並將任務放回佇列，減少重試次數。
    9. 步驟 5： 任務成功完成後，調用 /history/{prompt_id} 或利用 WebSocket 返回信息獲取圖片輸出路徑；將圖片移至永久儲存並更新 generated_images 表。
    10. 步驟 6： 重複上述步驟直到全部 Prompt 處理完畢。
    11. 錯誤與重試策略
    12. 為每個 Prompt 設置 retries_remaining（建議 3）。
    13. 遇到臨時錯誤（例如網路問題、OOM）時重試；遇到永久錯誤（如 Prompt 語法錯誤、模型缺失）則標記為失敗並記錄原因。
E 標註工作台詳細規範
    1. 圖片載入與緩存： 使用前端框架（例如 React 或 Vue）實作標註頁。當使用者打開某組 Prompt 時，前端利用 API 請求下載 N 張圖片；一旦使用者開始標註，提前預取下一組的圖片以減少等待時間。可使用瀏覽器的 Cache API 或 IndexedDB 快取已下載圖片。
    2. 標註操作流程：
    3. 每張圖片提供「查看大圖」、「旋轉/放大」等輔助功能，確保標註公平。
    4. 用戶選擇最佳圖後，前端回傳 chosen_index（0 到 N-1）；若選擇 rejected_index，則同時回傳；若全數垃圾則回傳 spam = true。
    5. 後端根據 index 對應種子和圖片路徑更新 task_prompts 表。
    6. 桌機與手機介面： 桌機版可使用網格模式一次顯示 N 張圖，使用滑鼠點擊；手機版採用水平輪播配合底部縮圖列，並確保放大手勢流暢。
F 資料庫模式範例
建立關聯資料庫（例如 PostgreSQL 或 MySQL），包含以下表格：
tasks
欄位
說明
task_id (UUID)
任務唯一標識
workflow_id
引用 Admin 上傳的 workflow
num_prompts
欲生成的 Prompt 組數
status
PENDING/PILOT/FROZEN/RUNNING/FAILED etc.
created_at
建立時間
frozen_at
Freeze 時間
completed_at
完成時間
task_prompts
欄位
說明
prompt_id (UUID)
Prompt 唯一標識
task_id
所屬 task
prompt_text
完整 Prompt 字串
seed_list (JSON)
對應此 Prompt 的 seed 列表
status
QUEUED/RUNNING/FAILED/COMPLETED
retries_remaining
剩餘重試次數
chosen_index
標註選中的 index（0‑based）
rejected_index
標註最差的 index（可為 null）
spam (boolean)
此組是否被判定為垃圾
generated_images
欄位
說明
image_id (UUID)
圖片唯一標識
prompt_id
對應 Prompt
seed
此圖片的種子
image_path
儲存路徑（相對於 Storage）
created_at
生成時間
G 常見問題與對策
    • workflow 格式變更： 使用一個中間層將 workflow 映射到內部結構，並在升級 ComfyUI 前於測試環境驗證舊 workflow 是否能正常解析。
    • 自訂節點與 API 節點： 在匯入 workflow 時檢查是否包含需要額外 API Key 或未支援的 custom node。對於未支援的節點，阻止上傳或要求手動審核。
    • 標註者速度差異： 可在資料庫層加入鎖定機制，允許多位標註者同時從公開任務池領取不同的 prompt，並以先回傳者為準。對於衝突標註可啟動二審流程。
H ComfyUI API 請求／回應結構詳解
為了避免 AI 代理在與 ComfyUI 服務交互時猜測回應格式，以下列出常用端點的請求範例與回應結構。這些範例來源於官方伺服器路由文件[9]。
    1. POST /prompt – 提交 workflow
    2. 請求範例：
    • {
  "prompt": {
    "nodes": {
      "1": {"class_type": "LoadCheckpoint", "inputs": {"ckpt_name": "SDXL_v1"}},
      "2": {"class_type": "KSampler", "inputs": {"seed": 123456789, "steps": 30, "cfg": 7.5, "batch_size": 2}},
      "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "1girl, red dress"}},
      "4": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "test"}}
    },
    "extra_data": {}
  },
  "client_id": "hil-agent"
}
    3. 回應欄位：
        ◦ prompt_id：伺服器分配的唯一識別碼。
        ◦ number：排入佇列的位置（0 表示立即執行）。
        ◦ error：若驗證失敗則回傳錯誤訊息，並在 node_errors 指出哪個 node 有問題。
    4. GET /queue – 查詢佇列狀態
    5. 回應範例：
    • {
  "queue_pending": ["8e54c8f6-..."],
  "queue_running": ["9455b5b2-..."]
}
    6. queue_pending 列出待執行的 prompt_id，queue_running 列出正在執行的 prompt_id[9]。
    7. GET /history/{prompt_id} – 查詢歷史結果
    8. 成功生成後，可透過此端點取得輸出資訊。
    9. 回應包含每個節點的輸出，對於 SaveImage 節點會有 filename 和 subfolder，需組合出圖片路徑，如 output/images/filename.png。
    10. POST /interrupt – 中止執行
    11. 將當前執行的 prompt 停止並返回成功訊息。建議在監測到錯誤或超時時調用。
I 錯誤訊息與對應處理建議
ComfyUI 在 workflow 驗證與執行階段可能產生錯誤。AI 代理應根據不同錯誤類型採取相應策略。
    1. 驗證錯誤： 提交 /prompt 時若 workflow 結構不合法，回應將包含 error 與 node_errors。應立即中止該任務，記錄錯誤並通知開發者修正 workflow。
    2. 資源不足（OOM）： 在執行時會透過 WebSocket execution_error 消息提醒，或在 /prompt 回應中帶 error，內容可能出現 OutOfMemory。建議將 batch_size 減半或降低解析度後重試。
    3. 模型缺失或路徑錯誤： 若 workflow 指定了不存在的模型或檔案，會在驗證階段即報錯。請確保 models/{folder} 端點回傳的模型列表中包含所需模型[10]。
    4. 佇列滿載： 若 /queue 回傳的 queue_pending 長度超過允許值，表示伺服器忙碌；調度器應稍後再提交。
    5. 自訂節點失敗： WebSocket 會發送 execution_error 並指出出錯的 node_type[11]。除非邏輯錯誤被修正，否則不應重試。
J 前端介面設計具體指南
以下列出前端開發時應遵循的具體規範，以減少界面設計時的自由度：
    1. 色彩與排版： 採用淺色系主題，主要文字顏色為深灰 (#333)，按鈕使用一致的高亮色（如藍色 #007aff）。
    2. 按鈕與點擊區域： 手機端按鈕高度至少 44 px，寬度不小於螢幕寬度的一半，確保拇指容易點擊。
    3. 圖片展示： 手機版採用水平輪播呈現 N 張圖；桌機版可用 2 × 2 或 3 × 2 網格呈現。每張縮圖下方顯示簡短標籤（例如「Seed 1」、「Seed 2」）。
    4. 標註流程提示： 在標註頁面上方顯示步驟導航（例如「步驟 1/1000」），並使用進度條表示完成度；提供「返回上一組」按鈕以利修正。
    5. 無障礙： 按鈕與標籤必須加上 aria-label，確保螢幕閱讀器可讀。色彩對比度需滿足 WCAG 2.1 AA 標準。
K 測試計畫與品質保證
    1. 單元測試： 針對 API 封裝層編寫測試，模擬 /prompt、/queue 等端點的回應與錯誤，確保調度器行為一致。
    2. 整合測試： 在測試環境部署 ComfyUI 伺服器，測試整個流程（生成、鎖定、標註、導出），並驗證每張生成圖片的 seed 與 metadata 是否與資料庫一致。
    3. 壓力測試： 使用工具（如 Locust）模擬多用戶同時提交任務與標註，檢測佇列管理與資料庫性能。目標是在 GPU 處理能力允許範圍內保持接口響應時間 < 1 秒。
    4. 回歸測試： ComfyUI 每次升級前執行舊版 workflow，確保產出與 metadata 格式仍然一致。若有重大版本更新（例如 v0.4 → v0.5），在測試環境上先驗證相容性再升級正式環境。
L 安全與 API 金鑰管理
    1. 使用者驗證： 若將 ComfyUI 部署為共享服務，建議啟用 API 金鑰以限制 API 節點存取。官方文件指出，可以透過 extra_data.api_key_comfy_org 在提交 payload 時傳遞 ComfyUI Platform 的金鑰[12]。代理應從安全儲存（如環境變量或密鑰管理服務）讀取金鑰，並加入 extra_data。
    2. 敏感資料保護： 所有包含種子和 Prompt 的資料庫表需設置適當權限，僅允許後端服務帳號讀寫。禁止前端直接存取種子列表。
    3. 網路安全： 建議在服務與 ComfyUI 之間啟用 TLS；若使用公有雲儲存圖片，需設置適當的存取權限（例如 S3 pre‑signed URL）。
M 系統架構圖
下圖展示整體人類回饋資料整備平台的架構，包含主要模組與流程。粗略分為前端層（Admin UI、User UI、Annotation Workbench）、後端層（Smart Orchestrator、資料庫／儲存）、生成引擎層（ComfyUI 伺服器）。圖中箭頭標示了工作流程：Admin 上傳 workflow、User 設定任務並啟動試產、調度器呼叫 /prompt 生成圖片並將結果存入儲存，標註工作台取得圖片進行偏好標註後匯出 DPO JSONL。

@startuml

!define RECTANGLE class

RECTANGLE "Admin UI" as Admin {
}

RECTANGLE "User UI" as User {
}

RECTANGLE "Smart Orchestrator\n(Backend)" as Orchestrator {
}

RECTANGLE "ComfyUI Server" as Comfy {
}

RECTANGLE "Storage & DB" as Storage {
}

RECTANGLE "Annotation Workbench" as Workbench {
}

' --- Relationships ---

' Admin interactions
Admin --> Orchestrator : Upload workflow
Orchestrator --> ComfyUI: ensure workflow exit in ComfyUI
Admin --> Orchestrator : Upload variable pool
Admin --> Orchestrator : Upload variable run

' User interactions
User --> Orchestrator : Trigger pilot run
Orchestrator --> User : Pilot run results

' Orchestrator triggers
User --> Workbench : Configure tasks
Orchestrator --> Workbench : Trigger mass generation

' Backend → ComfyUI
Orchestrator --> Comfy : Send /prompt (POST)

' ComfyUI → Storage
Comfy --> Storage : Generate images

@enduml

這些詳細指南將可大幅降低 AI 程式開發代理需要做出的推斷與決策量，並為每一步提供具體的 API 呼叫、資料結構與處理邏輯。

[1] [2] [3] [7] [8] [9] [10] Routes - ComfyUI
https://docs.comfy.org/development/comfyui-server/comms_routes
[4] ComfyUI : Isolating An Image From a Batch | by YushanT7 | Medium
https://medium.com/@yushantripleseven/comfyui-isolating-an-image-from-a-batch-7062f275c113
[5] [6] Changelog - ComfyUI
https://docs.comfy.org/changelog
[11] Messages - ComfyUI
https://docs.comfy.org/development/comfyui-server/comms_messages
[12] ComfyUI Account API Key Integration - ComfyUI
https://docs.comfy.org/development/comfyui-server/api-key-integration
