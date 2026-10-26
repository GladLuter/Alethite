import re

H = '[скрыто]'
# shortcut: `pass` counts only when no letter precedes it (DB_PASS, pass; bypass and compass stay), so dbPass is missed; add a case-sensitive camelCase branch if it shows up.
# After a word and a space it is mostly the verb ("tests pass: all green"): there it counts only before a value with a digit or a sign
# ("admin pass: hunter22"), so a password of letters alone written that way is missed.
_NAME = (r'(?:password|passwd|pwd|(?<![a-z])(?:(?<![^\W\d_][ \t])pass'
         r'|pass(?=["\']?[ \t]*[:=][ \t]*["\']?[^\s"\',;]*?(?![^\W\d_])[^\s"\',;]))|secret|token|passphrase|пароль'
         r'|(?:api|access|secret|private|encryption|signing|session)[_-]?key\w*)')
# a value after an opening quote runs to the closing quote, else to a separator
_ANY = r'(?:(?<=["\'])[^"\'\n]+|[^\s"\',;]+)'
# what follows a flag's name in prose ("the --token flag", "--token or --password"): no value, nothing to hide. Only a whole word
# with at most closing punctuation after it: "Or@nge99" or "a!b" starts like prose and is a password
_PROSE = (r'(?:flags?|options?|arguments?|args?|parameters?|params?|switch|value|and|or|to|with|for|of|in|is|the|a|an'
          r'|флаг\w*|опци\w*|параметр\w*|ключ\w*|прапор\w*|опці\w*|и|или|і|або)(?=[.,;:)]*(?:\s|\Z))')

# (pattern, replacement): group 1, when present, is the part that stays.
_RULES = [(re.compile(p), r) for p, r in [
    # a block cut short (a tool summary keeps 300 characters) is hidden to the end of the text
    (r'(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)', H),
    (r'https://(?:hooks\.slack\.com/services|discord(?:app)?\.com/api/webhooks)/\S+', H),
    # the password may hold @: it ends at the last @ before the host
    (r'(?<=://)[^\s/:@]*:[^\s/?#]+(?=@)', H),
    (r'\b(?:sk-[\w-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,}|(?:AKIA|ASIA)[0-9A-Z]{16}'
     r'|xox[abprs]-[\w-]{10,}|AIza[\w-]{35,}|eyJ[\w-]+\.[\w-]+\.[\w-]+'
     r'|shp(?:at|ss)_\w{20,}|hf_[A-Za-z0-9]{20,}|glpat-[\w-]{20,}|npm_[A-Za-z0-9]{20,}|pypi-Ag[\w-]{50,}'
     r'|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}|ya29\.[\w-]{20,}|SG\.[\w-]{22}\.[\w-]{43})', H),
    (r'(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}', H),  # Telegram bot token
    # the recovery code of the encryption key (crypto.make_code): written down, it is typed back with hyphens, spaces or neither, so it goes
    # whole before the rules below can cut it at a space
    (r'(?i)\bCG1(?:[- ]?[A-Z2-7]{4}){14}\b', H),
    (r'(?i)(\bAuthorization["\']?[ \t]*[:=][ \t]*["\']?(?:\w+[ \t]+)?|\bBearer[ \t]+)[^\s"\',;]{16,}', r'\1' + H),
    (r'(?i)(\bAuthorization["\']?[ \t]*[:=][ \t]*["\']?Basic[ \t]+)[A-Za-z0-9+/=]{8,}', r'\1' + H),  # user:password in base64, short too
    # value: after an opening quote it runs to the closing quote, else to a separator; a value of only digits stays.
    # Quotes may come escaped (JSON inside a command: \"password\":\"x\"), and then the value ends before the backslash.
    (r'(?i)(' + _NAME + r'\\*["\']?[ \t]*[:=][ \t]*\\*["\']?)'
     r'(?:(?<=["\'])(?!\d+(?![^"\'\n]))(?:[^"\'\n\\]|\\(?!["\']))+|(?!\d+(?![^\s"\',;]))(?:[^\s"\',;\\]|\\(?!["\']))+)', r'\1' + H),
    # `--token <value>` (PowerShell: `-Password <value>`): a flag or a word of prose after it means there is no value; here digits
    # are hidden too. The lookbehind keeps a long run of dashes linear.
    (r'(?i)((?<![\w-])--?[\w-]*' + _NAME + r'[ \t]+(?!-|' + _PROSE + r')["\']?)' + _ANY, r'\1' + H),
    # `mysql -p<password>`: a space after -p is the prompt form, nothing to hide. The search for -p stops at the next mysql,
    # which starts a search of its own, and a mysql right after a letter or a digit starts none (run_mysql.sh does): a line with
    # thousands of mysql stays linear.
    (r'((?<![^\W_])(?i:mysql)(?:(?!(?<![^\W_])(?i:mysql))[^\n|;&])*?[ \t]-p(?=\S)["\']?)' + _ANY, r'\1' + H),
    # other commands that take a password as an argument
    (r'(?i)(\bcurl\b[^\n|;&]*?\s(?:-u|--user)[ =]+[^\s:]+:)\S+', r'\1' + H),
    (r'(?i)(\b(?:sshpass|docker\s+login)\b[^\n|;&]*?\s-p\s*)\S+', r'\1' + H),
    (r'(?i)(\bnet\s+use\s+(?:\S+\s+)?\\\\\S+\s+)(?!/)\S+', r'\1' + H),
]]


def redact(text):
    for rx, repl in _RULES:
        text = rx.sub(repl, text)
    return text
