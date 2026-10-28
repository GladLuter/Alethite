import json
import ntpath
import os
import posixpath
import re

HANDOFF_START = '[cg-handoff]'
HANDOFF_END = '[/cg-handoff]'
# a whole block anywhere, an unclosed one only at the start: a mention of the marker in the owner's text must not cut the rest of it
_HANDOFF = re.compile(r'\A\s*%s.*?(?:%s|\Z)|%s.*?%s' % ((re.escape(HANDOFF_START), re.escape(HANDOFF_END)) * 2), re.S)
# user text Claude Code injects without isMeta: cc_token_audit.INJECTED plus the echo of a typed slash command
INJECTED = re.compile(r'\s*<(task-notification|local-command-|system-reminder|command-(?:name|message)>)')
_REMINDER = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
# the lessons the mod put beside the owner's message (stage 4), wherever the engine keeps that context: not the owner's words;
# a closed block only (the mod writes both marks): a mention of the mark in the owner's text must not cut the rest of it
_LESSONS = re.compile(r'\[cg-lessons\].*?\[/cg-lessons\]', re.S)
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


def _norm(p):
    """A path in the one form keys are compared in on every computer: a Windows path (a drive or a share, and every path on Windows)
    by Windows rules, lower case and backslashes, whatever system reads it; a relative one made absolute."""
    m = ntpath if os.name == 'nt' or re.match(r'[A-Za-z]:|[\\/]{2}[^\\/]', p) else posixpath
    if not m.isabs(p):
        p = os.path.abspath(p)
    return m.normcase(m.normpath(p)), m.sep


def project_key(cwd, home=None):
    """Same key for a project folder and for any of its worktrees. Under the home folder (`home`, by default this computer's) it starts
    with ~, so one project inside two profiles (C:\\Users\\AIJ, C:\\Users\\gladl) has one key on both computers."""
    key, sep = _norm(cwd)
    key = _WORKTREE.sub('', key)
    h, hsep = _norm(os.path.expanduser('~') if home is None else home)
    h = h.rstrip(hsep)
    if h and hsep == sep and (key == h or key.startswith(h + sep)):
        key = '~' + key[len(h):]
    return key


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
    text = _LESSONS.sub('', _REMINDER.sub('', strip_handoff(text))).strip()
    text = '' if INJECTED.match(text) or text.startswith('[Request interrupted by user') else text  # what is left still starts like injected text: an unclosed reminder, a slash command, an Esc interrupt
    return [(kind, text, None)] if text else []


def _tool(b):
    inp = b.get('input') if isinstance(b.get('input'), dict) else {}
    name = b.get('name') if isinstance(b.get('name'), str) and b.get('name') else 'unknown'  # a broken line must not stop the file
    arg = next((inp[k] for k in _ARGS if isinstance(inp.get(k), str) and inp[k].strip()), '')
    path = inp.get('file_path') or inp.get('notebook_path')
    edit = path if name in ('Edit', 'Write', 'NotebookEdit') and isinstance(path, str) and path else None
    return 'tool', name + ': ' + ' '.join(arg.split())[:300] if arg else name, edit


ERROR_CHARS = 500
# no trace to find again: a bare exit code, a refusal without the owner's words (with them it has a colon: "... the user said: ...")
_BARE_ERROR = re.compile(r"Exit code \d+|The user doesn't want to proceed with this tool use\.[^:]*")


def _error(b):
    """A failed tool call's result, its head and tail: the trace a past error is found by again (429, ENOENT, 'database is locked')."""
    c = b.get('content')
    text = c if isinstance(c, str) else '\n'.join(x['text'] for x in _blocks(c) if x.get('type') == 'text' and isinstance(x.get('text'), str))
    text = ' '.join(_LESSONS.sub('', _REMINDER.sub('', text)).split())  # the gate's denial is the lesson's text, not an error to find again
    if _BARE_ERROR.fullmatch(text):
        return []
    if len(text) > ERROR_CHARS:
        text = text[:200] + ' … ' + text[-(ERROR_CHARS - 200):]  # a traceback ends with the error itself
    return [('error', text, None)] if text else []


def _items(o, sub):
    """(kind, text, edit) for each indexable piece of one transcript line."""
    t, m = o.get('type'), o.get('message')
    m = m if isinstance(m, dict) else {}
    out = []
    if t == 'user' and not (o.get('isMeta') or o.get('isCompactSummary')):
        for b in _blocks(m.get('content')):  # a successful tool_result and image blocks are not indexed
            if b.get('type') == 'text':
                out += _typed(b.get('text'), 'task' if sub else 'owner')
            elif b.get('type') == 'tool_result' and b.get('is_error') is True:
                out += _error(b)
    elif t == 'attachment' and isinstance(o.get('attachment'), dict):
        a = o['attachment']  # a message typed while Claude was busy
        if a.get('type') == 'queued_command' and not a.get('isMeta') and a.get('commandMode') != 'task-notification':
            prompt = a.get('prompt')
            if isinstance(prompt, list):  # a queued message with a picture is a list of blocks: its text is the message
                prompt = '\n'.join(b['text'] for b in _blocks(prompt) if b.get('type') == 'text' and isinstance(b.get('text'), str))
            out += _typed(prompt, 'task' if sub else 'owner')
    elif t == 'assistant' and m.get('model') != '<synthetic>':
        for b in _blocks(m.get('content')):
            if b.get('type') == 'text' and isinstance(b.get('text'), str) and b['text'].strip():
                out.append(('sub' if sub else 'claude', b['text'].strip(), None))
            elif b.get('type') == 'tool_use' and not str(b.get('name')).startswith('mcp__cacheguard__'):  # the summary of a recall would be the top hit of the very query that made it
                out.append(_tool(b))
    return out


def read_records(path, start=0, only=None):
    """Records from the whole lines at byte offset `start` on, and the offset right after the last '\\n'."""
    # with `only`, lines without that substring are not parsed
    with open(lp(path), 'rb') as f:
        f.seek(start)
        data = f.read()
    end = data.rfind(b'\n') + 1
    sub = 'subagents' in os.path.normpath(path).split(os.sep)
    recs, pos = [], start
    for raw in data[:end].split(b'\n')[:-1]:
        at, pos = pos, pos + len(raw) + 1
        o = _load(raw) if only is None or only in raw else None
        for kind, text, edit in _items(o, sub) if o else ():
            # a lone surrogate (\ud83d of a cut emoji) cannot be stored or written as UTF-8 and would fail the whole file every time
            text = text.encode('utf-8', 'replace').decode('utf-8')
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
    """Prompt+output tokens of the last API call of a main transcript, or None. The window of 256 KB at the end doubles until it holds one."""
    with open(lp(path), 'rb') as f:
        f.seek(0, 2)
        size, window = f.tell(), _TAIL
        while True:
            f.seek(max(0, size - window))
            best = _last_usage(f.read())
            if best or window >= size:
                return best
            window *= 2


def _last_usage(data):
    best = None
    for raw in data.split(b'\n'):  # a line cut by the window is not valid JSON and is skipped
        o = _load(raw)
        m = o.get('message') if o and o.get('type') == 'assistant' else None
        u = m.get('usage') if isinstance(m, dict) and m.get('model') != '<synthetic>' else None
        if isinstance(u, dict):
            best = {'tokens': sum(v for v in (u.get(k) for k in _USAGE) if isinstance(v, int)),
                    'at': o.get('timestamp') or '', 'model': m.get('model') or ''}
    return best
