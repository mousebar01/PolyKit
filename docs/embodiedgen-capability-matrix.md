# EmbodiedGen 能力复刻边界

这份表把 `/home/sy/EmbodiedGen` 当前仓库的能力，映射到 PolyKit 的单一运行时。
目标是复刻它的“输入/输出契约和编排思想”。World/structure 的生产建模和离线
渲染走 Blender process backend；独立的文本/图片资产仍可由已安装的本地模型节点
生成。两者都不取代 FastAPI 的任务状态和资产持久化，也不把 Blender、Gradio 或
仿真环境直接复制进产品。

| 参考能力 | EmbodiedGen 的实现 | PolyKit 当前落点 | 决策 |
| --- | --- | --- | --- |
| 文本/图片 → 3D 资产 | `textto3d.py` / `imageto3d.py`，文生图后接 SAM3D、TRELLIS 或 Hunyuan3D | `/workflow-runs/text-to-asset`：`polykit.text → anima/generate → remove-background → trellis2/generate → trellis2/refine? → mesh-optimizer → polykit.output` | 复刻编排；保留现有本地模型节点 |
| 纹理生成/精修 | `gen_texture.py` 与 `texture_model.py` | `trellis2/refine`，由工作流参数决定是否启用 | 复刻，默认可关闭 |
| 资产质量与面数控制 | 重试、几何检查、碰撞网格和面数约束 | `mesh-optimizer/optimize`，默认目标 100k 三角面；结果仍登记为 workspace 资产 | 先复刻可展示的质量门槛，不引入第二套运行时 |
| 任务 → 场景图 | `LayoutDesigner` 的 disassemble + hierarchy 两步 GPT 提示词 | Agent 通过 MCP 生成 `polykit.scene-plan`；服务端校验对象 ID、关系和边界 | 复刻语义契约；LLM 决策仍由 Agent 负责 |
| 关系布局 | `bfs_placement`，基于父子树、表面包围盒、可达性和随机种子放置 | `solve_scene_layout`：确定性种子、支撑/包含/相对关系、2D AABB 避碰；`layoutQuality` 做相机无关的边界、接触、包含、关系和重叠审计 | 复刻核心；当前不是完整 navmesh/物理求解 |
| 多资产场景合成 | `compose_mesh_scene` 将背景和对象按变换写入一个 `Iscene.glb` | `scene-composer/compose` 批量接收 Mesh、保留源节点名和变换；`POST /workspace-library/worlds/{id}/compose` 编译并提交 | 复刻可展示 GLB 合成；不改变可编辑 ScenePlan |
| 资产检索与复用 | `dataset_index.csv` + 语义匹配，返回 URDF | `polykit_world_find_assets` + workspace `*.asset.json` sidecar，返回相对路径 | 复刻语义检索；不复制外部数据集 |
| 室内房间/多房屋 | Infinigen + Blender `room_gen`，输出 URDF/USD | 可由 Blender worker/MCP 执行，结果回写为 `.blend`、GLB 或 URDF/USD；ScenePlan 仍是上游契约 | 可接入，先做单房间，再扩展多房间 |
| 3DGS 背景场景 | `gen_scene3d.py` / `gs_model.py` | 可作为 Blender 离线渲染的背景层或独立 splat 资产；不强行转成可编辑 Mesh | 可接入，但与 GLB 主场景保持不同资产类型 |
| 可编辑空间操作 | `spatial-computing`：插入、删除、查询、地面图和语义匹配 | ScenePlan 关系/实例可重新编译；资产搜索和世界持久化已接入 MCP | 先做 JSON 级编辑，再接入实例级 UI 操作 |
| 物理属性/碰撞/URDF | `URDFGenerator`、CoACD、仿真验证 | 当前 GLB 展示链不改变；碰撞/URDF 作为后续独立 process pack | 不改变现有 Three.js 资产契约 |
| 模拟器导出 | URDF → MJCF/USD，适配 SAPIEN、Isaac、MuJoCo 等 | 暂不作为 Web 展示必需能力 | 后续按目标模拟器增加导出节点 |
| affordance/抓取 | 部件语义、分割、6-DoF 抓取与成功率评估 | 当前产品不做机器人运行 | 不引入，避免扩大产品目标 |
| 对话式编辑 | Claude Code slash command + bounded skill call | External Agent / Chat / CLI + PolyKit MCP + FastAPI canonical workflow APIs | 复刻交互理念，不在 PolyKit 内嵌 Agent runtime |

## Blender MCP 的接入边界

官方 Blender MCP 是一个轻量桥接层，不是模型推理服务，也不是任务队列。它由
Blender Add-on（在 Blender 主线程执行 Python）和独立 MCP Server 组成，支持 stdio
以及可选的 HTTP transport；工具包括执行 Blender Python、读取场景摘要、截图、渲染
和导出。它适合做“场景构建/检查/渲染执行器”，不应直接成为浏览器的产品 API。

PolyKit 采用下面的边界：

1. Agent 生成 `ScenePlan` 和 Blender 任务参数；不直接把自然语言变成无约束脚本。
2. FastAPI 创建并持久化 Blender run，保存 `.blend`、渲染图、GLB/GLTF、验证报告的
   workspace 相对路径；失败、取消和重试仍归 `RunCoordinator` 管理。
3. Blender MCP 只执行经过白名单/模板约束的 Blender 操作。交互式 MCP 可用于检查
   和修复，批处理使用 Blender background worker；两者都不能绕过 FastAPI 的资产登记。
4. Three.js 保留为浏览器交互预览；最终质量渲染可以来自 Blender，不要求浏览器复刻
   Cycles/Eevee 的渲染结果。

## 不变的产品边界

- FastAPI 仍是唯一的执行、排队、取消和持久化边界；Agent 只提交计划或工作流。
- `/workflow-runs/*` 和 `/workflow-definitions/*` 是唯一的新增工作流入口；不新建一套
  Gradio/CLI 运行时。
- Three.js 只读取世界文档和 workspace 资产负责交互展示。ScenePlan 预览支持盒状占位
  和可用 GLB 的真实网格，但不在浏览器启动模型；Blender 负责生产建模和离线最终渲染。
- 资产名称使用稳定的 `objectId`/`assetId`，文件名只是路径；检索结果可通过 sidecar
  提供别名和类别，避免把误匹配写进世界文档。
- `seed` 和诊断信息随计划保存，确保同一份计划可复现并能解释放置失败。

## 当前验证结果

本地 4090 已实测完整的文本资产链（Anima → 抠图 → Trellis2 → 面数优化）：输出
GLB 约 1.8 MB、99,967 三角面，可被 `trimesh` 读取并由 Three.js 的 GLTFLoader
加载。这个结果验证的是“资产生成和质量门槛”，不代表已经接通 Infinigen、Blender
MCP、导航网格或机器人仿真。

`scene-composer/compose` 也已通过本地双 Mesh 集成测试：两个 GLB 通过同一个批量输入
保留为两个命名几何体，位置变换写入组合后的 GLB。世界合成路由只把 ScenePlan 编译
成这个工作流，不在浏览器或 Electron 内另起运行时。
