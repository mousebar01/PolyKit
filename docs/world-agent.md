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

## 风格化本地概念图与透明链路（当前盘点）

我们的目标不是照片级写实，而是可以连续生成、方便 Trellis2 重建的风格化
游戏资产：低多边形、等距/三分之四视角、单物体、轮廓干净、材质简化。这里的
“效果好”优先看轮廓、构图、风格一致性和重建稳定性，不看皮肤/镜头/光照等
摄影指标。

当前机器上已经有 SDXL 基础模型和 RealVisXL 权重，位于
`/home/sy/llm/comfyui-wan/data/models/checkpoints/`；现有
`sdxl-first-frame-img2img.json` 可以证明 ComfyUI 的 SDXL 图像链路可用，
但它的输出是 RGB PNG，不是带 alpha 的概念图。PolyKit 的内置 node pack 目前
也没有 `text-to-image` 或 `text-to-image-transparent` 节点。

重新按社区主流方案盘点后，结论是：透明不是生成质量的核心，应该和风格生成
分开。先用合适的风格模型生成干净的单物体图，再用专门的 alpha matte 得到
RGBA，最后交给 Trellis2。这样不会为了“原生透明”牺牲轮廓和画面质量。

按风格选择工作流：

1. **默认：低多边形/等距游戏资产**：
   `SDXL 风格化 checkpoint + style LoRA → BiRefNet-HR → RGBA PNG → Trellis2`。
   SDXL 不是因为写实质量最高，而是因为风格化 checkpoint/LoRA 的生态最成熟，
   更容易锁定 `low-poly`、`flat-shaded`、`isometric`、`hand-painted` 这类
   视觉规则并保持一组资产一致。当前本机的 RealVisXL 是写实模型，不能直接当
   默认；需要另装一个风格化 SDXL checkpoint 或 LoRA。
   第一批只做 A/B 的社区候选可以从
   [Low Poly Art Style XL LoRA](https://civitai.com/models/578356/low-poly-art-style-xl-lora)
   和
   [Mobile Game Isometric Building XL](https://civitai.com/models/1857872/mobile-game-isometric-building-xl)
   开始；前者偏通用低多边形，后者偏移动游戏建筑。它们的许可证和训练数据
   需要在引入产品前单独核对，不能因为样张好看就直接作为默认依赖。
2. **最值得做风格后训练：Krea 2 RAW → LoRA → Krea 2 Turbo**：
   `Krea-2-Raw + anime/lowpoly LoRA → Krea-2-Turbo → BiRefNet-HR → Trellis2`。
   Krea 2 是以审美和风格控制为目标的 12B 文生图模型；官方明确建议在 RAW
   上训练 LoRA，再把 LoRA 用在 8-step 的 Turbo 上。这样可以把社区后训练集中
   到一个动漫、低多边形或我们的世界资产风格，而不是依赖通用模型的 prompt
   猜测。[官方仓库](https://github.com/krea-ai/krea-2) 和
   [ComfyUI 本地支持](https://blog.comfy.org/p/krea-2-open-source-models-are-now)
   都已提供这条路径。它的权重不是 Apache 2.0，而是 Krea 2 Community License；
   目前社区商业使用有收入门槛和内容安全义务，正式产品前要读
   [完整许可](https://www.krea.ai/krea-2-licensing)。
3. **质量上限候选：HunyuanImage-2.1**：
   `HunyuanImage-2.1 FP8 + refiner → BiRefNet-HR → RGBA PNG → Trellis2`。
   [官方仓库](https://github.com/Tencent-Hunyuan/HunyuanImage-2.1)报告它是 17B、
   原生 2K 的文生图模型，并在自有 SSAE/GSB 评估中与 GPT-Image 接近；这不是
   对 GPT Image 2 的同基准证明。FP8+CPU offload 的官方最低配置是 24 GB，正好
   卡在我们的 4090 D 边缘。它使用 Tencent Hunyuan Community License，排除
   EU/UK/韩国，必须先确认项目部署地域和分发方式。
4. **备选：绘本/插画/赛璐璐风格**：
   `Qwen-Image-2512 + Alvdansen Illustration LoRA → BiRefNet-HR → RGBA PNG → Trellis2`。
   [Qwen 官方仓库](https://github.com/QwenLM/Qwen-Image)采用 Apache 2.0，具备
   较强的提示词理解、编辑和多种艺术风格能力；[官方 ComfyUI 插画工作流](https://comfy.org/workflows/template_qwen_image_illustration_lora-e41b80eb587d/)
   覆盖 cel shading、bande dessinée、risograph、storybook watercolor 等非写实
   风格；[LoRA 页面](https://huggingface.co/alvdansen/illustration-1.0-qwen-image)
   也明确以插画一致性为目标。它的基座更重，当前本机没有权重，所以先作为第二
   个插画风格的 A/B 测试候选。
5. **轻量候选：Z-Image / Z-Image-Turbo**：
   [官方仓库](https://github.com/Tongyi-MAI/Z-Image)是 Apache 2.0 的 6B 模型；
   Base 更适合风格探索，Turbo 只有 8 次函数评估，适合批量生成。它的默认展示
   偏写实，风格化程度仍需依赖 prompt/LoRA，因此作为速度对照而不是质量上限。
6. **通用/写实备选，不作为默认**：
   `FLUX.2 Klein 4B → BiRefNet-HR/Lucida → RGBA PNG → Trellis2`。
   [官方工作流](https://docs.comfy.org/tutorials/flux/flux-2-klein) 的局部质量和
   提示词遵循度很好，但默认观感更接近写实/通用生成；除非后续有合适的风格
   LoRA 或参考图，不把它用于我们的主资产风格。

透明边缘采用 [BiRefNet](https://github.com/ZhengPeng7/BiRefNet)；玻璃、发光
特效、插画细边缘再由 Agent 选择 [Lucida](https://github.com/egeorcun/lucida)。
`Qwen-Image-Layered`、[LayerDiffuse](https://github.com/huchenlei/ComfyUI-layerdiffuse)、
FluxLayerDiffuse 和 [OmniAlpha](https://github.com/Longin-Yu/OmniAlpha) 保留为
研究/对照项：它们能探索原生 RGBA，但不是当前最稳的风格化资产生产默认。

目前 `/home/sy/llm/comfyui-wan` 还是旧版 ComfyUI，且没有安装 FLUX.2、Krea 2、
HunyuanImage、BiRefNet 或 Lucida 权重；系统中仍没有一条已注册、可被 Agent
直接提交的透明文生图工作流。
建议新增独立的本地图像 ComfyUI 服务，不要直接升级正在承载 Wan 视频工作流的
容器；这样可以单独跟进新版本节点和模型，不影响现有视频链路。

主链和实验链都应作为 Agent 的一个资产阶段：Agent 提交 `style_profile`、prompt、
negative prompt、seed 和 `alpha_policy`，FastAPI 通过 `/workflow-runs/*` 执行并持久化 `image-rgba`
工作区产物，完成后再把同一份相对路径交给 `trellis2/generate`。浏览器端的
Three.js 只负责预览和编排结果，不负责启动模型或抠图。

`style_profile` 先约定三个值：`lowpoly_flat`（默认）、`cel_shaded`、
`storybook_painterly`。提示词模板至少锁定：`single object`、`full object in
frame`、`centered`、`orthographic/isometric 3/4 view`、`clean silhouette`；
negative prompt 排除 `photorealistic`、`cinematic photography`、`multiple
objects`、`cropped`、`text` 和 `watermark`。

在下载新权重前不要凭感觉选模型。先对 keep、cottage、monolith、pine、boulder、
crystal 六类现有原型各生成固定 seed 的小样本，按下面的顺序评估：轮廓 30%、
风格一致性 25%、单物体构图 20%、alpha 边缘 15%、Trellis2 重建成功率 10%。
只有通过这组 A/B 测试的工作流，才登记为 `polykit_world_generate_asset` 的
默认资产工作流。

## MiniMax H3 Easy 参考结论

这个 [B 站视频](https://www.bilibili.com/video/BV1mxuJ6bErr/) 展示的
[ComfyUI-MiniMaxH3-Easy](https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy)
是 **视频**节点：支持文生视频、图生视频、首尾帧和参考生视频。这里的
`image` 模式是“用图片作为首/尾帧生成视频”，不是静态文生图；输出节点也是
视频和音频。视频本身主要演示节点和工作流接线，没有足够的资产样片，不能据此
判断我们关心的低多边形概念图画质。MiniMax 的确另有
[`image-01` 文生图 API](https://platform.minimax.io/docs/api-reference/image-generation-t2i)，
但那是独立的图像模型和云端接口，不等于开源 H3 本地节点。

值得借鉴的是它的编排接口，而不是直接复用模型：

- 用一个可多线连接的 `Media` 输入承载图片、视频、音频，并用 `@` 引用具体素材；
- 用 `Media Bridge` 把画布上的虚拟连线转换成适合 API/无头执行的显式输入；
- 保留外部 prompt 输入和可选的提示词优化，不把模型决策藏在节点内部。

这三点可以映射到 PolyKit 的 Agent→FastAPI→ComfyUI 链路，尤其适合未来的
`reference_video` 或世界预览动画阶段。若要验证 H3 的静态画面观感，可以把
文生视频的第 0 帧抽出来，再接 BiRefNet 和 Trellis2；这只能作为实验对照，
因为视频模型没有承诺第 0 帧是干净的单物体资产，也会多付出视频采样成本。
因此它暂时不改变当前资产主链：风格化 T2I、alpha matte、Trellis2。

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
