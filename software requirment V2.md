Available Hardware:
HDD * N (Unequal size), SDD *1 , Postgresql Database 

This project  should contain 3 system.
S01 File manage system
Handle multiple disk , io of file
Handle optimization
Provide endpoint on batch Read write and single read write. ( high level endpoint for task of other system.
Ensure Copy_of_raw and rebuild when disk failure etc
S02 Schelder system 
A GUI system to start scheduler process of S03 S04 S07
Do not contain business logic but just a scheldre system
S03 Formatting and adapter system
Handle different complex structure of different source
Output an interface  to tell S05 to update without access to SQL database
S04 Data cleaning and system.  ( core logic and gpu worker)
Note S04 is run in other VM and have contain code that current running , ready for migration
Each action contain specific input and output dir structure and not able to change
S05 Database system
Handle to interface to do io in SQL server
Handle backup of SQL server
Handle health and verify status of SQL server
S06 Visualize system
A simple Visualize of S05 
S07 Dataset_generate system
Invoke by S02
Handle by core business logic
Use S01 and S05 to write in TYPE 3D
S08 testing system 
Test in a other DB and also ensure different unit test of different script 



DATA storage
TYPE 1 : cold storage (enough for raw data) ( not involve in system consider amazon galacer block storage for storing)
TYPE 2 : MAIN HDD storage ( a few second hand HDD with different size) (handle by S01) 
TYPE 3 : CO working area SSD (1TB)( 3a 3b 3c 3d is 3 dir in same ssd with quota)
	TYPE 3A : Input of Web scrapped data  (Handle by user)
	TYPE 3B : Output of Web scrapped data (Handle by user)
	TYPE 3C : other VM , node data handling (Direct use by S04 as io space)
	TYPE 3D : output dataset (output by S07)

Functional Requirement.
F_01 
store target 2TB -3TB raw data , 3-4 TB generated data. (store in type 2)
F_02 
Handle complex web scraping data from 10-20 sources with meta structure. (handle by S_03)
F_03
Handle web scrapping that input in batch and which may repeat in content but have different meta data. Also need to identify parent and DAG relationships across different batches without accessing data of other batches 
F_04.
Handle human-in-loop label data , preprocess action and gpu labeled data and store in an easy management way.
Action Contain few type  label as AC[0-5]
AC1 :  input a batch of tabular data in sql and output tag by complicated filtering(save in SQL) , no copy of data required ( work in SQL space only)
AC2 :  deterministic , no hyper parameter , need copy data (may include parent and child) to a working dir (type 3C)(worker by docker k8s or a *.py *.sh script). compute non intensive (CPU only)  
AC3 :  deterministic , no hyper parameter , need copy data (may include parent and child) to a working dir (type 3C)(worker by docker k8s or a *.py *.sh script). compute  intensive ( wait gpu node awake)
AC4:   deterministic , contain parameter , need copy data (may include parent and child) to a working dir (type 3C)(worker by docker k8s or a *.py *.sh script). compute  intensive ( wait gpu node awake), will generate child data ( crop resize canny)(with a maxim of scale in constant A4_scale)
AC5:   Undeterministic Human label, notation (temperate store in type 3C)

Handle different media and data structure tree relationship such it can sort and handle by complicated query which across parent and their meta data as well as label

F_05	
Able to schedule different workers and work by batch.
F_06
Able generates a dataset  n with specific structure in dir by doing complicated query over different tree
F_07	
Use load balance to handle different HDDs to have best performance across different bandwidth. 
F_08	
Able to rebuild data in a new disk when disk failure. 
F_09	
Optimize batch IO by multiple copy and brandwidth of disk.(handle by S01)


system constant :
shard_size  : in MB
batch_size  : in GB
Copy_of_raw : 1-No of disk
A4_scale : 10 time



User Action flow
UA_1 ( webscrap data write in)
put web-scrapped data in SSD type 3a
invoke S02 to start corresponding script ( base on webscrapped source and business logic) in S03.
ensure process handle by S03
calling S01 and S05 and do IO
copy data to type 3B
user copy the data from type 3B to type 1     

UA_2 (start worker Process)

invoke S02 to start corresponding script ( base on webscrapped source and business logic) in S03, S02 also handle the workflow
S03 handle the logic and submit request in IO by make use of S05 and S01 
Ensure data copy to type 3c in specific format
Invoke S04 to start process and save data
Invoke S03 to do reformatting such S05 can write GPU / human label result 
Invoke S01 again if it is action_04

UA_3

Invoke S02 by UI
Start corresponding S07 dataset logic
Request S05 to get label and IO in type 3D
Request S01 to do batch IO to type 3D

Note
I)    user should only input data to type3a and give a signal to system on format. we should assume user no IO after sending this signal
II)   there are a few user systems. and the cleaning process is human in loop
III)  SSD storage may be full, it should be managed by the system and show warning and limit export when  there is not enough space. It should maintain by more than 1 system

