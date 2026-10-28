"""Mechanical handoff for a new worker chat: the owner's last words, Claude's last reply, git state. No model calls."""
import glob
import os
import subprocess

from compress import compress
from texts import t
from transcripts import HANDOFF_END, HANDOFF_START, find_session, read_records

_CAP = 10500  # the whole text
_STOPPED_CAP = 7000  # the "where we stopped" section
_ENV = dict(os.environ, LC_ALL='C', GIT_OPTIONAL_LOCKS='0')  # English errors for the "not a repository" check; status must not write to the owner's repo
_GIT_SECONDS = 10


def _clip(s, n):
    return s if len(s) <= n else s[:n] + '…'


def _git(root, *args):
    """(ok, stdout lines) or (False, [first line of the error])."""
    try:
        p = subprocess.run(['git', '-C', root, '-c', 'core.quotepath=off', *args], capture_output=True, timeout=_GIT_SECONDS, env=_ENV)
    except subprocess.TimeoutExpired:  # its text is the whole command line, a different one for every folder
        return False, [t('handoff_git_timeout', s=_GIT_SECONDS)]
    except (OSError, subprocess.SubprocessError) as e:
        return False, [str(e)]
    lines = (p.stdout if p.returncode == 0 else p.stderr).decode('utf-8', errors='replace').splitlines()
    return p.returncode == 0, lines if p.returncode == 0 else (lines[:1] or ['exit %d' % p.returncode])


def _show(root, *args):
    ok, out = _git(root, *args)
    return out if ok else ['(git: %s)' % out[0]]


def _repos(dirs):
    """Text blocks for the git repositories around `dirs`, 3 at most; a git failure is a block of its own, once."""
    roots, blocks, seen = set(), [], set()
    for d in dirs:
        if len(roots) == 3:
            break
        if not d or d in seen or not os.path.isdir(d):
            continue
        seen.add(d)
        ok, out = _git(d, 'rev-parse', '--show-toplevel')
        if ok:
            root = os.path.normpath(out[0])
            if os.path.normcase(root) not in roots:
                roots.add(os.path.normcase(root))
                blocks.append('\n'.join(['### ' + root, *_show(root, 'status', '--short')[:30], t('handoff_commits'), *_show(root, 'log', '--oneline', '-5')]))
        elif 'not a git repository' not in out[0] and '(git: %s)' % out[0] not in blocks:
            blocks.append('(git: %s)' % out[0])
    return blocks


def changed_files(recs, subs):
    """Files edited in the main chat and its subagents, newest first, 30 at most."""
    edits = sorted((r for r in recs + [x for p in subs for x in read_records(p, only=b'tool_use')[0]] if 'edit' in r), key=lambda r: r['ts'])
    return list(dict.fromkeys(r['edit'] for r in reversed(edits)))[:30]


def build_handoff(projects, session_id, today, memory_dir=None, level='off'):
    """The handoff text; the quotes of the conversation are compressed at `level` (stage 3), the rest is left as it is."""
    main, subs = find_session(projects, session_id)
    if not main:
        raise ValueError(t('session_not_found', id=session_id, dir=projects))
    recs = read_records(main)[0]
    owner = [r for r in recs if r['kind'] == 'owner']
    turns, fresh = [], True  # Claude's replies: all its texts from one owner message to the next
    for r in recs:
        if r['kind'] == 'owner':
            fresh = True
        elif r['kind'] == 'claude':
            if fresh:
                turns.append((r['pos'], []))
            turns[-1][1].append(r['text'])
            fresh = False

    def stopped(items):
        # by pos: the order of the conversation; the parts of one message share its pos and keep their own order (a stable sort)
        return t('handoff_stopped') + '\n' + '\n\n'.join(s for _, s in sorted(items, key=lambda i: i[0]))

    fixed = [(r['pos'], t('handoff_owner') + _clip(compress(r['text'], level), 1500)) for r in owner[-4:]]
    if turns and (not owner or turns[-1][0] > owner[-1]['pos']):
        pos, parts = turns.pop()
        s = compress('\n\n'.join(parts), level)  # the tail is kept: the probe lost the decisive last paragraph by clipping the head only
        fixed.append((pos, t('handoff_claude') + (s if len(s) <= 2400 else s[:1200] + '\n…\n' + s[-1000:])))
    early = []  # newest first
    for pos, parts in reversed(turns):
        item = (pos, t('handoff_claude') + _clip(compress('\n\n'.join(parts), level), 800))
        if len(stopped(fixed + early + [item])) > _STOPPED_CAP:
            break
        early.append(item)

    # the edited files only lead to their repositories: a list of them is no task for the next chat, which may well be about something
    # else (the owner, stage 4); recall finds them when it is not
    files = changed_files(recs, subs)
    cwd = next((r['cwd'] for r in reversed(recs) if r['cwd']), '')
    git = _repos([cwd] + [os.path.dirname(f) for f in files])  # the work may have happened outside the chat's folder

    head = [t('handoff_head', id=session_id, today=today)]
    if memory_dir:
        n = sum(os.path.basename(f) != 'MEMORY.md' for f in glob.glob(os.path.join(glob.escape(memory_dir), '*.md')))
        head.append(t('handoff_memory' if n else 'handoff_no_memory', dir=memory_dir, n=n))
    head += [t('handoff_recall', id=session_id),
             t('handoff_resume', cwd=next((r['cwd'] for r in recs if r['cwd']), ''), id=session_id)]

    def render():
        parts = ['\n'.join(head), stopped(fixed + early)] + ([t('handoff_state') + '\n' + '\n\n'.join(git)] if git else [])
        # a marker quoted inside the text would end the block early for strip_handoff
        body = '\n\n'.join(parts).replace(HANDOFF_START, '[cg-handoff ]').replace(HANDOFF_END, '[/cg-handoff ]')
        return HANDOFF_START + '\n' + body + '\n' + HANDOFF_END

    out = render()
    while len(out) > _CAP and early:
        early.pop()  # the oldest early reply first
        out = render()
    # shortcut: if the owner's messages, the last reply and git alone exceed the cap, the end of the text is cut; the state is last, so it goes first
    return out if len(out) <= _CAP else out[:_CAP - len(HANDOFF_END) - 2] + '…\n' + HANDOFF_END
