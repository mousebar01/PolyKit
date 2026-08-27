import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const agentRoot = join(root, 'agent')
const marker = join(agentRoot, 'node_modules', '@earendil-works', 'pi-coding-agent', 'dist', 'index.js')

if (existsSync(marker)) process.exit(0)

console.log('Installing embedded Agent runtime dependencies...')
const result = spawnSync('npm', ['ci', '--prefix', agentRoot], {
  cwd: root,
  stdio: 'inherit',
})
if (result.error) {
  console.error(`Failed to install Agent dependencies: ${result.error.message}`)
  process.exit(1)
}
process.exit(result.status ?? 1)
