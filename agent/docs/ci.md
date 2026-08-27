# CI（GitHub Actions）

仓库有一个工作流：`.github/workflows/ci.yml`（Web 静态检查）。仓库不发布桌面安装包或 Web 服务端压缩包，因此没有构建/发布工作流。

## 现有工作流

### Web checks（.github/workflows/ci.yml）

push 任意分支 / 开 PR 时跑：

```yaml
steps:
  - run: npm ci
  - run: npm run lint -- --quiet
  - run: npm run typecheck
  - run: npm test
```

本地等价命令：

```bash
npm run lint -- --quiet && npm run typecheck && npm test
```

## 还没做 / 可选增强

| 项 | 状态 | 建议 |
| --- | --- | --- |
| Web 静态检查 CI | ✅ 已加（ci.yml） | 若不需要可随时删掉 |
| 覆盖度报告 | ❌ | 需要时可加 `node --test --experimental-test-coverage` 并上传 artifact |
| 依赖自动更新 | ❌ | 可在仓库 Settings 开启 Dependabot（npm 依赖） |
| 分支保护 | ❌ | 在 GitHub 仓库 Settings → Branches 里对 `main` 开启「PR 必须通过 CI」 |

## 本地先自查

开发时提交前建议跑一遍 CI 会跑的检查（很快）：

```bash
npm run lint -- --quiet && npm run typecheck && npm test
```

发布前再跑 `npm run build`（见 [release.md](release.md)）。
