declare module '@agent/components/ChatWindow' {
  import type { ReactElement, RefObject } from 'react'

  type ToolPreset = 'none' | 'default' | 'full'
  type ThinkingLevel = 'auto' | 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'

  export interface ChatWindowProps {
    session: unknown
    newSessionCwd: string | null
    showWorkspacePicker?: boolean
    initialModel?: { provider: string; modelId: string } | null
    initialToolPreset?: ToolPreset
    initialThinkingLevel?: ThinkingLevel
    onAgentEnd?: () => void
    onSessionCreated?: (session: unknown) => void
    onSessionForked?: (sessionId: string) => void
    modelsRefreshKey?: number
    chatInputRef?: RefObject<unknown>
    onChooseProject?: () => void
  }

  export function ChatWindow(props: ChatWindowProps): ReactElement
}

declare module '@agent/hooks/useI18n' {
  import type { ReactElement, ReactNode } from 'react'

  export function I18nProvider(props: { children: ReactNode }): ReactElement
}

declare module '@agent/components/ArchivedSessionsConfig' {
  import type { ReactElement } from 'react'
  export function ArchivedSessionsConfig(props?: { onSessionDeleted?: (sessionId: string) => void; onSessionsChanged?: () => void }): ReactElement
}

declare module '@agent/components/HiddenWorkspacesConfig' {
  import type { ReactElement } from 'react'
  export function HiddenWorkspacesConfig(props?: { onChanged?: () => void }): ReactElement
}

declare module '@agent/components/ModelsConfig' {
  import type { ReactElement } from 'react'
  export function ModelsConfig(props: { onClose: () => void; embedded?: boolean }): ReactElement
}

declare module '@agent/components/SkillsConfig' {
  import type { ReactElement } from 'react'
  export function SkillsConfig(props: { cwd: string; onClose: () => void; embedded?: boolean }): ReactElement
}

declare module '@agent/components/PluginsConfig' {
  import type { ReactElement } from 'react'
  export function PluginsConfig(props: { cwd: string; sessionId: string | null; onClose: () => void; onReloaded?: () => void; embedded?: boolean }): ReactElement
}

declare module '@agent/components/McpConfig' {
  import type { ReactElement } from 'react'
  export function McpConfig(props: { cwd: string; sessionId: string | null; onAgentConfigure: (serverName?: string) => void; onReloaded: () => void }): ReactElement
}
