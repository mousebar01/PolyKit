# 架构总览

polykit-agent 是一个 npm workspace 项目，Web 应用直接承载 Next.js 服务端。核心思路是：**服务端进程内直接复用 pi SDK（`@earendil-works/pi-*`），会话数据就是本机 `~/.pi/agent/` 里的 `.jsonl` 文件，界面只负责展示和发命令**。所以终端里的 pi 和 polykit-agent 里看到的是同一份数据，polykit-agent 不需要自己的数据库。

## 仓库布局

```
polykit-agent/
├── apps/
│   └── web/          # Next.js 16 网页应用（polykit-agent-web）
├── docs/             # 本目录
└── AGENTS.md         # 给 AI 助手的开发说明
```

根 `package.json` 通过 workspace 脚本转发 Web 开发命令。

## 分层

### 1. 展示层（浏览器）

- **Web**：React 19 + Tailwind 4 + CSS 变量主题，所有状态都存在浏览器 URL / localStorage / 内存里，不引状态管理库。核心状态机在 `hooks/useAgentSession.ts`。

### 2. HTTP 守门层（apps/web/proxy.ts）

Next.js 中间件（`matcher: ["/", "/api/:path*"]`）是**所有请求**的第一道门，依次做两件事：

1. **Host/Origin 校验**：API 请求要求 Host 在允许集合内且 Origin 同源（防 DNS rebinding 和跨站请求），非 API 请求只查 Host。
2. **认证**：本地配置在用户设置密码后提供 HTTP Basic Auth（账号是配置字段，默认 `pi`）；部署环境可通过 `POLYKIT_AGENT_USERNAME` + `POLYKIT_AGENT_PASSWORD_FILE` 注入，另外放行已注册设备的 Bearer token。

### 3. 业务层（App Router 路由 + lib/）

路由全部位于 `apps/web/app/api/**/route.ts`，按领域分组（agent / sessions / auth / models / skills / plugins / mcp / mobile / worktrees / cwd）。Agent UI 不提供项目文件浏览；工作区路径只用于会话、插件、技能、MCP、模型和 worktree 的服务端校验。业务逻辑尽量下沉到 `lib/` 的纯模块，路由只做参数校验、安全检查和 JSON/SSE 包装。

两大核心模块：

- **`lib/rpc-manager.ts`**（会话运行时）：维护进程内 `AgentSessionWrapper`，包装 pi SDK 的 `AgentSessionLike`，提供 `send()` 命令协议、空闲回收、fork 特殊处理（详见 [session-runtime.md](session-runtime.md)）。
- **`lib/session-reader.ts`**（会话浏览）：通过 SDK 的 `SessionManager` 读 `.jsonl` 文件、构建上下文、缓存会话列表（详见 [session-files.md](session-files.md)）。

### 4. 数据层（~/.pi/agent/）

polykit-agent 与 pi CLI 共享的目录（`getAgentDir()`，可用 `PI_CODING_AGENT_DIR` 覆盖）：

| 文件/目录 | 内容 | 谁读写 |
| --- | --- | --- |
| `sessions/<encoded-cwd>/<ts>_<uuid>.jsonl` | 会话文件，格式见 [session-files.md](session-files.md) | SDK SessionManager / 直接读文件 |
| `models.json` | 模型与 provider 配置 | `/api/models-config` GET/PUT |
| `settings.json` | 默认模型、thinking 级别等 | pi SDK，启动偏好持久化会写 |
| `auth.json` | 每个 provider 的 API key / OAuth 凭据（0600） | pi AuthStorage / `/api/auth/*` |
| `mobile-devices.json` | 移动设备 token 的 SHA-256 哈希（0600） | `lib/mobile-device-auth.ts` |
| 项目信任存储 | 哪些项目被信任可加载 `.pi/extensions` 等 | pi `ProjectTrustStore`（与 CLI 共享） |

## 关键请求链路

### 发送一条消息（以 Web 为例）

