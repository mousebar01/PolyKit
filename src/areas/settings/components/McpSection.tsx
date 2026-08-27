import type { ReactNode } from 'react'
import { Copy } from 'lucide-react'

import { Button } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { SettingsCard, SettingsSection } from './SettingsLayout'

function Group({ title, children }: { title: string; children: ReactNode }): JSX.Element {
  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">{title}</h3>
      {children}
    </div>
  )
}

function CopyableCode({ value }: { value: string }): JSX.Element {
  const { t } = useI18n()
  return (
    <div className="relative">
      <pre className="overflow-x-auto whitespace-pre rounded-lg border border-divider bg-muted/40 px-4 py-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
        {value}
      </pre>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="absolute right-1.5 top-1.5 size-7 text-muted-foreground"
        onClick={() => { void navigator.clipboard.writeText(value) }}
        title={t('assets.copy')}
        aria-label={t('settings.copyConfiguration')}
      >
        <Copy className="size-3.5" />
      </Button>
    </div>
  )
}

export function McpSection(): JSX.Element {
  const { t } = useI18n()
  const mcpConfigs = {
    opencode: `{\n  "$schema": "https://opencode.ai/config.json",\n  "mcp": {\n    "polykit": {\n      "type": "local",\n      "command": ["python", "<POLYKIT_ROOT>/api/mcp_server.py"]\n    }\n  }\n}`,
    codex: `[mcp_servers.polykit]\ncommand = "python"\nargs = ["<POLYKIT_ROOT>/api/mcp_server.py"]`,
    claude: `{\n  "mcpServers": {\n    "polykit": {\n      "command": "python",\n      "args": ["<POLYKIT_ROOT>/api/mcp_server.py"]\n    }\n  }\n}`,
  }

  return (
    <SettingsSection title={t('settings.externalAgents')} subtitle={t('settings.externalAgentsSubtitle')}>
      <SettingsCard>
        <div className="space-y-5 p-5">
          <Group title="MCP Server">
            <p className="text-xs leading-relaxed text-muted-foreground">
              {t('settings.replacePolykitRoot')} <code className="text-foreground">&lt;POLYKIT_ROOT&gt;</code>
            </p>
            <CopyableCode value="python <POLYKIT_ROOT>/api/mcp_server.py" />

            <div className="flex flex-col gap-4">
              {([
                { label: 'Claude Desktop', key: 'claude' as const, hint: '~/.config/claude/claude_desktop_config.json' },
                { label: 'Codex CLI', key: 'codex' as const, hint: '~/.codex/config.toml' },
                { label: 'OpenCode', key: 'opencode' as const, hint: '~/.config/opencode/config.json' },
              ] as const).map(({ label, key, hint }) => (
                <div key={key} className="space-y-1.5">
                  <p className="text-[11px] text-muted-foreground">
                    {label} <span className="opacity-70">— {hint}</span>
                  </p>
                  <CopyableCode value={mcpConfigs[key]} />
                </div>
              ))}
            </div>
          </Group>
        </div>
      </SettingsCard>
    </SettingsSection>
  )
}
