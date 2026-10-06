// Puts the Jaguar I-Pace model the tablet draws its trip car with in web/public/models/, so a browser
// draws the same car (web/src/carscene):
//
//   models/jaguar_i-pace.glb   copied from the tablet app's assets (kushagra/tablet/app/src/main/assets)
//
// Runs with the other asset scripts (predev, prebuild, postinstall). The model is not in git (it is 9 MB
// and licensed for the demo), so the copy only happens where the tablet's copy exists. Without it the
// browser draws the road alone, as the tablet does without its model. It never fails the command it runs
// in. The folder is git-ignored.

import { copyFileSync, existsSync, mkdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const from = join(web, '..', 'kushagra', 'tablet', 'app', 'src', 'main', 'assets', 'jaguar_i-pace.glb')
const outDir = join(web, 'public', 'models')
const to = join(outDir, 'jaguar_i-pace.glb')

if (!existsSync(from)) {
  if (!existsSync(to)) console.warn('car-model: no jaguar_i-pace.glb in the tablet assets; the browser car will show the road only')
} else if (!existsSync(to) || statSync(to).size !== statSync(from).size) {
  mkdirSync(outDir, { recursive: true })
  copyFileSync(from, to)
  console.log('car-model: copied the car model to public/models/')
}
