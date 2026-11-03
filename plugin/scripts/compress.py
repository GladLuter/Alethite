"""The archiver of stage 3: mechanical compression of the memory text the mod gives the model (recall hits, recall_more rows, the quotes
of a handoff). No model calls: the same text always gives the same result. Code, paths, links, numbers and names are never touched.

off    nothing
lite   runs of spaces, blank lines, markdown emphasis
full   lite + function words (articles, copulas, fillers) of English, Russian and Ukrainian; negations and joining words stay
ultra  full + punctuation and capital letters of plain words"""
import re

LEVELS = ('off', 'lite', 'full', 'ultra')

# Words a reader does not miss. Never here: negations (not, no, не, нет, ні, без), words that join or oppose (or, but, if, или, но,
# если, або, але, якщо), quantifiers (all, only, все, только, лише), prepositions of direction or source (to, from, with, к, из, з, від),
# possession (have, у), the verbs do and have, which also carry meaning of their own. One set for the three languages: a function word of
# one of them is no content word of another.
# of and who stay: "12 of 40 failed", "who deleted it?" lose their sense without them. So do on ("compression is on"), just and просто
# ("not just a bug" is no "not a bug"), бы, б, би (what would have happened did not) and ли (a question).
STOP = frozenset('''
a an the and is are was were be been being am in at for that this these those it its very really quite also there which
и а же вот ведь уж это этот эта эти этого этой этом этим этих то та тот есть был была было были в во на о об обо по для при очень
тоже также
і й та ж ось адже це цей ця ці цього цієї цьому цим цих той є був була було були на о об по для при дуже теж також
'''.split())

# shortcut: only code in backticks is safe; a path with spaces (C:\Documents and Settings) or a commit message in quotes written as
# prose loses its function words like prose does. Teach _prose to spare quoted runs if a worker misreads one.
_CODE = re.compile(r'```.*?(?:```|\Z)|`[^`\n]*`', re.S)  # a fence a snippet cut off runs to the end
# around whole words only, not inside a formula or a path (x**2, src/**/*.ts); the inside never runs past another ** (a long line stays linear)
_EMPHASIS = re.compile(r'(?<![\w*/])\*\*(?=\S)((?:(?!\*\*).)+?)(?<=\S)\*\*(?![\w*/])')
_SPACES = re.compile(r'[ \t]+')
_BLANKS = re.compile(r'\n[ \t]*(?:\n[ \t]*)+')
_TOKEN = re.compile(r'(\S+)')
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")  # letters only, an apostrophe or a hyphen inside: пам'ять, що-небудь
_LEAD = '(["\'«“‘[{…'
_TAIL = ')"\'»”’.,;:!?…]}'
_DROP = ',.;:!"\'“”‘’()'  # what ultra takes off a plain word; « » (a match), … (a cut), ? and brackets ([al-handoff]) stay
_KEEP = '…?'  # what stays of a function word that goes: a cut and a question are no function words
_DASHES = frozenset('-–—:;,.')  # a token of these alone is no word
_MARKERS = frozenset('-.')  # but of these alone (-, --, ., ..) it is part of a command (cd .., git checkout -) unless it opens the line: a list's dash


def _word(tok, level, first=False):
    """One token of prose at `level` (full or ultra): the token, changed or not, or '' when it goes. `first`: it opens its line."""
    i = 0
    while i < len(tok) and tok[i] in _LEAD:
        i += 1
    j = len(tok)
    while j > i and tok[j - 1] in _TAIL:
        j -= 1
    lead, core, tail = tok[:i], tok[i:j], tok[j:]
    if set(tok) <= _DASHES:
        return '' if level == 'ultra' and (first or not set(tok) <= _MARKERS) else tok
    if not _WORD.fullmatch(core):
        return tok  # code, a path, a link, a number, a name with digits or _: as it is
    marked = '«' in lead or '»' in tail  # the word the search matched stays whatever it is
    # an abbreviation is no function word: at 9 AM, Mode: ON; nor is a capital letter inside a line (plan A or plan B)
    if not marked and core.lower() in STOP and not (core.isupper() and (len(core) > 1 or not first)):
        return ''.join(c for c in lead + tail if c in _KEEP)
    if level != 'ultra':
        return tok
    if not marked and len(core) > 1 and (core == core.lower() or core == core.capitalize()):
        core = core.lower()  # not an abbreviation (API, MEMORY) nor a mixed-case name (GitHub), nor a single letter (git's M)
    return ''.join(c for c in lead if c not in _DROP) + core + ''.join(c for c in tail if c not in _DROP)


def _prose(text, level):
    """A stretch of text without code: its spaces and blank lines squeezed, and at full and ultra its words; the whitespace at its two
    ends stays, so it still parts from the code beside it."""
    start = len(text) - len(text.lstrip())  # slices, not a regular expression: that was quadratic on a long run of spaces
    end = max(start, len(text.rstrip()))
    pre, mid, post = text[:start], text[start:end], text[end:]
    mid = _EMPHASIS.sub(r'\1', mid)
    if level in ('full', 'ultra'):
        lines = []
        for line in mid.split('\n'):
            words = [w for w in (_word(tok, level, i == 0) for i, tok in enumerate(line.split())) if w]
            lines.append(line[:len(line) - len(line.lstrip())] + ' '.join(words))  # the indent of a list item stays
        mid = '\n'.join(lines)
    squeezed = []
    for line in mid.split('\n'):
        rest = line.lstrip(' \t')
        squeezed.append(line[:len(line) - len(rest)] + _SPACES.sub(' ', rest).rstrip())
    return _SPACES.sub(' ', pre) + _BLANKS.sub('\n\n', '\n'.join(squeezed)) + _SPACES.sub(' ', post)


def compress(text, level):
    """`text` at `level`, one of LEVELS. Code (fenced or inline) goes through as it is."""
    if level == 'off' or not text:
        return text
    if level not in LEVELS:
        raise ValueError('compression level: %s (%s)' % (level, ', '.join(LEVELS)))
    if text.count('```') % 2:
        return text  # a snippet cut a fence in two: which side is code cannot be told, so none of it is touched
    out, at = [], 0
    for m in _CODE.finditer(text):
        out += [_prose(text[at:m.start()], level), m.group()]
        at = m.end()
    out.append(_prose(text[at:], level))
    return ''.join(out).strip()
