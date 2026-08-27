import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const root = new URL('./', import.meta.url)
const repoRoot = new URL('../../../', import.meta.url)

async function read(path) {
  return readFile(new URL(path, root), 'utf8')
}

test('Settings sections route user-facing copy through i18n', async () => {
  const [about, agent, integrations, mcp, storage, layout, i18n] = await Promise.all([
    read('./components/AboutSection.tsx'),
    read('./components/AgentSection.tsx'),
    read('./components/IntegrationsSection.tsx'),
    read('./components/McpSection.tsx'),
    read('./components/StorageSection.tsx'),
    read('./components/SettingsLayout.tsx'),
    readFile(new URL('src/shared/i18n.ts', repoRoot), 'utf8'),
  ])

  const ui = [about, agent, integrations, mcp, storage, layout].join('\n')
  for (const literal of [
    'title="About"',
    'title="Integrations"',
    '>Browse…<',
    '>Saving…<',
    '>Saved<',
    '>Clear cache<',
    '>Please wait…<',
    'title="Copy"',
    'aria-label="Copy configuration"',
  ]) {
    assert.equal(ui.includes(literal), false, `Settings UI still hardcodes ${literal}`)
  }

  for (const key of [
    'settings.aboutSubtitle',
    'settings.integrationsSubtitle',
    'settings.externalAgents',
    'settings.browse',
    'settings.storageSubtitle',
    'settings.existingStorageItems',
    'settings.agentSubtitle',
    'settings.agentRuntime',
    'settings.agentEnabled',
    'settings.agentToolProfile',
  ]) {
    const count = i18n.split(`'${key}'`).length - 1
    assert.equal(count, 2, `${key} must exist in both en-US and zh-CN dictionaries`)
  }
})

test('technical product and protocol names remain stable', async () => {
  const [integrations, mcp] = await Promise.all([
    read('./components/IntegrationsSection.tsx'),
    read('./components/McpSection.tsx'),
  ])
  for (const technicalName of ['Hugging Face Hub', 'MCP Server', 'Claude Desktop', 'Codex CLI', 'OpenCode']) {
    assert.match(`${integrations}\n${mcp}`, new RegExp(technicalName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
})

test('Agent settings keep the migrated management sections', async () => {
  const agent = await read('./components/AgentSection.tsx')
  for (const section of ['runtime', 'models', 'skills', 'plugins', 'mcp', 'archives', 'workspaces']) {
    assert.match(agent, new RegExp(`id: '${section}'`), `${section} settings section is missing`)
  }
})
