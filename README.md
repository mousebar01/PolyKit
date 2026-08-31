# PolyKit

PolyKit 是一个面向本地与远程 GPU 环境的 3D 生成 Web 工作台。它把 React 界面、FastAPI 服务端和可安装的 Node Pack 组合在一起，用同一套工作流完成生成、处理、预览、导出、世界构建和资产管理。

## 能做什么

- 从图片或文本生成 3D 资产，并在浏览器中预览、优化和导出。
- 用可视化 DAG 工作流连接图片、文本、网格、模型节点和处理节点。
- 安装、修复和管理模型/处理器 Node Pack，支持共享环境和隔离虚拟环境。
- 管理服务端工作区中的模型、缩略图、生成结果和历史记录。
- 通过 Web、HTTP API 或普通 CLI 使用同一个服务端运行时。
- 用 schema-v2 World contracts、确定性 validators 和 Workflow Runs 构建可交互 3D 场景。
- 通过可选的无状态 MCP adapter，把同一套 FastAPI 能力暴露给外部 MCP 客户端。

## 架构

```text
Web / CLI / automation / external MCP clients
        │
        ▼
   FastAPI control plane
        ├── workflow runs / definitions
        ├── world domain + validators
        ├── node registry and execution
        ├── workspace artifacts
        └── model downloads and status
                    │
                    ▼
                 Node Packs
                    │
                    ▼
          Blender / local models
```

FastAPI 负责执行、工作流定义、任务状态、World domain rules 和持久化资产；浏览器、CLI 和 MCP adapter 都只通过 HTTP API 使用服务端能力。PolyKit 不包含嵌入式 Agent runtime，也没有第二套聊天任务状态机。

## 文档

- [用户指南](docs/user-guide.md)：从首次启动到完成一次生成任务。
- [系统架构](docs/architecture.md)：系统边界、Workflow Runtime 和关键运行流程。
- [World Builder](docs/world-builder.md)：WorldDocument、validators、WorkflowRun 与 CLI 的边界。
- [Node Packs & Workflow Templates](docs/node-packs.md)：Node Pack、运行环境和模板约定。
- [Workflow observability](docs/workflow-observability.md)：运行状态、节点事件和 evidence inspection。
- [PolyKit MCP Adapter](tools/polykit-mcp/README.md)：外部 MCP client、MCP Inspector 与真实 Agent 验证方式。
- [Blender MCP](docs/blender-mcp.md)：独立的开发/authoring 集成，不属于产品运行时。

## 快速开始

### 开发模式

