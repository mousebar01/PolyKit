"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, Check, CircleAlert, Plug, RefreshCw, Server, Wrench } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { agentApiPath } from "@/lib/agent-client";
import type { McpConfigView, McpServerView } from "@/lib/mcp-config";

interface McpResponse extends McpConfigView {
  runtime: { running: boolean; summary?: string };
  error?: string;
}

interface Props {
  cwd: string;
  sessionId: string | null;
  onAgentConfigure: (serverName?: string) => void;
  onReloaded: () => void;
}

function shortPath(value: string): string {
  return value.replace(/^\/(?:Users|home)\/[^/]+/, "~");
}

function transportLabel(server: McpServerView): string {
  if (server.transport === "stdio") return server.command ? `stdio · ${server.command}` : "stdio";
  if (server.transport === "http") return server.urlHost ? `HTTP · ${server.urlHost}` : "HTTP";
  if (server.transport === "socket") return "Socket";
  return "Unknown";
}

function Toggle({ enabled, busy, label, onChange }: {
  enabled: boolean;
  busy: boolean;
  label: string;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      className={`mcp-toggle${enabled ? " is-on" : ""}`}
      aria-pressed={enabled}
      aria-label={label}
      title={label}
      disabled={busy}
      onClick={onChange}
    >
      <span />
    </button>
  );
}

