import datetime
import gc
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest import mock
from xml.sax.saxutils import escape

import cg
import job
import memdb
from test_memdb import claude_line, owner_line
from test_texts import use_lang
from test_vectors import fake
from texts import t

SCHTASKS = job._schtasks  # before any test patches it
NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}


def ran(code=0, out=b'', err=b''):
    return lambda args, **kw: SimpleNamespace(returncode=code, stdout=out, stderr=err, args=args)


class Scheduler:
    """The Task Scheduler as the `run` of job.py: its tasks ({name: minutes, or (minutes, the cg.py it runs)}) kept as the XML they were made from, and
    every call it got ([verb, ...] without 'schtasks') in order. `deny` are the verbs it refuses with 'Access is denied'. `probe`, if set, is called with
    each call before it is answered: what a test wants to see of the disk at that moment."""

    def __init__(self, tasks=None, deny=(), running=()):
        self.tasks = {name: self.xml(*(spec if isinstance(spec, tuple) else (spec,))) for name, spec in (tasks or {}).items()}
        self.calls, self.deny, self.running, self.probe = [], deny, set(running), None  # running: the tasks with an instance up, /End stops it

    @staticmethod
    def xml(minutes, script=job.CG):
        return job.task_xml('pythonw.exe', '"%s" maintain' % script, minutes, datetime.datetime(2026, 11, 3, 9, 0))

    def __call__(self, args, **kw):
        verb, name = args[1], args[3]
        self.calls.append(args[1:])
        if self.probe:
            self.probe(args[1:])
        answer = lambda code=0, out=b'', err=b'': SimpleNamespace(returncode=code, stdout=out, stderr=err)
        if verb in self.deny:
            return answer(1, err=b'ERROR: Access is denied.\r\n')
        if verb == '/Create':
            with open(args[5], encoding='utf-16') as f:
                self.tasks[name] = f.read()
        elif name not in self.tasks:
            return answer(1, err=b'ERROR: The system cannot find the file specified.\r\n')
        elif verb == '/Delete':
            del self.tasks[name]
        elif verb == '/End':
            if name not in self.running:
                return answer(1, err=b'ERROR: There are no running instances of the task.\r\n')
            self.running.discard(name)
        else:  # /Query /XML
            return answer(out=self.tasks[name].encode('utf-8'))
        return answer()

    def patched(self):
        """job._schtasks as it is, but asking this stub where it would have run schtasks: for a test of cg.main, which has no `run` to give."""
        return mock.patch.object(job, '_schtasks', lambda args, run: SCHTASKS(args, self))


class TaskTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name

    def test_the_task_runs_cg_py_hidden_every_n_minutes_one_at_a_time(self):
        root = ET.fromstring(job.task_xml('C:\\Py\\pythonw.exe', '"C:\\cg\\cg.py" maintain', 30, datetime.datetime(2026, 10, 20, 9, 0), 'PC\\Ann'))
        find = lambda path: root.find(path, NS).text
        self.assertEqual(find('t:Triggers/t:TimeTrigger/t:Repetition/t:Interval'), 'PT30M')
        self.assertEqual(find('t:Triggers/t:TimeTrigger/t:StartBoundary'), '2026-10-20T09:00:00')
        self.assertEqual((find('t:Actions/t:Exec/t:Command'), find('t:Actions/t:Exec/t:Arguments')), ('C:\\Py\\pythonw.exe', '"C:\\cg\\cg.py" maintain'))
        self.assertEqual((find('t:Principals/t:Principal/t:UserId'), find('t:Principals/t:Principal/t:LogonType')), ('PC\\Ann', 'InteractiveToken'))
        settings = {e.tag.split('}')[1]: e.text for e in root.find('t:Settings', NS)}
        self.assertEqual((settings['MultipleInstancesPolicy'], settings['Hidden'], settings['DisallowStartIfOnBatteries'], settings['StartWhenAvailable']),
                         ('IgnoreNew', 'true', 'false', 'true'))
        self.assertIsNone(ET.fromstring(job.task_xml('p', 'a', 5, datetime.datetime(2026, 1, 1))).find('t:Principals/t:Principal/t:UserId', NS))
        self.assertTrue(find('t:RegistrationInfo/t:Description').startswith('Alethite: '))

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
        self.assertEqual(calls, [['schtasks', '/Create', '/TN', 'Alethite', '/XML', os.path.join(self.dir, 'job.xml'), '/F']])
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

    def test_a_task_with_no_interval_in_its_xml_has_the_default_one(self):  # not "no task": the old one would be neither removed nor carried over
        self.assertEqual(job.installed(run=ran(0, b'<Task><Triggers/></Task>'), supported=lambda: True), job.MINUTES)
        self.assertEqual(job.installed(run=ran(1, b'<Task><Triggers/></Task>'), supported=lambda: True), 0)  # a refused query is no task

    def test_schtasks_does_not_wait_longer_than_the_prompt_hook_does(self):
        seen = {}

        def run(args, **kw):
            seen.update(kw)
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

        job._schtasks(['/Query', '/TN', 'x'], run)
        self.assertLess(seen['timeout'], 8)  # the mod kills the prompt hook after 8 s

    def test_a_scheduler_that_hangs_is_a_note_and_never_stops_follow(self):
        hung = subprocess.TimeoutExpired('schtasks', 5)

        def run(args, **kw):
            raise hung

        self.assertEqual(job.follow(self.dir, run=run, supported=lambda: True), [t('home_job_failed', e=hung)])

    def test_a_refused_delete_is_not_off_while_the_task_stays(self):
        def run(args, **kw):  # the delete is refused, the query still finds the task
            return SimpleNamespace(returncode=0 if '/Query' in args else 1, stdout=b'<Interval>PT30M</Interval>' if '/Query' in args else b'',
                                   stderr=b'' if '/Query' in args else b'ERROR: Access is denied.\r\n')

        with self.assertRaisesRegex(RuntimeError, 'Access is denied'):
            job.remove(run=run, supported=lambda: True)

    def test_installed_and_remove_take_task_name(self):
        self.assertEqual((job.TASK, job.OLD_TASK), ('Alethite', 'CacheGuard'))
        s = Scheduler({'CacheGuard': 45, 'Alethite': 20})
        self.assertEqual((job.installed(s, lambda: True, task='CacheGuard'), job.installed(s, lambda: True)), (45, 20))
        job.remove(s, lambda: True, task='CacheGuard')
        self.assertEqual((s.calls[-1], list(s.tasks)), (['/Delete', '/TN', 'CacheGuard', '/F'], ['Alethite']))
        job.remove(s, lambda: True)
        self.assertEqual((s.calls[-1], list(s.tasks)), (['/Delete', '/TN', 'Alethite', '/F'], []))

    def test_remove_can_end_a_running_instance_first(self):
        s = Scheduler({'CacheGuard': 45}, running=('CacheGuard',))
        job.remove(s, lambda: True, task='CacheGuard', end=True)
        self.assertEqual(([c[0] for c in s.calls], list(s.tasks), s.running), (['/End', '/Delete'], [], set()))
        s = Scheduler({'CacheGuard': 45})  # nothing runs: /End is refused and the delete still goes
        job.remove(s, lambda: True, task='CacheGuard', end=True)
        self.assertEqual(([c[0] for c in s.calls], list(s.tasks)), (['/End', '/Delete'], []))
        s = Scheduler({'CacheGuard': 45}, running=('CacheGuard',))
        job.remove(s, lambda: True, task='CacheGuard')  # no end asked: the instance is left alone
        self.assertEqual(([c[0] for c in s.calls], s.running), (['/Delete'], {'CacheGuard'}))

    def test_target_reads_script_path_from_task_xml(self):
        script = 'C:\\Users\\A&B\\plugin\\scripts\\cg.py'  # the XML says &amp;
        s = Scheduler({'Alethite': (30, script), 'CacheGuard': (30, 'C:\\old\\cg.py')})
        self.assertEqual(job.target(s, lambda: True), script)
        self.assertEqual(job.target(s, lambda: True, task='CacheGuard'), 'C:\\old\\cg.py')
        self.assertIsNone(job.target(Scheduler(), lambda: True))  # no such task
        self.assertIsNone(job.target(s, lambda: False))  # no scheduler

    def follow(self, s):
        return job.follow(self.dir, python='C:\\Py\\python.exe', run=s, supported=lambda: True)

    def test_follow_reinstalls_when_target_differs(self):
        s = Scheduler({'Alethite': (45, 'C:\\clone\\plugin\\scripts\\cg.py')})
        self.assertEqual(self.follow(s), [])
        self.assertEqual([c[:3] for c in s.calls if c[0] != '/Query'], [['/Create', '/TN', 'Alethite']])
        self.assertIn('<Interval>PT45M</Interval>', s.tasks['Alethite'])  # the interval it had
        self.assertIn('"%s" maintain' % escape(job.CG), s.tasks['Alethite'])

    def test_follow_leaves_task_with_same_target(self):
        for script in (job.CG, job.CG.upper()):  # Windows paths differ in case only
            s = Scheduler({'Alethite': (45, script)})
            before = dict(s.tasks)
            self.assertEqual(self.follow(s), [])
            self.assertEqual(([c[0] for c in s.calls], s.tasks), (['/Query'], before))

    def test_follow_without_task_does_nothing(self):
        s = Scheduler({'CacheGuard': 45})  # the old name is not followed
        self.assertEqual(self.follow(s), [])
        self.assertEqual(([c[:3] for c in s.calls], list(s.tasks)), ([['/Query', '/TN', 'Alethite']], ['CacheGuard']))

    def test_follow_error_is_a_note_and_the_task_stays(self):
        s = Scheduler({'Alethite': (45, 'C:\\clone\\cg.py')}, deny=('/Create',))
        before = dict(s.tasks)
        self.assertEqual(self.follow(s), [t('home_job_failed', e=t('job_schtasks', e='ERROR: Access is denied.'))])
        self.assertEqual(s.tasks, before)


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
        with mock.patch.dict(os.environ, {'COMPUTERNAME': 'HOMEPC'}):
            return cg.maintain(self.args(), embed_fn=fake, battery=lambda: battery, digest=lambda m: None)

    def test_one_run_indexes_embeds_and_syncs_with_the_mods_settings(self):
        out = self.run_job()
        self.assertEqual((out['index'], out['embed'], out['sync'], out['errors']),
                         ({'rows': 2, 'errors': 0}, {'done': 2, 'pending': 0, 'errors': 0}, {'pushed': 2, 'pulled': 0, 'errors': 0}, []))
        self.assertTrue(os.path.exists(os.path.join(self.share, 'HOMEPC', 'batch-00000001.jsonl.gz')))  # the quotes of "Copy as path" went
        conn = memdb.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(json.loads(memdb.meta(conn, 'job_last'))['sync']['pushed'], 2)  # kept for /al-health

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

        with mock.patch.dict(os.environ, {'COMPUTERNAME': 'HOMEPC'}):
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

        with mock.patch.dict(os.environ, {'COMPUTERNAME': 'HOMEPC'}):
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
        st = cg._status(SimpleNamespace(db=self.db, machine='HOMEPC', cwd=None, where=None))
        self.assertEqual(st['job']['sync']['pushed'], 2)

    def test_main_runs_without_streams_as_pythonw_starts_it(self):
        with mock.patch.object(sys, 'stdout', None), mock.patch.object(sys, 'stderr', None), mock.patch.object(sys, 'stdin', None), Scheduler().patched():
            self.assertEqual(cg.main(['--db', self.db, 'job', 'status']), 0)


if __name__ == '__main__':
    unittest.main()
