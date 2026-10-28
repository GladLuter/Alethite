"""Shared memory of the owner's computers through one folder (a network share), stage 2 design §5. Every computer writes only into its
own subfolder, in batches that never change once they have their name; the others only read them. No database file is ever shared:
SQLite locks do not hold on network file systems, and two writers would break the file. A computer that has to publish everything again
(in another format) starts a new generation of its folder, gen-NNNN/; the others read it from its first batch."""
import base64
import collections
import contextlib
import datetime
import gzip
import hashlib
import json
import os
import re
import socket
import time
import urllib.request

import crypto
import memdb
from texts import t
from transcripts import project_key
from vectors import DIMS, MODEL, OLLAMA

FORMAT = 1
BATCH_BYTES = 8 * 2 ** 20  # of lines in one batch, about; the first push of a whole history is a few dozen batches
# the one spelling of a number, `%04d`: gen-00001 or a Unicode digit cannot alias another folder; not gen-0000, generation 0 is the folder itself
_GEN = re.compile(r'gen-(?!0000)([0-9]{4}|[1-9][0-9]{4,8})')
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]|^\.{1,2}$|[. ]$')
# a generation of a computer's folder: n 0 is the folder itself (plain, as before stage 5), else its gen-NNNN subfolder; `path` holds the batches
Gen = collections.namedtuple('Gen', 'n path cipher fingerprint')


def machine_name():
    return os.environ.get('COMPUTERNAME') or socket.gethostname().split('.')[0]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


def _batches(folder, ext=crypto.EXT[None]):
    """{number: file name} of the finished batches in `folder` (`ext` is that of the cipher); a .tmp is a batch cut short and is no batch."""
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return {}
    pat = re.compile(r'batch-([0-9]{8})' + re.escape(ext))
    return {int(m.group(1)): n for n in names for m in [pat.fullmatch(n)] if m}


# ---- generations: a computer publishes everything again in a new format by starting a new one, which the others read from batch 1

def _gens(machine_dir):
    """{number: path} of the gen-NNNN folders of a computer, with a readable label or not."""
    try:
        names = os.listdir(machine_dir)
    except FileNotFoundError:
        return {}
    return {int(m.group(1)): os.path.join(machine_dir, n) for n in names for m in [_GEN.fullmatch(n)] if m}


def _label(path, n):
    """The generation in folder `path` numbered `n`, None unless its label is a readable one. The share is untrusted input: with no label or
    anything off in it the folder is ignored whole, and an older generation is the current one. Any other OSError is a share that hiccups,
    not a missing label, and is raised: taking it for one would send our new records into a superseded generation."""
    try:
        with open(os.path.join(path, 'generation.json'), encoding='utf-8') as f:
            o = json.loads(f.read(4096))
        cipher, fp = o['cipher'], o['fingerprint']
        ok = (o['format'] == FORMAT and o['gen'] == n and cipher in (None, *crypto.CIPHERS)
              and (cipher is None and fp is None or isinstance(fp, str) and re.fullmatch(r'[0-9a-f]{16}', fp)))
    except (FileNotFoundError, NotADirectoryError, ValueError, KeyError, TypeError, RecursionError):
        return None
    return Gen(n, path, cipher, fp) if ok else None


def generation(machine_dir):
    """The current generation of a computer's folder: the highest gen-NNNN with a readable label, else generation 0, the folder itself."""
    for n, path in sorted(_gens(machine_dir).items(), reverse=True):
        gen = _label(path, n)
        if gen:
            return gen
    return Gen(0, machine_dir, None, None)


def old_generations(machine_dir):
    """Paths of what in a computer's folder is not of its current generation, for a cleanup once the others have read the new one: the
    batches and memory of generation 0 that lie in the folder itself, and the folders of the generations before the current one."""
    cur = generation(machine_dir).n
    if not cur:
        return []  # generation 0 is the current one: nothing is old
    return ([os.path.join(machine_dir, n) for n in sorted(os.listdir(machine_dir)) if n.startswith('batch-') or n == 'memory']
            + [p for n, p in sorted(_gens(machine_dir).items()) if n < cur])


