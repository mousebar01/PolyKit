/**
 * Compile built-in node packs (TypeScript → CommonJS JS) and copy manifests.
 * Output: out/builtin-node-packs/{id}/processor.js + manifest.json
 */

import { execSync }                                           from 'child_process'
import { readdirSync, existsSync, cpSync, mkdirSync, statSync, readFileSync } from 'fs'
import { join, dirname }                                      from 'path'
import { fileURLToPath }                                      from 'url'

const root   = join(dirname(fileURLToPath(import.meta.url)), '..')
const srcDir = join(root, 'src', 'areas', 'workflows', 'nodes')
const outDir = join(root, 'out', 'builtin-node-packs')

if (!existsSync(srcDir)) {
  console.log('[build-builtins] No builtin-node-packs directory found, skipping.')
  process.exit(0)
}

// 1. Compile TypeScript
console.log('[build-builtins] Compiling TypeScript…')
execSync('npx tsc -p tsconfig.builtins.json', { cwd: root, stdio: 'inherit' })

// 2. Copy manifest.json, and optionally package.json + npm install.
// Built-in processors run in a separate Node worker.  Their dependencies are
// data shipped with the app, not a development-time install, so do not run
// arbitrary native postinstall scripts during every build.  This also
// avoids rebuilding optional packages such as sharp against the developer's
// Node headers.  A processor that genuinely needs a postinstall hook can opt
// in explicitly with POLYKIT_BUILTIN_INSTALL_SCRIPTS=1.
for (const id of readdirSync(srcDir)) {
  const extSrcDir = join(srcDir, id)
  if (!statSync(extSrcDir).isDirectory()) continue
  // Only process node pack folders (those with a manifest.json)
  if (!existsSync(join(extSrcDir, 'manifest.json'))) continue

  const extOutDir = join(outDir, id)
  mkdirSync(extOutDir, { recursive: true })

  const manifestSrc = join(extSrcDir, 'manifest.json')
  if (existsSync(manifestSrc)) {
    cpSync(manifestSrc, join(extOutDir, 'manifest.json'))
    console.log(`[build-builtins] ${id}: manifest.json copied`)
  } else {
    console.warn(`[build-builtins] ${id}: manifest.json missing — skipping`)
  }

  const pkgSrc = join(extSrcDir, 'package.json')
  if (existsSync(pkgSrc)) {
    cpSync(pkgSrc, join(extOutDir, 'package.json'))
    console.log(`[build-builtins] ${id}: Installing npm dependencies…`)
    // Native dependencies (sharp, etc.) need their postinstall scripts to
    // fetch/compile platform binaries. Packs that require them opt in with
    // "polykit": { "installScripts": true } in package.json; everything else
    // keeps --ignore-scripts for reproducible, offline-friendly builds.
    let needsScripts = false
    try {
      const pkg = JSON.parse(readFileSync(pkgSrc, 'utf-8'))
      needsScripts = pkg?.polykit?.installScripts === true
    } catch { /* missing/unparseable package.json -> default no scripts */ }
    const envWantsScripts = process.env.POLYKIT_BUILTIN_INSTALL_SCRIPTS === '1'
    const installArgs = (envWantsScripts || needsScripts)
      ? '--omit=dev --no-audit --no-fund'
      : '--omit=dev --no-audit --no-fund --ignore-scripts'
    execSync(`npm install ${installArgs}`, {
      cwd:   extOutDir,
      stdio: 'inherit',
    })
    console.log(`[build-builtins] ${id}: npm install done`)
  }

  // Copy any Python processor files
  for (const file of readdirSync(extSrcDir)) {
    if (file.endsWith('.py')) {
      cpSync(join(extSrcDir, file), join(extOutDir, file))
      console.log(`[build-builtins] ${id}: ${file} copied`)
    }
  }
}

console.log('[build-builtins] Done.')
