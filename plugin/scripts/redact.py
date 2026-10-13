import re

H = '[скрыто]'
_NAME = r'(?:password|passwd|pwd|secret|token|api_key|apikey|access_key|пароль)'

# (pattern, replacement): group 1, when present, is the part that stays.
_RULES = [(re.compile(p), r) for p, r in [
    (r'(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', H),
    (r'(?<=://)[^\s/:@]*:[^\s/@]+(?=@)', H),
    (r'\b(?:sk-[\w-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,}|(?:AKIA|ASIA)[0-9A-Z]{16}'
     r'|xox[abprs]-[\w-]{10,}|AIza[\w-]{35,}|eyJ[\w-]+\.[\w-]+\.[\w-]+)', H),
    (r'(?i)(\bAuthorization["\']?[ \t]*[:=][ \t]*["\']?(?:\w+[ \t]+)?|\bBearer[ \t]+)[^\s"\',;]{16,}', r'\1' + H),
    # value: all digits stay; after an opening quote take up to the closing quote, else up to a separator
    (r'(?i)(' + _NAME + r'["\']?[ \t]*[:=][ \t]*["\']?)(?!\d+\b)(?:(?<=["\'])[^"\'\n]+|[^\s"\',;]+)', r'\1' + H),
]]


def redact(text):
    for rx, repl in _RULES:
        text = rx.sub(repl, text)
    return text