def _restart(conn, machine, n):
    """Inside the caller's transaction: we are at generation `n` of our folder from its first batch, and everything of ours goes out again
    into it (records, vectors, and every forget, which the new readers never saw)."""
    conn.execute("INSERT INTO peers(machine, gen) VALUES(?, ?) ON CONFLICT(machine) DO UPDATE SET gen = excluded.gen, last_batch = 0, "
                 "skipped = '[]', error = NULL", (machine, n))
    conn.execute('UPDATE keys SET origin = NULL, pushed = 0, vec_pushed = 0 WHERE origin IS NULL OR origin = ?', (machine,))
    for k in conn.execute('SELECT conv, cls, hash, n FROM forgotten').fetchall():
        memdb.forget_key(conn, *k)
    conn.execute("DELETE FROM meta WHERE key = 'share_plain'")


def start_generation(conn, machine_dir, machine, cipher, key):
    """Begin a new generation of our folder: the label, then in one transaction the marks of the old generation are cleared (`_restart`). The
    index is locked first: a busy one must not leave a labelled generation whose records were never sent again. A cipher goes with a key
    and no cipher with no key; anything else is the caller's error. No label goes out for a cipher whose package is missing here."""
    if bool(cipher) != bool(key) or cipher not in (None, *crypto.CIPHERS):
        raise ValueError('start_generation: a cipher of %r with %s key' % (cipher, 'a' if key else 'no'))
    if cipher:
        crypto.need(cipher)
    conn.execute('BEGIN IMMEDIATE')
    label = None  # set once this call has written one: only that is taken back on a failure
    try:
        n = max(_gens(machine_dir), default=0) + 1  # beyond every gen-NNNN folder there is, a readable one or not; under the lock
        gen = Gen(n, os.path.join(machine_dir, 'gen-%04d' % n), cipher, crypto.fingerprint(key) if key else None)
        os.makedirs(gen.path)
        path = os.path.join(gen.path, 'generation.json')
        _put(path, _dumps({'format': FORMAT, 'gen': n, 'cipher': cipher, 'fingerprint': gen.fingerprint, 'created': _now()}).encode('utf-8'))
        label = path
        _restart(conn, machine, n)
        conn.commit()
    except BaseException:
        conn.rollback()
        if label:
            with contextlib.suppress(OSError):  # without its label the folder is no generation, and the next start goes beyond it
                os.remove(label)
        raise
    return gen


def _own_generation(conn, own, machine, cipher, key, pull):
    """The generation of our folder we write into: the current one when it is of our (cipher, key), else a new one (`cipher` and `key` both
    None for an open one). With no key we do not leave an encrypted share by ourselves: only `encrypt off` does, by meta.share_plain.
    A generation we leave is first all in the index here (a fresh index holds of our records only what it has read back, and the new
    generation sends everything of ours again): one we can read is taken in with `pull(gen)`. None: that is not all read yet (a broken
    batch, the time cap); nothing starts. One we cannot read (no key, another key, no package) and have not all read is `own_share_unread`."""
    gen = generation(own)
    if (gen.cipher, gen.fingerprint) == (cipher, crypto.fingerprint(key) if key else None):
        row = conn.execute('SELECT gen FROM peers WHERE machine = ?', (machine,)).fetchone()
        if row and row[0] != gen.n:  # a start killed after its label, before its reset: what is marked out is not in this generation
            conn.execute('BEGIN IMMEDIATE')
            with conn:
                _restart(conn, machine, gen.n)
        return gen
    if not key and gen.cipher and memdb.meta(conn, 'share_plain') != '1':
        raise ValueError(t('share_needs_join', machine=machine))
    unreadable = _refusal(gen, key, machine, False)
    if not unreadable:  # our own open history is ours to read, key or none
        pull(gen)
    row = conn.execute('SELECT gen, last_batch FROM peers WHERE machine = ?', (machine,)).fetchone()
    if max(_batches(gen.path, crypto.EXT[gen.cipher]), default=0) > (row[1] if row and row[0] == gen.n else 0):
        if unreadable:
            raise ValueError(t('own_share_unread', machine=machine, path=gen.path))
        return None
    return start_generation(conn, own, machine, cipher, key)


