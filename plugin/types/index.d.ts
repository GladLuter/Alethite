// The record `bg:<session id>` in the store: a background run of that chat.
export type BgRun = { dir: string; name: string; pid: number; started: number; log: string; statusFile?: string; ifCold: 'show' | 'new_worker'; ended?: { code: number | null; at: number; lost?: true } }

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
  worker: { usd: number; links: number; calls: number; lean: { usd: number; agents: number } }
  project: { usd: number; links: number; lean: { usd: number; agents: number } } | null
}

// The band's other source: `cg.py status` as it came, and the savings; null where the answer did not come.
export type Info = { status: Record<string, unknown> | null; savings: Savings | null; isSyncOn: boolean }

// What `cg.py review-hint` answers: ponytail is installed, and the code edits since the last /ponytail-review.
export type Review = { ponytail: boolean; edits: number }

declare module 'claude-code' {
  interface PluginState {
    alethite: {
      snap: Snap | null
      // session id the band is hidden for (/al-band off); a /clear changes the id and ends it
      hiddenId: string
      // the band is showing the model picker instead of its row
      isArmed: boolean
      // After reset menu level: null = first menu, 'time' = pick the time, a number = pick the model for that time
      afterLevel: null | 'time' | number
      // a new-worker launch is running: the band shows T.busy and ignores presses
      isBusy: boolean
      info: Info | null
      isDetails: boolean
      delayedAt: number | null
      // this chat's background run as the band shows it
      bgView: { run: BgRun; last: string } | null
    }
  }
}
