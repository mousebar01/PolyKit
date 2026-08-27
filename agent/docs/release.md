# 发布流程

polykit-agent 目前只有 Web 从源码运行/自建这一种发布方式，不发布到 npm，也不在 GitHub Release 发布安装包。

版本号策略：`apps/web` 版本号（当前 `1.0.0`）跟随代码演进。

```
Web（源码运行 / 自建）
        ▲
   git clone + npm install
```

## Web：从源码运行

Web 不产出安装包或压缩包，直接拉源码跑：

```bash
cd agent
npm install
npm run dev        # 开发模式 → http://127.0.0.1:30001
npm run build && npm start   # 生产模式
```

需要给局域网或私有组网使用时，在设置中选择访问范围并点击“保存并重启”。服务器部署使用 HTTPS，并通过服务管理器注入 Secret 文件：

```bash
POLYKIT_AGENT_USERNAME=operator POLYKIT_AGENT_PASSWORD_FILE=/run/secrets/polykit-agent-password npm start
```

> 打 `v*` 标签目前不触发任何发布工作流（`release.yml` 已移除）；tag 仅作版本标记，将来若要自动发 Release 需另配工作流。

## 发布检查清单

- [ ] `npm run lint -- --quiet && npm run typecheck && npm test` 全绿
- [ ] Web：`npm run build && npm start` 本地验证可访问
- [ ] （可选）更新 README 的功能描述 / docs 里过时的内容

## 相关

- CI 现状与建议：[ci.md](ci.md)
