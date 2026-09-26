// Puts the MediaPipe face tracking files in web/public/mediapipe/ so the board never needs a CDN at
// runtime (venue wifi may be bad):
//
//   wasm/                    copied from node_modules/@mediapipe/tasks-vision/wasm (same version as the JS)
//   face_landmarker.task     the Face Landmarker model, downloaded once from Google's model storage
//
// Runs after `npm install` and before `npm run dev` / `npm run build`; also `npm run assets`. Files
// already in place are skipped, so it is instant after the first run. It never fails the command it
// runs in: without the model the board shows "Face tracking model missing" and Auto keeps scanning.
// The folder is git-ignored.

import { copyFileSync, existsSync, mkdirSync, readdirSync, renameSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const out = join(web, 'public', 'mediapipe')
const wasmFrom = join(web, 'node_modules', '@mediapipe', 'tasks-vision', 'wasm')
const wasmTo = join(out, 'wasm')
const model = join(out, 'face_landmarker.task')

function copyWasm() {
  if (!existsSync(wasmFrom)) {
    console.warn('mediapipe-assets: @mediapipe/tasks-vision is not installed; run npm --prefix web install')
    return
  }
  mkdirSync(wasmTo, { recursive: true })
  let copied = 0
  for (const name of readdirSync(wasmFrom)) {
    const from = join(wasmFrom, name)
    const to = join(wasmTo, name)
    if (existsSync(to) && statSync(to).size === statSync(from).size) continue
    copyFileSync(from, to)
    copied++
  }
  if (copied) console.log(`mediapipe-assets: copied ${copied} wasm files to public/mediapipe/wasm`)
}

async function fetchModel() {
  if (existsSync(model) && statSync(model).size > 0) return
  mkdirSync(out, { recursive: true })
  console.log(`mediapipe-assets: downloading the face model (about 4 MB) from ${MODEL_URL}`)
  try {
    const res = await fetch(MODEL_URL)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = Buffer.from(await res.arrayBuffer())
    const tmp = `${model}.part`
    writeFileSync(tmp, data)
    renameSync(tmp, model) // never leave a half-written model behind
    console.log(`mediapipe-assets: saved public/mediapipe/face_landmarker.task (${(data.length / 1e6).toFixed(1)} MB)`)
  } catch (e) {
    console.warn(`mediapipe-assets: could not download the face model (${e.message}).`)
    console.warn('  Webcam pointing stays off until it is there: run `npm --prefix web run assets` with internet.')
  }
}

copyWasm()
await fetchModel()
