# PolyKit Agent 集成边界

`agent/` 保存的是 AgentSession 的可迁移源码，不是 PolyKit 里的第二个 Web 应用。生产运行时必须继续遵守 PolyKit 的单一服务边界：浏览器只访问 FastAPI，工作流、资产、任务状态和工作区路径都由 FastAPI 持有。

## 当前状态

- Agent 源码已放入 `agent/`，没有嵌套 Git 历史。
- 移动端应用已移除；保留的移动设备 API 是服务端兼容代码，不会被 Agent 页面直接依赖。
- 源码中的包名、CLI、配置文件名、环境变量、缓存键和可见文案已改为 `PolyKit Agent` / `polykit-agent`。
- PolyKit 设置页已原生提供 `Agent` 子板块；运行时开关、默认服务商/模型、思考级别和工具权限由 FastAPI 的 `/settings/agent` 持久化，模型认证、技能、插件、MCP、归档会话和隐藏工作区则沿用 Agent 的本地配置边界并嵌入同一设置页。
- PolyKit 主导航已提供原生 `对话` 入口（`src/areas/agent/AgentPage.tsx`），直接嵌入迁移后的 `ChatWindow`、`ChatInput` 和消息渲染，不启动独立 Next 页面。
- FastAPI 已提供 `/agent/*` 公共边界，并按需启动 loopback Node sidecar；会话创建、状态、上下文、命令、模型列表和 SSE 都通过该边界提供。

## 目标运行时

```text
PolyKit React
   │  /agent/* + SSE
   ▼
FastAPI（唯一公开 API、任务/路径/权限所有者）
   │  loopback + 随机内部 token
   ▼
Node Agent sidecar（只运行 pi SDK 的 AgentSession）
   │
   ├─ PolyKit MCP：工作流、资产和 Blender 工具
   └─ runtime_paths.data / agent：会话 JSONL（已有本地 Agent 配置可复用）
```

Node sidecar 只负责 `AgentSession`、会话 JSONL 和 pi 扩展；它不能再承载 Next 页面、独立认证或另一套设置。FastAPI 负责 sidecar 的启停、崩溃回收、SSE 转发、取消和审计。若机器已有 `~/.pi/agent` 登录与模型配置，sidecar 只读复用它们；会话仍写入 PolyKit 的 `runtime_paths.data / agent / sessions`，不会复制凭据。

## 设置归属

Agent 的设置只保留运行时真正需要的产品级选项，并归入 PolyKit 的统一设置存储：

| 原 Agent 设置 | PolyKit 归属 |
| --- | --- |
| 默认服务商、默认模型、思考级别 | `设置 → Agent → 会话默认值`，由 `/settings/agent` 持久化 |
| 工具开关 | `设置 → Agent → 工具权限`，映射到 sidecar 的白名单工具集 |
| 语言、主题 | PolyKit `设置 → 应用`，不再保留第二套主题/语言状态 |
| 项目文件浏览、移动端 | 不迁移；分别由资产库/服务器工作区边界负责，移动端已移除 |
| 隐藏工作区、归档会话 | `设置 → Agent → 隐藏工作区 / 归档会话`，由 Agent 配置和会话目录管理 |
| 模型凭据、MCP/插件信任 | 复用本机 Agent 配置并沿用项目置信门控；不能由 Agent 页面绕过 |

因此 `agent/apps/web` 只作为运行时迁移源，不能被当作兼容页面挂载；最终生产包只启动 PolyKit FastAPI 和它管理的 sidecar。

## 分阶段落地

1. **运行时桥接（已完成第一版）**：从 `agent/apps/web/lib/` 复用 `rpc-manager.ts`、`session-reader.ts`、`normalize.ts` 和模型作用域代码，由 `agent/runtime/server.ts` 提供内部 HTTP/SSE 服务；FastAPI `/agent/*` 负责鉴权、启停和转发，所有新会话工作目录限定在 PolyKit workspace。
2. **Agent 页面运行时（已完成第一版）**：现有 `src/areas/agent/AgentPage.tsx` 直接渲染迁移后的 `ChatWindow`、消息渲染和 SSE 状态机；不复制 Next 的 AppShell、项目文件浏览和主题系统。Agent 原有的管理设置则以子板块方式嵌入 PolyKit 设置页。资产必须由用户显式附加 workspace 相对路径。
3. **Blender 桥**：增加 `api/services/blender_bridge.py`、`api/routers/blender.py` 和 `integrations/blender/polykit_bridge/`。Blender 插件用队列和 `bpy.app.timers` 在主线程执行结构化命令，首批只开放导入、校验、变换、材质、预览和导出；不提供任意 Python 执行。
4. **工作流连接**：Agent 生成或修改的 GLB 通过 `/workspace/...` 和 workspace library 返回，资产能力标记为 `mesh` / `rigged-mesh`，不在浏览器里传递绝对路径。

## Worlds 编排

Worlds 使用同一条 Agent 边界，不再另起一个云端 Director。Agent 通过
`api/mcp_server.py` 暴露的 `polykit_world_*` 工具保存意图/区域/资产计划，
按 WorldClaw 论文的 intent → plan → terrain → placement/assets → refine 阶段
推进，并调用现有 `/workflow-runs/*` 完成本地生成。Three.js 只读取世界文档和
workspace 资产做预览；阶段契约和工具顺序见 [`docs/world-agent.md`](world-agent.md)。

## 安全和资源约束

- Agent 默认不开放 `bash`、`edit`、`write` 等通用编码工具，只开放 PolyKit 和 Blender 白名单工具；开发者模式才允许扩展权限。
- sidecar 只监听 loopback 随机端口，外部不暴露其端口或 Next 路由。
- Blender 与 3D 推理共享 GPU；启动 Blender GPU 任务前要通过现有模型运行时卸载/协调机制释放显存。
- 项目扩展和技能会执行外部代码，必须沿用项目置信门控；不能因为迁移 Agent 而绕过 `project-trust`。
- 取消、超时、sidecar 崩溃和 Blender 断开都要映射成 FastAPI 的明确任务状态。

## 验收标准

- 只启动 PolyKit FastAPI 一个对外端口（默认 `8765`）。
- 浏览器刷新后，Agent 会话和 SSE 能从 FastAPI 恢复。
- 可显式附加 workspace GLB，Agent 能调用白名单 Blender 工具并把结果写回 workspace library。
- PolyKit UI、日志和配置中不再出现来源项目的品牌标识。
