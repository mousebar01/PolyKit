# 会话文件与会话浏览

polykit-agent 不建自己的数据库，会话就是 `~/.pi/agent/sessions/` 下的 `.jsonl` 文件。所有只读浏览走 `lib/session-reader.ts`，所有「活会话」操作走 `lib/rpc-manager.ts`（见 [session-runtime.md](session-runtime.md)）。

## 文件位置与命名

```
~/.pi/agent/sessions/<编码后的 cwd>/<时间戳>_<uuid>.jsonl
```

- `<编码后的 cwd>`：`lib/session-path.ts` 的 `sessionPathKey()` 按平台规范化（Windows 小写化），同时作为 path↔id 缓存键与 `parentSession` 比较键
- 可用环境变量 `PI_CODING_AGENT_DIR` 指定别的 agent 目录

## JSONL 格式

每行一个 JSON 对象，首行是 header：

```jsonl
{"type":"session","version":3,"id":"<uuid>","timestamp":"...","cwd":"/path","parentSession":"/abs/path/to/parent.jsonl"}
{"type":"model_change","id":"<8hex>","parentId":null,"provider":"zenmux","modelId":"claude-sonnet-4-6","timestamp":"..."}
{"type":"message","id":"<8hex>","parentId":"<8hex>","message":{"role":"user","content":"..."}}
{"type":"message","id":"<8hex>","parentId":"<8hex>","message":{"role":"assistant","content":[...]}}
{"type":"message","id":"<8hex>","parentId":"<8hex>","message":{"role":"toolResult","toolCallId":"...","content":[...]}}
{"type":"compaction","id":"<8hex>","parentId":"<8hex>","summary":"...","firstKeptEntryId":"<8hex>","tokensBefore":N}
{"type":"session_info","id":"...","parentId":"...","name":"用户起的名字"}
```

条目类型：`session`（header）、`message`（role 有 user / assistant / toolResult / custom / bashExecution）、`model_change`、`thinking_level_change`、`compaction`、`branch_summary`、`custom`、`custom_message`、`label`、`session_info`。

关键点：

- `parentId` 串起同文件内的线性链条；**分支**时多个叶子条目共享同一个 `parentId`
- `parentSession`（header）是**跨文件**的父指针，仅用于侧边栏展示（Fork 生成），对聊天内容零影响——删除会话级联改写子文件时可直接 `writeFileSync` 整个文件
- `SessionContext.entryIds[]` 是与 `messages[]` 平行的数组，把每条显示消息映射回 `.jsonl` 条目 id，fork / navigate_tree 都靠它定位

## 读取与解析（lib/session-reader.ts）

- `listAllSessions()`：用 SDK `SessionManager.listAll()` 列出全部会话，转成 `SessionInfo`，并给每条附加 `projectRoot`（worktree 归并到主仓库，见 `lib/worktree.ts`）与 `parentSessionId`。结果缓存 `__piSessionListCache`（TTL 30s）+ in-flight 去重 + generation 失效
- `readSessionHeader()`：只读首行（≤64KB，须 `type: "session"`）
- `getSessionEntries()`：`SessionManager.open` 打开文件拿全部条目
- `buildSessionContext(entries, leafId?, { deferThinking, deferToolResultImages })`：用 SDK `piBuildSessionContext` + `piBuildContextEntries` 按 leaf 选分支，保留 compaction 顺序，返回 `{ messages, entryIds, thinkingLevel, model }`
  - `deferThinking`：thinking 置空并标 `deferred: true`（前端按需从 `/api/sessions/[id]/entries/[entryId]/thinking` 加载）
  - `deferMedia`：用占位文本替换 toolResult 里的 base64 图片（减少客户端传输量）
  - compaction → 转成 custom 消息（details 带 tokensBefore / firstKeptEntryId）；branch_summary → user 消息

## ToolCall 字段归一化（lib/normalize.ts）

pi 存储的 toolCall 块是 `{ type: "toolCall", id, name, arguments }`，而 polykit-agent 的 `ToolCallContent` 用 `{ toolCallId, toolName, input }`。`normalizeToolCalls()` 负责映射，**两处调用**：`session-reader.ts`（文件加载）和 `ChatWindow.handleAgentEvent`（流式事件）。

## 两种分支的对应关系

| | Fork | 会话内分支 |
| --- | --- | --- |
| 入口 | 用户消息上的 Fork 按钮 | Continue 按钮 / BranchNavigator |
| 命令 | `send({type:"fork", entryId, includeEntry})` | `send({type:"navigate_tree", targetId})` |
| 结果 | **新 `.jsonl` 文件**，header `parentSession` 指向原文件 | 同一文件内的新分支，共享 `parentId` |
| 展示 | 侧边栏树（`parentSession` 建树） | BranchNavigator / `GET /api/sessions/[id]/context?leafId=` |
| wrapper | fork 后立即销毁（[原因](session-runtime.md#fork-必须立即销毁-wrapper)） | 在运行中的 wrapper 上切换内存分支 |

`leafId` 标识同文件内一条分支的叶子；`/api/sessions/[id]/context?leafId=` 只读计算 UI 上下文，`navigate_tree` 才真正切换运行中的 AgentSession 内存状态。

## 会话树（/api/sessions/[id] 返回的 tree）

响应里的 `tree` 用 `projectTreeForResponse` 压平：

- 保留根、分支点、叶子
- 单链路径压缩进 `compressedEntryIds`（不展开无分支的中段）
- 深度上限 200

## 懒加载（lib/chat-lazy-load.ts）

长会话不全量渲染：

- `VISIBLE_PAGE_SIZE = 50`，`getVisibleRenderWindow` 返回 `{ startIndex, hasMore }`
- ChatWindow 用顶部哨兵 + IntersectionObserver 翻页，滚动位置保持
- thinking 块按需加载 + LRU 缓存

## 压缩摘要（lib/compaction-summary.ts）

`parseCompactionSummary` 用正则切掉 summary 尾部 `<read-files>/<modified-files>` 元数据，返回 `{ body, readFiles, modifiedFiles }`。

## 自动命名（lib/session-title.ts）

`POST /api/sessions/[id]/auto-name`：

1. 等会话空闲
2. `sanitizeTitleMessages` 只保留 toolCall + 配对的 toolResult
3. 建 shadow agent（工具执行即抛错，防副作用），把尾部 user 消息折叠进标题 prompt
4. 临时运行（90s 超时），`parseGeneratedSessionTitle` 去围栏/JSON/引号，≤80 字符

## 删除与级联（DELETE /api/sessions/[id]）

1. 读首行拿 `parentSession`
2. 把子会话文件 header 的 `parentSession` 级联改写为被删会话的父（孤儿会话归到爷辈）
3. shutdown 对应 RPC 会话 → unlink 文件 → 失效列表缓存

## 导出（GET /api/sessions/[id]/export）

- 经 pi CLI 的 `exportFromFile` 生成 HTML
- `patchExportHtml` 把生成 HTML 里的递归树遍历（sortChildren/mapNodes/markActive）替换成迭代版本——非常深的线性会话不会爆浏览器调用栈
- 响应带 CSP 与 `X-Frame-Options: DENY`
