# Agent-first world orchestration

PolyKit 的 World 不是另一个云端生成器。Agent 是世界导演，负责把用户的
开放式描述拆成可执行的世界计划；FastAPI 负责本地工作流、任务状态和工作区
资产；Three.js 负责把计划和已生成资产呈现出来。

这套分层参考 [WorldClaw: Agentic 3D Open-World Generation at Scale](https://arxiv.org/abs/2608.05248)：
论文将生成过程组织为意图分析、场景规划、全局地形、区域资产与空间放置，最后
通过渲染反馈进行细化。论文里的模型和 Blender/MCP 调用在 PolyKit 中替换为
本地 Agent + FastAPI 工作流，不把请求发往 fal 或其他托管 API。

## 阶段契约

世界文档可以在 `agent_plan` 中保存阶段状态。MCP 工具会维护下面这组固定阶段，
但不会替 Agent 做决定，也不会在浏览器里启动模型：

| 阶段 | Agent 的职责 | PolyKit 的职责 |
| --- | --- | --- |
| `intent` | 提取主题、尺度、风格、硬约束和不可违背的关系 | 持久化提示词与约束 |
| `plan` | 定义区域、地貌、材质、资产原型和空间关系 | 保存可编辑 `WorldDocument` |
| `terrain` | 选择地形工作流及语义布局 | 通过 `/workflow-runs/*` 执行本地节点 |
| `placement` | 决定锚点、朝向、尺度和区域归属 | 保存实例/变换，供 Three.js 读取 |
| `assets` | 为 hero/支撑资产选择概念图和工作流 | 运行本地图像到 3D，并返回 `run_id` |
| `materials` | 选择表面材质、天空和局部外观策略 | 运行对应本地工作流（如果已安装） |
| `refine` | 查看渲染结果，修正接触、遮挡、比例和语义错误 | 保存反馈与最终工作区引用 |

`materials` 是显式阶段，因为论文把材质/外观作为独立的细化关注点；在当前
本地节点包尚未提供材质节点时，Agent 可以将它标记为 `blocked`，而不是偷偷
回退到云端服务。

## 本地透明概念图（当前盘点）

当前机器上已经有 SDXL 基础模型和 RealVisXL 权重，位于
`/home/sy/llm/comfyui-wan/data/models/checkpoints/`；现有
`sdxl-first-frame-img2img.json` 可以证明 ComfyUI 的 SDXL 图像链路可用，
但它的输出是 RGB PNG，不是带 alpha 的概念图。PolyKit 的内置 node pack 目前
也没有 `text-to-image` 或 `text-to-image-transparent` 节点。

重新按社区主流方案盘点后，结论是：不要把旧的 SDXL LayerDiffuse 当主链路。
LayerDiffuse 的 ComfyUI 实现仍然主要覆盖 SDXL/SD1.5；而现在更稳妥的本地
资产路线是使用较新的高质量 T2I，再接专门的 alpha matte：

1. **主方案**：`FLUX.2 Klein 4B → BiRefNet-HR/Lucida → RGBA PNG → Trellis2`
   。[FLUX.2 Klein 4B 的 ComfyUI 工作流](https://docs.comfy.org/tutorials/flux/flux-2-klein)
   约需 13 GB 显存，适合我们的 RTX 4090 D；[BiRefNet](https://github.com/ZhengPeng7/BiRefNet)
   是 MIT 许可的高分辨率抠图模型，[Lucida](https://github.com/egeorcun/lucida)
   是针对玻璃、发丝、发光效果和插画边缘的 BiRefNet 微调版本。默认先用
   BiRefNet-HR，遇到透明材质/特效再由 Agent 选择 Lucida。
2. **直接 RGBA 的实验方案**：`Qwen-Image-Edit-2509 + OmniAlpha → RGBA PNG`
   。[OmniAlpha](https://github.com/Longin-Yu/OmniAlpha) 原生处理 RGBA，但仓库
   目前采用者很少，依赖 20B 级 Qwen 基座和额外 AlphaVAE/LoRA，暂不适合当
   PolyKit 默认工作流。
3. **不选作主方案**：[Qwen-Image-Layered](https://github.com/QwenLM/Qwen-Image-Layered)、
   [LayerDiffuse](https://github.com/huchenlei/ComfyUI-layerdiffuse)、FluxLayerDiffuse。
   Qwen 官方明确说明 text-to-multi-RGBA 性能有限，LayerDiffuse 主要覆盖
   SDXL/SD1.5，FluxLayerDiffuse 采用者很少；可以保留做对照测试，不作为资产
   生产默认值。

目前 `/home/sy/llm/comfyui-wan` 还是旧版 ComfyUI，且没有安装 FLUX.2、BiRefNet
或 Lucida 权重；系统中仍没有一条已注册、可被 Agent 直接提交的透明文生图工作流。
建议新增独立的本地图像 ComfyUI 服务，不要直接升级正在承载 Wan 视频工作流的
容器；这样可以单独跟进新版本节点和模型，不影响现有视频链路。

主链和实验链都应作为 Agent 的一个资产阶段：Agent 只提交 prompt、风格、seed 和
`alpha_policy`，FastAPI 通过 `/workflow-runs/*` 执行并持久化 `image-rgba`
工作区产物，完成后再把同一份相对路径交给 `trellis2/generate`。浏览器端的
Three.js 只负责预览和编排结果，不负责启动模型或抠图。

## MCP 工具

项目根目录的 `.mcp.json` 已声明本地 `polykit` MCP server。内置 Agent sidecar
会通过 `POLYKIT_MCP_CONFIG` 自动发现这份声明，即使实际 workspace 是
`~/.polykit/workspace` 也不需要复制配置文件；配置默认使用
`uv run python api/mcp_server.py` 启动本地服务。启用 Agent 的 MCP adapter 后，
内置或外部 Agent 都可以使用：

- `polykit_world_get` / `polykit_world_save`：读取和保存世界计划。
- `polykit_world_update_stage`：记录上述阶段的 `pending/running/done/blocked`。
- `polykit_world_list_workflows`：查看可用的本地可编辑工作流。
- `polykit_world_generate_asset`：为某个原型提交本地 image-to-3D 任务。
- `polykit_get_generation_status`：轮询服务端任务，直到拿到 `scene_candidate.workspace_path`。
- `polykit_world_attach_asset`：把完成的 workspace 相对路径写回原型，不复制二进制文件。

一个最小的 Agent 编排顺序是：

1. `polykit_world_get`；没有文档时先用 `polykit_world_save` 写入 `intent` 和 `plan`。
2. 将 `intent`、`plan` 标记为 `done`，把 `terrain` / `placement` / `assets` 标记为 `running`。
3. 用 `polykit_world_list_workflows` 选择已经存在的本地工作流；需要概念图资产时调用
   `polykit_world_generate_asset`。
4. 轮询任务，完成后将输出的 `scene_candidate.workspace_path` 传给
   `polykit_world_attach_asset`，再更新 `assets` 和 `refine`。

世界文档中的所有 `workspace_path` 都必须是工作区相对路径，例如
`Workflows/Worlds/observatory.glb`。绝对路径只允许作为本地生成工具的输入，不能
进入可持久化的世界清单。

## 当前 Three.js 边界

`src/areas/worlds/runtime/` 仍然提供无模型、无网络的确定性预览：它根据 seed 生成
高度场和程序化散布。它不是 Agent 的规划器，也不是第二个任务运行时。Agent 计划
和服务器资产可用时，Three.js 读取同一个世界文档；没有资产时，程序化原型只作为
预览和兜底。这样可以先验证论文里的编排契约，再逐步增加本地 terrain/material/VLM
节点包。
