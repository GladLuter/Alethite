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
STOP = frozenset('''
a an the and is are was were be been being am of in on at for that this these those it its very just really quite also there which who
whom whose
и а же ли бы вот ведь уж это этот эта эти этого этой этом этим этих то та тот есть был была было были в во на о об обо по для при очень
просто тоже также
і й та ж б би ось адже це цей ця ці цього цієї цьому цим цих той є був була було були на о об по для при дуже просто теж також
'''.split())

_CODE = re.compile(r'```.*?(?:```|\Z)|`[^`\n]*`', re.S)  # a fence a snippet cut off runs to the end
_EMPHASIS = re.compile(r'\*\*(?=\S)(.+?)(?<=\S)\*\*')
_SPACES = re.compile(r'[ \t]+')
_BLANKS = re.compile(r'\n[ \t]*(?:\n[ \t]*)+')
_TOKEN = re.compile(r'(\S+)')
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")  # letters only, an apostrophe or a hyphen inside: пам'ять, що-небудь
_LEAD = '(["\'«“‘[{…'
_TAIL = ')"\'»”’.,;:!?…]}'
_DROP = ',.;:!"\'“”‘’()'  # what ultra takes off a plain word; « » (a match), … (a cut), ? and brackets ([cg-handoff]) stay
_KEEP = '…?'  # what stays of a function word that goes: a cut and a question are no function words
_DASHES = frozenset('-–—:;,.')  # a token of these alone is no word (a list's dash too: the line break keeps the list)


def _word(tok, level):
    """One token of prose at `level` (full or ultra): the token, changed or not, or '' when it goes."""
    i = 0
    while i < len(tok) and tok[i] in _LEAD:
        i += 1
    j = len(tok)
    while j > i and tok[j - 1] in _TAIL:
        j -= 1
    lead, core, tail = tok[:i], tok[i:j], tok[j:]
    if set(tok) <= _DASHES:
        return '' if level == 'ultra' else tok
    if not _WORD.fullmatch(core):
        return tok  # code, a path, a link, a number, a name with digits or _: as it is
    marked = '«' in lead or '»' in tail  # the word the search matched stays whatever it is
    if not marked and core.lower() in STOP:
        return ''.join(c for c in lead + tail if c in _KEEP)
    if level != 'ultra':
        return tok
    if len(core) > 1 and (core == core.lower() or core == core.capitalize()):
        core = core.lower()  # not an abbreviation (API, MEMORY) nor a mixed-case name (GitHub), nor a single letter (git's M)
    return ''.join(c for c in lead if c not in _DROP) + core + ''.join(c for c in tail if c not in _DROP)


def _prose(text, level):
    """A stretch of text without code: its spaces and blank lines squeezed, and at full and ultra its words; the whitespace at its two
    ends stays, so it still parts from the code beside it."""
    pre, mid, post = re.match(r'(\s*)(.*?)(\s*)\Z', text, re.S).groups()
    mid = _EMPHASIS.sub(r'\1', mid)
    if level in ('full', 'ultra'):
        lines = []
        for line in mid.split('\n'):
            words = [w for w in (_word(tok, level) for tok in line.split()) if w]
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
    out, at = [], 0
    for m in _CODE.finditer(text):
        out += [_prose(text[at:m.start()], level), m.group()]
        at = m.end()
    out.append(_prose(text[at:], level))
    return ''.join(out).strip()
