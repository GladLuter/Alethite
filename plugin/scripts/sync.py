"""Shared memory of the owner's computers through one folder (a network share), stage 2 design §5. Every computer writes only into its
own subfolder, in batches that never change once they have their name; the others only read them. No database file is ever shared:
SQLite locks do not hold on network file systems, and two writers would break the file."""
import base64
import datetime
import gzip
import hashlib
import json
import os
import re
import socket
import time
import urllib.request

import memdb
from texts import t
from transcripts import project_key
from vectors import DIMS, MODEL, OLLAMA

FORMAT = 1
BATCH_BYTES = 8 * 2 ** 20  # of lines in one batch, about; the first push of a whole history is a few dozen batches
_BATCH = re.compile(r'batch-(\d{8})\.jsonl\.gz$')
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]|^\.{1,2}$|[. ]$')


def machine_name():
    return os.environ.get('COMPUTERNAME') or socket.gethostname().split('.')[0]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


def _batches(folder):
    """{number: file name} of the finished batches in `folder`; a .tmp is a batch cut short and is no batch."""
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return {}
    return {int(m.group(1)): n for n in names for m in [_BATCH.match(n)] if m}


def ollama_digest(model, url=OLLAMA, timeout=5):
    """The digest Ollama gives the model, for the batch header and the warning when two computers run different builds; None if unknown."""
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + '/api/tags', timeout=timeout) as r:
            models = json.load(r).get('models') or []
    except (OSError, ValueError):
        return None
    return next((m.get('digest') for m in models if m.get('name') in (model, model + ':latest') or m.get('model') in (model, model + ':latest')), None)


# ---- push: what this computer's transcripts added since its last batch

def _collect(conn):
    """The lines of our next batch, inside the caller's transaction: forgets, then conversations, records and vectors of ours that are not
    out yet, about BATCH_BYTES of them. The records are marked pushed already, so their vectors may come in the same batch."""
    lines, size, seqs, ids = [], 0, [], []
    for seq, line in conn.execute('SELECT seq, line FROM outbox ORDER BY seq').fetchall():
        if size > BATCH_BYTES:
            break
        lines.append(line)
        seqs.append(seq)
        size += len(line)
    chunks, convs = [], {}
    cur = conn.execute('SELECT k.id, k.conv, k.cls, k.hash, k.n, c.pos, c.ts, c.kind, c.text FROM keys k JOIN chunks c ON c.rowid = k.id '
                       'WHERE k.origin IS NULL AND k.pushed = 0 ORDER BY k.id')
    while size <= BATCH_BYTES:
        rows = cur.fetchmany(500)
        if not rows:
            break
        for rid, conv, cls, h, n, pos, ts, kind, text in rows:
            line = _dumps({'type': 'chunk', 'key': [conv, cls, h, n], 'pos': pos, 'ts': ts, 'kind': kind, 'text': text})
            chunks.append(line)
            ids.append(rid)
            convs[conv] = None
            size += len(line)
            if size > BATCH_BYTES:
                break
    cur.close()
    for conv in convs:
        row = conn.execute('SELECT parent, first_ts, last_ts FROM convs WHERE conv = ?', (conv,)).fetchone()
        if row:
            lines.append(_dumps({'type': 'conv', 'conv': conv, 'parent': row[0], 'first_ts': row[1], 'last_ts': row[2]}))
        lines += [_dumps({'type': 'project', 'conv': conv, 'project': p}) for (p,) in conn.execute('SELECT project FROM conv_projects WHERE conv = ?', (conv,))]
    lines += chunks
    conn.executemany('UPDATE keys SET pushed = 1 WHERE id = ?', [(i,) for i in ids])
    vec_ids = []
    cur = conn.execute('SELECT k.id, k.conv, k.cls, k.hash, k.n FROM keys k WHERE k.origin IS NULL AND k.pushed = 1 AND k.vec_pushed = 0 '
                       'AND EXISTS (SELECT 1 FROM vecs v WHERE v.chunk = k.id AND length(v.vec) = ?) ORDER BY k.id', (4 * DIMS,))
    for rid, conv, cls, h, n in cur.fetchall():
        if size > BATCH_BYTES:
            break
        for part, vec in conn.execute('SELECT part, vec FROM vecs WHERE chunk = ? AND length(vec) = ? ORDER BY part', (rid, 4 * DIMS)):
            line = _dumps({'type': 'vec', 'key': [conv, cls, h, n], 'part': part, 'vec': base64.b64encode(vec).decode('ascii')})
            lines.append(line)
            size += len(line)
        vec_ids.append(rid)
    conn.executemany('UPDATE keys SET vec_pushed = 1 WHERE id = ?', [(i,) for i in vec_ids])
    conn.executemany('DELETE FROM outbox WHERE seq = ?', [(s,) for s in seqs])
    return lines, len(ids), len(vec_ids), len(seqs)


