#!/usr/bin/env node
// Node subprocess shim for JS process node packs (headless backend).
//
// The Python backend cannot run worker_threads, so it spawns this script:
//   node node_processor_shim.js
//
// Protocol — stdin : one JSON line  { extDir, entry, input, params, workspaceDir, tempDir }
// Protocol — stdout: JSON lines     { type: 'progress'|'log'|'done'|'error', ... }
//
// This runs as a separate process so the FastAPI server can execute JS
// processors exactly like Python ones (same line-delimited protocol handled by
// run_processor).
'use strict'

const readline = require('readline')
const path = require('path')
const Module = require('module')

function emit(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n')
}

const rl = readline.createInterface({ input: process.stdin })
let received = false

rl.on('line', (line) => {
  received = true
  rl.close()
  let data
  try {
    data = JSON.parse(line)
  } catch (err) {
    emit({ type: 'error', message: 'node shim: invalid JSON on stdin: ' + String(err) })
    process.exit(1)
  }

  const { extDir, entry, input, params, workspaceDir, tempDir } = data
  try {
    const require_ext = Module.createRequire(path.join(extDir, '_'))
    const processor = require_ext(path.join(extDir, entry))
    if (typeof processor !== 'function') {
      throw new Error('processor.js must export a function as module.exports')
    }

    const context = {
      workspaceDir,
      tempDir,
      nodeId: (input && input.nodeId) || '',
      log: (m) => emit({ type: 'log', message: String(m) }),
      progress: (pct, label) => emit({ type: 'progress', percent: pct, label }),
    }

    Promise.resolve(processor(input || {}, params || {}, context))
      .then((result) => {
        emit({ type: 'done', result: result || {} })
      })
      .catch((err) => {
        emit({ type: 'error', message: String(err && err.stack ? err.stack : err) })
      })
  } catch (err) {
    emit({ type: 'error', message: 'Failed to load processor: ' + String(err) })
  }
})

rl.on('close', () => {
  // If stdin closes without a message, exit quietly. When a message WAS
  // received, let the async processor finish and emit done/error naturally —
  // never force-exit here or we race the processor's promise.
  if (!received) process.exit(0)
})
