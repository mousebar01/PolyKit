# AgentSession 运行时（lib/rpc-manager.ts）

`rpc-manager.ts` 是 polykit-agent 的「活会话」核心：它把 pi SDK 的 `AgentSessionLike` 包成 `AgentSessionWrapper`，挂在全局注册表里，对外提供统一的 `send()` 命令协议，并负责生命周期、空闲回收和 fork 的特殊处理。

```
POST /api/agent/new ──▶ startRpcSession(id, file, cwd, opts)
                             │
                    ┌────────┴────────┐
                    │ __piSessions    │ 已有存活 wrapper？→ 直接复用
                    │ (id → wrapper)  │ 否则 → 新建 AgentSession（共享启动锁）
                    └────────┬────────┘
                             ▼
                    AgentSessionWrapper
                      ├─ inner: AgentSessionLike（pi SDK 对象）
                      ├─ promptRunning / isStreaming / isCompacting / isBashRunning
                      ├─ idleTimer（10 分钟空闲超时）
                      └─ send({type, ...}) → inner 对应方法
```

## Wrapper 生命周期

### startRpcSession(id, file, cwd, { toolNames, initialModel, thinkingLevel })

1. 注册表里已有存活的 wrapper → 直接返回（同一会话只有一个运行实例）
2. 并发调用共享同一个 start Promise（`globalThis.__piStartLocks`），避免重复创建
3. 模型初始化：`resolveVisibleModels` + `selectInitialModelScope` 解析 enabledModels 作用域，选中初始模型/thinking 后**原子**传给 `createAgentSessionFromServices`（有历史消息时只传 scopedModels，不重放 setter）
4. 工具初始化（见下）
5. `persistExplicitStartupPreferences` 把显式选择落盘为默认值（不重放 setter，避免产生多余的会话条目和扩展事件）
6. `beginExtensionBinding` 绑定项目扩展；返回 pi 真实 session id，`cacheSessionPath`

### 工具预设

- `toolNames` 通过 `POST /api/agent/new` 传入
- `toolNames === []`（全关）：创建时传空 allow-list，并 `setForceEmptySystemPrompt(true)`——因为 SDK 即使没有工具也会生成非空 system prompt，polykit-agent 在启动/扩展绑定/reload/set_tools 后反复强制 `agent.state.systemPrompt = ""`
- `toolNames` 非空：创建时**不传** allow-list（避免滤掉扩展提供的工具），创建后 `setActiveToolsByName(withExtensionTools())` = 请求项 + 全部非内置工具。内置工具集合 `CODING_TOOL_NAMES = [read, bash, edit, write, grep, find, ls]`
- 预设定义在 `lib/tool-presets.ts`：`PRESET_NONE=[]`、`DEFAULT=[read,bash,edit,write]`、`FULL=[bash,read,edit,write,grep,find,ls]`

### 运行状态与空闲回收

- `isRunning()` = alive && (promptRunning || isStreaming || isCompacting || isBashRunning)
- 订阅 inner 事件：
  - `agent_end` → 失效会话列表缓存
  - `IDLE_RESET_EVENT_TYPES`（agent_end / agent_settled / auto_compaction_end / compaction_end）→ 重置空闲定时器
  - `RUNNING_STATE_EVENT_TYPES`（agent_start / agent_end / agent_settled / compaction_* / auto_compaction_*）→ 通知 `/api/agent/running` 的订阅者
- **空闲超时 10 分钟**：运行中持续续期，空闲到期 `shutdown()`
- `shutdown()` 幂等：先向扩展 emit `session_shutdown`，再停止；`destroy()` 清理 timer、abortBash、取消挂起的扩展 UI、`inner.dispose()`、从注册表删除

### Fork 必须立即销毁 wrapper ⚠️

`AgentSession.fork()` **会原地改写 inner 状态**——fork 之后 `inner.sessionId` 变成新会话的 id。如果 wrapper 还留在注册表的旧 id 下：

- 下一次请求拿到的是已经被 fork 过的状态
- 连续 fork 会产生损坏的 `parentSession` 链

**Fix**：`send("fork")` 捕获 `newSessionId` 后，`await this.shutdown()` **立即销毁 wrapper**，再从注册表删除；下次请求从原文件重新加载干净的 AgentSession。

