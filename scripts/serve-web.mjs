import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawnSync, spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const condaPrefix = process.env.CONDA_PREFIX
const venvPython = process.platform === 'win32'
  ? join(root, '.venv', 'Scripts', 'python.exe')
  : join(root, '.venv', 'bin', 'python')
const candidates = [
  process.env.POLYKIT_PYTHON,
  venvPython,
  condaPrefix && join(condaPrefix, 'bin', 'python'),
  join(homedir(), 'miniconda3', 'envs', 'polykit', 'bin', 'python'),
  'python3',
  'python',
].filter(Boolean)

function isUsable(python) {
  if (python.includes('/') && !existsSync(python)) return false
  return spawnSync(python, ['-c', 'import fastapi, uvicorn'], { stdio: 'ignore' }).status === 0
}

const python = candidates.find(isUsable)
if (!python) {
  console.error('Could not find a Python environment with FastAPI and Uvicorn.')
  console.error('Set POLYKIT_PYTHON to the PolyKit runtime Python executable.')
  process.exit(1)
}

console.log(`Starting PolyKit server with ${python}`)
const model = process.env.POLYKIT_MODEL?.trim() || 'trellis2/generate'
const idleUnloadSeconds = process.env.POLYKIT_IDLE_UNLOAD_SECONDS?.trim() || '300'
const host = process.env.POLYKIT_HOST?.trim() || '127.0.0.1'
const port = process.env.POLYKIT_PORT?.trim() || '8765'
const child = spawn(
  python,
  [
    'api/serve.py',
    '--host',
    host,
    '--port',
    port,
    '--model',
    model,
    '--idle-unload-seconds',
    idleUnloadSeconds,
    '--web-dir',
    resolve(root, 'dist-web'),
  ],
  { cwd: root, stdio: 'inherit', env: process.env },
)

child.on('error', (error) => {
  console.error(`Failed to start PolyKit server: ${error.message}`)
  process.exit(1)
})

child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal)
  else process.exit(code ?? 1)
})
