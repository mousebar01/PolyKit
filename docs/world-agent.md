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