def ollama_digest(model, url=OLLAMA, timeout=5):
    """The digest Ollama gives the model, for the batch header and the warning when two computers run different builds; None if unknown."""
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + '/api/tags', timeout=timeout) as r:
            models = json.load(r).get('models') or []
    except (OSError, ValueError):
        return None
    return next((m.get('digest') for m in models if m.get('name') in (model, model + ':latest') or m.get('model') in (model, model + ':latest')), None)


# ---- push: what this computer's transcripts added since its last batch

# of a record of ours: a vector that is not out yet (of the right length; the empty one of a record that failed to embed is none)
_VEC_OUT = 'k.vec_pushed = 0 AND EXISTS (SELECT 1 FROM vecs v WHERE v.chunk = k.id AND length(v.vec) = ?)'


def pending(conn):
    """Whether something of ours is not out yet: a forget, a record, or a vector."""
    return bool(conn.execute('SELECT EXISTS(SELECT 1 FROM outbox) OR EXISTS(SELECT 1 FROM keys k WHERE k.origin IS NULL AND (k.pushed = 0 OR '
                             + _VEC_OUT + '))', (4 * DIMS,)).fetchone()[0])


def _collect(conn):
    """The lines of our next batch, by plain reads: forgets, then conversations, records and vectors of ours that are not out yet, about
    BATCH_BYTES of them, and what to mark once the file is written: (id, hash) of the records, (id, hash) of the vectors, outbox seqs. The
    vectors of the records of this batch may come with it."""
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
            ids.append((rid, h))
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
    vec_ids, going = [], {i for i, _ in ids}
    cur = conn.execute('SELECT k.id, k.conv, k.cls, k.hash, k.n, k.pushed FROM keys k WHERE k.origin IS NULL AND ' + _VEC_OUT
                       + ' ORDER BY k.id', (4 * DIMS,))
    for rid, conv, cls, h, n, pushed in cur.fetchall():
        if size > BATCH_BYTES:
            break
        if not (pushed or rid in going):
            continue
        for part, vec in conn.execute('SELECT part, vec FROM vecs WHERE chunk = ? AND length(vec) = ? ORDER BY part', (rid, 4 * DIMS)):
            line = _dumps({'type': 'vec', 'key': [conv, cls, h, n], 'part': part, 'vec': base64.b64encode(vec).decode('ascii')})
            lines.append(line)
            size += len(line)
        vec_ids.append((rid, h))
    return lines, ids, vec_ids, seqs


def _aad(machine, gen, n):
    """What ties a sealed batch to its place: a copy under another name, generation or computer does not open."""
    return '%s/%d/batch-%08d' % (machine, gen.n, n)


def _write(gen, n, lines, machine, key):
    """Batch `n` of generation `gen` as a whole or not at all: written to a .tmp beside it (a cut leaves only that, and the next run writes
    it anew), then renamed. A finished batch is never overwritten."""
    final = os.path.join(gen.path, 'batch-%08d%s' % (n, crypto.EXT[gen.cipher]))
    tmp = final + '.tmp'
    sealed = crypto.seal(lines, gen.cipher, key, _aad(machine, gen, n)) if gen.cipher else None
    with open(tmp, 'wb') as raw:
        if gen.cipher:  # by the generation, never by what came back: no gzip of plain lines under a sealed name
            raw.write(sealed)
        else:
            with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as gz:
                for line in lines:
                    gz.write(line.encode('utf-8') + b'\n')
        raw.flush()
        os.fsync(raw.fileno())
    if os.path.exists(final):  # os.rename replaces a file on POSIX; on Windows it refuses by itself
        raise FileExistsError(final)
    os.rename(tmp, final)