## send() 命令协议

`POST /api/agent/[id]` body `{ type, ... }`，wrapper 分发到 inner 对应方法，返回 `{ success, data }`。

命令全集（与 `lib/agent-client.ts` 的 `sendAgentCommand` 对应）：

| 类型 | 作用 |
| --- | --- |
| `prompt` | 发消息（fire-and-forget，结束后 emit `prompt_done`，出错先 `prompt_error`） |
| `steer` / `follow_up` | 中途改写/追问 |
| `abort` / `abort_bash` | 中止 |
| `bash` | 执行 bash（结果写临时文件，经 `/api/agent/[id]/bash-output` 读取） |
| `get_state` / `get_session_stats` / `get_last_assistant_text` | 状态查询 |
| `set_model` / `set_thinking_level` / `set_tools` / `get_tools` / `get_commands` | 运行时配置 |
| `fork` / `navigate_tree` | 创建新会话 / 会话内切换分支 |
| `compact` / `abort_compaction` | 压缩 |
| `reload` | 重载（插件/技能变更后） |
| `set_session_name` / `set_auto_compaction` / `set_auto_retry` / `clear_queue` | 会话级配置 |
| `extension_ui_response` / `extension_ui_input` | 响应扩展 UI 请求 |

## SSE 事件流协议

浏览器用 EventSource 连接。服务端先发 `connected`，然后转发 pi 的事件，中间做**瘦身**：

- 剔除 `turn_start` / `turn_end` / `tool_execution_update`（对 UI 无用）
- `message_update` 剥离 `assistantMessageEvent` 字段
- `agent_end` 只保留 `{ type: "agent_end" }`
- 每 30s 发一条注释帧心跳

**事件名全集**：

```
connected
agent_start / agent_end / agent_settled
prompt_done / prompt_error
message_start / message_update / message_end
tool_execution_start / tool_execution_end
queue_update
auto_retry_start / auto_retry_end
compaction_start / compaction_end        ← 新版本 pi
auto_compaction_start / auto_compaction_end ← 旧版本 pi（兼容，前端两个都认）
extension_ui_request（method: select/confirm/input/editor/notify/setStatus/setWidget/setTitle/set_editor_text/custom）
extension_error
running（/api/agent/running/events 专用）
```

### 前端如何消费（hooks/useAgentSession.ts）

核心状态机，几个关键点：

- **SSE 是主通道**：每次发送 prompt 前 `ensureEventsConnected` 复用/新建连接；`prompt_done` 结算当前 UI 阶段，但连接保持 30 秒宽限窗口供下一条 prompt 复用；`agent_settled` 结束扩展注入的运行并开启新一轮宽限
- **不要看到 `agent_end` 就关流**：重试、压缩、扩展排队可能继续同一个逻辑 prompt
- **运行轮询 + 对账（reconciliation）**：运行期间周期性 `GET /api/agent/[id]`，并在 `visibilitychange`（回到前台）/`online` 时对账；服务端空闲就走与流事件相同的结算路径，补偿后台标签页漏掉的终结事件
- **单调 runId**：每次发送 prompt 自增 `promptRunIdRef`，所有异步回调（对账响应、宽限检查）先比对 runId，旧的直接丢弃——防止旧 run 的迟到响应复活流式气泡
- **页面刷新恢复**：挂载时 `GET /api/agent/[id]` 拿到 `state.running && isStreaming` 就自动重连 SSE 并等待本次 prompt 结算；`thinkingLevel`、`isCompacting` 也从该响应同步
- **压缩事件**：新老两套事件名都处理，保持 `isCompacting` 同步；手动压缩是阻塞 POST，按钮在响应返回前保持禁用

## 运行中会话的广播

- `GET /api/agent/running` → `{ runningSessionIds }`（侧边栏 2.5s 轮询，后台标签页暂停）
- `GET /api/agent/running/events` → SSE，先订阅再发快照，之后每次运行状态变化推送 `{ type: "running", runningSessionIds }`

## 相关文档

- 会话文件与只读浏览：[session-files.md](session-files.md)
- API 端点细节：[api.md](api.md)
- 前端的完整状态机说明在 `hooks/useAgentSession.ts` 源码注释里
