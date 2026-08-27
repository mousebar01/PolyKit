import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const workflowsRoot = new URL('./', import.meta.url)
const repoRoot = new URL('../../../', import.meta.url)

test('workflow chrome uses shared locale copy', async () => {
  const [page, i18n] = await Promise.all([
    readFile(new URL('./WorkflowsPage.tsx', workflowsRoot), 'utf8'),
    readFile(new URL('src/shared/i18n.ts', repoRoot), 'utf8'),
  ])

  for (const literal of [
    '>Nodes<',
    '>Generated products<',
    '>Workflow running<',
    '>How the workflow system works<',
    '>Run failed<',
    'title="Open workflow"',
    'placeholder="Search nodes and node packs…"',
  ]) {
    assert.equal(page.includes(literal), false, `Workflow UI still hardcodes ${literal}`)
  }

  for (const key of [
    'workflows.nodes',
    'workflows.outputs',
    'workflows.helpTitle',
    'workflows.nodeMeshDescription',
    'workflows.deleteDescription',
  ]) {
    const count = i18n.split(`'${key}'`).length - 1
    assert.equal(count, 2, `${key} must exist in both en-US and zh-CN dictionaries`)
  }
})

test('workflow editor keeps chrome stable while data loads', async () => {
  const [page, store] = await Promise.all([
    readFile(new URL('./WorkflowsPage.tsx', workflowsRoot), 'utf8'),
    readFile(new URL('../../shared/stores/workflowsStore.ts', workflowsRoot), 'utf8'),
  ])

  assert.match(page, /<div className="flex h-10 shrink-0 items-stretch overflow-x-auto border-b border-divider bg-card\/55">/)
  assert.match(page, /loading \? \(/)
  assert.match(store, /loading:\s+true/)
})
