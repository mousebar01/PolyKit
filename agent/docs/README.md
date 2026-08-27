# polykit-agent 项目文档

本目录是 polykit-agent 的开发者文档。polykit-agent 是 [pi 编程智能体](https://github.com/badlogic/pi-mono) 的本地网页界面（`apps/web`），直接读写本机 `~/.pi/agent/` 下的 pi 数据，与终端里的 pi 看到同一份会话。

> 面向用户的安装、用法和功能说明见仓库根目录的 [README](../README.md)；本目录面向想改代码、想搞清楚内部机制的人。

## 阅读顺序

| 文档 | 内容 | 适合谁 |
| --- | --- | --- |
| [architecture.md](architecture.md) | 整体架构：Web 应用、请求链路、数据流、关键设计决策 | 所有开发者，先读这篇 |
| [development.md](development.md) | 环境要求、开发启动、常用脚本、类型检查/测试、构建与发布 | 刚接手项目的人 |
| [session-runtime.md](session-runtime.md) | AgentSession 生命周期：wrapper、全局注册表、命令协议、空闲回收、SSE 事件流 | 改聊天/会话相关功能的人 |
| [session-files.md](session-files.md) | pi 会话 `.jsonl` 文件格式、解析、fork 与会话内分支、懒加载、压缩摘要、自动命名 | 改会话浏览/导出的人 |
| [security.md](security.md) | 安全模型：请求守门、Host/Origin 校验、认证（Basic / 移动 Bearer）、文件访问白名单、项目信任 | 改任何 API/文件/认证代码前必读 |
| [api.md](api.md) | API 路由参考：按领域分组的全部路由、方法、关键行为 | 前后端联调、加新端点时 |
| [configuration.md](configuration.md) | 环境变量、CLI 参数、`~/.pi/agent/` 下的数据文件（models.json / auth.json / mobile-devices.json） | 部署、排障、扩展配置 |
| [i18n.md](i18n.md) | 国际化机制与新增语言步骤 | 做翻译/本地化的人 |
| [ci.md](ci.md) | CI 现状与建议：现有 Web 检查、可选项 | 维护仓库/提 PR 前想确认门禁的人 |
| [release.md](release.md) | Web 源码运行与发布检查 | 要发版的人 |
| [cheatsheet.md](cheatsheet.md) | CI/CD/版本号/Release 一页纸速查（面向新手） | 还不熟工程流程的人，先看这篇 |

## 一张图

```
Browser
        │  HTTP + SSE（Basic）
        ▼
apps/web  Next.js 16（proxy.ts 守门 → App Router 路由）
        │   ├─ 会话浏览：直接读 ~/.pi/agent/sessions/*.jsonl（SDK SessionManager）
        │   └─ 会话运行：lib/rpc-manager.ts 创建进程内 AgentSession（pi SDK）
        ▼
   pi coding agent SDK（@earendil-works/pi-*）
        ▼
   ~/.pi/agent/（sessions/ models.json auth.json settings.json mobile-devices.json）
```

## 常用入口

- 服务端启动：`apps/web/bin/polykit-agent.js`（发布）、`apps/web/scripts/run-next.mjs`（开发）
- 请求守门中间件：`apps/web/proxy.ts` + `lib/request-security.ts` + `lib/web-auth.ts`
- 会话运行时：`apps/web/lib/rpc-manager.ts`、`lib/session-reader.ts`
- 前端状态机：`apps/web/hooks/useAgentSession.ts`
- 布局与标签页：`apps/web/components/AppShell.tsx`

## 速查

- 开发启动：`npm run dev` → http://127.0.0.1:30001
- 类型检查：`npm run typecheck`；测试：`npm test`；lint：`npm run lint`
- 开发时**不要**跑 `next build`（会污染 `.next/` 破坏 dev server），详见 [development.md](development.md)
- 版本：`apps/web/package.json` 的 `version`