def _push(conn, gen, machine, key, digest, deadline, now, report):
    # the caller holds the sync lock, which keeps a second sync of this computer from taking the same batch number; the share is written
    # with no database lock held, and the write lock is taken only to mark what went out
    while now() < deadline:
        lines, ids, vec_ids, seqs = _collect(conn)
        if not lines:
            return
        n = max(_batches(gen.path, crypto.EXT[gen.cipher]), default=0) + 1
        model = memdb.meta(conn, 'embed_model')
        header = {'type': 'header', 'format': FORMAT, 'machine': machine, 'gen': gen.n, 'batch': n, 'model': model, 'dims': DIMS,
                  'digest': digest(model) if vec_ids and model else None, 'created': _now()}
        _write(gen, n, [_dumps(header)] + lines, machine, key)
        conn.execute('BEGIN IMMEDIATE')
        try:
            # a record rebuilt since the collect (same id, other hash) was not what we sent: it stays unmarked and goes out again
            conn.executemany('UPDATE keys SET pushed = 1 WHERE id = ? AND hash = ?', ids)
            conn.executemany('UPDATE keys SET vec_pushed = 1 WHERE id = ? AND hash = ?', vec_ids)
            conn.executemany('DELETE FROM outbox WHERE seq = ?', [(s,) for s in seqs])
            # our mark moves on only from the batch before: when our own folder stopped at a broken batch, the batches after it
            # must still be read once it is skipped
            conn.execute('INSERT INTO peers(machine, last_batch, gen) VALUES(?, ?, ?) ON CONFLICT(machine) DO UPDATE SET last_batch = excluded.last_batch '
                         'WHERE last_batch = excluded.last_batch - 1', (machine, n, gen.n))
            conn.commit()  # if this fails the batch is out and its records go out again next time: a repeat changes nothing anywhere
        except BaseException:
            conn.rollback()
            raise
        report['pushed'] += len(ids)
        report['vectors']['pushed'] += len(vec_ids)
        report['batches']['pushed'] += 1


# ---- pull: what the other computers pushed, and our own batches when the index here is new

def _import(conn, path, peer, gen, n, own, key, model, digest, report):
    """Take in one batch of generation `gen` in one transaction: a record, a vector or a forget that is here already changes nothing. Raises
    on anything broken, and then nothing of the batch is kept."""
    with open(path, 'rb') as f:  # read whole before the write lock: a slow share must not hold the index
        data = f.read()
    lines = crypto.unseal(data, gen.cipher, key, _aad(peer, gen, n)) if gen.cipher else gzip.decompress(data).decode('utf-8').split('\n')
    head = json.loads(lines[0])
    # a batch of before stage 5 has no `gen`: it is generation 0
    if not (isinstance(head, dict) and head.get('type') == 'header' and head.get('machine') == peer and head.get('batch') == n
            and head.get('gen', 0) == gen.n):
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


def _refuse(conn, peer, error, report):
    _peer_error(conn, peer, error)
    report['errors'].append({'where': 'pull', 'machine': peer, 'error': error})


def _refusal(gen, key, peer, strict):
    """Why we do not read the current generation of `peer` at all, in words for the owner, else None: with `strict` (another computer's
    generation that has batches) an encrypted computer takes nothing open, and a generation is read only with the key it was written
    under and the package of its cipher."""
    if not gen.cipher:
        return t('peer_plain', machine=peer) if key and strict else None
    if not key:
        return t('peer_needs_key', machine=peer)
    if gen.fingerprint != crypto.fingerprint(key):
        return t('peer_other_key', machine=peer)
    try:
        crypto.need(gen.cipher)
    except ValueError as e:
        return str(e)


def _passing(e):
    """A share that hiccups or an index held by another run: no verdict on what was being read, so nothing is called broken for it."""
    return isinstance(e, memdb.db_errors()) or (isinstance(e, OSError) and not isinstance(e, gzip.BadGzipFile))


