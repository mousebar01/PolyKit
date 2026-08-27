# 安全模型

polykit-agent 能调用高权限的智能体并执行受控工作区操作，所以一旦开放到网络，安全边界就变得关键。默认配置仅本机监听；用户设置访问密码后，才应切换到局域网或可信虚拟网络。整个模型分三层，按请求经过顺序：

```
proxy.ts（Next 中间件）
  ① Host / Origin 校验（防 DNS rebinding / 跨站）
  ② Basic Auth / 移动 Bearer 认证
        ▼
业务路由内
  ③ 文件访问白名单 + 项目信任（按需）
```

## Host / Origin 校验

实现：`apps/web/lib/request-security.ts`。

目标：防 **DNS rebinding**（恶意域名解析到 127.0.0.1 后窃取本地服务）和**跨站请求**。

**Host 校验 `isApiRequestHostAllowed`**：

- 从 `Host` 头解析主机名（拒绝带空格/`@`/反斜杠/用户信息/非根路径的畸形值）
- 回环名（`localhost`、`*.localhost`）或**任意 IP 字面量**直接放行——浏览器会把字面 IP 留在 Host 头里，天然免疫 DNS rebinding
- 其他主机名必须与配置集合完全相等：`POLYKIT_AGENT_HOSTNAME` + `POLYKIT_AGENT_ALLOWED_HOSTS`（逗号分隔的精确主机名）。反向代理换了外部主机名时必须配置 `POLYKIT_AGENT_ALLOWED_HOSTS`

**Origin 校验 `isApiRequestOriginAllowed`**：

- `sec-fetch-site: cross-site` 直接拒绝
- 有 `Origin` 头时，必须与请求自身 origin 同源
- 只有带 `origin`/`sec-fetch-site` 头时才检查（保留非浏览器客户端的兼容）
- 例外：用户主动导航（`sec-fetch-mode: navigate` + `dest: document` + `user: ?1`）到 `/api/sessions/<id>/export` 时豁免 Origin 检查（这是从地址栏打开导出的 HTML）

非 API 请求（页面、静态资源）只做 Host 校验，不做 Origin 校验。

## 认证

实现：`apps/web/lib/web-auth.ts`。

设置访问密码后，服务使用配置文件中的 HTTP Basic Auth；首次启动的配置没有随机密码，默认仅本机监听。部署时可由 `POLYKIT_AGENT_USERNAME` 与 `POLYKIT_AGENT_PASSWORD_FILE` 提供账号和密码：

- 用户名保存为配置字段，默认是 `pi`；部署环境中的 `POLYKIT_AGENT_USERNAME` 会覆盖配置中的账号
- 本地配置以 `0600` 权限保存访问密码，设置页可以回显密码；不要把配置文件放入共享目录、镜像或公开备份
- 凭据解析：base64 严格往返校验 + UTF-8 fatal 解码，取第一个 `:` 分割
- 用户名与密码各自 SHA-256 后 `timingSafeEqual` 比较（定长哈希消除长度侧信道）
- 401 响应带 `WWW-Authenticate: Basic realm="polykit-agent"`

在 Basic 之上还有移动端 Bearer：

- API 请求可被有效的移动设备 token（`Authorization: Bearer pim_...`）放行，页面仍必须 Basic
- `/api/mobile/devices`（设备管理）**只认 Basic 不认 Bearer**——设备 token 不能用来添加/吊销其他设备
- `/api/mobile/discovery`（局域网发现）返回服务元数据，但不授予访问权限；移动端首次连接仍必须提交 Basic 账号和密码

⚠️ Basic Auth 不加密传输，明文 HTTP 暴露到公网等于泄露密码。服务器部署应使用 HTTPS；在私有网络中也应只允许可信局域网或可信 VPN/组网工具访问。

## 工作区路径白名单

实现：`apps/web/lib/file-access.ts` + `path-security.ts`。

Agent UI 不提供项目文件浏览、文件预览或文件标签页。服务端仍对工作区、插件、技能、MCP、模型和 worktree 操作执行白名单校验：

**白名单来源**（`getAllowedFileRoots()`，5s TTL 缓存）：

1. 所有会话的 `cwd`
2. 每个 cwd 解析出的 `projectRoot`（worktree 归并到主仓库根，见 [architecture.md](architecture.md) 与 `lib/worktree.ts`）
3. `~/pi-cwd-YYYYMMDD` 目录（`/api/default-cwd` 创建）
4. `allowFileRoot()` 显式追加的根（`/api/cwd/validate`、`/api/worktrees` 等会追加）

**判定**：

- 词法判定 `isFilePathAllowed`：resolve 后前缀匹配（Windows 规则大小写不敏感）
- 对已存在路径再做 `isExistingFilePathAllowed`：目标与每个根都 `realpathSync` 后再比，**防符号链接逃逸**

## 项目信任

实现：`apps/web/lib/project-trust.ts`。

为什么需要：pi 的扩展系统允许项目 `.pi/extensions`、项目 `.pi/settings.json` 扩展、`.agents/skills` 在 agent 启动时**执行代码**。如果没有门控，在 polykit-agent 里打开一个仓库就可能执行仓库里的代码（issue #236）。

机制：

- `getProjectTrustStatus(cwd)`：SDK 的 `hasTrustRequiringProjectResources(cwd)` 判断该项目是否有需要信任的资源；没有 → 直接视为已信任
- 有 → 查与 pi CLI **共享**的 `ProjectTrustStore(agentDir)`，**默认不信任**，只有 `trusted === true` 才放行
- `projectTrustReloadOptions()` 把扩展加载门控在 `resolveProjectTrust` 之后，未信任时这些资源保持休眠
- 前端：AppShell 轮询 `/api/project-trust`，未信任的项目弹 `ProjectTrustDialog`；信任成功后销毁该 cwd 的 RPC 会话并刷新模型缓存（`/api/project-trust` POST 的行为）

## 敏感文件权限

| 文件 | 权限 | 说明 |
| --- | --- | --- |
| `~/.pi/agent/mobile-devices.json` | 0600 | 只存 token 的 SHA-256 哈希，绝不落明文 |
| `~/.pi/agent/auth.json` | 0600 | API key / OAuth 凭据，由 pi AuthStorage 管理 |

## 移动端设备 token

实现：`apps/web/lib/mobile-device-auth.ts`。

- token 形如 `pim_` + 32 字节 base64url，只在首次注册成功那一刻返回给客户端
- 服务端存 `sha256(token)` 的 hex；校验时对提交的 token 哈希后 `timingSafeEqual` 比较
- 读写用 `proper-lockfile` 串行化（与 pi auth storage 同一把锁的文件操作模式），`ensureFile` 建 0600
- 首次移动端连接只使用 Web Basic Auth；服务端成功创建设备后返回独立 Bearer token，之后移动端不再发送共享密码

## 给开发者的清单

改任何 API 端点前确认：

- [ ] 端点是否要纳入 Host/Origin 校验（API 默认纳入，豁免要有理由）
- [ ] 认证策略：Basic / 移动 Bearer / 两者；新增无认证端点必须有明确理由
- [ ] 涉及文件路径时过白名单 + realpath；涉及 git 时校验仓库归属
- [ ] 涉及项目资源时考虑项目信任门控
- [ ] API key、移动 token 绝不返回明文；访问密码仅由已认证的设置端点按用户选择返回