def _write(folder, n, lines):
    """Batch `n` as a whole or not at all: written to a .tmp beside it (a cut leaves only that, and the next run writes it anew), then
    renamed. A finished batch is never overwritten."""
    final = os.path.join(folder, 'batch-%08d.jsonl.gz' % n)
    tmp = final + '.tmp'
    with open(tmp, 'wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as gz:
            for line in lines:
                gz.write(line.encode('utf-8') + b'\n')
        raw.flush()
        os.fsync(raw.fileno())
    if os.path.exists(final):  # os.rename replaces a file on POSIX; on Windows it refuses by itself
        raise FileExistsError(final)
    os.rename(tmp, final)


def _push(conn, folder, machine, digest, deadline, now, report):
    while now() < deadline:
        conn.execute('BEGIN IMMEDIATE')  # also keeps a second sync of this computer from taking the same batch number
        try:
            lines, chunks, vecs, forgets = _collect(conn)
            if not lines:
                conn.rollback()
                return
            n = max(_batches(folder), default=0) + 1
            model = memdb.meta(conn, 'embed_model')
            header = {'type': 'header', 'format': FORMAT, 'machine': machine, 'batch': n, 'model': model, 'dims': DIMS,
                      'digest': digest(model) if vecs and model else None, 'created': _now()}
            _write(folder, n, [_dumps(header)] + lines)
            # our mark moves on only from the batch before: when our own folder stopped at a broken batch, the batches after it
            # must still be read once it is skipped
            conn.execute('INSERT INTO peers(machine, last_batch) VALUES(?, ?) ON CONFLICT(machine) DO UPDATE SET last_batch = excluded.last_batch '
                         'WHERE last_batch = excluded.last_batch - 1', (machine, n))
            conn.commit()  # if this fails the batch is out and its records go out again next time: a repeat changes nothing anywhere
        except BaseException:
            conn.rollback()
            raise
        report['pushed'] += chunks
        report['vectors']['pushed'] += vecs
        report['batches']['pushed'] += 1


# ---- pull: what the other computers pushed, and our own batches when the index here is new

def _import(conn, path, peer, n, own, model, digest, report):
    """Take in one batch in one transaction: a record, a vector or a forget that is here already changes nothing. Raises on anything broken,
    and then nothing of the batch is kept."""
    with open(path, 'rb') as f:  # read whole before the write lock: a slow share must not hold the index
        lines = gzip.decompress(f.read()).decode('utf-8').split('\n')
    head = json.loads(lines[0])
    if not (isinstance(head, dict) and head.get('type') == 'header' and head.get('machine') == peer and head.get('batch') == n):
        raise ValueError(t('batch_header', machine=peer, n=n))
    counts = {'chunks': 0, 'forgets': 0}
    skipped = set()
    conn.execute('BEGIN IMMEDIATE')
    try:
        have = memdb.meta(conn, 'embed_model')
        theirs = head.get('model')
        # vectors of another model cannot be compared with ours; an index that has none yet takes the model it is set to use anyway
        vec_ok = bool(theirs) and head.get('dims') == DIMS and (have == theirs or (not have and theirs == model))
        adopt = vec_ok and not have
        taking, refused = set(), set()
        for i, line in enumerate(lines[1:], 2):
            if not line:
                continue
            try:
                o = json.loads(line)
                kind = o['type']
                if kind == 'conv':
                    conn.execute('INSERT INTO convs VALUES(?, ?, ?, ?) ON CONFLICT(conv) DO UPDATE SET '
                                 'first_ts = min(first_ts, excluded.first_ts), last_ts = max(last_ts, excluded.last_ts)',
                                 (o['conv'], o.get('parent') or '', o.get('first_ts') or '', o.get('last_ts') or ''))
                elif kind == 'project':
                    conn.execute('INSERT OR IGNORE INTO conv_projects VALUES(?, ?)', (o['conv'], o['project']))
                elif kind == 'chunk':
                    conv, cls, h, k = o['key']
                    if memdb.text_hash(o['text']) != h:
                        raise ValueError(t('bad_hash'))
                    if conn.execute('SELECT 1 FROM forgotten WHERE conv = ? AND cls = ? AND hash = ? AND n = ?', (conv, cls, h, k)).fetchone():
                        continue
                    row = conn.execute('SELECT id FROM keys WHERE conv = ? AND cls = ? AND hash = ? AND n = ?', (conv, cls, h, k)).fetchone()
                    if row:
                        if own:  # ours, and in our batches already
                            conn.execute('UPDATE keys SET pushed = 1 WHERE id = ?', row)
                        continue
                    rid = conn.execute('INSERT INTO chunks(text, conv, pos, ts, kind) VALUES(?, ?, ?, ?, ?)',
                                       (o['text'], conv, o['pos'], o['ts'], o['kind'])).lastrowid
                    conn.execute('INSERT INTO keys(id, conv, cls, hash, n, origin, pushed, vec_pushed) VALUES(?, ?, ?, ?, ?, ?, 1, 1)', (rid, conv, cls, h, k, peer))
                    if o['kind'] == 'report':
                        memdb.set_report(conn, conv, rid)
                    counts['chunks'] += 1
                elif kind == 'vec':
                    if not vec_ok:
                        skipped.add(tuple(o['key']))
                        continue
                    row = conn.execute('SELECT id FROM keys WHERE conv = ? AND cls = ? AND hash = ? AND n = ?', tuple(o['key'])).fetchone()
                    if not row or row[0] in refused:
                        continue
                    rid = row[0]
                    vec = base64.b64decode(o['vec'], validate=True)
                    if len(vec) != 4 * DIMS:
                        raise ValueError(t('bad_vector', n=len(vec), want=4 * DIMS))
                    if rid not in taking:
                        lens = [l for (l,) in conn.execute('SELECT length(vec) FROM vecs WHERE chunk = ?', (rid,))]
                        if any(lens):  # computed here already; the same model gives the same vector
                            refused.add(rid)
                            if own:
                                conn.execute('UPDATE keys SET vec_pushed = 1 WHERE id = ?', (rid,))
                            continue
                        conn.execute('DELETE FROM vecs WHERE chunk = ?', (rid,))  # a sentinel: the record failed to embed here
                        taking.add(rid)
                        if own:
                            conn.execute('UPDATE keys SET vec_pushed = 1 WHERE id = ?', (rid,))
                    conn.execute('INSERT OR REPLACE INTO vecs VALUES(?, ?, ?, ?)', (rid, o['part'], o['key'][0], vec))
                elif kind == 'forget':
                    memdb.forget_key(conn, *o['key'], push=False)
                    counts['forgets'] += 1
                # another type is of a later format: left for the version that knows it
            except (ValueError, KeyError, TypeError) as e:
                raise ValueError(t('bad_line', line=i, e='%s: %s' % (type(e).__name__, e))) from None
        if adopt and taking:
            conn.execute("INSERT OR REPLACE INTO meta VALUES('embed_model', ?)", (theirs,))
        conn.execute('INSERT INTO peers(machine, last_batch, model, digest) VALUES(?, ?, ?, ?) ON CONFLICT(machine) DO UPDATE SET '
                     'last_batch = excluded.last_batch, model = excluded.model, digest = excluded.digest', (peer, n, theirs, head.get('digest')))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if taking:
        mine = digest(theirs)
        if head.get('digest') and mine and mine != head['digest']:
            report['warnings'].append(t('digest_differs', machine=peer, model=theirs, theirs=head['digest'][:19], ours=mine[:19]))
    report['pulled'] += counts['chunks']
    report['vectors']['pulled'] += len(taking)
    report['forgotten'] += counts['forgets']
    report['skipped_vectors'] += len(skipped)
    report['batches']['pulled'] += 1


def _peer_error(conn, peer, error):
    conn.execute('INSERT INTO peers(machine, error) VALUES(?, ?) ON CONFLICT(machine) DO UPDATE SET error = excluded.error', (peer, error))


def _pull(conn, folder, peer, own, model, digest, deadline, now, report):
    """Take in the batches of `peer` that are new here, in number order. A broken or missing one stops that computer: its later batches
    wait, and the error is reported on every sync until it is fixed or skipped by hand (cg.py sync --skip-batch)."""
    row = conn.execute('SELECT last_batch, skipped FROM peers WHERE machine = ?', (peer,)).fetchone()
    last, skipped = (row[0], set(json.loads(row[1]))) if row else (0, set())
    names = _batches(folder)
    if not names and not row:
        return  # a folder of something else, or a computer that has pushed nothing yet: no computer to show
    error, n = None, last + 1
    while now() < deadline:
        if n in skipped:
            conn.execute('UPDATE peers SET last_batch = ? WHERE machine = ?', (n, peer))
        elif n in names:
            try:
                _import(conn, os.path.join(folder, names[n]), peer, n, own, model, digest, report)
            except Exception as e:
                # a share that hiccups or an index held by another run is no broken batch: no skip is offered, the next sync tries again
                passing = isinstance(e, memdb.db_errors()) or (isinstance(e, OSError) and not isinstance(e, gzip.BadGzipFile))
                error = t('batch_unreadable' if passing else 'batch_broken', n=n, machine=peer, e='%s: %s' % (type(e).__name__, e))
                break
        elif any(k > n for k in names):
            error = t('batch_missing', n=n, machine=peer)
            break
        else:
            break
        n += 1
    _peer_error(conn, peer, error)
    if error:
        report['errors'].append({'where': 'pull', 'machine': peer, 'batch': n, 'error': error})


def skip_batch(conn, machine, n):
    """Go on past batch `n` of `machine` without it: the owner's way out of a broken batch. Kept, and shown in /cg-health. Only a computer
    known here (its name in any case) and only the batch it is stuck at: a typo must not skip a sound batch or invent a computer."""
    if not isinstance(n, int) or n < 1:
        raise ValueError(t('skip_batch_bad'))
    rows = conn.execute('SELECT machine, last_batch, skipped FROM peers').fetchall()
    row = next((r for r in rows if r[0].lower() == machine.lower()), None)
    if not row:
        raise ValueError(t('skip_batch_unknown', machine=machine, known=', '.join(sorted(r[0] for r in rows)) or '—'))
    machine, last, skipped = row[0], row[1] or 0, set(json.loads(row[2]) if row[2] else [])
    if n != last + 1:
        raise ValueError(t('skip_batch_not_next', machine=machine, n=n, want=last + 1))
    conn.execute('UPDATE peers SET skipped = ? WHERE machine = ?', (json.dumps(sorted(skipped | {n})), machine))


# ---- short memory: the fact files of the auto-memory folder of one project

def _hash(data):
    return hashlib.sha1(data).hexdigest()


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def _put(path, data):
    tmp = path + '.cg-tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def project_folder(key):
    """A folder name for a project key: readable, and unique by the hash at its end."""
    return (re.sub(r'[^0-9A-Za-z]+', '-', key).strip('-')[-60:] or 'home') + '-' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]