System principle
1) text, metadata, human label, GPU label should store in a external data base system ( system should daily back up by export the data)
2) use suitable method to manage the all small file by S01
3) 2 type of deletion : type A, delete in database level. type b delete a batch at once ( more than 0 shard level)
4) TYPE2 , there will be HDD but not run in raid. system should do load balance on each mount HDD endpoint. The load balance is also done during export dataset to type 3D and type3C for other vm to work. The HDD load balance need to save multiple copy of raw data to prevent disk failure.
5) batch is above of file system level, batch as tag  type of data is group by something

load balance policy
1) if any disk fail, warn user
2) ensure load balance of different HDD in batch IO , minimize seek time of each HDD and allow reorder of IO to make batch IO and copy to max average efficiency
DATA copy policy
0) type 3a3b3c3d is 4 dir in the same ssd partition, each expected a quota for each dir.


—
Database_schema ver 3

Tabel_00 
Node_id | sha256 | is_vitral | is_physical

Tabel_01A Physical content
Record_id | node_id | file_size  | create_time | in_black_list | is_delete | file_extension_id 

Tabel_01B Physical content
Record_id | filepath 
( consider a string for rebuild an locate resource if all disk failure>S01 recoverability non daily use , use as backup such allow non normalization)

tabel : 03 : filetype_mapping
file_extension_id | file_extension | type 
1  , jpg , image
2  , png , image

# note this tabel only for video image sound and txt , not inculde exe binary 3d object zip tar all other format of data

tabel 05 : source_tabel_mapping 

source_ID | description | meta_tabel_NAME (allowed value is tabel name of tabel 07 series)
1,twitter scrape by api | AOTHER TABEL NAME
2,facebook scrape by api | AOTHER TABEL NAME

