import assert from 'node:assert/strict'
import test from 'node:test'

import { translate } from './i18n.ts'

test('asset delete copy preserves long generated filenames', () => {
  const name = 'generate_mesh_20260823_014740_30ddc6c6_20260826-152357_56d1b346_rigged.glb'

  assert.equal(
    translate('assets.deleteDescriptionSingle', 'zh-CN', { name }),
    `删除“${name}”及其元数据？此操作无法撤销。`,
  )
})
