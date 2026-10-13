import json
import os
import re

HANDOFF_START = '[cg-handoff]'
HANDOFF_END = '[/cg-handoff]'
_HANDOFF = re.compile(re.escape(HANDOFF_START) + r'.*?(?:' + re.escape(HANDOFF_END) + r'|\Z)', re.S)
# user text Claude Code injects without isMeta: cc_token_audit.INJECTED plus the echo of a typed slash command
INJECTED = re.compile(r'\s*<(task-notification|local-command-|system-reminder|command-(?:name|message)>)')
_REMINDER = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
_WORKTREE = re.compile(r'[\\/]\.claude[\\/]worktrees(?:[\\/].*)?$')
_ARGS = ('command', 'file_path', 'notebook_path', 'path', 'pattern', 'skill', 'url', 'query', 'description')
_USAGE = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')
_TAIL = 256 * 1024


def lp(p):
    """Absolute path; the \\\\?\\ prefix lifts the 260-char Windows limit (nested subagent paths exceed it)."""
    p = os.path.abspath(os.path.expanduser(p))
    # shortcut: UNC paths (\\server\share) are left as is, they need \\?\UNC\ if they ever hit the limit
    return '\\\\?\\' + p if os.name == 'nt' and not p.startswith('\\\\') else p


def strip_handoff(text):
    """Cut every [cg-handoff] block, up to its end marker or, without one, to the end of the text."""
    return _HANDOFF.sub('', text)


def project_key(cwd):
    """Same key for a project folder and for any of its worktrees."""
    return _WORKTREE.sub('', os.path.normcase(os.path.normpath(cwd)))


def _load(raw):
    try:
        o = json.loads(raw.decode('utf-8', errors='replace'))
    except ValueError:
        return None
    return o if isinstance(o, dict) else None


def _blocks(c):
    if isinstance(c, str):
        return [{'type': 'text', 'text': c}]
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def _typed(text, kind):
    """What a person (or, in a subagent, the parent agent) wrote, without injected parts."""
    if not isinstance(text, str):
        return []
    text = _REMINDER.sub('', strip_handoff(text)).strip()
    text = '' if INJECTED.match(text) else text  # what is left still starts like injected text: an unclosed reminder, a slash command
    return [(kind, text, None)] if text else []


def _tool(b):
    inp = b.get('input') if isinstance(b.get('input'), dict) else {}
    name = b.get('name') or 'unknown'
    arg = next((inp[k] for k in _ARGS if isinstance(inp.get(k), str) and inp[k].strip()), '')
    path = inp.get('file_path') or inp.get('notebook_path')
    edit = path if name in ('Edit', 'Write', 'NotebookEdit') and isinstance(path, str) and path else None
    return 'tool', name + ': ' + ' '.join(arg.split())[:300] if arg else name, edit


def _items(o, sub):
    """(kind, text, edit) for each indexable piece of one transcript line."""
    t, m = o.get('type'), o.get('message')
    m = m if isinstance(m, dict) else {}
    out = []
    if t == 'user' and not (o.get('isMeta') or o.get('isCompactSummary')):
        for b in _blocks(m.get('content')):  # tool_result and image blocks are not indexed
            if b.get('type') == 'text':
                out += _typed(b.get('text'), 'task' if sub else 'owner')
    elif t == 'attachment' and isinstance(o.get('attachment'), dict):
        a = o['attachment']  # a message typed while Claude was busy
        if a.get('type') == 'queued_command' and not a.get('isMeta') and a.get('commandMode') != 'task-notification':
            out += _typed(a.get('prompt'), 'task' if sub else 'owner')
    elif t == 'assistant' and m.get('model') != '<synthetic>':
        for b in _blocks(m.get('content')):
            if b.get('type') == 'text' and isinstance(b.get('text'), str) and b['text'].strip():
                out.append(('sub' if sub else 'claude', b['text'].strip(), None))
            elif b.get('type') == 'tool_use':
                out.append(_tool(b))
    return out


def read_records(path, start=0):
    """Records from the whole lines at byte offset `start` on, and the offset right after the last '\\n'."""
    with open(lp(path), 'rb') as f:
        f.seek(start)
        data = f.read()
    end = data.rfind(b'\n') + 1
    sub = 'subagents' in os.path.normpath(path).split(os.sep)
    recs, pos = [], start
    for raw in data[:end].split(b'\n')[:-1]:
        at, pos = pos, pos + len(raw) + 1
        o = _load(raw)
        for kind, text, edit in _items(o, sub) if o else ():
            r = {'pos': at, 'ts': o.get('timestamp') or '', 'cwd': o.get('cwd') or '', 'kind': kind, 'text': text}
            if edit:
                r['edit'] = edit
            recs.append(r)
    return recs, start + end


def find_session(projects, session_id):
    """(main transcript or None, all its subagent transcripts, nested workflow agents included)."""
    main, subs = None, []
    try:
        dirs = sorted(os.listdir(lp(projects)))
    except OSError:
        return None, []
    for d in dirs:
        f = os.path.join(projects, d, session_id + '.jsonl')
        main = main or (f if os.path.isfile(lp(f)) else None)
        sd = os.path.join(projects, d, session_id, 'subagents')
        top = lp(sd)  # os.walk yields nothing for a missing folder; dp[len(top):] turns the \\?\ form back into the caller's
        subs += sorted(os.path.join(sd + dp[len(top):], n) for dp, _, ns in os.walk(top)
                       for n in ns if n.startswith('agent-') and n.endswith('.jsonl'))
    return main, subs


def last_usage(path):
    """Prompt+output tokens of the last API call in the last 256 KB of a main transcript, or None."""
    with open(lp(path), 'rb') as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - _TAIL))
        data = f.read()
    best = None
    for raw in data.split(b'\n'):  # a line cut by the window is not valid JSON and is skipped
        o = _load(raw)
        m = o.get('message') if o and o.get('type') == 'assistant' else None
        u = m.get('usage') if isinstance(m, dict) and m.get('model') != '<synthetic>' else None
        if isinstance(u, dict):
            best = {'tokens': sum(v for v in (u.get(k) for k in _USAGE) if isinstance(v, int)),
                    'at': o.get('timestamp') or '', 'model': m.get('model') or ''}
    return best
