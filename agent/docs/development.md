# 开发指南

## 环境要求

- Node.js ≥ 22.19.0（`apps/web/bin/node-version.js` 里强制检查；装完跑 `node --version` 确认）
- npm（根仓库用 npm workspaces，由根 `package-lock.json` 管理）

## 首次启动

```bash
npm install
npm run dev        # 开发服务器 → http://127.0.0.1:30001
```

`npm run dev` 实际执行 `node apps/web/scripts/run-next.mjs dev`，以 `POLYKIT_AGENT_PORT`/`PORT` 为端口（默认 30001），并按 `~/.pi/agent/polykit-agent-config.json` 的 `network.mode` 选择监听范围。设置页的“保存并重启”由这个启动器监测请求文件并重启 Next.js 子进程；直接运行 `next dev` 不提供自动重启。

**铁律：开发时不要跑 `next build` / `npm run build`。** 它会写 `.next/`，与正在运行的 dev server 冲突；构建只留给发布流程。类型检查请用 `npm run typecheck`（即 `tsc --noEmit`）。

## 常用命令（根 package.json）

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 启动 Web 开发服务器 |
| `npm run typecheck` | Web 类型检查（`tsc --noEmit`） |
| `npm test` | Web 测试（`node --test`，见下） |
| `npm run lint` | ESLint（根目录 `eslint .`） |
| `npm run build` | Web 生产构建（发布用） |
| `npm run start` | 启动生产服务器（需要先 build） |

## 测试

用的是 Node 内置 test runner（`node --test`），不是 Jest/Vitest。`npm test`（web workspace）只显式跑 4 个文件（见 `apps/web/package.json` 的 `test` 脚本：`app/api/agent/events-route.test.mjs`、`lib/mobile-device-auth.test.mjs`、`lib/i18n/registry.test.mjs`、`lib/polykit-agent-options.test.mjs`），但仓库里散落着约 60 个 `*.test.mjs`（`lib/`、`components/`、`hooks/`、`app/api/` 下），改动相关模块时可以单独跑对应的测试文件：

```bash
node --test apps/web/lib/rpc-manager.test.mjs        # 举例：会话运行时测试
node --test apps/web/lib/request-security.test.mjs   # 举例：安全层测试
```

## 发布流程与 CI

完整说明见 [release.md](release.md) 和 [ci.md](ci.md)。速览：

- **Web**：从源码运行/自建（`npm run dev` 或 `npm run build && npm start`），不发布二进制产物
- **CI**：Web 检查（lint/typecheck/test）由 `.github/workflows/ci.yml` 覆盖

发布时先跑一遍：

```bash
npm run lint -- --quiet && npm run typecheck && npm test
```

## 代码约定

- **服务端跨请求状态挂 `globalThis.__pi*`**，不要用模块级 `let`（热重载会丢）——见 [architecture.md](architecture.md#全局状态与热重载)
- **API 路由做薄**：参数校验 + 安全检查在路由，逻辑在 `lib/`；新端点先看 [security.md](security.md) 的守门规则
- **会话数据永远读 `.jsonl`**，不要造第二份副本
- **i18n**：新文案加进 `lib/i18n/messages/zh-CN.ts` 和 `en.ts`，见 [i18n.md](i18n.md)
- 本仓库的 `AGENTS.md` 是给 AI 助手的说明，改动需与真实代码保持同步
