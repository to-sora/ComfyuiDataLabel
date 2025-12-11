# ComfyUI Headless & API Usage (zh-CN quick reference)

本文整理自 https://www.comfy.org/zh-cn/ 的公开信息与常见实践，重点说明在完全无界面(headless)环境下运行 ComfyUI、调用 API，以及理解工作流(`workflow_api.json`)结构。

## 1. 以 Headless 方式启动 ComfyUI
- 推荐在服务器上使用以下示例命令：
  ```bash
  # 监听所有网卡、固定端口，禁用自动打开浏览器，允许跨域
  python main.py --listen 0.0.0.0 --port 8188 \
    --disable-auto-launch --enable-cors-header
  ```
- 将服务放到 systemd/pm2 等守护进程中，并把输出目录挂载到对象存储或 NFS，方便大规模导出。
- 健康检查与监控：定期调用 `GET /system_stats`，同时监控磁盘占用、显存/显卡利用率和日志异常。

## 2. 工作流文件与 `workflow_api.json`
- 在 ComfyUI 网页端导出的 `workflow_api.json` 可直接用于 API 调用，**无需访问官网即可理解与编辑**。顶层字段通常包含：
  - `client_id`：调用方生成的唯一 ID，用于区分会话与 WebSocket 消息。
  - `prompt`：**节点图定义**（必填）。键为节点 ID（字符串），值为节点对象。
  - `extra`：附加运行信息（因节点而异，如输出目录、批尺寸、缓存策略）。
  - `last_node_id` / `last_link_id`：导出时的节点/连线序号，主要供 UI 追踪，可保留。
  - `save_workflow`：布尔值，指示是否让服务端把本次工作流写入磁盘（默认 `false`）。
  - `version` (可选)：工作流版本号，便于你在代码里做兼容性判断。

### 2.1 节点对象的标准结构
| 字段 | 说明 |
| --- | --- |
| `class_type` | 节点类型（如 `CheckpointLoaderSimple`、`CLIPTextEncode`、`KSampler`）。**用于在后端匹配安全/白名单。** |
| `inputs` | **输入数据/连线集合**。键为输入名称，值为静态值或指向上游输出的引用。|
| `meta` (可选) | UI 备注、标题、注释，不影响执行。 |
| `widgets_values` (可选) | 某些节点把默认表单值保存在此（布尔/字符串/数字列表）。 |

**节点输出位置与数量**：通常由节点类型决定，索引从 0 开始；示例：`CheckpointLoaderSimple` 输出 `[0:model, 1:clip, 2:vae]`，`EmptyLatentImage` 输出 `[0:latent]`。构建 DAG 时，`inputs` 中的 `["node_id", <index>]` 即引用上游的该输出。

### 2.2 `inputs` 的数据类型（静态 vs 动态）
| 形态 | 示例 | 解释 |
| --- | --- | --- |
| 常量（静态参数） | `"seed": 123456`<br>`"cfg": 7.5`<br>`"text": "a cat"` | 直接作为参数使用；适合检测“静态变量”。 |
| 上游输出引用（动态变量） | `"latent_image": ["4", 0]` | 数组形式 `[node_id, output_index]`，在运行时从节点 `4` 的第 0 个输出取得数据。工作流中凡是此形态均表示动态依赖。 |
| 复合常量 | `"lora_list": [["lorafile.safetensors", 0.7]]` | 用于批量/多路输入；仍视为静态值。 |
| 可选输入 | `"control_net": null` | 允许为空的输入，空时节点按默认行为执行。 |

静态/动态判别要点：
- **静态变量**：JSON 原生类型（字符串、数字、布尔、null、数组/对象但不含 `[id, index]` 结构）。适合在代码中直接覆盖或批量替换。
- **动态变量**：出现形如 `["<nodeId>", <outputIdx>]` 的数组即为节点依赖；用于构建 DAG 与数据流，运行时解析。
- **混合结构**：如果数组里既有常量又有 `[id, idx]`，视作“动态集合”，需要分别处理各元素。
- **输入默认值**：缺失的可选输入会使用节点默认值（不必在 JSON 中写出）；当需要覆盖默认值时写入显式常量或 `null`。

### 2.3 最小可运行示例（文本生成图片）
```json
{
  "client_id": "demo-123",
  "prompt": {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned.ckpt"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat on the beach", "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": 123456, "steps": 30, "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["5", 0]}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["1", 2]}},
    "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "demo"}}
  },
  "extra": {"save_workflow": true}
}
```

- 节点 `1` 输出模型、CLIP、VAE；`2/3` 编码正/负文案；`4` KSampler 使用静态参数（seed、steps、cfg）与动态输入（模型、latent、prompt 文本）；`7` 保存图片。
- 通过扫描 `inputs` 中的 `["node", index]` 数组即可检测所有动态依赖，其余为可替换的静态参数。
- 若要批量生成，可在提交前替换 `seed/text/width/height` 等静态字段，或增大 `EmptyLatentImage.batch_size`。

