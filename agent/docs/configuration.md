# 配置参考

## CLI 参数（apps/web/bin/polykit-agent-options.js）

发布版 `polykit-agent` 命令（`bin/polykit-agent.js`）的参数优先级：**CLI > 环境变量**。

| CLI | 短参 | 环境变量 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `--port <n>` | `-p` | `POLYKIT_AGENT_PORT` / `PORT` | `30001` | 监听端口 |
| `--hostname <h>` | `-H` | `POLYKIT_AGENT_HOSTNAME` | 按 `polykit-agent-config.json` 的 `network.mode` | 监听地址；CLI/环境变量只用于显式覆盖 |
| `--no-open` | — | `POLYKIT_AGENT_NO_OPEN` | 关 | 不自动打开浏览器（`1/true/yes/on` 视为启用） |

`--hostname` 不是回环地址时会打印安全警告（有密码 → 提示用 HTTPS/VPN；无密码 → 提示仅限可信网络）。

## 环境变量总表

| 变量 | 作用 | 详见 |
| --- | --- | --- |
| `POLYKIT_AGENT_PORT` / `PORT` | 端口（run-next.mjs 会校验 1-65535） | [development.md](development.md) |
| `POLYKIT_AGENT_HOSTNAME` | 监听地址 | [security.md](security.md) |
| `POLYKIT_AGENT_NO_OPEN` | 禁用自动开浏览器 | — |
| `POLYKIT_AGENT_USERNAME` | 部署环境提供访问账号；与 `POLYKIT_AGENT_PASSWORD_FILE` 一起使用 | [security.md](security.md) |
| `POLYKIT_AGENT_PASSWORD_FILE` | 从权限受限的 Secret 文件读取访问密码 | [security.md](security.md) |
| `POLYKIT_AGENT_CONFIG_PATH` | 自定义 PolyKit Agent 配置文件路径 | [security.md](security.md) |
| `POLYKIT_AGENT_ALLOWED_HOSTS` | 逗号分隔的额外允许 Host（反代场景） | [security.md](security.md) |
| `PI_CODING_AGENT_DIR` | 指定 pi agent 目录（默认 `~/.pi/agent`） | [session-files.md](session-files.md) |
| `PI_MOBILE_DEVICES_PATH` | 移动设备注册文件路径（默认 `~/.pi/agent/mobile-devices.json`） | [security.md](security.md) |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | 服务端模型/API 请求代理 | [architecture.md](architecture.md) |
| `NEXT_PUBLIC_APP_VERSION` / `NEXT_PUBLIC_PI_VERSION` | 构建期注入的版本号（`next.config.ts`） | — |

## ~/.pi/agent/ 数据文件

`getAgentDir()` 返回的目录（`PI_CODING_AGENT_DIR` 可覆盖）下，polykit-agent 与 pi CLI 共享这些文件：

### models.json

模型/provider 配置，`/api/models-config` 直接读写。结构是 pi SDK 的模型配置格式（顶层 `providers`，含 `enabledModels` 等）。要点：

- `enabledModels` 使用 pi 的 `--models` 语法：minimatch glob（对 `provider/modelId` 或裸 `modelId`）、非 glob 模糊匹配、可选 `:thinkingLevel` 后缀
- **永远不要把这些模式当字面字符串比较**：`lib/model-scope.ts` 委托 SDK `resolveModelScopeWithDiagnostics` 解析，polykit-agent 与 TUI 看到同一份可见模型列表
- `startRpcSession` 解析作用域后，把初始模型、thinking 引脚、SDK 原生 `scopedModels` **原子**传给 AgentSession；`GET /api/models` 复用同一 helper 只做选择器数据、`thinkingLevelPins`、`modelScopeWarnings` 展示
- 显式浏览器选择经 `lib/startup-preferences.ts` 持久化为 `settings.json` 里的默认值，**不重放** `set_model`/`set_thinking_level`（避免重复会话条目与扩展事件）；隐式的 enabledModels 回退与 thinking 引脚不持久化

### settings.json

pi 的全局设置（默认模型、默认 thinking 级别、skills 路径、packages 等），由 SDK 的 `SettingsManager` 管理。polykit-agent 的启动偏好持久化会写默认模型/默认 thinking。

### auth.json

每个 provider 的凭据（API key 或 OAuth），0600，pi `AuthStorage` 管理。**每个 provider 只存一份**，删除按凭据类型在锁内比对（见 [security.md](security.md)）。

### sessions/

会话文件，格式见 [session-files.md](session-files.md)。

### mobile-devices.json

移动设备注册表：`{ version: 1, devices: [{ id, name, tokenHash, createdAt }] }`，0600，只存 `sha256(token)`。移动端首次使用「Basic 账号/密码 → 设备 token」的 `POST /api/mobile/devices`；没有二维码或云端配对流程。

### 项目信任存储

pi `ProjectTrustStore` 在 agent 目录下记录哪些项目被信任可加载 `.pi/extensions` 等项目资源；polykit-agent 与 pi CLI 共享，默认不信任（见 [security.md](security.md)）。

## 前端本地存储

| key | 内容 |
| --- | --- |
| `pi-theme` | 亮/暗主题 |
| `pi-locale` | 界面语言（默认 zh-CN） |
| `pi-sound-enabled` | 完成提示音开关 |
| `pi-sidebar-width` | 侧栏宽度记忆 |
| 草稿 | `draft-store` 内存 Map（页面内会话间共享，不落 localStorage 的文本草稿） |
| PWA 缓存 | `polykit-agent-*` 前缀，`sw.js` 版本化（[前端笔记](architecture.md)） |