—
tabel 6 vitral_asset_description
record_id | vitral_asset_description | Node_id 
1|vitral_asset:description|01
Sha256 of table 0 of virtual asset is come from this description ( this description is in special format




# source_id map to table 4
# Note batch id is not equal to action batch id
# is_root mean is is the top level of tree ( follow in version 1)
# is black_list is use to filter large scale of dirty data from sql filter state in some other tabel
---
tabel 06 : common_meta_data_node
metadata_id |node_id | source_ID | is_raw | is_root | title | view | update_time |  download_time | auther_description | user | raw_metadata (max1kb) 

# is raw refer to is it the first media or vitral assets input in system by UA_1
# is_root mean it is  physical media that can consider as direct sample point ( it may consider as a tag that label by DAG but not only determine by DAG, it is abstract meaning in user space)
-aka image cropped is no root , but key  frame extract in video is root


#vitral asset also have common_meta_data 

---
tabel 07 : 
# this is a series of tabel define base on differnt source
Specidic_metadata_tabel07[00-99]_id | node_id   | a list of key meta data sort out by raw metadata by deterministic rule 

Tabel 8 
relationship _id | description | action_id (accept 0, 0 mean from UA_1)
# note from UA_1 it can also have more than one relationship as different web scrapped source 
Tabel 9
Edge_id | parent_node_id | child_node_id |relationship_id


tabel 09 Action tabel
Action_id | action name | action name | action type
---
tabel 10_a Action_batch_ID
Action_batch_ID  | Create_date | seal_date
Tabel 10_b Node_enter_batch_record
Node_enter_batch_record_id | Action_batch_ID | Node_ID | created_date

# use to ensure action batch is seal ( by verify create date)
---
tabel 11 action history
Action_event_id | Action_id | Action_batch_ID | start time | end time | status
—
Tabel 12_a pipeline_definition
# ensure all Node_id in this pipeline_batch is done the follow process
Pipline_id | pipline_name
Tabel 12_b pipeline_stage
# ensure all Node_id in this pipeline_batch is done the follow process
Pipline_id | stage_id | action_id

(Pipline_id | stage_id ) combine_key

Tabel 12_c
Action_batch_ID | Pipline_id | current stage


# note Action_batch can in multiple pipeline process and different pipeline share some common action. This tabel should generate from tabel 10 11 12 



Tabel 13 action_result

for all metadata and output, each action should have a define schema in tabel 13 series
Tabel 13.1 e.g. ocr_detect
Record_A[[01-99]_ID|Node_ID | position | character
1|2|(0,0),(15,16)|HelloWorld
2|2|(15,16),(60,16)|HelloWorld


E.g. canny generate 
Record_132_ID | status
1| success

Other info , FAQ chat for  reference

這個dag（table 6)關係 是用來篩選哪些數據要放入 batch 然之後密封數據庫(f03即係次次代表爬蟲數據第一次進入系統的批次 和之後的行動批次沒有關係） 現在這些batch完成之後才會放入dataset

實體數據的話就是sha256 image 虛擬資產就是要碰撞 確保 兩個批次的同一個作者可以透過這個地方識別到然後掛在dag 01B是自動備份失敗才會做 是和type 1有關 寫入到s01 就已經使用一個key value media storage 所以01B沒有實際意思
「補充設計文件（v0.9 草案）」：

0.1 目標
建立一套可擴展的資料資產平台，支援：
多來源 web scraping（10–20 sources）→ 統一資產樹（Node/Edge/DAG）→ 可查詢、可追溯
多類 action（AC1–AC5）含 GPU 與 human-in-loop
批量排程與批量 I/O（尤其 TYPE2→TYPE3C/TYPE3D）
多 HDD（大小不一、非 RAID）下的吞吐最佳化、載入平衡、副本（Copy_of_raw 2–4）、壞碟後可停機重建
0.2 非目標（先明確排除，避免工程擴散）
不追求無間斷服務（容許 k 硬碟死亡 → 停機維護）
不要求跨資料中心/跨機房一致性
不要求 user 在 UA_1 signal 後繼續手動操作 I/O（已列為原則）
不要求 S02 承載 business logic（S02 只 trigger + monitor）

1. 系統總覽（S01–S08）
1.1 系統分工（凍結版）
S01 Storage（核心 I/O）
管理 TYPE2（多 HDD）與 TYPE3（SSD 3A/3B/3C/3D quota）
提供穩定 API：BatchWrite、StageTo3C、ExportTo3D、Head/Verify/Health/Rebuild
實作 load balance、reorder、Copy_of_raw（2–4，後台補齊）、壞碟告警、重建流程
S02 Scheduler（Dagster UI + Orchestration）
只負責：觸發、參數傳遞、狀態追蹤、重試、排隊、告警
不寫 business logic、不直連 storage、不直連 DB
S03 Adapter/Formatter（契約層 + 意圖生成器）
對 source：解析 raw_metadata → 生成 SQL write-intent + storage manifest
對 action：讀 ActionContract（每個 S04 流程）→ 生成 stage layout（3C）+ parse output → SQL write-intent
僅透過 S01/S05 API
S04 Cleaning/Worker（GPU/CPU、人手流程執行）
在另一 VM/節點，唔長期在線
只遵守固定 input/output 目錄契約（不可改）
S05 Database（PostgreSQL + API）
提供 DB I/O API（apply write-intent、query node set、pipeline state）
備份/健康檢查/還原驗證
S06 Visualize（簡單可視化）
讀 S05（和少量 S01 health）展示資產、batch、pipeline、告警
S07 Dataset_generate（核心 business logic）
由 S02 觸發
經 S05 查詢 node 集合 + label → 經 S01 ExportTo3D 落地 dataset
S08 Testing（contract/integration/unit）
另一 DB（測試用）
固定測試資料生成、S01 contract test、S03/S05 交接測試、S04 handoff 測試

2. 儲存分層與目錄規範（TYPE1/2/3）
2.1 TYPE1 冷存（不納入系統核心）
由 user 把 TYPE3B 輸出拷到 TYPE1（或後續改成自動化亦可）
系統只需產出「可搬運的封裝」與「manifest（審計用）」
2.2 TYPE2 主 HDD（多盤、非 RAID）
系統主存儲：raw + generated 的長期存放
需求：2–4 份 Copy_of_raw、副本後台補齊、壞碟可停機重建
2.3 TYPE3 SSD（單盤分四區：3A/3B/3C/3D）
3A：user 放入 web-scraped input（UA_1）
3B：S03/S01 處理後輸出（供 user 搬到 TYPE1）
3C：worker working area（S04 讀寫）
3D：dataset export（S07 輸出）
2.3.1 SSD 軟 quota（必須由 S01 統一管理）
採用「Quota Reservation + Seal」模型：
job 開始前計算 expected_bytes → 預留 quota
copy 寫入 tmp → 校驗 → rename 成正式 → 寫 seal
成功後扣實際使用；失敗則釋放預留並清理 tmp
告警閾值（建議預設）：
warning：每區使用率 ≥ 80%
hard deny（拒絕新 job）：≥ 90%
emergency：≥ 95%（僅允許完成中的 job，禁止新 stage/export）
2.3.2 3C/3D 目錄命名（凍結，便於測試）
3C：/ssd/3c/<action_batch_id>/<layout_id>/...
3D：/ssd/3d/<dataset_export_id>/<dataset_layout_id>/...
seal 檔案：_SEALED（純空檔即可）
job meta：_JOB.json（記錄 job_id、開始/完結、expected/actual、版本號、manifest hash）

3. 系統常數與建議預設值
你已列出：
Copy_of_raw = 2–4
A4_scale = 10
以下係我建議的可落地預設（可日後調參，但建議先凍結一套做 S08 測試基準）：
3.1 shard_size（MB）
目標：把大量小檔聚合成較大單位，降低 metadata/seek 成本，同時控制重建/搬運粒度。
建議預設：256 MB
可調範圍：128–1024 MB
原則：
小檔極多 → shard_size 大啲通常更好
但 shard 太大 → rebuild / 校驗時間長、以及 3C/3D export 可能形成大顆粒 IO 波動
3.2 batch_size（GB）
用於 StageTo3C / ExportTo3D 的 job 切分（避免一次性搬走過大、亦方便重試）。
建議預設：50 GB
可調範圍：20–200 GB
原則：
SSD 1TB，3C/3D 會共用：batch_size 唔應過大，避免碰到 quota deny
若某 action 產物大（AC4 crop/canny 等）可為該 action 單獨調大/調細
3.3 replication write-ack 門檻（重要）
建議固定：至少 2 份 replica 成功並校驗 sha256 → 才算 Put/BatchWrite 成功
其餘（到 3 或 4）由後台補齊，狀態暴露 replication_lag

4. S01 Storage：接口、語義、狀態機（凍結層）
S01 的核心價值係「對上層提供穩定 contract」，讓你可以之後改 backend 而唔影響 S03/S07/S04。
4.1 主要 API（第一期必備）
4.1.1 BatchWrite（3A/3B → TYPE2）
入參：manifest（檔案清單 + metadata minimal）
行為：
內容尋址（sha256 → node_id）
寫入 TYPE2，達到 write-ack（2 replicas）即返回成功
回傳：node_id 對應結果（已有/新寫）、replica_count_current/target、lag
4.1.2 StageTo3C（TYPE2 → 3C）
入參：action_batch_id + layout_id + manifest_id|node_id_list
保障：
quota reservation
tmp→seal 原子完成
允許 reorder（按 shard/volume/disk 分組）
回傳：
target_root
stage_job_id
expected_bytes/files、quota_reservation_id
4.1.3 ExportTo3D（TYPE2 → 3D）
同 StageTo3C，但加上：
dataset_layout_id
dedup_mode（by_sha256 / by_node）
4.1.4 Health / Capacity / Warnings
GetCapacity()：分區、分 disk 用量
GetHealth()：disk 狀態、replication lag、最近錯誤、rebuild 建議
4.1.5 Verify（抽樣校驗）
Verify(scope=batch|sample_rate)：重新算 hash/size 對照 DB 記錄
4.2 Copy_of_raw 策略（2–4）
4.2.1 放置策略（Placement）
基本原則：同一 node 的 replicas 必須落在不同 HDD（避免單盤失效全損）
目標：近似均衡各 HDD 的 used_bytes、同時避免單 batch 的熱點集中
4.2.2 後台補齊（Async replication）
觸發：
新寫入未達 target replicas
某 HDD 下線後造成 replica_count 降低
調度：
低優先級、限速（避免干擾前台 StageTo3C/ExportTo3D）
4.3 壞碟與重建（可停機維護模型）
偵測：
I/O error / mount 消失 / SMART（可選）
行為：
立刻告警（S06/S02）
禁止新寫入到該 disk
若 replica_count 仍 ≥ 2：系統可繼續提供讀（視你是否接受 degraded mode）
進入維護窗口：更換新 disk → 觸發 Rebuild
Rebuild 單位：
以 shard/volume 粒度重建（比 per-file 更可控）

5. S03：契約層（ActionContract）與「SQL 寫入意圖」模型
你指出的關鍵：S04 流程多、每個格式不同。解法係把「格式差異」收斂成可配置 contract，而唔係寫死在程式邏輯入面。
5.1 核心概念
ActionContract（每個 S04 流程一份）
描述：
input selector（從 DB 揀 node）
stage layout（如何落 3C 目錄結構）
run spec（點觸發 S04）
output parser（點解析輸出、生成新 node/edge/result）
SQL write-intent schema（要寫入哪些表、如何 idempotent）
WriteIntent（S03→S05）
係一個“DB 寫入計劃”，由 S05 負責：
驗證欄位/關聯完整性
transaction
idempotency（同一 action_event 重入不重複落庫）
5.2 ActionContract 的最小字段（建議凍結）
action_id, action_type (AC1–AC5), version
input_selector（只描述條件，不寫 SQL）
stage_layout（layout_id、相對路徑規則、sidecar 規則、seal 規則）
run_spec（script/container 名稱、參數映射 input_root/output_root/action_batch_id）
output_parser（掃描規則、如何計 sha256、如何生成 child node 與 edge）
result_mapping（對應 table 13.x）
5.3 AC 類型處理規範（標準化）
AC1（純 SQL 空間）
不經 S01，不 copy data
S03：提交 selector + SQL write-intent（例如標記/過濾結果）
AC2（CPU、deterministic、需要 copy 到 3C）
StageTo3C → run S04 → parse → write-intent
AC3（GPU、deterministic、需等 GPU node）
同 AC2，但 S02 排程允許「人手啟動 GPU」或「自動喚起」兩種模式
AC4（GPU、帶參數、會生成 child data，A4_scale=10）
output_parser 必須能識別 child 產物並寫入：
table_00/01A（新 node）
edge（parent-child）
table 13.x（action_result）
AC5（human label，臨時存 3C）
產物既要可落 DB（label），亦要可追溯（annotation 文件可作附件資產或 virtual asset）

6. S05（PostgreSQL）API 與備份/健康
6.1 對外 API（最小集合）
ApplyWriteIntent(intent)：原子、idempotent
QueryNodes(selector)：供 S03/S07 取輸入集合（含 file_size 聚合供 S01 quota reservation）
GetPipelineState(action_batch_id|pipeline_id)
RecordActionHistory(action_event)（可併入 write-intent）
6.2 備份與驗證（每日）
備份工具：pgBackRest（你已同意方向）
每日流程（建議）：
full/增量備份（視你資料量）
自動驗證：restore 到測試 DB（S08 的測試環境）跑一次最小查詢/一致性檢查
生成報告（S06 顯示最近一次 backup 狀態）

7. S02（Dagster）排程模型（不含 business logic）
7.1 Dagster 只負責三條工作流
UA_1 Ingest：觸發 S03 source adapter → S01 BatchWrite → S05 ApplyWriteIntent → S01 輸出到 3B（如需要）
UA_2 Worker：S03 準備 selector → S01 StageTo3C → 觸發 S04 → S03 parse → S05 write →（如 AC4）S01 追加寫入
UA_3 Dataset：觸發 S07 query → S05 回 node set → S01 ExportTo3D → seal → 完成
7.2 GPU node 非長在線策略
兩個模式（contract 一樣，只係 run spec 不同）：
Manual gate：Dagster job 停在 “WAIT_GPU_READY” step，人手開 VM 後點繼續
Auto gate（可後續加）：Dagster step call 一個“GPU lifecycle service”喚起/關閉

8. S06 可視化（最小可用）
Dashboard 分三塊：
Storage：TYPE3 quota、TYPE2 disk health、replication lag、rebuild queue
Pipeline：action_batch 列表、stage、最近 runs、失敗原因
Dataset export：dataset_export_id、目錄、manifest、完成 seal、大小

9. S08 測試策略（你要求的固定資料 + contract）
9.1 固定測試資料集（必備）
生成一套 deterministic fixture：
高比例小檔（對應真實 95%）
多 source metadata（table_07xx）
有重複內容但 metadata 不同（同 sha256、不同 common_meta_data_node）
有 parent/child edge（模擬 crop/frames）
9.2 必做測試
S01 Contract Test（最優先）
BatchWrite → StageTo3C → ExportTo3D 的 seal、hash、size、一致性
quota reservation 行為（不足時應該在開始前拒絕）
重入（同一 job 重跑不重複拷貝、不產生半成品）
S03↔S05 交接測試
WriteIntent 的 idempotency
schema 校驗（錯字段應拒絕）
S04 Handoff 測試
以 mock output fixture 代替真 S04，驗證 output_parser + write-intent 是否正確落庫
人手只需保證 S04 真實邏輯正確；系統要保證交接正確


