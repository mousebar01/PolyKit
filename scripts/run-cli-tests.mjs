// Run the stdlib-only CLI tests with the project's supported Python version.
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const venvPython = process.platform === 'win32'
  ? join(root, '.venv', 'Scripts', 'python.exe')
  : join(root, '.venv', 'bin', 'python')
const candidates = [
  process.env.POLYKIT_PYTHON ? [process.env.POLYKIT_PYTHON, []] : null,
  [venvPython, []],
  ['python3.12', []],
  ['python3.11', []],
  ['python3.10', []],
  ['python3', []],
  ['python', []],
  ['py', ['-3']],
].filter(Boolean)

function works(command, prefix) {
  try {
    const result = spawnSync(command, [...prefix, '--version'], { encoding: 'utf8' })
    if (result.status !== 0) return false
    const match = `${result.stdout ?? ''} ${result.stderr ?? ''}`.match(/Python (\d+)\.(\d+)/)
    return Boolean(match && (Number(match[1]) > 3 || (Number(match[1]) === 3 && Number(match[2]) >= 10)))
  } catch {
    return false
  }
}

const found = candidates.find(([command, prefix]) => works(command, prefix))
if (!found) {
  console.error('[run-cli-tests] No supported Python interpreter found.')
  process.exit(1)
}

const [command, prefix] = found
const result = spawnSync(command, [...prefix, 'tools/polykit-cli/test_cli.py'], {
  cwd: root,
  stdio: 'inherit',
})
process.exit(result.status ?? 1)
