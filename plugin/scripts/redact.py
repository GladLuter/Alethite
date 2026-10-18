import re

H = '[скрыто]'
# shortcut: `pass` counts only when no letter precedes it (DB_PASS, pass; bypass and compass stay), so dbPass is missed; add a case-sensitive camelCase branch if it shows up
_NAME = (r'(?:password|passwd|pwd|(?<![a-z])pass|secret|token|passphrase|пароль'
         r'|(?:api|access|secret|private|encryption|signing|session)[_-]?key)')
# a value after an opening quote runs to the closing quote, else to a separator
_ANY = r'(?:(?<=["\'])[^"\'\n]+|[^\s"\',;]+)'

# (pattern, replacement): group 1, when present, is the part that stays.
_RULES = [(re.compile(p), r) for p, r in [
    (r'(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', H),
    # the password may hold @: it ends at the last @ before the host
    (r'(?<=://)[^\s/:@]*:[^\s/?#]+(?=@)', H),
    (r'\b(?:sk-[\w-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,}|(?:AKIA|ASIA)[0-9A-Z]{16}'
     r'|xox[abprs]-[\w-]{10,}|AIza[\w-]{35,}|eyJ[\w-]+\.[\w-]+\.[\w-]+'
     r'|shp(?:at|ss)_\w{20,}|hf_[A-Za-z0-9]{20,}|glpat-[\w-]{20,}|npm_[A-Za-z0-9]{20,}|pypi-Ag[\w-]{50,}'
     r'|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}|ya29\.[\w-]{20,}|SG\.[\w-]{22}\.[\w-]{43})', H),
    (r'(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}', H),  # Telegram bot token
    (r'(?i)(\bAuthorization["\']?[ \t]*[:=][ \t]*["\']?(?:\w+[ \t]+)?|\bBearer[ \t]+)[^\s"\',;]{16,}', r'\1' + H),
    # value: after an opening quote it runs to the closing quote, else to a separator; a value of only digits stays
    (r'(?i)(' + _NAME + r'["\']?[ \t]*[:=][ \t]*["\']?)'
     r'(?:(?<=["\'])(?!\d+(?![^"\'\n]))[^"\'\n]+|(?!\d+(?![^\s"\',;]))[^\s"\',;]+)', r'\1' + H),
    # `--token <value>`: a flag after it means there is no value; here digits are hidden too. The lookbehind keeps a long run of dashes linear.
    (r'(?i)((?<![\w-])--[\w-]*' + _NAME + r'[ \t]+(?!-)["\']?)' + _ANY, r'\1' + H),
    # `mysql -p<password>`: a space after -p is the prompt form, nothing to hide
    (r'((?i:mysql)\w*\b[^\n|;&]*?[ \t]-p(?=\S)["\']?)' + _ANY, r'\1' + H),
]]


def redact(text):
    for rx, repl in _RULES:
        text = rx.sub(repl, text)
    return text
