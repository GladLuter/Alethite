import re

H = '[скрыто]'
_NAME = r'(?:password|passwd|pwd|secret|token|passphrase|пароль|(?:api|access|secret|private)[_-]?key)'

# (pattern, replacement): group 1, when present, is the part that stays.
_RULES = [(re.compile(p), r) for p, r in [
    (r'(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', H),
    (r'(?<=://)[^\s/:@]*:[^\s/@]+(?=@)', H),
    (r'\b(?:sk-[\w-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,}|(?:AKIA|ASIA)[0-9A-Z]{16}'
     r'|xox[abprs]-[\w-]{10,}|AIza[\w-]{35,}|eyJ[\w-]+\.[\w-]+\.[\w-]+)', H),
    (r'(?i)(\bAuthorization["\']?[ \t]*[:=][ \t]*["\']?(?:\w+[ \t]+)?|\bBearer[ \t]+)[^\s"\',;]{16,}', r'\1' + H),
    # value: after an opening quote it runs to the closing quote, else to a separator; a value of only digits stays
    (r'(?i)(' + _NAME + r'["\']?[ \t]*[:=][ \t]*["\']?)'
     r'(?:(?<=["\'])(?!\d+(?![^"\'\n]))[^"\'\n]+|(?!\d+(?![^\s"\',;]))[^\s"\',;]+)', r'\1' + H),
]]


def redact(text):
    for rx, repl in _RULES:
        text = rx.sub(repl, text)
    return text
