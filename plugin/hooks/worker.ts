// The "New worker" launch as plain steps. register.tsx hands in closures that call `$`: the loader follows `$` only inside its own file.
import { message } from './py'
import { T } from './text'

export type Model = 'opus' | 'sonnet'

export type LaunchIO = {
  draft: () => Promise<string>
  handoff: () => Promise<{ text: string; path: string }>
  clear: () => Promise<unknown>
  submit: (text: string) => Promise<{ drop?: string }>
  fill: (text: string) => Promise<void>
  emptyBox: (draft: string) => Promise<void>
  toast: (text: string) => void
  switchModel: (model: string) => Promise<boolean> // true: the chat is now on that model
}

// Until /clear the chat is untouched; after it, a failed step puts handoff + draft back in the box so nothing is lost.
// 'send' submits them as the first message; 'fill' leaves them in the box ('ready') for the owner to pick model and effort in the app and send.
// A model (After reset) is switched after the handoff, before /clear, and holds through /clear; one that did not switch stops the launch with the chat and the draft untouched.
export const launch = async (io: LaunchIO, mode: 'send' | 'fill', model?: Model): Promise<'empty' | 'failed' | 'dropped' | 'sent' | 'ready'> => {
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
  if (model) {
    let isSwitched = false
    let why = ''
    try {
      isSwitched = await io.switchModel(model)
    } catch (err) {
      why = message(err) // a throw counts as a model that did not switch
    }
    if (!isSwitched) {
      io.toast(T.modelSwitchFailed(model, why))
      return 'failed'
    }
  }
  try {
    await io.clear()
  } catch (err) {
    io.toast(T.clearFailed(message(err), handoff.path))
    return 'failed'
  }

  const text = `${handoff.text}\n\n${draft}`
  // every failure from here on names the saved file; if even the box refuses the text, the draft is most likely still there
  const back = async (why: string) => {
    try {
      await io.fill(text)
      return true
    } catch (err) {
      io.toast(T.fillFailed(why, message(err), handoff.path))
      return false
    }
  }
  if (mode === 'fill') return (await back(T.notSent)) ? 'ready' : 'failed' // register.tsx tells the owner what to do next
  let sent
  try {
    sent = await io.submit(text)
  } catch (err) {
    const why = message(err)
    if (await back(why)) io.toast(T.submitFailed(why, handoff.path))
    return 'failed'
  }
  if (sent.drop !== undefined) {
    io.toast(T.dropped(sent.drop, handoff.path))
    return 'dropped'
  }

  await io.emptyBox(draft)
  return 'sent'
}