def _pull(conn, machine_dir, peer, own, key, model, digest, deadline, now, report, gen=None):
    """Take in the batches of `peer` (in its current generation, `gen` when the caller has it) that are new here, in number order. A broken
    or missing one stops that computer: its later batches wait, and the error is reported on every sync until it is fixed or skipped by hand
    (cg.py sync --skip-batch). A generation we may not read (`_refusal`) is told before anything of it is read or reset."""
    try:
        gen = gen or generation(machine_dir)
    except OSError as e:  # a share that hiccups: nothing is reset, the next sync tries again
        return _refuse(conn, peer, t('generation_unreadable', machine=peer, e='%s: %s' % (type(e).__name__, e)), report)
    names = _batches(gen.path, crypto.EXT[gen.cipher])
    if not names and not conn.execute('SELECT 1 FROM peers WHERE machine = ?', (peer,)).fetchone():
        return  # a folder of something else, or a computer that has pushed nothing yet: no computer to show
    error = _refusal(gen, key, peer, bool(names) and not own)  # our own open history is ours, whatever the key
    if error:
        return _refuse(conn, peer, error, report)
    # a generation not read yet is read from its first batch, and the marks of the one before mean nothing in it
    conn.execute("INSERT INTO peers(machine, gen) VALUES(?, ?) ON CONFLICT(machine) DO UPDATE SET gen = excluded.gen, last_batch = 0, "
                 "skipped = '[]', error = NULL WHERE gen != excluded.gen", (peer, gen.n))
    last, skipped = conn.execute('SELECT last_batch, skipped FROM peers WHERE machine = ?', (peer,)).fetchone()
    skipped = set(json.loads(skipped))
    error, n = None, last + 1
    while now() < deadline:
        if n in skipped:
            conn.execute('UPDATE peers SET last_batch = ? WHERE machine = ?', (n, peer))
        elif n in names:
            try:
                _import(conn, os.path.join(gen.path, names[n]), peer, gen, n, own, key, model, digest, report)
            except Exception as e:
                # a share that hiccups or an index held by another run is no broken batch: no skip is offered, the next sync tries again
                error = t('batch_unreadable' if _passing(e) else 'batch_broken', n=n, machine=peer, e='%s: %s' % (type(e).__name__, e))
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


def _memory_file(gen, machine, project, secret):
    """Path and aad of the one sealed memory file of `project` in an encrypted generation: its name says nothing, and the aad ties what is
    in it to that place, so a file moved under another project's name does not open."""
    name = crypto.opaque(secret, project)
    return os.path.join(gen.path, 'memory', name + crypto.EXT[gen.cipher]), '%s/%d/memory/%s' % (machine, gen.n, name)


def _read_facts(gen, machine, project, secret):
    """{name: bytes} of the facts `machine` shares for `project` in generation `gen`: a file per fact in an open one, a single sealed file in
    an encrypted one. The share is untrusted: a sealed file that is not what it says, or holds a name that is no fact file (one that leads
    out of the memory folder, MEMORY.md), is a ValueError and none of it is used."""
    if not gen.cipher:
        folder = os.path.join(gen.path, 'memory', project_folder(project))
        return {n: _read(os.path.join(folder, n)) for n in _facts(folder)}
    path, aad = _memory_file(gen, machine, project, secret)
    try:
        data = _read(path)
    except FileNotFoundError:
        return {}
    lines = crypto.unseal(data, gen.cipher, secret, aad)
    head = json.loads(lines[0])
    if not (isinstance(head, dict) and head.get('type') == 'memory' and head.get('machine') == machine and head.get('gen') == gen.n):
        raise ValueError(t('memory_bad', machine=machine))
    facts = {}
    for line in lines[1:]:
        if line:
            o = json.loads(line)
            if not (o['name'].endswith('.md') and o['name'].lower() != 'memory.md' and not _BAD_NAME.search(o['name'])):
                raise ValueError(t('memory_bad', machine=machine))
            facts[o['name']] = base64.b64decode(o['data'], validate=True)
    return facts


def _write_facts(gen, machine, project, secret, facts):
    """Add `facts` ({name: bytes}) to those of `machine` for `project` in `gen`; the others stay. An encrypted generation has one file: it is
    read, extended and put back whole (tmp + replace), and only when that changes it."""
    if not gen.cipher:
        folder = os.path.join(gen.path, 'memory', project_folder(project))
        os.makedirs(folder, exist_ok=True)
        for name, data in facts.items():
            _put(os.path.join(folder, name), data)
        return
    have = _read_facts(gen, machine, project, secret)
    merged = {**have, **facts}
    if merged == have:
        return
    path, aad = _memory_file(gen, machine, project, secret)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    head = _dumps({'type': 'memory', 'machine': machine, 'gen': gen.n})
    _put(path, crypto.seal([head] + [_dumps({'name': n, 'data': base64.b64encode(d).decode('ascii')}) for n, d in sorted(merged.items())],
                           gen.cipher, secret, aad))


