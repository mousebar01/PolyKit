# CI / CD / 版本号 / Release 一页纸速查

> 面向还不太熟悉工程流程的人。所有概念都对应到你仓库里真实存在的文件。

## 4 个词，一句话

| 词 | 全称 | 一句话 |
| --- | --- | --- |
| **CI** | Continuous Integration 持续集成 | 每次 push / 开 PR，自动跑检查（lint、类型检查、测试），红了就拦住 |
| **CD** | Continuous Delivery/Deployment 持续交付/部署 | 检查通过后，自动把产物发布出去（本仓库暂无自动发布产物） |
| **Version** | 版本号 | 语义化 `主.次.补丁`，比如 `1.0.0`：补丁号 +1 = 修 bug |
| **Release** | 发布 | GitHub 上「某个版本的一坨交付物」；本仓库 Web 无 Release 产物 |

```
你改代码 ──push──▶ CI 自动检查 ──绿──▶ 合并
                      │红                    │
                      └─ 回去改
```

## 本仓库对照

| 文件 | 干什么 | 什么时候触发 |
| --- | --- | --- |
| `.github/workflows/ci.yml` | Web 检查：`npm ci` → lint → typecheck → test | push 任意分支 / 开 PR |
Web 从源码运行（`npm install` → `npm run dev` 或 `npm run build && npm start`），不发布安装包或压缩包。

## 版本号怎么动

| 渠道 | 包/产物 | 版本怎么变 | 谁管 |
| --- | --- | --- | --- |
| Web | 源码（无发布产物） | 跟代码提交演进（当前 `1.0.0`） | 你手动维护 `apps/web/package.json` |

## 常见操作

**push 之后在哪看结果？**
GitHub 仓库 → **Actions** 标签页 → 最新一次 run → 绿色 ✓ / 红色 ✗。

**红了怎么办？**
点进红色 run → 看是哪个 job/step 报错 → 按日志里的提示改代码 → 再 push。不用猜，日志会精确到命令。

**提代码前本地自查：**
```bash
npm run lint -- --quiet && npm run typecheck && npm test
```

## 容易混的三对

- **CI ≠ 测试**：测试只是 CI 里的一步；CI 是「自动跑所有检查」这个机制
- **tag ≠ Release**：tag 是 git 里的一个标记；Release 是 GitHub 上挂产物的地方。本仓库 Web 没有 Release 产物，`v*` tag 只做版本标记
- **patch ≠ minor ≠ major**：`1.0.0 → 1.0.1` 是 patch（修 bug）；`1.0 → 1.1` 是 minor（加功能）；`1 → 2` 是 major（破坏性变更）。习惯上按这个语义递增

## 想深入

- 本仓库 CI 细节：[ci.md](ci.md)、[release.md](release.md)
- 官方文档：GitHub Actions（https://docs.github.com/actions）、语义化版本（https://semver.org/lang/zh-CN/）
