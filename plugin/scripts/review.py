"""The /ponytail-review hint: is the third-party ponytail plugin installed, and how many code edits has the chat made since its last review.
Nothing of ponytail is copied or used: only its name in the plugin files and its command in the transcripts are looked at."""
import json
import os
import re

from transcripts import _blocks, _load, lp

EDIT_TOOLS = ('Edit', 'Write', 'NotebookEdit')
REVIEW = 'ponytail-review'
_COMMAND = re.compile(r'<command-name>/(?:[\w-]+:)?' + REVIEW + r'</command-name>')  # how a typed slash command is stored


def _json(path):
    try:
        with open(lp(path), encoding='utf-8-sig') as f:
            o = json.load(f)
    except (OSError, ValueError):
        return {}
    return o if isinstance(o, dict) else {}


def ponytail(home):
    """True when a `ponytail@...` plugin is installed (a key of installed_plugins.json, at the top or under `plugins`) and not switched off in enabledPlugins."""
    d = os.path.join(home, '.claude')
    installed = _json(os.path.join(d, 'plugins', 'installed_plugins.json'))
    keys = list(installed['plugins'] if isinstance(installed.get('plugins'), dict) else installed)
    enabled = _json(os.path.join(d, 'settings.json')).get('enabledPlugins')
    enabled = enabled if isinstance(enabled, dict) else {}
    return any(isinstance(k, str) and k.startswith('ponytail@') and enabled.get(k) is not False for k in keys)


def _events(path):
    """(time, 'edit' | 'review') of the lines of one transcript that are one of them."""
    try:
        with open(lp(path), 'rb') as f:
            lines = f.read().split(b'\n')
    except OSError:
        return
    for raw in lines:
        if b'ponytail-review' not in raw and b'"Edit"' not in raw and b'"Write"' not in raw and b'"NotebookEdit"' not in raw:
            continue
        o = _load(raw)
        m = o.get('message') if o else None
        if not isinstance(m, dict):
            continue
        ts = o.get('timestamp') if isinstance(o.get('timestamp'), str) else ''
        for b in _blocks(m.get('content')):
            inp = b.get('input') if isinstance(b.get('input'), dict) else {}
            if o.get('type') == 'assistant' and b.get('type') == 'tool_use':
                if b.get('name') in EDIT_TOOLS:
                    yield ts, 'edit'
                elif (b.get('name') == 'Skill' and REVIEW in str(inp.get('skill'))) or (b.get('name') == 'Agent' and REVIEW in str(inp.get('prompt'))):
                    yield ts, 'review'
            elif o.get('type') == 'user' and b.get('type') == 'text' and isinstance(b.get('text'), str):
                text = b['text'].strip()
                if text.startswith('/' + REVIEW) or _COMMAND.search(text):
                    yield ts, 'review'


def edits(paths):
    """Edit, Write and NotebookEdit calls after the last review, over all the transcripts (the chat's and its agents'), by the time of their lines."""
    events = sorted((e for p in paths for e in _events(p)), key=lambda e: e[0])
    n = 0
    for _, kind in events:
        n = 0 if kind == 'review' else n + 1
    return n
