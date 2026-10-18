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

    def test_prefix_tokens(self):
        for raw in ['shpat_' + 'a1' * 16, 'shpss_' + 'b2' * 16, 'hf_' + 'A' * 34, 'glpat-' + 'x' * 20, 'npm_' + 'Z' * 36,
                    'pypi-AgEIcHlwaS5vcmc' + 'A' * 60, 'sk_live_' + 'a' * 24, 'sk_test_' + 'b' * 24, 'rk_live_' + 'c' * 24,
                    'ya29.' + 'a0AfH6SM' * 4, 'SG.' + 'a' * 22 + '.' + 'b' * 43, '123456789:AAH' + 'x' * 32]:
            self.assertEqual(redact('key ' + raw + ' end'), 'key ' + H + ' end', raw)
        self.assertEqual(redact('https://api.telegram.org/bot123456789:AAH' + 'x' * 32 + '/getMe'), 'https://api.telegram.org/bot' + H + '/getMe')

    def test_more_key_names(self):
        for raw, kept in [('DB_PASS=hunter22', 'DB_PASS='), ('pass: hunter22', 'pass: '), ('"pass": "hunter22"', '"pass": "'),
                          ('ENCRYPTION_KEY=abc123xyz', 'ENCRYPTION_KEY='), ('encryption-key: abc123xyz', 'encryption-key: '),
                          ('signing_key = "abc123xyz"', 'signing_key = "'), ('signingKey=abc123xyz', 'signingKey='),
                          ('SESSION_KEY=abc123xyz', 'SESSION_KEY='), ('sessionkey: abc123xyz', 'sessionkey: ')]:
            self.assertEqual(redact(raw), kept + H + ('"' if kept.endswith('"') else ''), raw)

    def test_command_line_forms(self):
        for raw, want in [('curl --token abc123xyz https://x', 'curl --token ' + H + ' https://x'),
                          ('--password hunter22', '--password ' + H), ('--password 123456', '--password ' + H),
                          ('--password=hunter22', '--password=' + H), ('--api-key abc123xyz --v', '--api-key ' + H + ' --v'),
                          ('--secret "my secret phrase" x', '--secret "' + H + '" x'), ('--db-pass hunter22', '--db-pass ' + H),
                          ('mysql -u root -phunter22 db', 'mysql -u root -p' + H + ' db'),
                          ('mysqldump -p"my secret" db', 'mysqldump -p"' + H + '" db')]:
            self.assertEqual(redact(raw), want, raw)

    def test_url_password_with_at(self):
        self.assertEqual(redact('https://user:p@ss@host/x'), 'https://' + H + '@host/x')
        self.assertEqual(redact('git clone https://u:a@b@c@host.org?x=a@b ok'), 'git clone https://' + H + '@host.org?x=a@b ok')

    def test_no_false_positives(self):
        for ok in ['pass the test', 'bypass: true', 'compass=north', 'bypass=1', '--token-budget 500', '--max-tokens 4096',
                   '--secret-key-file key.pem', '--password-stdin', 'ssh -p 22 host', 'docker run -p 8080:80 img', 'mysql -u root -p mydb',
                   'mysql -P3306 db', 'gcc -pthread x.c', 'hf_hub_download(repo)', 'npm_config_cache=/tmp', 'sk_live_ is the Stripe prefix',
                   'pypi-mirror-for-the-intranet-server-of-the-team', 'SG.short.id', 'https://host:8080/a@b', 'at 12:34:56 and 1234567890:abc',
                   'Session key rotation is documented', 'ya29 is a prefix', 'shpat_ is the Shopify prefix','max_tokens = 4096', '"input_tokens": 12, "cache_read_input_tokens": 9000',
                   'token budget is tight', 'password reset flow is broken', 'Кэш истёк, ctx 290k',
                   'https://platform.claude.com/docs/en/about-claude/pricing']:
            self.assertEqual(redact(ok), ok)

if __name__ == '__main__':
    unittest.main()
