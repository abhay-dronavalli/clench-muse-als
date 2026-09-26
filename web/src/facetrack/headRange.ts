// The calibrated head range lives in the Core's database profile (PRD A7 head_range), so it survives
// reloads and restarts: GET / PUT /api/head-range (proxied to the Core by the dev server).

import type { HeadRange } from '../contracts'

const URL = '/api/head-range'

/** The saved range, or null before the first calibration. Throws when the Core cannot be reached. */
export async function loadHeadRange(): Promise<HeadRange | null> {
  const res = await fetch(URL)
  if (!res.ok) throw new Error(`GET ${URL}: HTTP ${res.status}`)
  return (await res.json()) as HeadRange | null
}

export async function saveHeadRange(range: HeadRange): Promise<HeadRange> {
  const res = await fetch(URL, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(range),
  })
  if (!res.ok) throw new Error(`PUT ${URL}: HTTP ${res.status} ${await res.text()}`)
  return (await res.json()) as HeadRange
}
