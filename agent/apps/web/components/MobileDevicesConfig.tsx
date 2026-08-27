"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, Eye, EyeOff, KeyRound, Pencil, Smartphone, Trash2, X } from "lucide-react";

interface MobileDeviceInfo {
  id: string;
  name: string;
  createdAt: string;
}

interface SecurityStatus {
  configured: boolean;
  source: "config" | "environment" | "none";
  passwordConfigured: boolean;
  passwordSource: "config" | "environment" | "none";
  password: string | null;
  username: string;
  networkMode: "local" | "lan";
}

export function MobileDevicesConfig() {
  const [devices, setDevices] = useState<MobileDeviceInfo[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [securityStatus, setSecurityStatus] = useState<SecurityStatus | null>(null);
  const [username, setUsername] = useState("pi");
  const [networkMode, setNetworkMode] = useState<"local" | "lan">("local");
  const [savingSettings, setSavingSettings] = useState(false);
  const [restartPending, setRestartPending] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);
  const [customPassword, setCustomPassword] = useState("");
  const [customPasswordConfirm, setCustomPasswordConfirm] = useState("");
  const [customPasswordOpen, setCustomPasswordOpen] = useState(false);
  const [showCustomPassword, setShowCustomPassword] = useState(false);
  const [showCustomPasswordConfirm, setShowCustomPasswordConfirm] = useState(false);
  const [showSavedPassword, setShowSavedPassword] = useState(false);

  const loadDevices = useCallback(async () => {
    const response = await fetch("/api/mobile/devices", { cache: "no-store" });
    const body = await response.json() as { devices?: MobileDeviceInfo[]; error?: string };
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    setDevices(body.devices ?? []);
  }, []);

  const loadSecurityStatus = useCallback(async () => {
    const response = await fetch("/api/config/security", { cache: "no-store" });
    const body = await response.json() as SecurityStatus & { error?: string };
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    setSecurityStatus(body);
    setUsername(body.username);
    setNetworkMode(body.networkMode);
  }, []);

  useEffect(() => {
    void Promise.all([loadDevices(), loadSecurityStatus()]).catch((cause) => setError(String(cause)));
  }, [loadDevices, loadSecurityStatus]);

  const rename = async (id: string) => {
    const response = await fetch("/api/mobile/devices", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, name: editingName }),
    });
    if (!response.ok) return setError("无法重命名设备");
    setEditingId(null);
    await loadDevices();
  };

  const revoke = async (device: MobileDeviceInfo) => {
    if (!window.confirm(`吊销“${device.name}”的访问权限？`)) return;
    const response = await fetch(`/api/mobile/devices?id=${encodeURIComponent(device.id)}`, { method: "DELETE" });
    if (!response.ok) return setError("无法吊销设备");
    await loadDevices();
  };

  const setCustomAccessPassword = async () => {
    if (securityStatus?.passwordSource === "environment" || savingPassword) return;
    if (customPassword.length < 12) {
      setError("访问密码至少需要 12 个字符。");
      return;
    }
    if (customPassword !== customPasswordConfirm) {
      setError("两次输入的访问密码不一致。");
      return;
    }
    setSavingPassword(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/config/security", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: customPassword }),
      });
      const body = await response.json() as { error?: string };
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      setCustomPassword("");
      setCustomPasswordConfirm("");
      setCustomPasswordOpen(false);
      setShowCustomPassword(false);
      setShowCustomPasswordConfirm(false);
      setShowSavedPassword(false);
      setNotice("访问密码已保存；手机下次连接时使用新密码。");
      await loadSecurityStatus();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingPassword(false);
    }
  };

  const waitForRestart = () => {
    const startedAt = Date.now();
    let sawUnavailable = false;
    const probe = async () => {
      try {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 800);
        const response = await fetch(`/api/health?restart=${Date.now()}`, { cache: "no-store", signal: controller.signal });
        window.clearTimeout(timeout);
        if (sawUnavailable && response.ok) {
          window.location.reload();
          return;
        }
      } catch {
        sawUnavailable = true;
      }
      if (Date.now() - startedAt < 20_000) {
        window.setTimeout(() => void probe(), 500);
      } else {
        setRestartPending(false);
        setNotice("PolyKit Agent 已保存配置，但自动重启未完成，请手动重启后刷新页面。");
      }
    };
    window.setTimeout(() => void probe(), 1_000);
  };

  const networkModeChanged = Boolean(securityStatus && networkMode !== securityStatus.networkMode);
  const settingsDirty = Boolean(securityStatus && (
    networkModeChanged
    || (securityStatus.source !== "environment" && username !== securityStatus.username)
  ));

  const saveAccessSettings = async () => {
    if (savingSettings || restartPending || !settingsDirty) return;
    setSavingSettings(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/config/security", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(securityStatus?.source === "environment" ? {} : { username }),
          networkMode,
        }),
      });
      const body = await response.json() as SecurityStatus & { error?: string };
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      const modeChanged = Boolean(securityStatus && body.networkMode !== securityStatus.networkMode);
      setSecurityStatus(body);
      setUsername(body.username);
      setNetworkMode(body.networkMode);
      if (modeChanged) {
        const restartResponse = await fetch("/api/config/security/restart", { method: "POST" });
        const restartBody = await restartResponse.json() as { error?: string };
        if (!restartResponse.ok) throw new Error(restartBody.error || `HTTP ${restartResponse.status}`);
        setRestartPending(true);
        setNotice("访问范围已保存，PolyKit Agent 正在重启；服务恢复后页面会自动刷新。");
        waitForRestart();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingSettings(false);
    }
  };

  return (
    <div className="mobile-devices-config">
      <div className="settings-section-heading"><h2>移动设备</h2><p>使用访问账号和密码连接 PolyKit Agent Mobile，并管理已授权的设备。</p></div>
      <section className="mobile-access-card" aria-label="PolyKit Agent 连接设置">
        <div className="mobile-access-card-heading">
          <span className="mobile-access-card-icon"><KeyRound size={16} /></span>
          <div><strong>连接设置</strong><small>配置手机连接 PolyKit Agent 所需的账号、密码和网络范围</small></div>
        </div>
        <p className="mobile-access-card-copy">首次连接时，在手机输入下方账号和访问密码。连接成功后，手机会保存独立设备令牌，之后无需重复输入共享密码。</p>
        <div className="mobile-password-status">
          <div className="mobile-password-status-copy"><span>访问密码</span><strong>{securityStatus ? (securityStatus.passwordConfigured ? (showSavedPassword && securityStatus.password ? securityStatus.password : "••••••••••") : "未设置") : "读取中"}</strong><small>{securityStatus?.passwordSource === "environment" ? "由 POLYKIT_AGENT_PASSWORD_FILE 管理" : "保存在本地配置中"}</small></div>
          <div className="mobile-password-status-actions">
            {securityStatus?.passwordSource === "config" && securityStatus.password && <button type="button" className="mobile-password-visibility" aria-label={showSavedPassword ? "隐藏已保存密码" : "显示已保存密码"} title={showSavedPassword ? "隐藏密码" : "显示密码"} onClick={() => setShowSavedPassword((visible) => !visible)}>{showSavedPassword ? <EyeOff size={15} /> : <Eye size={15} />}</button>}
            {!customPasswordOpen && <button type="button" className="mobile-link-button" disabled={securityStatus?.passwordSource === "environment" || savingPassword} onClick={() => setCustomPasswordOpen(true)}>{securityStatus?.passwordConfigured ? "修改访问密码" : "设置访问密码"}</button>}
          </div>
        </div>
        <div className="mobile-access-settings-grid">
          <label><span>连接账号</span><input value={username} maxLength={128} disabled={securityStatus?.source === "environment" || savingSettings || restartPending} onChange={(event) => setUsername(event.target.value)} /></label>
          <label><span>访问范围</span><div className="mobile-select-wrap"><select value={networkMode} disabled={savingSettings || restartPending} onChange={(event) => setNetworkMode(event.target.value as "local" | "lan")}><option value="local">仅本机</option><option value="lan">局域网与虚拟网络</option></select><ChevronDown size={15} aria-hidden="true" /></div><small className="mobile-field-help">允许其他设备访问；保存后会自动重启服务。</small></label>
        </div>
        <div className="mobile-access-actions"><button type="button" className={`mobile-secondary-button${settingsDirty ? " is-primary" : ""}`} disabled={!settingsDirty || savingSettings || restartPending} onClick={() => void saveAccessSettings()}>{savingSettings ? (networkModeChanged ? "保存并重启中" : "正在保存") : (networkModeChanged ? "保存并重启" : "保存访问设置")}</button></div>
        {customPasswordOpen && securityStatus?.passwordSource !== "environment" && <div className="mobile-custom-password-form">
          <label><span>新访问密码</span><div className="mobile-password-input"><input type={showCustomPassword ? "text" : "password"} value={customPassword} maxLength={512} autoComplete="new-password" onChange={(event) => setCustomPassword(event.target.value)} placeholder="至少 12 个字符" /><button type="button" aria-label={showCustomPassword ? "隐藏访问密码" : "显示访问密码"} title={showCustomPassword ? "隐藏密码" : "显示密码"} onClick={() => setShowCustomPassword((visible) => !visible)}>{showCustomPassword ? <EyeOff size={15} /> : <Eye size={15} />}</button></div></label>
          <label><span>再次输入</span><div className="mobile-password-input"><input type={showCustomPasswordConfirm ? "text" : "password"} value={customPasswordConfirm} maxLength={512} autoComplete="new-password" onChange={(event) => setCustomPasswordConfirm(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void setCustomAccessPassword(); }} /><button type="button" aria-label={showCustomPasswordConfirm ? "隐藏确认密码" : "显示确认密码"} title={showCustomPasswordConfirm ? "隐藏密码" : "显示密码"} onClick={() => setShowCustomPasswordConfirm((visible) => !visible)}>{showCustomPasswordConfirm ? <EyeOff size={15} /> : <Eye size={15} />}</button></div></label>
          <div className="mobile-custom-password-actions"><button type="button" className="mobile-secondary-button" disabled={savingPassword} onClick={() => void setCustomAccessPassword()}>{savingPassword ? "正在保存" : "保存新密码"}</button><button type="button" className="mobile-link-button" onClick={() => { setCustomPasswordOpen(false); setCustomPassword(""); setCustomPasswordConfirm(""); setShowCustomPassword(false); setShowCustomPasswordConfirm(false); setShowSavedPassword(false); }}>取消</button></div>
        </div>}
        {securityStatus?.passwordSource === "environment" && <small className="mobile-access-card-hint">当前由 POLYKIT_AGENT_USERNAME 或 POLYKIT_AGENT_PASSWORD_FILE 管理，请在启动环境中修改。</small>}
        {notice && <div className="mobile-devices-notice" role="status">{notice}</div>}
        {error && <div className="mobile-devices-error" role="alert">{error}</div>}
      </section>
      <div className="mobile-connect-guide">
        <div className="mobile-connect-guide-title"><span>首次连接</span><small>三步完成</small></div>
        <ol><li>先设置访问密码。</li><li>选择访问范围并点击“保存并重启”。</li><li>在手机输入服务地址、连接账号和访问密码；局域网内可尝试查找 PolyKit Agent。</li></ol>
      </div>
      <section className="mobile-device-list-pane" aria-label="已授权设备">
        <div className="mobile-pane-heading"><Smartphone size={16} /><span>已授权设备</span><em>{devices.length}</em></div>
        <div className="mobile-device-list">
          {devices.length === 0 ? <div className="mobile-device-empty">还没有已授权的设备</div> : devices.map((device) => (
            <div className="mobile-device-item" key={device.id}>
              <span className="mobile-device-icon"><Smartphone size={17} /></span>
              <div className="mobile-device-copy">
                {editingId === device.id ? <input autoFocus value={editingName} maxLength={80} onChange={(event) => setEditingName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void rename(device.id); if (event.key === "Escape") setEditingId(null); }} /> : <strong>{device.name}</strong>}
                <small>授权于 {new Date(device.createdAt).toLocaleString()}</small>
              </div>
              <div className="mobile-device-actions">
                {editingId === device.id ? <><button title="保存" onClick={() => void rename(device.id)}><Check size={15} /></button><button title="取消" onClick={() => setEditingId(null)}><X size={15} /></button></> : <><button title="重命名" onClick={() => { setEditingId(device.id); setEditingName(device.name); }}><Pencil size={14} /></button><button className="is-danger" title="吊销" onClick={() => void revoke(device)}><Trash2 size={14} /></button></>}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
