"use client";

import { useEffect } from "react";
import { useI18n } from "@/hooks/useI18n";
import { X } from "lucide-react";

export function HelpModal({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const appVersion = process.env.NEXT_PUBLIC_APP_VERSION ?? "0.0.0";
  const piVersion = process.env.NEXT_PUBLIC_PI_VERSION ?? "unknown";

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          width: 460,
          maxWidth: "calc(100vw - 32px)",
          maxHeight: "min(72vh, calc(100vh - 32px))",
          background: "var(--bg)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 18px",
            borderBottom: "1px solid var(--border)",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 15, fontWeight: 700, color: "var(--text)" }}>
            {t("common.help")}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("i18n.close")}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              width: 30,
              height: 30,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 0,
              borderRadius: 7,
            }}
          >
            <X size={18} strokeWidth={1.8} />
          </button>
        </div>

        {/* Body */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 12,
            fontSize: 12,
            color: "var(--text-muted)",
            lineHeight: 1.6,
          }}
        >
          <section aria-labelledby="help-input-title">
            <h2
              id="help-input-title"
              style={{ margin: "0 0 8px", color: "var(--text)", fontSize: 12, fontWeight: 650 }}
            >
              {t("help.inputTitle")}
            </h2>
            <div
              style={{
                border: "1px solid var(--border)",
                borderRadius: 8,
                overflow: "hidden",
                background: "var(--bg-panel)",
              }}
            >
              {[
                {
                  key: "slash",
                  symbol: "/",
                  title: t("help.commandsTitle"),
                  description: t("help.commandsDescription"),
                },
              ].map((item, index) => (
                <div
                  key={item.key}
                  style={{
                    minHeight: 58,
                    display: "grid",
                    gridTemplateColumns: "34px minmax(0, 1fr)",
                    alignItems: "center",
                    gap: 10,
                    padding: "9px 12px",
                    borderTop: index === 0 ? "none" : "1px solid var(--border)",
                  }}
                >
                  <kbd
                    style={{
                      width: 32,
                      height: 32,
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      border: "1px solid var(--border)",
                      borderRadius: 7,
                      background: "var(--bg)",
                      color: "var(--text)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 15,
                      fontWeight: 600,
                    }}
                  >
                    {item.symbol}
                  </kbd>
                  <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
                    <strong style={{ color: "var(--text)", fontSize: 12, fontWeight: 600 }}>{item.title}</strong>
                    <span style={{ color: "var(--text-dim)", fontSize: 11 }}>{item.description}</span>
                  </span>
                </div>
              ))}
            </div>
          </section>
          <div
            style={{
              marginTop: "auto",
              paddingTop: 12,
              borderTop: "1px solid var(--border)",
              display: "flex",
              flexDirection: "column",
              gap: 4,
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-dim)",
            }}
          >
            <span>PolyKit Agent v{appVersion}</span>
            <span>pi v{piVersion}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
