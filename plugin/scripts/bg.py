"""Long background runs: a detached wrapper runs the command, keeps a lock while alive and writes exit.json at the end (Windows only)."""
import datetime
import json
import msvcrt
import os
import shutil
import subprocess
import sys
import time

import texts

DAYS = 7  # a run folder older than this is deleted by the next start
LOCK = 'alive.lock'


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def spawn_detached(argv, cwd=None):
    """No console window, no parent pipes (with them `$.process.run` waits for the child); no job breakaway until it is needed."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(argv, cwd=cwd, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True).pid


def alive(dir):
    """True while the wrapper holds the lock: we cannot take it ourselves."""
    try:
        fd = os.open(os.path.join(dir, LOCK), os.O_RDWR)
    except OSError:
        return False
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        return True
    finally:
        os.close(fd)  # closing gives a lock we took back
    return False


def _prune(base):
    root = os.path.join(base, 'bg')
    if not os.path.isdir(root):
        return
    old = time.time() - DAYS * 86400
    for n in os.listdir(root):
        d = os.path.join(root, n)
        if os.path.getmtime(d) < old and not alive(d):
            shutil.rmtree(d, ignore_errors=True)


def start(base, session, name, command, cwd, status_file, if_cold):
    for bad, key in ((not os.path.isdir(cwd), 'bg_bad_cwd'), (if_cold not in ('show', 'new_worker'), 'bg_bad_if_cold'),
                     (not name.strip() or not command.strip(), 'bg_empty'),
                     (status_file is not None and not os.path.isabs(status_file), 'bg_bad_status_file')):
        if bad:
            raise ValueError(texts.t(key))
    _prune(base)
    now = datetime.datetime.now(datetime.timezone.utc)
    d = os.path.join(base, 'bg', '%s-%s' % (session, now.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(d)  # shortcut: two starts of one session within a second collide and the second fails, upgrade if that ever happens
    with open(os.path.join(d, 'run.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(name=name, command=command, cwd=cwd, status_file=status_file, if_cold=if_cold, session=session,
                       started=now.isoformat()), f, ensure_ascii=False)
    pid = spawn_detached([sys.executable, os.path.abspath(__file__), 'wrap', d])
    end = time.monotonic() + 5  # the wrapper takes its lock a moment after the start: wait, so alive() is True when we return
    while not alive(d) and not os.path.exists(os.path.join(d, 'exit.json')) and time.monotonic() < end:
        time.sleep(0.02)
    return {'dir': d, 'pid': pid, 'log': os.path.join(d, 'log.txt')}


def wrap(dir):
    fd = os.open(os.path.join(dir, LOCK), os.O_RDWR | os.O_CREAT)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    out = {'code': None}
    try:
        with open(os.path.join(dir, 'run.json'), encoding='utf-8') as f:
            run = json.load(f)
        script = os.path.join(dir, 'run.cmd')  # cmd /c drops letters its OEM page lacks from a command line; a UTF-8 batch file read after chcp keeps them
        with open(script, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('@chcp 65001 >nul\n' + run['command'] + '\n')
        with open(os.path.join(dir, 'log.txt'), 'wb') as log:  # the raw bytes of the command (UTF-8 thanks to chcp)
            out['code'] = subprocess.run(['cmd.exe', '/c', script], cwd=run['cwd'], stdin=subprocess.DEVNULL, stdout=log,
                                         stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW).returncode
    except Exception as e:
        out['error'] = '%s: %s' % (type(e).__name__, e)
    out['ended'] = _now()
    tmp = os.path.join(dir, 'exit.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(dir, 'exit.json'))
    return 0  # the lock goes with the process, after exit.json


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'wrap':
        sys.exit(wrap(sys.argv[2]))
    sys.exit('usage: bg.py wrap <dir>')
