import assert from 'node:assert/strict'
import test from 'node:test'

import { migrateMeshInputSources } from './workflowsStore.ts'

test('migrates legacy current mesh inputs to explicit file inputs', () => {
  const [node] = migrateMeshInputSources([
    {
      id: 'mesh',
      type: 'meshNode',
      position: { x: 0, y: 0 },
      data: { params: { source: 'current' } },
    },
  ])

  assert.equal(node.data.params?.source, 'file')
})
