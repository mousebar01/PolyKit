# PolyKit

PolyKit 是一个面向本地与远程 GPU 环境的图像生成 3D Web 工作台。它把 React 界面、FastAPI 服务端和可安装的 Node Pack 组合在一起，用同一套工作流完成生成、处理、预览、导出和资产管理。

## 能做什么

- 从图片生成 GLB 网格，并在浏览器中预览、优化和导出。
- 用可视化 DAG 工作流连接图片、文本、网格、模型节点和处理节点。
- 安装、修复和管理模型/处理器 Node Pack，支持共享环境和隔离虚拟环境。
- 管理服务端工作区中的模型、缩略图、生成结果和历史记录。
- 通过 Web 浏览器或 HTTP API 使用同一个服务端运行时。
- 使用 CLI 启动工作流、查询状态、取消任务和导出结果。

## 架构

```text
React Web UI
        │
        ▼
   FastAPI control plane
        ├── workflow runs / definitions
        ├── node registry and execution
        ├── workspace artifacts
        └── model downloads and status
```

FastAPI 负责生成执行、工作流定义、任务状态和持久化资产；浏览器只负责界面交互，通过 HTTP API 使用服务端能力。

## 文档

- [用户指南](docs/user-guide.md)：从首次启动到完成一次 Image → 3D 任务。
- [系统架构](docs/architecture.md)：C4 系统边界、Workflow Runtime 和关键运行流程。
- [Node Packs & Workflow Templates](docs/node-packs.md)：Node Pack、运行环境和模板约定。
- [Agent 集成边界](docs/agent-integration.md)：AgentSession、FastAPI、工作区资产和 Blender 桥接方案。

## 快速开始

### 开发模式

