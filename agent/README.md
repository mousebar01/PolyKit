# PolyKit Agent

[pi 编程智能体](https://github.com/badlogic/pi-mono) 的本地网页界面。PolyKit Agent 读取本机的 pi 会话文件，在浏览器里提供会话管理、实时对话、模型配置和技能管理；同一个 pi 会话在终端里和 PolyKit Agent 里看到的是同一份数据。

## 快速开始

PolyKit Agent 要求 Node.js 22.19.0 或更高版本，可用 `node --version` 检查。从源码安装和运行：

```bash
cd agent
npm install
npm run dev                      # 开发模式
# 或生产模式
npm run build && npm start
```

独立运行时打开 [http://127.0.0.1:30001](http://127.0.0.1:30001)。首次启动会在 `~/.pi/agent/polykit-agent-config.json` 创建权限为 `0600` 的本地配置，不生成随机密码，默认仅本机监听。生产接入 PolyKit 时不启动这个 Next.js Web 壳，而由 PolyKit FastAPI 统一托管 Agent API。

**可选参数：**

```bash
POLYKIT_AGENT_PORT=8080 npm run start              # 自定义端口（开发时：POLYKIT_AGENT_PORT=8080 npm run dev）
POLYKIT_AGENT_USERNAME=operator \
POLYKIT_AGENT_PASSWORD_FILE=/run/secrets/polykit-agent-password npm start # 部署时从 Secret 文件读取凭据
POLYKIT_AGENT_ALLOWED_HOSTS=polykit-agent.internal npm run start  # 允许指定的代理或自定义主机名
POLYKIT_AGENT_CONFIG_PATH=/path/to/polykit-agent-config.json npm run start # 自定义本地配置路径
POLYKIT_AGENT_NO_OPEN=1 npm run start              # 适用于后台服务或开机自启
```

普通启动使用本地配置中的账号、密码和访问范围。配置文件权限为 `0600`，密码可在设置页回显；部署时可用 `POLYKIT_AGENT_USERNAME` 与 `POLYKIT_AGENT_PASSWORD_FILE` 注入账号和 Secret 文件。旧的 `POLYKIT_AGENT_PASSWORD`、`POLYKIT_AGENT_NETWORK` 和 `start:lan` 入口不再支持。
生产接入时不启用这套独立 Web 壳、独立认证或移动端入口；Agent 由 PolyKit FastAPI 统一托管，并沿用 PolyKit 的权限与网络边界。

## HTTP 代理

PolyKit Agent 的服务端模型请求和 API 请求会读取标准的 `HTTP_PROXY`、`HTTPS_PROXY` 和 `NO_PROXY` 环境变量。

macOS 或 Linux：

```bash
HTTP_PROXY=http://127.0.0.1:7890 \
HTTPS_PROXY=http://127.0.0.1:7890 \
NO_PROXY=localhost,127.0.0.1 \
npm run start
```

Windows PowerShell：

```powershell
$env:HTTP_PROXY = "http://127.0.0.1:7890"
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
$env:NO_PROXY = "localhost,127.0.0.1"
npm run start
```

## 功能介绍

- **把历史工作接回来**：打开网页就能按项目找到以前的 pi 对话，不必在终端里翻文件或记住会话路径。
- **放心试不同方向**：可以从某条历史消息重新开始，也可以复制出一条独立的新路线，探索方案时不怕弄乱原来的对话。
- **跨分支工作**：在侧边栏切换 Git worktree，让新会话跟随你选择的 checkout。
- **随时掌握会话状态**：在顶部就能看到上下文占用、花费、压缩结果和系统提示，长会话不再像黑箱。
- **少离开当前界面**：模型、登录/API key、模型测试和技能开关都能在网页里处理，配置 agent 时不用在多个工具之间来回切换。

## 注意事项

- **数据目录**：默认读取 `~/.pi/agent/sessions` 下的会话文件。可通过环境变量 `PI_CODING_AGENT_DIR` 指定其他 pi agent 目录。
- **会话文件**：路径形如 `~/.pi/agent/sessions/<编码后的工作目录>/<时间戳>_<uuid>.jsonl`。
- **模型配置**：Models 面板读写 pi agent 目录下的 `models.json`，模型列表和默认模型由 pi 的配置解析得到。
- **项目目录**：通过目录选择器设置会话工作目录；Agent UI 不提供项目文件浏览或预览。
- **Git worktree**：在侧边栏切换同一项目下的不同 checkout，新建目录位于 `<repo>-worktrees/<分支>`；删除 worktree 不会删除对应分支。
- **Fork 与会话内分支不同**：Fork 会创建新的 `.jsonl` 文件；“Edit from here” 是同一会话文件里的分支。
- **国际化和多语言**：界面内置简体中文和英文，可在顶部栏切换；新增语言在 `apps/web/lib/i18n/` 下添加。

## 开发

```bash
npm install
npm run dev
```

本地开发端口为 [http://127.0.0.1:30001](http://127.0.0.1:30001)。
如需换端口，可设置 `POLYKIT_AGENT_PORT`，例如 `POLYKIT_AGENT_PORT=30002 npm run dev`。

常用检查：

```bash
npm run typecheck
npm run lint
```

开发时不要运行 `next build` / `npm run build`，它会写入 `.next/`，容易影响正在运行的 dev server。发布流程再执行构建。

## 项目结构

```
apps/web/app/
  api/
    agent/          # 创建/驱动 AgentSession，提供 SSE 事件流
    auth/           # OAuth 和 API key 管理
    cwd/browse/     # 服务端目录浏览
    cwd/validate/   # 自定义工作目录校验
    default-cwd/    # 获取 pi 默认工作目录
    home/           # 当前用户 home 目录
    models/         # 可用模型、默认模型、thinking levels
    models-config/  # 读写 models.json、测试模型
    sessions/       # 会话读取、重命名、删除、上下文、HTML 导出
    skills/         # skills 列表、搜索、安装、启停
apps/web/components/
  AppShell.tsx        # 主布局、URL 状态和会话区
  SessionSidebar.tsx  # 项目选择和会话树
  DirectoryPicker.tsx # 支持浏览和路径输入的工作目录选择器
  ChatWindow.tsx      # 消息区、SSE、拖拽图片、minimap
  ChatInput.tsx       # 输入栏、模型/工具/thinking/compact/slash controls
  MessageView.tsx     # 消息、thinking、tool call/result 渲染
  ModelsConfig.tsx    # 模型和认证配置面板
  SkillsConfig.tsx    # 技能管理面板
apps/web/lib/
  directory-browser.ts # 目录规范化和安全枚举工具
  http-dispatcher.ts  # 服务端 fetch 的 HTTP(S) 代理配置
  rpc-manager.ts      # AgentSessionWrapper 生命周期和全局 registry
  session-reader.ts   # 解析 .jsonl 会话文件和分支上下文
  normalize.ts        # 规范化 toolCall 字段名
  file-access.ts      # 服务端工作区/插件路径安全边界
  markdown.ts         # Markdown/Mermaid/KaTeX 插件配置
  pi-types.ts         # pi 相关类型
apps/web/hooks/
  useAgentSession.ts  # 会话加载、发送命令、SSE 状态机
  useAudio.ts         # 完成提示音
  useDragDrop.ts      # 图片拖拽
  useTheme.ts         # 主题切换
apps/web/bin/
  polykit-agent.js             # CLI 入口（构建后可运行）
apps/web/instrumentation.ts # 初始化服务端 HTTP dispatcher
```