def _facts(folder):
    try:
        return sorted(n for n in os.listdir(folder) if n.endswith('.md') and n.lower() != 'memory.md' and os.path.isfile(os.path.join(folder, n)))
    except FileNotFoundError:
        return []


def _pointer(name, data):
    """The line of MEMORY.md that points to a fact, in the form of Claude Code's auto-memory: its name and description from the frontmatter."""
    m = re.match(r'---\r?\n(.*?)\r?\n---', data.decode('utf-8', errors='replace'), re.S)
    front = dict(re.findall(r'^(\w+):[ \t]*(.*?)[ \t]*$', m.group(1), re.M)) if m else {}
    title = front.get('name') or name[:-3]
    return '- [%s](%s)' % (title, name) + (' — ' + front['description'] if front.get('description') else '')


def sync_memory(conn, sync_dir, machine, peers, memory_dir, key):
    """Facts made on this computer go into its folder; facts of the other computers come into `memory_dir` when it lacks them. A file here
    is never overwritten unless it is a copy taken in earlier and left as it was; a fact the owner deleted after it came does not come back."""
    if not os.path.isdir(memory_dir):
        raise FileNotFoundError(t('memory_dir_missing', dir=memory_dir))
    sub = project_folder(key)
    out = {'pushed': 0, 'pulled': 0, 'conflicts': 0}
    taken = {name: (src, src_name, h) for name, src, src_name, h in conn.execute(
        'SELECT name, source, src_name, hash FROM mem_files WHERE project = ?', (key,))}
    mine = os.path.join(sync_dir, machine, 'memory', sub)
    for name in _facts(memory_dir):
        # a copy from another computer is not ours to push: it would come back to where it came from
        if name in taken or any(name[:-3].endswith('--' + p) for p in peers):
            continue
        data = _read(os.path.join(memory_dir, name))
        target = os.path.join(mine, name)
        if os.path.isfile(target) and _read(target) == data:
            continue
        os.makedirs(mine, exist_ok=True)
        _put(target, data)
        out['pushed'] += 1
    added = []
    for peer in peers:
        theirs = os.path.join(sync_dir, peer, 'memory', sub)
        for name in _facts(theirs):
            data = _read(os.path.join(theirs, name))
            h = _hash(data)
            if any(r[0] == peer and r[1] == name and r[2] == h for r in taken.values()):
                continue  # this version came in already, wherever it went since

            def place(local):
                path, rec = os.path.join(memory_dir, local), taken.get(local)
                if not os.path.exists(path):
                    if rec:
                        return 'deleted'  # the owner deleted it after it came: it stays deleted
                    _put(path, data)
                    added.append((local, data))
                    got = 'written'
                elif _hash(_read(path)) == h:
                    if not rec:  # the same text got here otherwise (by hand, a run that died before its record): still ours to push
                        added.append((local, data))
                        return 'same'
                    got = 'same'
                elif rec and rec[:2] == (peer, name) and _hash(_read(path)) == rec[2]:
                    _put(path, data)  # our copy is as it came, and its source has a newer version
                    got = 'written'
                else:
                    return 'busy'  # the owner's own text, or a copy the owner changed
                taken[local] = (peer, name, h)
                conn.execute('INSERT OR REPLACE INTO mem_files VALUES(?, ?, ?, ?, ?)', (key, local, peer, name, h))
                return got

            got = place(name)
            if got == 'busy':  # the same name holds other text here: the other computer's version goes beside it
                out['conflicts'] += 1
                got = place(name[:-3] + '--' + peer + '.md')
            out['pulled'] += got == 'written'
    if added:
        index = os.path.join(memory_dir, 'MEMORY.md')
        old = _read(index).decode('utf-8') if os.path.isfile(index) else ''
        new = [_pointer(n, d) for n, d in added if '](%s)' % n not in old]
        if new:
            _put(index, (old + ('\n' if old and not old.endswith('\n') else '') + '\n'.join(new) + '\n').encode('utf-8'))
    return out


