// VisualCamp's browser eye tracker (Eyedid web) ships no types. eyedidWeb.ts describes the parts it uses.
declare module 'seeso' {
  const Seeso: unknown
  export default Seeso
  /** attention, blink, drowsiness: which extra user-status signals the SDK computes */
  export class UserStatusOption {
    constructor(useAttention: boolean, useBlink: boolean, useDrowsiness: boolean)
  }
}
