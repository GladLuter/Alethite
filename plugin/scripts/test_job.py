import gc
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest import mock

import cg
import job
import memdb
from test_memdb import claude_line, owner_line
from test_texts import use_lang
from test_vectors import fake

NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}


def ran(code=0, out=b'', err=b''):
    return lambda args, **kw: SimpleNamespace(returncode=code, stdout=out, stderr=err, args=args)


class TaskTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name

    def test_the_task_runs_cg_py_hidden_every_n_minutes_one_at_a_time(self):
        import datetime
        root = ET.fromstring(job.task_xml('C:\\Py\\pythonw.exe', '"C:\\cg\\cg.py" maintain', 30, datetime.datetime(2026, 10, 20, 9, 0), 'PC\\AIJ'))
        find = lambda path: root.find(path, NS).text
        self.assertEqual(find('t:Triggers/t:TimeTrigger/t:Repetition/t:Interval'), 'PT30M')
        self.assertEqual(find('t:Triggers/t:TimeTrigger/t:StartBoundary'), '2026-10-20T09:00:00')
        self.assertEqual((find('t:Actions/t:Exec/t:Command'), find('t:Actions/t:Exec/t:Arguments')), ('C:\\Py\\pythonw.exe', '"C:\\cg\\cg.py" maintain'))
        self.assertEqual((find('t:Principals/t:Principal/t:UserId'), find('t:Principals/t:Principal/t:LogonType')), ('PC\\AIJ', 'InteractiveToken'))
        settings = {e.tag.split('}')[1]: e.text for e in root.find('t:Settings', NS)}
        self.assertEqual((settings['MultipleInstancesPolicy'], settings['Hidden'], settings['DisallowStartIfOnBatteries'], settings['StartWhenAvailable']),
                         ('IgnoreNew', 'true', 'false', 'true'))
        self.assertIsNone(ET.fromstring(job.task_xml('p', 'a', 5, datetime.datetime(2026, 1, 1))).find('t:Principals/t:Principal/t:UserId', NS))

    def test_pythonw_beside_python_or_python_itself(self):
        for name in ('python.exe', 'pythonw.exe'):
            open(os.path.join(self.dir, name), 'w').close()
        self.assertEqual(job.pythonw(os.path.join(self.dir, 'python.exe')), os.path.join(self.dir, 'pythonw.exe'))
        self.assertEqual(job.pythonw('/usr/bin/python3'), '/usr/bin/python3')
        os.remove(os.path.join(self.dir, 'pythonw.exe'))
        self.assertEqual(job.pythonw(os.path.join(self.dir, 'python.exe')), os.path.join(self.dir, 'python.exe'))

    def test_install_writes_the_task_file_and_asks_schtasks(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

        out = job.install(self.dir, 20, python='C:\\Py\\python.exe', run=run, supported=lambda: True)
        self.assertEqual(calls, [['schtasks', '/Create', '/TN', 'CacheGuard', '/XML', os.path.join(self.dir, 'job.xml'), '/F']])
        with open(out['file'], encoding='utf-16') as f:
            self.assertIn('<Interval>PT20M</Interval>', f.read())
        self.assertEqual((out['installed'], out['minutes']), (True, 20))

    def test_install_refusals(self):
        with self.assertRaisesRegex(ValueError, 'Windows'):
            job.install(self.dir, run=ran(), supported=lambda: False)
        with self.assertRaisesRegex(ValueError, '5–1440'):
            job.install(self.dir, 2, run=ran(), supported=lambda: True)
        with self.assertRaisesRegex(RuntimeError, 'Access is denied'):
            job.install(self.dir, run=ran(1, err=b'ERROR: Access is denied.\r\n'), supported=lambda: True)

    def test_remove_and_what_is_installed(self):
        self.assertEqual(job.remove(run=ran(1), supported=lambda: True), {'installed': False})  # no task is what was asked for
        with self.assertRaisesRegex(ValueError, 'Windows'):
            job.remove(run=ran(), supported=lambda: False)
        self.assertIsNone(job.installed(run=ran(), supported=lambda: False))
        self.assertEqual(job.installed(run=ran(1), supported=lambda: True), 0)
        for interval, minutes in (('PT45M', 45), ('PT1H', 60), ('PT1H30M', 90), ('P1D', 1440)):  # Windows may write the interval in hours or days
            self.assertEqual(job.installed(run=ran(0, ('<Task><Interval>%s</Interval></Task>' % interval).encode('utf-8')), supported=lambda: True), minutes)

    def test_a_refused_delete_is_not_off_while_the_task_stays(self):
        def run(args, **kw):  # the delete is refused, the query still finds the task
            return SimpleNamespace(returncode=0 if '/Query' in args else 1, stdout=b'<Interval>PT30M</Interval>' if '/Query' in args else b'',
                                   stderr=b'' if '/Query' in args else b'ERROR: Access is denied.\r\n')

        with self.assertRaisesRegex(RuntimeError, 'Access is denied'):
            job.remove(run=run, supported=lambda: True)


class MaintainTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.addCleanup(gc.collect)  # cg never closes its connection; a collected one releases the file that the temp folder cleanup deletes
        self.dir = d.name
        self.db = os.path.join(d.name, 'cacheguard', 'index.db')
        self.projects = os.path.join(d.name, 'projects')
        self.share = os.path.join(d.name, 'share')
        os.makedirs(self.share)
        os.makedirs(os.path.join(self.projects, 'P'))
        with open(os.path.join(self.projects, 'P', 'S.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(json.dumps(o, ensure_ascii=False) for o in (owner_line('ёжик в тумане'), claude_line('ответ про ёжика'))) + '\n')
        self.settings = os.path.join(d.name, 'cacheguard', 'settings.json')
        self.write_settings({'syncDir': '"%s"' % self.share, 'embedModel': 'embeddinggemma', 'language': 'uk'})

    def write_settings(self, s, raw=None):
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        with open(self.settings, 'w', encoding='utf-8-sig') as f:  # a BOM, as PowerShell writes it
            f.write(raw if raw is not None else json.dumps(s))

    def args(self):
        return cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'maintain'])

    def run_job(self, battery=False):
        with mock.patch.dict(os.environ, {'COMPUTERNAME': 'AIJ-HP'}):
            return cg.maintain(self.args(), embed_fn=fake, battery=lambda: battery, digest=lambda m: None)

    def test_one_run_indexes_embeds_and_syncs_with_the_mods_settings(self):
        out = self.run_job()
        self.assertEqual((out['index'], out['embed'], out['sync'], out['errors']),
                         ({'rows': 2, 'errors': 0}, {'done': 2, 'pending': 0, 'errors': 0}, {'pushed': 2, 'pulled': 0, 'errors': 0}, []))
        self.assertTrue(os.path.exists(os.path.join(self.share, 'AIJ-HP', 'batch-00000001.jsonl.gz')))  # the quotes of "Copy as path" went
        conn = memdb.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(json.loads(memdb.meta(conn, 'job_last'))['sync']['pushed'], 2)  # kept for /cg-health

    def test_on_battery_the_vectors_wait_the_rest_runs(self):
        out = self.run_job(battery=True)
        self.assertEqual((out['embed'], out['sync']['pushed']), ({'skipped': 'battery'}, 2))

    def test_one_job_at_a_time_and_a_chat_that_embeds_is_left_alone(self):
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        memdb.connect(self.db).close()
        held = memdb.lock(self.db, 'maintain', 0)
        self.assertEqual(self.run_job(), {'busy': True})
        held.close()
        held = memdb.lock(self.db, 'embed', 0)
        self.assertEqual(self.run_job()['embed'], {'skipped': 'busy'})
        held.close()

    def test_no_folder_no_sync_and_a_broken_settings_file_is_said_and_survived(self):
        self.write_settings(None, raw='{ "syncDir": ')
        out = self.run_job()
        self.assertIsNone(out['sync'])
        self.assertEqual(out['errors'][0]['where'], 'settings')
        self.assertEqual(out['embed']['done'], 2)  # the defaults hold

    def test_a_language_that_is_no_text_and_a_file_that_cannot_be_read_are_survived(self):  # either killed the whole pass, silently under pythonw
        self.write_settings({'language': ['ru'], 'syncDir': '"%s"' % self.share})
        out = self.run_job()
        self.assertEqual((out['errors'], out['embed']['done']), ([], 2))  # the defaults hold
        os.remove(self.settings)
        os.mkdir(self.settings)  # a folder cannot be opened: the OSError of every system
        self.assertEqual(self.run_job()['errors'][0]['where'], 'settings')

    def test_a_failed_step_does_not_stop_the_next(self):
        def down(texts, model, timeout=None):
            raise RuntimeError('Ollama не отвечает: тест')

        with mock.patch.dict(os.environ, {'COMPUTERNAME': 'AIJ-HP'}):
            out = cg.maintain(self.args(), embed_fn=down, battery=lambda: False, digest=lambda m: None)
        self.assertEqual(out['errors'][0]['where'], 'embed')
        self.assertIn('Ollama не отвечает: тест', out['errors'][0]['error'])
        self.assertEqual(out['sync']['pushed'], 2)  # the sync ran all the same

    def test_a_run_marks_itself_at_its_start_so_a_cut_off_one_shows_unfinished(self):  # the 30-minute limit kills the task before the end is written
        seen = []

        def peek(texts, model, timeout=None):
            conn = memdb.connect(self.db)
            self.addCleanup(conn.close)
            seen.append(json.loads(memdb.meta(conn, 'job_last')))
            return fake(texts, model, timeout)

        with mock.patch.dict(os.environ, {'COMPUTERNAME': 'AIJ-HP'}):
            out = cg.maintain(self.args(), embed_fn=peek, battery=lambda: False, digest=lambda m: None)
        self.assertEqual(seen[0]['errors'], [{'where': 'job', 'error': 'запуск не завершився (зупинено, наприклад, за межею в 30 хвилин)'}])
        self.assertEqual(out['errors'], [])
        conn = memdb.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(json.loads(memdb.meta(conn, 'job_last'))['errors'], [])  # the end of the run overwrites the mark

    def test_only_a_locked_embed_lock_is_a_chat_embedding_any_other_error_is_said(self):
        def alone(db, name, wait):
            if name == 'embed':
                raise sqlite3.OperationalError('unable to open database file')
            return real(db, name, wait)

        real = memdb.lock
        with mock.patch.object(memdb, 'lock', alone):
            out = self.run_job()
        self.assertEqual((out['embed'], [e['where'] for e in out['errors']]), (None, ['embed']))
        self.assertIn('unable to open database file', out['errors'][0]['error'])
        self.assertEqual(out['sync']['pushed'], 2)  # the next step ran all the same

    def test_the_status_carries_the_last_run(self):
        self.run_job()
        st = cg._status(SimpleNamespace(db=self.db, machine='AIJ-HP', cwd=None, where=None))
        self.assertEqual(st['job']['sync']['pushed'], 2)

    def test_main_runs_without_streams_as_pythonw_starts_it(self):
        with mock.patch.object(sys, 'stdout', None), mock.patch.object(sys, 'stderr', None), mock.patch.object(sys, 'stdin', None):
            self.assertEqual(cg.main(['--db', self.db, 'job', 'status']), 0)


if __name__ == '__main__':
    unittest.main()
