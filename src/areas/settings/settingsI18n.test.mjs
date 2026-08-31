import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const root = new URL('./', import.meta.url)
const repoRoot = new URL('../../../', import.meta.url)

async function read(path) {
  return readFile(new URL(path, root), 'utf8')
}

test('Settings sections route user-facing copy through i18n', async () => {
  const [about, integrations, storage, layout, i18n] = await Promise.all([
    read('./components/AboutSection.tsx'),
    read('./components/IntegrationsSection.tsx'),
    read('./components/StorageSection.tsx'),
    read('./components/SettingsLayout.tsx'),
    readFile(new URL('src/shared/i18n.ts', repoRoot), 'utf8'),
  ])

  const ui = [about, integrations, storage, layout].join('\n')
  for (const literal of [
    'title="About"',
    'title="Integrations"',
    '>Browse…<',
    '>Saving…<',
    '>Saved<',
    '>Clear cache<',
    '>Please wait…<',
  ]) {
    assert.equal(ui.includes(literal), false, `Settings UI still hardcodes ${literal}`)
  }

  for (const key of [
    'settings.aboutSubtitle',
    'settings.integrationsSubtitle',
    'settings.browse',
    'settings.storageSubtitle',
    'settings.existingStorageItems',
  ]) {
    const count = i18n.split(`'${key}'`).length - 1
    assert.equal(count, 2, `${key} must exist in both en-US and zh-CN dictionaries`)
  }
})

test('technical product names remain stable', async () => {
  const integrations = await read('./components/IntegrationsSection.tsx')
  assert.match(integrations, /Hugging Face Hub/)
})

test('retired embedded Agent settings do not return', async () => {
  const settingsPage = await read('./SettingsPage.tsx')
  const integrations = await read('./components/IntegrationsSection.tsx')
  const combined = `${settingsPage}\n${integrations}`
  for (const retired of ['AgentSection', 'McpSection', '/settings/agent', 'api/mcp_server.py']) {
    assert.equal(combined.includes(retired), false, `Retired Agent settings surface returned: ${retired}`)
  }
})
