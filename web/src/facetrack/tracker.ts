// The webcam and MediaPipe Face Landmarker, running in this browser only (PRD section 11): video
// never leaves the page and nothing is recorded. One shared tracker for the board and the dev panel.
//
//   - Files are served from web/public/mediapipe/ (scripts/mediapipe-assets.mjs), never a CDN.
//   - GPU delegate first, CPU if the GPU fails (at start or on the first frames).
//   - About 25 detections a second; each gives the head's yaw and pitch (smoothed) or "no face".
//   - Camera problems (permission denied, no camera, in use by another app) become a status the
//     board shows in plain words.

import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision'
import { cameraProblem, type CameraProblem } from './camera'
import { matrixToAngles, smooth, type Angles } from './pose'

const WASM_PATH = '/mediapipe/wasm'
const MODEL_PATH = '/mediapipe/face_landmarker.task'
const FRAME_MS = 40 // about 25 detections a second (PRD A6: head turn -> highlight under 150 ms)
export const SMOOTHING = 0.35 // share of each new reading (exponential smoothing)

export type Delegate = 'GPU' | 'CPU'

export type TrackerStatus =
  | { kind: 'off' }
  | { kind: 'starting' }
  | { kind: 'on'; delegate: Delegate }
  | { kind: 'error'; problem: CameraProblem; detail: string }

export interface HeadSample {
  /** performance.now() of the frame, ms */
  t: number
  face: boolean
  /** this frame's angles (null without a face) */
  raw: Angles | null
  /** smoothed angles (null without a face) */
  angles: Angles | null
}

type Listener = () => void

class Tracker {
  status: TrackerStatus = { kind: 'off' }
  sample: HeadSample = { t: 0, face: false, raw: null, angles: null }
  stream: MediaStream | null = null

  private statusListeners = new Set<Listener>()
  private sampleListeners = new Set<(s: HeadSample) => void>()
  private video: HTMLVideoElement | null = null
  private landmarker: FaceLandmarker | null = null
  private delegate: Delegate = 'GPU'
  private raf = 0
  private timer = 0
  private background = false

  /** Computer mode needs frames even when Chromium covers the board and RAF is suspended. */
  setBackground(enabled: boolean): void {
    if (this.background === enabled) return
    this.background = enabled
    cancelAnimationFrame(this.raf)
    window.clearTimeout(this.timer)
    if (this.status.kind === 'on') this.schedule()
  }

  private schedule() {
    if (this.background) this.timer = window.setTimeout(() => this.loop(performance.now()), FRAME_MS)
    else this.raf = requestAnimationFrame(this.loop)
  }
  private lastFrame = 0
  private lastVideoTime = -1
  private run = 0 // bumps on every start/stop so a slow start that was cancelled cleans up

  subscribeStatus = (fn: Listener) => {
    this.statusListeners.add(fn)
    return () => {
      this.statusListeners.delete(fn)
    }
  }

  subscribeSample = (fn: (s: HeadSample) => void) => {
    this.sampleListeners.add(fn)
    return () => {
      this.sampleListeners.delete(fn)
    }
  }

  getStatus = () => this.status

  private setStatus(s: TrackerStatus) {
    this.status = s
    this.statusListeners.forEach((fn) => fn())
  }

  get running(): boolean {
    return this.status.kind === 'starting' || this.status.kind === 'on'
  }

  /** Turn the camera on and start tracking. Safe to call again (no-op while running). */
  async start(): Promise<void> {
    if (this.running) return
    const run = ++this.run
    this.setStatus({ kind: 'starting' })
    if (!navigator.mediaDevices?.getUserMedia) {
      this.setStatus({ kind: 'error', problem: 'insecure', detail: 'navigator.mediaDevices is not available' })
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
        audio: false,
      })
    } catch (e) {
      if (run === this.run) this.setStatus({ kind: 'error', problem: cameraProblem(e), detail: String(e) })
      return
    }
    if (run !== this.run) {
      stream.getTracks().forEach((t) => t.stop()) // stopped while the permission prompt was open
      return
    }
    this.stream = stream
    stream.getVideoTracks()[0]?.addEventListener('ended', () => {
      if (run === this.run) this.fail('none', 'the camera was unplugged or turned off')
    })
    const video = document.createElement('video')
    video.muted = true
    video.playsInline = true
    video.srcObject = stream
    this.video = video
    try {
      await video.play()
      if (!this.landmarker) this.landmarker = await this.createLandmarker('GPU')
    } catch (e) {
      if (run === this.run) this.fail('model', String(e))
      return
    }
    if (run !== this.run) return
    this.setStatus({ kind: 'on', delegate: this.delegate })
    this.schedule()
  }

  /** Camera off: the light goes out and no more frames are read. */
  stop(): void {
    this.run++
    cancelAnimationFrame(this.raf)
    window.clearTimeout(this.timer)
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
    if (this.video) this.video.srcObject = null
    this.video = null
    this.lastVideoTime = -1
    this.publish({ t: performance.now(), face: false, raw: null, angles: null })
    this.setStatus({ kind: 'off' })
  }

  private fail(problem: CameraProblem, detail: string) {
    console.warn(`face tracking: ${problem}: ${detail}`)
    this.stop()
    this.setStatus({ kind: 'error', problem, detail })
  }

  private async createLandmarker(delegate: Delegate): Promise<FaceLandmarker> {
    const fileset = await FilesetResolver.forVisionTasks(WASM_PATH)
    const make = (d: Delegate) =>
      FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_PATH, delegate: d },
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFaceBlendshapes: false,
        outputFacialTransformationMatrixes: true,
      })
    if (delegate === 'GPU') {
      try {
        const lm = await make('GPU')
        this.delegate = 'GPU'
        return lm
      } catch (e) {
        console.warn('face tracking: GPU delegate failed, using the CPU', e)
      }
    }
    this.delegate = 'CPU'
    return make('CPU')
  }

  private loop = (now: number) => {
    this.schedule()
    const video = this.video
    const lm = this.landmarker
    if (!video || !lm || now - this.lastFrame < FRAME_MS || video.readyState < 2) return
    if (video.currentTime === this.lastVideoTime) return // no new camera frame yet
    this.lastFrame = now
    this.lastVideoTime = video.currentTime
    let raw: Angles | null = null
    try {
      const result = lm.detectForVideo(video, now)
      const m = result.facialTransformationMatrixes?.[0]
      raw = m ? matrixToAngles(m.data) : null
    } catch (e) {
      this.frameFailed(e)
      return
    }
    const angles = raw ? smooth(this.sample.angles, raw, SMOOTHING) : null
    this.publish({ t: now, face: raw !== null, raw, angles })
  }

  /** A detection threw: on the GPU, switch to the CPU once; on the CPU, give up with a message. */
  private frameFailed(e: unknown) {
    if (this.delegate === 'GPU') {
      console.warn('face tracking: GPU frame failed, switching to the CPU', e)
      const run = this.run
      cancelAnimationFrame(this.raf)
      window.clearTimeout(this.timer)
      this.landmarker?.close()
      this.landmarker = null
      this.createLandmarker('CPU').then(
        (lm) => {
          if (run !== this.run) return lm.close()
          this.landmarker = lm
          this.setStatus({ kind: 'on', delegate: 'CPU' })
          this.schedule()
        },
        (err) => run === this.run && this.fail('model', String(err)),
      )
    } else {
      this.fail('error', String(e))
    }
  }

  private publish(s: HeadSample) {
    this.sample = s
    this.sampleListeners.forEach((fn) => fn(s))
  }
}

/** The one tracker on this page. */
export const tracker = new Tracker()