# ---- one sync

def sync(conn, sync_dir, machine=None, model=MODEL, max_seconds=60, memory_dir=None, project_cwd=None, home=None, digest=ollama_digest,
         now=time.monotonic):
    """Push our batches, take in those of the other computers, then the memory facts of the current project. Nothing here stops the local
    index: an unavailable folder or a broken batch is an entry in 'errors', and the next sync goes on from where this one stopped."""
    start = now()
    deadline = start + max_seconds
    machine = machine or machine_name()
    report = {'machine': machine, 'pushed': 0, 'pulled': 0, 'vectors': {'pushed': 0, 'pulled': 0}, 'batches': {'pushed': 0, 'pulled': 0},
              'forgotten': 0, 'skipped_vectors': 0, 'peers': {}, 'own_batch': None, 'memory': None, 'warnings': [], 'errors': []}
    seen = {}

    def digest_once(m):
        if m not in seen:
            seen[m] = digest(m)
        return seen[m]

    try:
        if _BAD_NAME.search(machine) or not machine:
            raise ValueError(t('sync_bad_machine', name=machine))
        try:
            peers = sorted(d for d in os.listdir(sync_dir) if d != machine and os.path.isdir(os.path.join(sync_dir, d)))
        except OSError as e:
            raise OSError(t('sync_dir_missing', dir=sync_dir, e='%s: %s' % (type(e).__name__, e))) from None
        own = os.path.join(sync_dir, machine)
        os.makedirs(own, exist_ok=True)
        # first our own batches the index here does not know (a new or restored index): our records and their vectors come back,
        # and nothing that is out already goes out again
        _pull(conn, own, machine, True, model, digest_once, deadline, now, report)
        _push(conn, own, machine, digest_once, deadline, now, report)
        for peer in peers:
            _pull(conn, os.path.join(sync_dir, peer), peer, False, model, digest_once, deadline, now, report)
        if memory_dir:
            conn.execute('BEGIN IMMEDIATE')
            with conn:
                report['memory'] = sync_memory(conn, sync_dir, machine, peers, memory_dir, project_key(project_cwd or os.getcwd(), home))
    except Exception as e:
        report['errors'].append({'where': 'sync', 'error': str(e) if isinstance(e, (OSError, ValueError)) and str(e) else '%s: %s' % (type(e).__name__, e)})
    report['peers'] = {m: last for m, last in conn.execute('SELECT machine, last_batch FROM peers WHERE machine != ? ORDER BY machine', (machine,))}
    report['own_batch'] = (conn.execute('SELECT last_batch FROM peers WHERE machine = ?', (machine,)).fetchone() or (None,))[0]
    report['seconds'] = round(now() - start, 2)
    # stopped: the run failed as a whole (an unavailable folder) and never reached the memory step; /cg-health tells only its error then
    last = {'at': _now(), 'dir': sync_dir, 'pushed': report['pushed'], 'pulled': report['pulled'], 'memory': report['memory'],
            'errors': len(report['errors']), 'error': report['errors'][0]['error'] if report['errors'] else None,
            'stopped': any(e['where'] == 'sync' for e in report['errors'])}
    conn.execute("INSERT OR REPLACE INTO meta VALUES('sync_last', ?)", (json.dumps(last, ensure_ascii=False),))
    return report


def status(conn, machine=None):
    """What /cg-health shows of the sync: the last run, every computer read and how far, the records by the computer they came from."""
    machine = machine or machine_name()
    last = memdb.meta(conn, 'sync_last')
    peers = [{'machine': m, 'last_batch': b, 'skipped': json.loads(s), 'model': mo, 'error': e}
             for m, b, s, mo, e in conn.execute('SELECT machine, last_batch, skipped, model, error FROM peers ORDER BY machine != ?, machine', (machine,))]
    origins = {}
    for m, c in conn.execute('SELECT origin, count(*) FROM keys GROUP BY origin'):  # records taken back from our own folder are ours too
        origins[m or machine] = origins.get(m or machine, 0) + c
    return {'machine': machine, 'last': json.loads(last) if last else None, 'peers': peers, 'origins': origins}
