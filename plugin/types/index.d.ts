// What refresh() last measured, kept in $.state; the band draws from it and from Info.
export type Snap = {
  id: string
  model: string
  now: number
  tokens: number | null
  at: number | null
  cost: number | null // USD spent in this chat, when the engine says
  limits: { kind: string; percentUsed: number; resetsAt?: string }[]
}

// What `cg.py savings` answers; usd is what was saved (negative: it cost more). project is null when the index could not list the chats.
export type Savings = {
  last: { usd: number; old_tokens: number; new_tokens: number } | null
  worker: { usd: number; links: number; calls: number }
  project: { usd: number; links: number } | null
}

// The band's other source: `cg.py status` as it came, and the savings; null where the answer did not come.
export type Info = { status: Record<string, unknown> | null; savings: Savings | null; isSyncOn: boolean }

declare module 'claude-code' {
  interface PluginState {
    alethite: {
      snap: Snap | null
      // session id the band is hidden for (/al-band off); a /clear changes the id and ends it
      hiddenId: string
      // the band is showing the model picker instead of its row
      isArmed: boolean
      // a new-worker launch is running: the band shows T.busy and ignores presses
      isBusy: boolean
      info: Info | null
      isDetails: boolean
      calmId: string
      delayedAt: number | null
    }
  }
}