```
浏览器 ChatInput ──POST /api/agent/[id] { type:"prompt", ... }
      │  proxy.ts 守门（token/Host/认证）
      ▼
POST /api/agent/[id]/route.ts ── rpc-manager.startRpcSession(id, file, cwd)
      │       （wrapper 不存在则新建 AgentSession，复用全局注册表）
      ▼
wrapper.send({type:"prompt", ...}) ── inner.prompt()（fire-and-forget）
      │
      ▼
GET /api/agent/[id]/events  ← SSE 事件流（agent_start/message_*/tool_execution_*/prompt_done...）
      │
      ▼
useAgentSession 状态机 → React 渲染流式气泡
```

关键点：**发送（POST）与接收（SSE）是两条独立连接**。POST 立刻返回，SSE 持续推送事件直到 `prompt_done` / `agent_settled`。

### 打开一个历史会话（只读浏览）

```
GET /api/sessions/[id]  ── session-reader 解析 .jsonl → SessionContext {messages, entryIds, tree, leafId}
GET /api/sessions/[id]/context?leafId=…  ← 切换会话内分支时重新构建上下文
GET /api/agent/[id]/events               ← 若该会话正在运行，恢复 SSE 监听
```

只读浏览**不会**创建 AgentSession；只有发送命令（prompt/fork/compact/...）时才惰性创建。

## 两种“分支”容易混淆

- **Fork**（用户消息上的 Fork 按钮）：调用 `send({type:"fork"})`，SDK 创建一条**新的 `.jsonl` 文件**，通过 header 的 `parentSession` 字段在侧边栏形成树。fork 后 wrapper 立即销毁（见 [session-runtime.md](session-runtime.md#fork-必须立即销毁-wrapper)）。
- **会话内分支**（Continue 按钮 / BranchNavigator）：`send({type:"navigate_tree"})` 在**同一个文件内**切换分支，多个分支叶子共享同一个 `parentId`。切换展示用 `GET /api/sessions/[id]/context?leafId=`。

## 全局状态与热重载

Next.js dev 的热重载会重置模块级变量，所以所有**跨请求共享的服务端状态**都挂在 `globalThis` 上（约定 `__pi*` 前缀）：

| 状态 | 用途 |
| --- | --- |
| `globalThis.__piSessions` | AgentSessionWrapper 注册表（id → wrapper） |
| `globalThis.__piStartLocks` | `startRpcSession` 并发共享的启动 Promise |
| `globalThis.__piRunningListeners` | `/api/agent/running/events` 的订阅者 |
| `globalThis.__piRpcImplementationVersion` | 版本不匹配时销毁旧 wrapper（防止热重载后行为不一致） |
| `globalThis.__piSessionListCache` | 会话列表缓存（TTL 30s） |
| `globalThis.__piAllowedRootsCache` | 文件访问白名单缓存（TTL 5s） |
| `globalThis.__piAdditionalAllowedRoots` | 额外放行的文件根 |
| `globalThis.__piModelsCache` | 模型数据缓存 |
| `globalThis.__piLoginCallbacks` | OAuth 手动 code 回传的短时 token |

> 开发约束：新增跨请求状态时沿用这个模式，并考虑是否需要像 `__piRpcImplementationVersion` 那样做版本失效。

## 服务端重要设计决策

1. **进程内 AgentSession**：不用子进程，避免序列化成本；代价是长时间运行会占用内存，用 10 分钟空闲超时回收。
2. **文件即数据库**：不复制会话数据，直接读 `.jsonl`；列表缓存只做 30s TTL 的展示加速。
3. **HTTP 代理适配**：`lib/http-dispatcher.ts` 在 instrumentation 里替换全局 fetch dispatcher 为 undici `EnvHttpProxyAgent`，使模型请求和 API 请求都遵循 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`，同时禁用 HTTP/2、设 5 分钟空闲超时。
4. **导出 HTML 防爆栈**：`/api/sessions/[id]/export` 用 pi 的导出，再把生成 HTML 里的递归树遍历函数替换成迭代实现，深会话不溢出调用栈。
## 相关文档

- 请求如何被守住：[security.md](security.md)
- AgentSession 怎么活着、怎么死：[session-runtime.md](session-runtime.md)
- 会话文件长什么样：[session-files.md](session-files.md)
- 全部 API 端点：[api.md](api.md)
- 环境变量与数据文件：[configuration.md](configuration.md)
