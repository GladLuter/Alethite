import unittest
from redact import redact

H = '[скрыто]'

class RedactTest(unittest.TestCase):
    def test_hidden(self):
        for raw in ['sk-ant-api03-' + 'a' * 40, 'sk-or-v1-' + 'b' * 40, 'ghp_' + 'C' * 36,
                    'github_pat_' + 'd' * 40, 'AKIA' + 'E' * 16, 'xoxb-1234567890-abcdefghij',
                    'AIza' + 'f' * 35, 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2ln']:
            self.assertNotIn(raw, redact('key ' + raw + ' end'), raw)

    def test_private_key_block(self):
        pem = '-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3Nza\nxyz\n-----END OPENSSH PRIVATE KEY-----'
        self.assertEqual(redact('a\n' + pem + '\nb'), 'a\n' + H + '\nb')

    def test_key_value_keeps_name(self):
        self.assertEqual(redact('password=hunter22'), 'password=' + H)
        self.assertEqual(redact('пароль: qwerty12'), 'пароль: ' + H)
        self.assertEqual(redact('GITHUB_TOKEN=abc123def456'), 'GITHUB_TOKEN=' + H)
        self.assertEqual(redact('"api_key": "k-123456789"'), '"api_key": "' + H + '"')
        self.assertEqual(redact('Authorization: Bearer abcdefghijklmnop1234'), 'Authorization: Bearer ' + H)
        self.assertEqual(redact('https://user:s3cret@host/x'), 'https://' + H + '@host/x')

    def test_review_round1(self):
        for raw, kept in [('password=1234!abc', 'password='), ('password=2024!', 'password='),
                          ('password=1234-5678', 'password='), ('token=123456.abcdef', 'token='),
                          ('password: "12 345"', 'password: "'), ('SECRET_KEY=abc123xyz', 'SECRET_KEY='),
                          ('secret_key = "abc123xyz"', 'secret_key = "'), ('private_key=abc123xyz', 'private_key='),
                          ('passphrase=abc123xyz', 'passphrase='), ('x-api-key: abc123xyz', 'x-api-key: '),
                          ('api-key=abc123xyz', 'api-key='), ('"secretAccessKey": "abc123xyz"', '"secretAccessKey": "')]:
            self.assertEqual(redact(raw), kept + H + ('"' if kept.endswith('"') else ''), raw)
        for ok in ['pwd: 1234', 'password=123456', 'token: "4096"']:
            self.assertEqual(redact(ok), ok)

    def test_no_false_positives(self):
        for ok in ['max_tokens = 4096', '"input_tokens": 12, "cache_read_input_tokens": 9000',
                   'token budget is tight', 'password reset flow is broken', 'Кэш истёк, ctx 290k',
                   'https://platform.claude.com/docs/en/about-claude/pricing']:
            self.assertEqual(redact(ok), ok)

if __name__ == '__main__':
    unittest.main()
