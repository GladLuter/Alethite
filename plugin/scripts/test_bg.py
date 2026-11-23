import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock
import bg


class BgTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def start(self, cmd, **kw):
        args = dict(session='s1', name='n', command=cmd, cwd=self.tmp, status_file=None, if_cold='show')
        args.update(kw)
        return bg.start(self.tmp, **args)

    def finished(self, d):
        path = os.path.join(d, 'exit.json')
        end = time.monotonic() + 15
        while not os.path.exists(path) and time.monotonic() < end:
            time.sleep(0.1)
        while bg.alive(d) and time.monotonic() < end:  # the wrapper drops its lock when the process exits, after exit.json
            time.sleep(0.05)
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def test_start_fails_when_the_wrapper_does_not_start(self):
        with mock.patch('bg.spawn_detached', return_value=1):
            with self.assertRaises(RuntimeError):
                self.start('echo ok')

    def test_start_returns_at_once(self):
        t0 = time.monotonic()
        r = self.start('ping -n 6 127.0.0.1 >nul')
        self.assertLess(time.monotonic() - t0, 2)
        self.assertTrue(bg.alive(r['dir']))
        self.finished(r['dir'])

    def test_exit_code_and_log(self):
        arg = os.path.join(self.tmp, 'arg.txt')  # the command line reaches the command intact; cmd's own echo into the log is lossy (OEM page)
        py = "import sys; open(r'%s', 'w', encoding='utf-8').write(sys.argv[1])" % arg
        r = self.start('"%s" -c "%s" привет & echo ok & exit 2' % (sys.executable, py))
        self.assertEqual(self.finished(r['dir'])['code'], 2)
        with open(arg, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'привет')
        self.assertIn(b'ok', open(r['log'], 'rb').read())
        self.assertFalse(os.path.exists(os.path.join(r['dir'], 'exit.json.tmp')))
        self.assertFalse(bg.alive(r['dir']))

    def test_zero_exit(self):
        self.assertEqual(self.finished(self.start('echo ok')['dir'])['code'], 0)

    def test_bad_input(self):
        for kw in ({'cwd': os.path.join(self.tmp, 'none')}, {'if_cold': 'later'}, {'status_file': 'rel.txt'}, {'name': ''}, {'command': ' '}):
            with self.assertRaises(ValueError, msg=kw):
                self.start('echo ok', **kw)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'bg')))

    def test_prune_old(self):
        old, new = (os.path.join(self.tmp, 'bg', n) for n in ('old', 'new'))
        for d, days in ((old, 8), (new, 6)):
            os.makedirs(d)
            t = time.time() - days * 86400
            os.utime(d, (t, t))
        self.finished(self.start('echo ok')['dir'])
        self.assertEqual((os.path.exists(old), os.path.exists(new)), (False, True))


if __name__ == '__main__':
    unittest.main()