### 2.4 全量字段速查（无 UI 也能理解）
- **图形标识**：节点 ID 必须是字符串数字（与 UI 导出一致），建议保持连续以便人工阅读，但执行时不要求排序。
- **分支/多输出**：当一个节点有多个输出供不同下游使用时，在 `inputs` 中分别引用对应的索引即可，不需要额外的链接数组。
- **子目录与命名**：保存类节点通常接受 `filename_prefix`、`subfolder` 等静态字符串；确保你的后端在落盘后将这些字段连同 `prompt_id` 写入数据库，方便导出 20 万张图片时快速索引。
- **推理缓存**：部分节点支持 `cache` 相关参数（如 `CLIPTextEncode.cache`），属于静态字段，可用于加速重复调用。
- **安全白名单**：开发时可维护 `AllowedNodes = Set<class_type>`，对 `prompt` 中每个节点校验；遇到未覆盖类型可打回给上传者，避免执行危险节点。

## 3. 核心 API（HTTP + WebSocket）
| 接口 | 方法 | 作用 | 响应/负载关键字段 | 官方文档 |
| --- | --- | --- | --- | --- |
| `/prompt` | `POST` | 提交工作流。请求体为 `workflow_api.json`（可附加 `client_id`）。返回 `prompt_id`。| 请求体：`client_id`、`prompt`。响应：`{ prompt_id }`。| https://docs.comfy.org/zh-Hans/automation/api#post-prompt |
| `/history/{prompt_id}` | `GET` | 轮询任务状态与输出（图像文件名、子目录）。 | 响应：`{ prompt: {}, outputs: {"<nodeId>": { images: [{filename, subfolder, type}]}}}`。| https://docs.comfy.org/zh-Hans/automation/api#get-historyprompt_id |
| `/queue` | `GET` | 查看待处理/运行中的队列长度。 | 响应包含 `queue_pending`、`queue_running` 列表，用于呈现等待与执行中的任务。| https://docs.comfy.org/zh-Hans/automation/api#get-queue |
| `/interrupt` | `POST` | 中断当前运行。 | 响应：`{ success: true }`。| https://docs.comfy.org/zh-Hans/automation/api#post-interrupt |
| `/free` | `POST` | 释放显存。 | 响应：`{ success: true }`。| https://docs.comfy.org/zh-Hans/automation/api#post-free |
| `/system_stats` | `GET` | GPU/CPU/内存统计，适合健康检查。 | 响应：`{ system: { ram_total, ram_used, vram_total, vram_used, cuda_devices[] } }`。| https://docs.comfy.org/zh-Hans/automation/api#get-system_stats |
| `/ws` | `WebSocket` | 接收进度、完成/失败事件。`client_id` 区分调用方。 | 消息类型：`progress`, `executed`, `execution_error`, `status`，字段含 `node`, `prompt_id`, `message`。| https://docs.comfy.org/zh-Hans/automation/api#websocket-api |
| `/view?filename=&subfolder=&type=output` | `GET` | 下载已生成的图片或资源。 | 查询参数：`filename`、`subfolder`、`type` (`output`/`temp`). | https://docs.comfy.org/zh-Hans/automation/api#get-view |
| `/upload/image` | `POST` | 上传自定义图像供后续节点使用。 | 表单字段：`image` (file)，返回 `{ name, subfolder }`。| https://docs.comfy.org/zh-Hans/automation/api#post-uploadimage |

## 4. 典型调用流程（完全无界面）
1) **提交任务**
   ```bash
   curl -X POST http://<host>:8188/prompt \
     -H "Content-Type: application/json" \
     -d @workflow_api.json
   # 响应示例: {"prompt_id": "<uuid>"}
   ```
2) **监听进度（可选）**：连接 `ws://<host>:8188/ws`，在消息中按 `prompt_id`/`client_id` 过滤事件。
3) **轮询结果**
   ```bash
   curl http://<host>:8188/history/<prompt_id>
   # outputs[x].images[] 包含 filename、subfolder、type
   ```
4) **下载图片**：使用返回的文件信息拼接：
  ```
  http://<host>:8188/view?filename=<file>&subfolder=<sub>&type=output
  ```
5) **中断或释放**：需要时调用 `/interrupt` 或 `/free`。
6) **上传前置资源（可选）**：如需参考图或 ControlNet 纹理，先 `POST /upload/image` 上传，再在工作流 `inputs` 中引用返回的 `subfolder/name`。

## 5. 大规模/批量使用建议
- **批量导出**：使用队列系统批量提交 `/prompt`，控制并发，避免单卡拥堵。根据 `/queue` 和 `/system_stats` 做节流。
- **结果管理**：将 `/history` 返回的文件名与业务 ID 一并写入数据库；使用对象存储生命周期策略，避免 20 万张以上图片占满磁盘。
- **DPO/数据集导出**：按 `prompt_id` 收集 chosen/rejected 对，组织为 JSONL（如 `{prompt, chosen:{uri,seed}, rejected:[...]}`），可直接供下游训练管道使用；导出脚本可直接依赖 `/history` 与 `/view`，无需访问官网。
- **安全与隔离**：开启 CORS 限制白名单、在反向代理层加鉴权；不要暴露 `/prompt` 给公网。

## 6. 常见排障
- 返回 400：检查 `prompt` 中的节点 ID/连接是否匹配，必填输入是否缺失。
- 任务不出图：查看 `/system_stats` 是否 OOM，必要时降低分辨率/步数或调用 `/free` 后重试。
- WebSocket 无事件：确认 `client_id` 一致，或直接改用 `/history` 轮询。

## 7. 官方参考链接
- API 端点与请求示例（含 `/prompt`、`/history`、`/ws`）：https://docs.comfy.org/zh-Hans/automation/api
- Comfy 官方站点（zh-CN）：https://www.comfy.org/zh-cn/
