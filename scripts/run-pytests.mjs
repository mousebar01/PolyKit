// Portable Python test runner.
// `python3` is the macOS/Linux name but does not exist on Windows (where the
// interpreter is `python` or the `py` launcher). Try each candidate until one
// actually runs, then forward unittest's exit code.
import { spawnSync } from 'node:child_process'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const apiDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'api')
const rootDir = join(apiDir, '..')
const venvPython = process.platform === 'win32'
  ? join(rootDir, '.venv', 'Scripts', 'python.exe')
  : join(rootDir, '.venv', 'bin', 'python')
const explicitPython = process.env.POLYKIT_PYTHON
  ? resolve(rootDir, process.env.POLYKIT_PYTHON)
  : null

// Prefer an explicitly versioned modern interpreter when the host also
// exposes an older `python3`; the project uses Python 3.10+ syntax. Resolve an
// explicit project-relative interpreter before the unittest subprocess changes
// cwd to api/, otherwise `.venv/bin/python` would become `api/.venv/bin/python`.
const condaPrefix = process.env.CONDA_PREFIX
const candidates = [
  explicitPython ? [explicitPython, []] : null,
  [venvPython, []],
  condaPrefix ? [join(condaPrefix, 'bin', 'python'), []] : null,
  [join(homedir(), 'miniconda3', 'envs', 'polykit', 'bin', 'python'), []],
  ['python3.12', []],
  ['python3.11', []],
  ['python3.10', []],
  ['python3', []],
  ['python', []],
  ['py', ['-3']],
].filter(Boolean)

function works(cmd, prefix) {
  try {
    const r = spawnSync(cmd, [...prefix, '--version'], { encoding: 'utf8' })
    if (r.status !== 0) return false
    const match = `${r.stdout ?? ''} ${r.stderr ?? ''}`.match(/Python (\d+)\.(\d+)/)
    return Boolean(match && (Number(match[1]) > 3 || (Number(match[1]) === 3 && Number(match[2]) >= 10)))
  } catch {
    return false
  }
}

const found = candidates.find(([cmd, prefix]) => works(cmd, prefix))
if (!found) {
  console.error('[run-pytests] No Python interpreter found (tried python3, python, py -3).')
  process.exit(1)
}

const [cmd, prefix] = found
const result = spawnSync(cmd, [...prefix, '-m', 'unittest', 'discover', '-s', 'tests'], {
  cwd: apiDir,
  stdio: 'inherit',
})
process.exit(result.status ?? 1)
