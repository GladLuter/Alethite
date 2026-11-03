"""The background job of stage 4: a scheduled task of this computer that keeps the long memory up to date with no chat open, every N
minutes: the index, the vectors (on mains power only), the sync with the other computers. It runs `cg.py maintain`, which reads the mod's
own settings file. Windows first (the Task Scheduler); the other systems later, as Claude_DelayedMessage does them (cron, systemd, launchd)."""
import datetime
import os
import re
import subprocess
import sys

from texts import t

TASK = 'Alethite'
OLD_TASK = 'CacheGuard'  # the task of the name before the rename: cg.py takes it off once, when it finds the old mod folder
CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')
MINUTES = 30


def is_supported():
    return os.name == 'nt'


def pythonw(python):
    """The windowless twin of `python` (pythonw.exe beside python.exe): a task that opens a console every N minutes steals the focus."""
    head, tail = os.path.split(python)
    if tail.lower() == 'python.exe':
        twin = os.path.join(head, 'pythonw.exe')
        if os.path.exists(twin):
            return twin
    return python


def task_xml(command, arguments, minutes, start, user=None):
    """The task: every `minutes` from `start`, only while the owner is logged on (no password kept), hidden, one at a time, stopped after
    30 minutes, at the priority below normal. A run missed while the computer slept starts when it wakes. It starts on batteries too:
    `maintain` itself leaves the vectors for the mains, and a sync costs little."""
    from xml.sax.saxutils import escape  # here, not on top: cg.py imports job on every run and this import alone costs 0.2 s
    user_id = '\n      <UserId>%s</UserId>' % escape(user) if user else ''
    return '''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Alethite: the index, the vectors and the sync of the long memory, with no chat open</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT%dM</Interval>
      </Repetition>
      <StartBoundary>%s</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">%s
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <Priority>7</Priority>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>%s</Command>
      <Arguments>%s</Arguments>
      <WorkingDirectory>%s</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
''' % (minutes, start.strftime('%Y-%m-%dT%H:%M:%S'), user_id, escape(command), escape(arguments), escape(os.path.dirname(CG)))


def _schtasks(args, run):
    r = run(['schtasks', *args], capture_output=True)
    out = (r.stdout or b'') + (r.stderr or b'')
    return r.returncode, out.decode('utf-8', errors='replace') if isinstance(out, bytes) else out  # schtasks speaks the OEM code page


def install(folder, minutes=MINUTES, python=sys.executable, run=subprocess.run, now=datetime.datetime.now, supported=is_supported):
    """Create or replace the task. The task file is kept beside the index, for the owner to look at."""
    if not supported():
        raise ValueError(t('job_windows_only'))
    if not 5 <= minutes <= 24 * 60:
        raise ValueError(t('job_minutes'))
    command = pythonw(python)
    user = '%s\\%s' % (os.environ['USERDOMAIN'], os.environ['USERNAME']) if os.environ.get('USERDOMAIN') and os.environ.get('USERNAME') else None
    path = os.path.join(folder, 'job.xml')
    os.makedirs(folder, exist_ok=True)
    with open(path, 'w', encoding='utf-16') as f:  # schtasks /XML reads UTF-16
        f.write(task_xml(command, '"%s" maintain' % CG, minutes, now().replace(microsecond=0) + datetime.timedelta(minutes=1), user))
    code, out = _schtasks(['/Create', '/TN', TASK, '/XML', path, '/F'], run)
    if code:
        raise RuntimeError(t('job_schtasks', e=' '.join(out.split())))
    return {'installed': True, 'minutes': minutes, 'command': command, 'file': path}


def remove(run=subprocess.run, supported=is_supported, task=TASK, end=False):
    if not supported():
        raise ValueError(t('job_windows_only'))
    if end:
        _schtasks(['/End', '/TN', task], run)  # a delete leaves a running instance up; with none running /End is refused, and that is fine
    code, out = _schtasks(['/Delete', '/TN', task, '/F'], run)
    if code and installed(run, supported, task):  # no such task is what was asked for, a refusal (Access is denied) is not
        raise RuntimeError(t('job_schtasks', e=' '.join(out.split())))
    return {'installed': False}


def installed(run=subprocess.run, supported=is_supported, task=TASK):
    """None where there is no scheduler yet, else the interval of the task in minutes, or 0 when there is no task."""
    if not supported():
        return None
    code, out = _schtasks(['/Query', '/TN', task, '/XML'], run)  # the XML, not the list: that one speaks the language of Windows
    m = re.search(r'<Interval>P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?</Interval>', out) if code == 0 else None  # Windows may write PT1H or P1D
    return sum(int(n or 0) * k for n, k in zip(m.groups(), (1440, 60, 1))) if m else 0


def target(run=subprocess.run, supported=is_supported, task=TASK):
    """The cg.py the task runs (the path in its Arguments); None where there is no scheduler or no such task."""
    if not supported():
        return None
    from xml.sax.saxutils import unescape  # here, not on top: see task_xml
    code, out = _schtasks(['/Query', '/TN', task, '/XML'], run)
    m = re.search(r'<Arguments>"([^"]+)"', out) if code == 0 else None
    return unescape(m.group(1)) if m else None


def follow(folder, python=sys.executable, run=subprocess.run, supported=is_supported):
    """Make the task run the cg.py of the mod that calls, with the interval it had: a task made from a plugin cache keeps running that version after
    an update. The notes for `home_notes` (none when all went well): a step that fails never stops the command."""
    try:
        there = target(run, supported)
        # shortcut: a non-ASCII path (schtasks prints the OEM code page) never matches and the task is made again at every call, decode it right if that bites
        if there and os.path.normcase(there) != os.path.normcase(CG):
            install(folder, installed(run, supported) or MINUTES, python, run, supported=supported)
    except Exception as e:
        return [t('home_job_failed', e=e)]
    return []


def on_battery():
    """True when a laptop runs on its battery: the vectors wait for the mains. False where it cannot be told."""
    if os.name != 'nt':
        return False
    import ctypes  # here, not on top: see task_xml

    class _Status(ctypes.Structure):
        _fields_ = [('ac', ctypes.c_ubyte), ('flag', ctypes.c_ubyte), ('percent', ctypes.c_ubyte), ('saver', ctypes.c_ubyte),
                    ('life', ctypes.c_ulong), ('full', ctypes.c_ulong)]

    s = _Status()
    return bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s))) and s.ac == 0