需要 Node.js、Python 3.10+、[uv](https://docs.astral.sh/uv/)，以及模型实际运行所需的 CUDA/CPU 环境。

```bash
git clone https://github.com/mousebar01/PolyKit.git
cd PolyKit
npm install
npm run dev
```

`npm run dev` 会编译内置处理节点并启动 Vite。FastAPI 可另开终端启动：

```bash
python api/serve.py --host 127.0.0.1 --port 8765
```

### Web / Headless 模式

```bash
uv sync --python 3.11
npm install
npm run web:serve
```

默认地址：`http://127.0.0.1:8765`。

没有 CUDA 时可使用 fake executor 验证 API 和 Workflow 链路：

```bash
POLYKIT_EXECUTOR=fake npm run web:serve
```

也可以分开启动 Web 与 API：

```bash
# terminal 1
npm run web

# terminal 2
python api/serve.py --host 127.0.0.1 --port 8765
```

构建后的 Web 文件位于 `dist-web/`：

```bash
npm run build
npm run web:build
```

## 工作流

Workflow 是带类型约束的 DAG：

```text
Image / Text / Mesh
        ↓
   Model / Process
        ↓
      Output
```

工作流定义通过 `/workflow-definitions/*` 持久化；运行通过 `/workflow-runs/*` 提交、查询、inspect 和取消。FastAPI 负责拓扑校验、节点执行、缓存、artifact 生命周期和 run persistence。

运行中的中间 artifact 位于 run 专属目录，只有 Output sink 才发布到用户可见的 workspace collection。

## World Builder

World Builder 不依赖聊天或 Agent。任何调用方都使用同一套 World API：

```text
WorldDocument
├── intent
├── BuildSpec
├── ScenePlan
├── GameSpec
├── quality
└── artifact refs

WorkflowRun
├── lifecycle
├── node snapshots
├── events
├── errors
└── evidence refs
```

`WorldDocument` 描述“世界是什么”；`WorkflowRun` 描述“什么计算正在/已经执行”。World 中不保存工作流阶段状态。

详情见 [docs/world-builder.md](docs/world-builder.md)。

## Node Pack

Node Pack 是提供一个或多个可执行节点的安装单元，通常包含：

```text
pack/
├── manifest.json
├── generator.py
├── processor.py
├── setup.py
└── venv/
```

运行环境分为：

- `shared`：使用 PolyKit API 的共享 Python 环境。
- `isolated`：使用 Node Pack 自己的虚拟环境，适合 CUDA/native 依赖冲突。

默认路径：

```text
~/.polykit/models
~/.polykit/workspace
~/.polykit/workflows
~/.polykit/node-packs
```

模型权重不会随仓库提交。需要访问 gated/private Hugging Face 仓库时使用 `HF_TOKEN` 或 `HUGGING_FACE_HUB_TOKEN`。

更多约定见 [docs/node-packs.md](docs/node-packs.md)。

## API

直接启动 FastAPI：

```bash
python api/serve.py \
  --host 127.0.0.1 \
  --port 8765 \
  --models-dir ~/.polykit/models \
  --workspace-dir ~/.polykit/workspace \
  --workflows-dir ~/.polykit/workflows \
  --node-packs-dir ~/.polykit/node-packs
```

主要入口：

- `/docs`：OpenAPI/Swagger。
- `/health`：服务健康状态。
- `/workflow-runs/*`：工作流运行与观测。
- `/workflow-definitions/*`：工作流定义。
- `/workspace-library/worlds/*`：World domain API。
- `/node_types`：可执行节点目录。
- `/workspace/*`：工作区文件。

如果 API 绑定到非 loopback 地址，应在反向代理或网络层提供访问控制；不要把未保护的推理 API 直接暴露到公网。

## CLI

`tools/polykit-cli/polykit.py` 是一个 stdlib-only、JSON-first 的 HTTP automation client。它不包含 Agent runtime，也不复制产品业务逻辑。

```bash
python tools/polykit-cli/polykit.py health
python tools/polykit-cli/polykit.py doctor
```

Workflow Runs：

```bash
python tools/polykit-cli/polykit.py workflow-run status <run-id>
python tools/polykit-cli/polykit.py workflow-run inspect <run-id>
python tools/polykit-cli/polykit.py workflow-run cancel <run-id>
python tools/polykit-cli/polykit.py workflow-run execute workflow-request.json
```

Assets / images：

```bash
python tools/polykit-cli/polykit.py asset from-image ./chair.png --texture
python tools/polykit-cli/polykit.py asset from-text "stylized wooden chair"
python tools/polykit-cli/polykit.py image generate "isolated low-poly lantern"
```

World：

```bash
python tools/polykit-cli/polykit.py world create --name Cabin --prompt "small playable winter cabin"
python tools/polykit-cli/polykit.py world get <world-id>
python tools/polykit-cli/polykit.py world compile-scene <world-id> scene-plan.json
python tools/polykit-cli/polykit.py world build-structure <world-id>
python tools/polykit-cli/polykit.py world validate <world-id> world.construction.validate --run-id <run-id>
python tools/polykit-cli/polykit.py world compose <world-id>
python tools/polykit-cli/polykit.py world attach-asset <world-id> chair Workflows/chair.glb --run-id <run-id>
```

使用 `--api-url` 或 `POLYKIT_API_URL` 指向远程/headless 服务。

## MCP

`tools/polykit-mcp/server.py` 是给外部 MCP client 使用的无状态 stdio adapter。它只把 MCP tools 翻译成现有 FastAPI 请求，不拥有 Workflow/World 业务状态。

直接运行：

```bash
npm run mcp:serve
```

开发调试推荐使用 MCP Inspector，不需要先在 Claude、Codex 或其他 Agent 中安装/调试：

```bash
npm run mcp:inspect
```

本地 adapter contract tests：

```bash
npm run test:mcp
```

仓库根目录 `.mcp.json` 已注册 `polykit` adapter，支持项目级 MCP 配置的客户端可以直接从仓库启动它。真正的 Agent 主要用于最后一层 smoke test：验证模型是否能根据 tool description 自然选择 `workflow_inspect`、`world_validate`、`world_build_structure` 等正确工具。

详见 [tools/polykit-mcp/README.md](tools/polykit-mcp/README.md)。

## 常用环境变量

| 变量 | 作用 |
| --- | --- |
| `POLYKIT_HOST` / `POLYKIT_PORT` | API 监听地址和端口 |
| `POLYKIT_PYTHON` | Web 一体化启动使用的 Python |
| `POLYKIT_EXECUTOR` | 执行器：`cuda` 或 `fake` |
| `POLYKIT_API_URL` | Web/CLI/MCP 客户端连接的 FastAPI 地址 |
| `POLYKIT_WEB_DIR` | FastAPI 托管的 Web 构建目录 |
| `POLYKIT_CORS_ORIGINS` | 允许访问 API 的来源列表 |
| `MODELS_DIR` | 模型权重目录 |
| `WORKSPACE_DIR` | 工作区和生成资产目录 |
| `WORKFLOWS_DIR` | 工作流定义目录 |
| `NODE_PACKS_DIR` | Node Pack 运行时目录 |
| `POLYKIT_STATE_DB` | WorkflowRun SQLite 状态文件 |
| `POLYKIT_IDLE_UNLOAD_SECONDS` | 模型空闲卸载时间 |
| `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` | Hugging Face 授权 |
| `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` | 服务端出站代理 |
| `HF_ENDPOINT` | Hugging Face endpoint override |
| `UV_INDEX_URL` / `PIP_INDEX_URL` | Python package index override |

## 开发与测试

```bash
npm run lint
npm run test:node
npm run test:py
npm run test:cli
npm run test:mcp
npm run mcp:inspect
npm test
npm run check
npm run web:build
```

## 目录结构

```text
src/                 React 应用、页面区域、共享组件和状态
src/areas/assets/    资产库与 3D 查看器
src/areas/workflows/ 工作流画布、节点和模板
src/areas/worlds/    Three.js world runtime / viewer
src/areas/settings/  应用、网络、存储和集成设置
api/                 FastAPI 服务、路由和执行引擎
node-packs/          官方 Node Pack 源码
tools/polykit-cli/   JSON-first 自动化 CLI
tools/polykit-mcp/   外部 MCP client 的无状态 FastAPI adapter
docs/                架构、Node Pack、Workflow、World 和部署说明
```

## 许可证

PolyKit 使用 [MIT License](LICENSE)。第三方模型、权重、Node Pack 适配器和上游运行时仍受各自许可证约束；使用相关模型前请阅读对应上游许可和使用条款。
