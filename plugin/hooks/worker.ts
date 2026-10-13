// The "New worker" launch as plain steps. register.tsx hands in closures that call `$`: the loader follows `$` only inside its own file.
import { message } from './py'
import { T } from './text'

export type LaunchIO = {
  draft: () => Promise<string>
  handoff: () => Promise<{ text: string; path: string }>
  clear: () => Promise<unknown>
  setModel: (model: string) => Promise<unknown>
  submit: (text: string) => Promise<{ drop?: string }>
  fill: (text: string) => Promise<void>
  emptyBox: (draft: string) => Promise<void>
  toast: (text: string) => void
}

// Until /clear the chat is untouched; after it, a failed step puts handoff + draft back in the box so nothing is lost.
export const launch = async (io: LaunchIO, model: string | null): Promise<'empty' | 'failed' | 'dropped' | 'sent'> => {
  const draft = await io.draft()
  if (draft.trim() === '') {
    io.toast(T.draftEmpty)
    return 'empty'
  }

  let handoff
  try {
    handoff = await io.handoff()
  } catch (err) {
    io.toast(T.handoffFailed(message(err)))
    return 'failed'
  }
  try {
    await io.clear()
  } catch (err) {
    io.toast(T.clearFailed(message(err), handoff.path))
    return 'failed'
  }

  const text = `${handoff.text}\n\n${draft}`
  // every failure from here on names the saved file; if even the box refuses the text, the draft is most likely still there
  const back = async (why: string, shown: string) => {
    try {
      await io.fill(text)
      io.toast(shown)
    } catch (err) {
      io.toast(T.fillFailed(why, message(err), handoff.path))
    }
    return 'failed' as const
  }
  // the context is empty after /clear, so the switch overwrites nothing
  if (model !== null) {
    try {
      await io.setModel(model)
    } catch (err) {
      const why = message(err)
      return back(why, T.modelFailed(model, why, handoff.path))
    }
  }
  let sent
  try {
    sent = await io.submit(text)
  } catch (err) {
    const why = message(err)
    return back(why, T.submitFailed(why, handoff.path))
  }
  if (sent.drop !== undefined) {
    io.toast(T.dropped(sent.drop, handoff.path))
    return 'dropped'
  }

  await io.emptyBox(draft)
  return 'sent'
}