export function McpConfig({ cwd, sessionId, onAgentConfigure, onReloaded }: Props) {
  const { t, locale } = useI18n();
  const zh = locale === "zh-CN";
  const [data, setData] = useState<McpResponse | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const query = new URLSearchParams({ cwd });
      if (sessionId) query.set("sessionId", sessionId);
      const response = await fetch(`${agentApiPath("/mcp")}?${query.toString()}`, { cache: "no-store" });
      const result = await response.json() as McpResponse;
      if (!response.ok || result.error) throw new Error(result.error ?? `HTTP ${response.status}`);
      setData(result);
      setSelectedName((current) => current && result.servers.some((server) => server.name === current)
        ? current
        : result.servers[0]?.name ?? null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [cwd, sessionId]);

  useEffect(() => { void load(); }, [load]);

  const selected = useMemo(
    () => data?.servers.find((server) => server.name === selectedName) ?? null,
    [data, selectedName],
  );

  const setEnabled = async (server: McpServerView) => {
    setBusyName(server.name);
    setError(null);
    try {
      const response = await fetch(agentApiPath("/mcp"), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cwd, sessionId, server: server.name, enabled: !server.enabled }),
      });
      const result = await response.json() as McpResponse;
      if (!response.ok || result.error) throw new Error(result.error ?? `HTTP ${response.status}`);
      setData(result);
      onReloaded();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyName(null);
    }
  };

  if (loading && !data) {
    return <div className="mcp-empty">{zh ? "正在读取 MCP 配置…" : "Loading MCP configuration…"}</div>;
  }

  if (!data) {
    return (
      <div className="mcp-empty">
        <CircleAlert size={20} />
        <span>{error ?? (zh ? "无法读取 MCP 配置" : "Unable to load MCP configuration")}</span>
        <button type="button" className="mcp-button" onClick={() => void load()}>{t("sidebar.refresh")}</button>
      </div>
    );
  }

  const adapterReady = data.adapter.installed && data.adapter.enabled;
  return (
    <div className="mcp-config">
      <header className="mcp-header">
        <div>
          <h2>MCP</h2>
          <p>{zh ? "管理 Pi 使用的 MCP Server。安装与环境修复交给 Agent。" : "Manage MCP servers used by Pi. Let the agent handle installation and environment fixes."}</p>
        </div>
        <button type="button" className="mcp-icon-button" onClick={() => void load()} title={t("sidebar.refresh")} aria-label={t("sidebar.refresh")} disabled={loading}>
          <RefreshCw size={15} className={loading ? "is-spinning" : undefined} />
        </button>
      </header>

      <div className={`mcp-adapter-status${adapterReady ? " is-ready" : " is-warning"}`}>
        <span className="mcp-status-icon">{adapterReady ? <Check size={14} /> : <CircleAlert size={14} />}</span>
        <div>
          <strong>{adapterReady ? (zh ? "MCP Adapter 已就绪" : "MCP adapter ready") : (zh ? "MCP Adapter 尚未就绪" : "MCP adapter not ready")}</strong>
          <span>
            {data.adapter.installed
              ? `${data.adapter.version ? `v${data.adapter.version}` : ""}${data.adapter.enabled ? "" : (zh ? " · 未启用" : " · disabled")}`
              : (zh ? "未安装 pi-mcp-adapter" : "pi-mcp-adapter is not installed")}
            {data.runtime.summary ? ` · ${data.runtime.summary}` : ""}
          </span>
        </div>
        <button type="button" className="mcp-agent-link" onClick={() => onAgentConfigure()}>
          <Bot size={15} />
          {zh ? "让 Agent 配置" : "Configure with agent"}
        </button>
      </div>

      {error && <div className="mcp-error"><CircleAlert size={14} />{error}</div>}
      {data.warnings.length > 0 && <div className="mcp-error"><CircleAlert size={14} />{data.warnings[0]}</div>}

      {data.servers.length === 0 ? (
        <div className="mcp-empty mcp-empty-servers">
          <Plug size={22} />
          <strong>{zh ? "还没有 MCP Server" : "No MCP servers yet"}</strong>
          <span>{zh ? `项目配置将写入 ${shortPath(data.projectOverridePath)}` : `Project configuration will be written to ${shortPath(data.projectOverridePath)}`}</span>
          <button type="button" className="mcp-button is-primary" onClick={() => onAgentConfigure()}><Bot size={15} />{zh ? "让 Agent 安装并配置" : "Ask agent to install and configure"}</button>
        </div>
      ) : (
        <div className="mcp-workspace">
          <div className="mcp-server-list" role="listbox" aria-label={zh ? "MCP Server" : "MCP servers"}>
            {data.servers.map((server) => (
              <button
                type="button"
                role="option"
                aria-selected={server.name === selectedName}
                className={`mcp-server-item${server.name === selectedName ? " is-selected" : ""}`}
                key={server.name}
                onClick={() => setSelectedName(server.name)}
              >
                <span className={`mcp-server-dot${server.enabled ? " is-enabled" : ""}`} />
                <span className="mcp-server-copy">
                  <strong>{server.name}</strong>
                  <span>{transportLabel(server)}</span>
                </span>
                <span className="mcp-scope">{server.scope === "project" ? (zh ? "项目" : "Project") : (zh ? "全局" : "Global")}</span>
              </button>
            ))}
          </div>

          {selected && (
            <section className="mcp-detail">
              <div className="mcp-detail-title">
                <div className="mcp-server-mark"><Server size={17} /></div>
                <div><h3>{selected.name}</h3><span>{transportLabel(selected)}</span></div>
                <Toggle
                  enabled={selected.enabled}
                  busy={busyName === selected.name}
                  label={selected.enabled ? (zh ? "禁用 Server" : "Disable server") : (zh ? "启用 Server" : "Enable server")}
                  onChange={() => void setEnabled(selected)}
                />
              </div>

              <dl className="mcp-facts">
                <div><dt>{zh ? "状态" : "Status"}</dt><dd className={selected.enabled ? "is-positive" : ""}>{selected.enabled ? (zh ? "已启用" : "Enabled") : (zh ? "已禁用" : "Disabled")}</dd></div>
                <div><dt>{zh ? "生命周期" : "Lifecycle"}</dt><dd>{selected.lifecycle}</dd></div>
                <div><dt>{zh ? "认证" : "Authentication"}</dt><dd>{selected.auth}</dd></div>
                <div><dt>{zh ? "敏感配置" : "Private config"}</dt><dd>{[selected.hasEnv ? "env" : "", selected.hasHeaders ? "headers" : ""].filter(Boolean).join(" · ") || (zh ? "无" : "None")}</dd></div>
              </dl>

              <div className="mcp-source-block">
                <span>{zh ? "最终来源" : "Effective source"}</span>
                <strong>{selected.sourceLabel}</strong>
                <code title={selected.source}>{shortPath(selected.source)}</code>
                {selected.sourceCount > 1 && <small>{zh ? `由 ${selected.sourceCount} 层配置合并` : `Merged from ${selected.sourceCount} configuration layers`}</small>}
              </div>

              <p className="mcp-ownership-note">
                {selected.sourceEditable
                  ? (zh ? "这是 Pi 管理的配置。开关仍只在当前项目写入覆盖。" : "This source is Pi-owned. The toggle still writes only a project override.")
                  : (zh ? "此外部配置保持只读；开关通过当前项目的 Pi 覆盖生效。" : "This external source stays read-only; the toggle uses a Pi project override.")}
              </p>

              <div className="mcp-detail-actions">
                <button type="button" className="mcp-button" onClick={() => onAgentConfigure(selected.name)}><Wrench size={14} />{zh ? "让 Agent 修复或修改" : "Ask agent to fix or edit"}</button>
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