def _pointer(name, data):
    """The line of MEMORY.md that points to a fact, in the form of Claude Code's auto-memory: its name and description from the frontmatter."""
    m = re.match(r'---\r?\n(.*?)\r?\n---', data.decode('utf-8', errors='replace'), re.S)
    front = dict(re.findall(r'^(\w+):[ \t]*(.*?)[ \t]*$', m.group(1), re.M)) if m else {}
    title = front.get('name') or name[:-3]
    return '- [%s](%s)' % (title, name) + (' — ' + front['description'] if front.get('description') else '')


def sync_memory(conn, sync_dir, machine, peers, memory_dir, key, gens, secret, errors=None):
    """Facts made on this computer go into its folder; facts of the other computers come into `memory_dir` when it lacks them. A file here
    is never overwritten unless it is a copy taken in earlier and left as it was; a fact the owner deleted after it came does not come back.
    `key` is the project's; `gens` holds the current generation of this computer and of each of `peers`, `secret` the encryption key or None.
    A computer whose generation we may not read (`_refusal`) is passed over, its batch pull told why; a memory file that cannot be read is an
    entry in `errors`, and none of its facts come in (ours is then not written over)."""
    if not os.path.isdir(memory_dir):
        raise FileNotFoundError(t('memory_dir_missing', dir=memory_dir))
    errors = [] if errors is None else errors
    out = {'pushed': 0, 'pulled': 0, 'conflicts': 0}
    taken = {name: (src, src_name, h) for name, src, src_name, h in conn.execute(
        'SELECT name, source, src_name, hash FROM mem_files WHERE project = ?', (key,))}

    def read(gen, who):
        try:
            return _read_facts(gen, who, key, secret)
        except Exception as e:
            if _passing(e):
                raise
            errors.append({'where': 'memory', 'machine': who, 'error': t('memory_broken', machine=who, e='%s: %s' % (type(e).__name__, e))})

    mine, new = read(gens[machine], machine), {}
    if mine is not None:
        for name in _facts(memory_dir):
            # a copy from another computer is not ours to push: it would come back to where it came from
            if name in taken or any(name[:-3].endswith('--' + p) for p in peers):
                continue
            data = _read(os.path.join(memory_dir, name))
            if mine.get(name) != data:
                new[name] = data
        if new:
            _write_facts(gens[machine], machine, key, secret, new)
    out['pushed'] = len(new)
    read_of = {peer: read(gens[peer], peer) or {} for peer in peers if peer in gens and not _refusal(gens[peer], secret, peer, True)}
    added = []
    conn.execute('BEGIN IMMEDIATE')  # the share is read; from here only the index and the memory folder
    with conn:
        for peer in read_of:
            theirs = read_of[peer]
            for name in sorted(theirs):
                data = theirs[name]
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
         now=time.monotonic, key=None, cipher='sqlcipher'):
    """Push our batches, take in those of the other computers, then the memory facts of the current project. Nothing here stops the local
    index: an unavailable folder or a broken batch is an entry in 'errors', and the next sync goes on from where this one stopped. With a
    `key` the batches are sealed by `cipher`, and a generation of another cipher or key, or an open one, is not read (`_refusal`)."""
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
        pull = lambda g: _pull(conn, own, machine, True, key, model, digest_once, deadline, now, report, g)
        gen = _own_generation(conn, own, machine, cipher if key else None, key, pull)  # the one we write into, looked at once for pull and push
        if gen:
            pull(gen)
            _push(conn, gen, machine, key, digest_once, deadline, now, report)
        for peer in peers:
            _pull(conn, os.path.join(sync_dir, peer), peer, False, key, model, digest_once, deadline, now, report)
        if memory_dir and gen:  # none of it while our own new generation is blocked: where our facts would go is not decided yet
            gens = {machine: gen}
            for p in peers:
                with contextlib.suppress(OSError):  # a share that hiccups: the pull told, and that computer's facts wait for the next sync
                    gens[p] = generation(os.path.join(sync_dir, p))
            report['memory'] = sync_memory(conn, sync_dir, machine, peers, memory_dir, project_key(project_cwd or os.getcwd(), home), gens, key,
                                           report['errors'])
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