需要 Node.js、Python 3.10+、[uv](https://docs.astral.sh/uv/)，以及模型实际运行所需的 CUDA/CPU 环境。

首次安装 uv（Windows PowerShell 请使用官方安装脚本）：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
git clone https://github.com/mousebar01/PolyKit.git
cd PolyKit
npm install
npm run dev
```

npm run dev 会先编译内置处理节点，再启动 Vite Web 开发服务器。它不会自动启动 FastAPI，请另开终端运行 API，或直接使用下方的一体化启动命令。

### Web / Headless 模式

先用 uv 同步 Python 环境：

```bash
uv sync --python 3.11
npm install
```

`uv sync` 会根据 `pyproject.toml` 和 `uv.lock` 创建或更新根目录 `.venv`。Windows PowerShell 使用同一条命令即可。

启动 Web 前端和 FastAPI：

```bash
npm run web:serve
```

默认地址：<http://127.0.0.1:8765>

没有 CUDA 时可以使用 fake executor 验证接口和工作流链路：

```bash
POLYKIT_EXECUTOR=fake npm run web:serve
```

也可以分开启动：

```bash
# 终端 1：开发前端
npm run web

# 终端 2：API 服务
python api/serve.py --host 127.0.0.1 --port 8765
```

如果需要为 Web/Headless 服务指定 Python 解释器：

```bash
POLYKIT_PYTHON=/path/to/python npm run web:serve
```

默认使用 `.venv/bin/python`（Windows 为 `.venv\Scripts\python.exe`）。

构建后的 Web 文件位于 dist-web/。FastAPI 会在该目录存在时直接托管前端，也可以通过 POLYKIT_WEB_DIR 指定其他目录。

### 构建和预览

```bash
# Web 构建
npm run build
npm run web:build
```

仓库还提供了简单启动脚本：

```bash
./launch.sh       # macOS / Linux
launch.bat        # Windows
```

脚本会在缺少 node_modules/ 或 dist-web/ 时先安装依赖、构建 Web 文件，然后启动 Web 预览。

## 工作流

工作流是由节点和有向边组成的 DAG。常见结构如下：

```text
Image / Text / Mesh
        ↓
   Model / Process
        ↓
      Output
```

工作流页面支持：

- 拖拽节点包和内置节点到画布。
- 按输入/输出类型连接节点，并在连接前执行类型和环路校验。
- 自动保存可编辑的工作流定义。
- 工作流标签、打开/导入/导出、复制、重命名和撤销/重做。
- 使用已验证的工作流模板快速开始。
- 通过 /workflow-runs/* 提交、查询、重连和取消运行。

工作流定义由服务端保存，浏览器刷新后仍可恢复。生成出的文件属于服务端工作区，可通过资产库或 API 继续查看和导出。

## Node Pack

Node Pack 是提供一个或多个可执行节点的安装单元，通常包含：

```text
pack/
├── manifest.json     # 节点、输入输出和参数声明
├── generator.py      # 模型节点入口
├── processor.py      # 可选的处理器入口
├── setup.py          # 可选的环境安装脚本
└── venv/             # 可选的隔离 Python 环境
```

Node Pack 可以从模型页面通过 GitHub、本地目录或内置官方包安装。运行环境分为：

- shared：使用 PolyKit API 的共享 Python 环境。
- isolated：使用 Node Pack 自己的虚拟环境，适合 CUDA/native 依赖冲突的模型。

默认路径：

```text
~/.polykit/models       # 模型权重
~/.polykit/workspace    # 工作流输入、输出和缩略图
~/.polykit/workflows    # 工作流定义
~/.polykit/node-packs   # Node Pack 运行时代码和环境
```

内置包的源码位于 node-packs/，运行时同步到用户目录；同步过程会保留虚拟环境、缓存和模型权重等运行时数据。

当前仓库包含的官方模型包包括：

- trellis2：Trellis.2 GGUF，支持图片生成几何网格和纹理网格。
- hunyuan3d-part：Hunyuan3D-Part / P3-SAM，用于网格几何分件。

模型权重不会随仓库提交。首次运行前，在模型页面下载对应权重；需要访问 gated/private Hugging Face 仓库时设置 HF_TOKEN 或 HUGGING_FACE_HUB_TOKEN。

更多 Node Pack 约定见 [docs/node-packs.md](docs/node-packs.md)。

## 独立 API

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

启动后可以访问：

- /docs：OpenAPI/Swagger 文档。
- /health：服务健康状态。
- /workflow-runs/*：生成和工作流运行。
- /workflow-definitions/*：工作流定义持久化。
- /node_types：编辑器可用节点。
- /workspace/*：工作区文件。

服务端默认监听本机 127.0.0.1:8765。如果绑定到其他主机，建议同时设置 POLYKIT_CORS_ORIGINS，并在反向代理或网络层提供访问控制；不要直接把未保护的推理 API 暴露到公网。

## 常用环境变量

| 变量 | 作用 |
| --- | --- |
| POLYKIT_HOST / POLYKIT_PORT | API 监听地址和端口（默认 127.0.0.1:8765） |
| POLYKIT_PYTHON | Web 一体化启动服务时使用的 Python 解释器 |
| POLYKIT_UV | uv 可执行文件路径；未设置时从 PATH 查找 |
| POLYKIT_SETUP_PYTHON | 节点包 Repair 创建隔离 venv 时使用的 Python；需要更高版本时显式指定 |
| POLYKIT_EXECUTOR | 执行器：cuda（默认）或 fake |
| POLYKIT_API_URL | Web 客户端连接的 FastAPI 地址 |
| POLYKIT_WEB_DIR | FastAPI 托管的 Web 构建目录 |
| POLYKIT_CORS_ORIGINS | 允许访问 API 的逗号分隔来源列表 |
| MODELS_DIR | 模型权重目录 |
| WORKSPACE_DIR | 工作区和生成资产目录 |
| WORKFLOWS_DIR | 工作流定义目录 |
| NODE_PACKS_DIR | Node Pack 运行时目录 |
| POLYKIT_STATE_DB | 工作流运行状态 SQLite 文件路径 |
| POLYKIT_IDLE_UNLOAD_SECONDS | 模型空闲自动卸载时间；0 表示禁用 |
| POLYKIT_DISABLE_NODE_CACHE | 设置为 1 禁用工作流节点缓存 |
| HF_TOKEN / HUGGING_FACE_HUB_TOKEN | Hugging Face 下载授权 |
| HTTP_PROXY / HTTPS_PROXY / ALL_PROXY | 启动时可提供服务器出站代理；Web 设置页保存的代理会应用到后端及其子进程 |
| HF_ENDPOINT | 启动时覆盖 Hugging Face API/文件端点；也可在 Web 设置页配置 |
| UV_INDEX_URL / PIP_INDEX_URL | 启动时覆盖 Python 包索引；也可在 Web 设置页配置 |
| POLYKIT_PYTORCH_INDEX_URL | SkinTokens CUDA wheel 索引；支持 `{tag}` 占位符（cu126/cu128） |

代理和下载源都由 FastAPI 服务器使用。Web 在本地、推理服务器在远程时，
设置页里的 `127.0.0.1` 指远程服务器本机；Hugging Face、PyPI 和 PyTorch
镜像也必须是远程服务器能够访问的地址。下载源可以在 Settings → Network
配置，留空表示使用官方源。

设置页提供官方、清华、阿里云和中科大预设，也支持逐项自定义；预设只填入
Hugging Face 与 PyPI，PyTorch CUDA wheel 默认仍走官方索引（可按需填写自定义
索引）。选择预设后点击保存，或直接点击某一项的测试按钮验证远程服务器连通性。

## CLI

CLI 是一个标准库 Python 工具，通过本机或 Headless PolyKit API 执行自动化任务。先启动 Web 服务或 api/serve.py，然后运行：

```bash
python tools/polykit-cli/agent.py health
python tools/polykit-cli/agent.py doctor
```

常用操作：

```bash
# 查看模型状态
python tools/polykit-cli/agent.py model list
python tools/polykit-cli/agent.py model status

# 启动工作流并等待完成
python tools/polykit-cli/agent.py workflow-run start --image ./input.png --wait

# 从图片生成并导出 GLB
python tools/polykit-cli/agent.py generate \
  --image ./input.png \
  --output ./output.glb \
  --progress

# 查询或取消任务
python tools/polykit-cli/agent.py workflow-run status <run-id>
python tools/polykit-cli/agent.py workflow-run cancel <run-id>
```

CLI 使用 /workflow-runs/* 作为新的执行合约；/generate/* 仅保留兼容用途。

## 开发与测试

```bash
npm run lint       # ESLint
npm run test:node  # Node/TypeScript 测试
npm run test:py    # Python API 测试
npm run test:cli   # CLI 测试
npm test           # 全部测试
npm run check      # lint + 全部测试
```

前端相关检查：

```bash
npm run web:build
node --test --experimental-strip-types \
  --experimental-loader ./scripts/node-ts-extensionless-loader.mjs \
  src/areas/workflows/workflowsI18n.test.mjs
```

## 目录结构

```text
src/                 React 应用、页面区域、共享组件和状态
src/areas/assets/    资产库与 3D 查看器
src/areas/models/    Node Pack 和模型管理
src/areas/workflows/ 工作流画布、节点和模板
src/areas/settings/  应用、网络、存储和集成设置
api/                 FastAPI 服务、路由和执行引擎
node-packs/          官方模型 Node Pack 源码
tools/polykit-cli/   JSON-first 自动化 CLI
docs/                架构、Node Pack、工作流和部署说明
```

## 相关文档

- [Node Packs 与工作流模板](docs/node-packs.md)
- [网格分件工作流](docs/mesh-segmentation-workflow.md)
- [国际化约定](docs/i18n.md)
- [UI 设计契约](DESIGN.md)

## 许可证

PolyKit 本身使用 [MIT License](LICENSE)。第三方模型、权重、Node Pack 适配器和上游运行时仍受各自许可证约束；使用相关模型前请阅读对应上游仓库的许可和使用条款。
