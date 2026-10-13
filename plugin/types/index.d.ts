// What refresh() last measured, kept in $.state so the band draws from it alone.
export type Snap = {
  id: string
  model: string
  now: number
  tokens: number | null
  at: number | null
  limits: { kind: string; percentUsed: number }[]
}

declare module 'claude-code' {
  interface PluginState {
    cacheguard: {
      snap: Snap | null
      // session ids the band is hidden / force-shown for; a /clear changes the id and ends both
      hiddenId: string
      forcedId: string
      // the band is showing the model picker instead of its two buttons
      isArmed: boolean
      // a new-worker launch is running (used from Task 8)
      isBusy: boolean
    }
  }
}
