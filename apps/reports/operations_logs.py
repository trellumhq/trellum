"""Opt-in authorized Operations tails and complete persisted-tail streaming.

Live files are read only on an explicit terminal request. Persisted downloads
contain Run.stdout_tail/stderr_tail, not complete original worker log files.
No helper creates directories, repairs state, or admits worker operations.
"""
from dataclasses import dataclass
from copy import copy
import json
import os
from pathlib import Path
import stat
from uuid import UUID

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import BinaryField, Func, IntegerField
from django.db.models.functions import Length, Substr
from django.http import Http404
from django.utils import timezone

from apps.reports.operations import operations_relation, validate_slugs
from apps.runner.models import Run

TAIL_BYTES = 64 * 1024
RESPONSE_BYTES = 128 * 1024
STREAM_CHARACTERS = 16 * 1024
STREAMS = ('stdout', 'stderr')
RUN_FIELDS = ('id', 'report_id', 'studio_id', 'slug', 'status', 'created_at',
              'started_at', 'finished_at', 'exit_code', 'log_dir', 'worker_id', 'pid')


class ByteLength(Func):
    function = 'octet_length'
    output_field = IntegerField()


class SharedByteTail(Func):
    """Transfer at most 64KiB across the two PostgreSQL bytea tails.

    A quiet stream lends its unused half to the other. Character-count RIGHT
    would transfer up to four times its nominal byte budget for Unicode.
    """
    output_field = BinaryField()

    def as_sql(self, compiler, connection):
        text, text_params = compiler.compile(self.source_expressions[0])
        other, other_params = compiler.compile(self.source_expressions[1])
        limit = f'LEAST({TAIL_BYTES}, {TAIL_BYTES // 2} + GREATEST({TAIL_BYTES // 2} - octet_length({other}), 0))'
        sql = f"substring(convert_to({text},'UTF8') from GREATEST(octet_length({text}) - {limit} + 1, 1) for {limit})"
        return sql, text_params * 2 + other_params * 2


class OperationsLogChanged(RuntimeError):
    """A streamed observation no longer identifies the selected persisted run."""


class _Unavailable(Exception):
    pass


class _Waiting(Exception):
    """No live run yet for a build that is starting."""


def _uuid(value):
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError('Invalid Operations run identity')
    try:
        result = UUID(value)
    except ValueError as error:
        raise ValueError('Invalid Operations run identity') from error
    if str(result) != value.lower():
        raise ValueError('Invalid Operations run identity')
    return result


def _select(request, slug, mode, run_id):
    validate_slugs([slug])
    if mode not in ('last', 'live'):
        raise ValueError('Invalid Operations log mode')
    identity = _uuid(run_id) if run_id is not None else None
    report = operations_relation(request).filter(slug=slug).values('pk').first()
    if report is None:
        raise Http404('Report unavailable')
    runs = Run.objects.filter(report_id=report['pk'], studio_id=request.studio.pk)
    if mode == 'last':
        runs = runs.exclude(status__in=Run.ACTIVE_STATUSES)
    else:
        runs = runs.filter(status__in=(Run.STARTING, Run.RUNNING))
    if identity is not None:
        runs = runs.filter(pk=identity)
    run = runs.order_by('-created_at', '-pk').values(*RUN_FIELDS).first()
    if run is None:
        if mode == 'live' and identity is None:
            # A build that was just requested is not running yet: that is a
            # state to wait in, not a missing log.
            raise _Waiting
        raise Http404('Log unavailable')
    return run


def _run_query(run):
    return Run.objects.filter(**run)


def _current(request, slug, run):
    return (operations_relation(_fresh_request(request)).filter(pk=run['report_id'], slug=slug, studio__org__is_active=True).exists()
            and _run_query(run).exists())


def _fresh_request(request):
    result = copy(request)
    result._bi_effective_roles = {}
    return result


def _public_run(run):
    return {key: str(run[key]) if key == 'id' else run[key]
            for key in ('id', 'slug', 'status', 'created_at', 'started_at', 'finished_at', 'exit_code')}


def _shares(lengths, budget):
    first = min(lengths[0], budget // 2)
    second = min(lengths[1], budget - first)
    return min(lengths[0], budget - second), second


def _decode_tail(raw, *, clipped):
    # A leading continuation byte at a seek boundary belongs to a character
    # outside this tail. Other malformed bytes retain read_tail's replacement
    # behavior instead of silently disappearing.
    if clipped:
        offset = 0
        while offset < min(3, len(raw)) and raw[offset] & 0xc0 == 0x80:
            offset += 1
        raw = raw[offset:]
    return raw.decode('utf-8', errors='replace')


def _clip(text, budget):
    encoded = text.encode('utf-8')
    if len(encoded) <= budget:
        return text
    return encoded[-budget:].decode('utf-8', errors='ignore') if budget else ''


def _fit(payload, texts, originals, already_clipped):
    """Enforce UTF-8 and escaped JSON ceilings without another storage read."""
    def candidate(budget):
        result = dict(payload)
        lengths = [len(text.encode('utf-8')) for text in texts]
        shares = _shares(lengths, budget)
        result['streams'] = {}
        for index, name in enumerate(STREAMS):
            text = _clip(texts[index], shares[index])
            result[name] = text
            result['streams'][name] = dict(
                original_bytes=originals[index], returned_bytes=len(text.encode('utf-8')),
                truncated=already_clipped[index] or text != texts[index])
        return result

    result = candidate(TAIL_BYTES)
    if len(json.dumps(result, cls=DjangoJSONEncoder).encode('utf-8')) <= RESPONSE_BYTES:
        return result
    low, high = 0, TAIL_BYTES
    while low < high:
        budget = (low + high + 1) // 2
        result = candidate(budget)
        if len(json.dumps(result, cls=DjangoJSONEncoder).encode('utf-8')) <= RESPONSE_BYTES:
            low = budget
        else:
            high = budget - 1
    return candidate(low)


def _file_identity(info):
    return info.st_dev, info.st_ino


def _open_directory(run):
    # executor.live_log_dir(run.id) is DATA_DIR/tmp/runs/<UUID>. Derive its
    # exact layout without calling executor._tmp_root(), which mkdirs on read.
    # A data directory that is itself a symlink (a mounted volume) is part of
    # the deployment, so the anchor is resolved once. Every component below it
    # is still opened without following symlinks.
    layout = ('tmp', 'runs', str(run['id']))
    anchor = Path(os.path.realpath(settings.DATA_DIR))
    expected = {str(Path(settings.DATA_DIR).joinpath(*layout)),
                str(Path(os.path.abspath(settings.DATA_DIR)).joinpath(*layout)),
                str(anchor.joinpath(*layout))}
    if run['log_dir'] not in expected:
        raise _Unavailable
    if not all(hasattr(os, flag) for flag in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')) or os.open not in os.supports_dir_fd:
        raise _Unavailable
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    descriptor = None
    identities = []
    try:
        descriptor = os.open(anchor, flags)
        identities.append(_file_identity(os.fstat(descriptor)))
        for part in layout:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            identities.append(_file_identity(os.fstat(descriptor)))
        return descriptor, tuple(identities)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _file_stat(directory, name):
    info = os.stat(name + '.log', dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise _Unavailable
    return info


def _read_file(directory, name, before, budget):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
    descriptor = os.open(name + '.log', flags, dir_fd=directory)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _file_identity(opened) != _file_identity(before) or opened.st_size < before.st_size:
            raise _Unavailable
        offset = max(0, before.st_size - budget)
        raw = os.pread(descriptor, min(before.st_size, budget), offset)
        after = os.fstat(descriptor)
        if len(raw) != min(before.st_size, budget) or after.st_size < before.st_size:
            raise _Unavailable
        return _decode_tail(raw, clipped=offset > 0)
    finally:
        os.close(descriptor)


def _live_tails(run):
    """At most two FDs, including traversal; allow append, reject rotation."""
    directory, identities = _open_directory(run)
    try:
        before = [_file_stat(directory, name) for name in STREAMS]
        originals = [info.st_size for info in before]
        shares = _shares(originals, TAIL_BYTES)
        texts = [_read_file(directory, name, before[index], shares[index])
                 for index, name in enumerate(STREAMS)]
    finally:
        os.close(directory)
    # Rewalk from the configured anchor after closing the old directory. This
    # catches directory swaps while respecting the same two-descriptor ceiling.
    directory, current_identities = _open_directory(run)
    try:
        if current_identities != identities:
            raise _Unavailable
        for index, name in enumerate(STREAMS):
            after = _file_stat(directory, name)
            if (_file_identity(after) != _file_identity(before[index]) or after.st_size < originals[index]
                    or (after.st_size == originals[index] and after.st_mtime_ns != before[index].st_mtime_ns)):
                raise _Unavailable
    finally:
        os.close(directory)
    return texts, originals, [originals[index] > shares[index] for index in range(2)]


def _persisted_tails(run):
    row = _run_query(run).annotate(
        stdout_part=SharedByteTail('stdout_tail', 'stderr_tail'),
        stderr_part=SharedByteTail('stderr_tail', 'stdout_tail'),
        stdout_bytes=ByteLength('stdout_tail'), stderr_bytes=ByteLength('stderr_tail'),
    ).values('stdout_part', 'stderr_part', 'stdout_bytes', 'stderr_bytes').first()
    if row is None:
        raise _Unavailable
    originals = [row[name + '_bytes'] for name in STREAMS]
    raw = [bytes(row[name + '_part']) for name in STREAMS]
    clipped = [originals[index] > len(raw[index]) for index in range(2)]
    return [_decode_tail(raw[index], clipped=clipped[index]) for index in range(2)], originals, clipped


def operations_log_payload(request, slug, *, mode='last', run_id=None):
    """Bound one explicitly requested authorized live or persisted terminal."""
    try:
        run = _select(request, slug, mode, run_id)
    except _Waiting:
        return dict(version=1, state='waiting', mode='live', run=None, observed_at=timezone.now(),
                    authority='live-local-tail', availability='waiting', message='Waiting for the build to start.',
                    stdout='', stderr='', streams=None)
    payload = dict(version=1, state='ready', mode=mode, run=_public_run(run),
                   observed_at=timezone.now(), authority='live-local-tail' if mode == 'live' else 'persisted-db-tail')
    try:
        texts, originals, clipped = _live_tails(run) if mode == 'live' else _persisted_tails(run)
        if not _current(request, slug, run):
            raise _Unavailable
    except (OSError, ValueError, _Unavailable):
        return {**payload, 'state': 'unavailable', 'availability': 'unavailable',
                'message': 'This log observation is unavailable.',
                'stdout': None, 'stderr': None, 'streams': None}
    payload['availability'] = 'available' if any(texts) else 'quiet'
    payload['label'] = 'Live local log tail' if mode == 'live' else 'Persisted stdout/stderr tails'
    return _fit(payload, texts, originals, clipped)


@dataclass
class PersistedTailStream:
    """Single-pass, fixed-chunk download of complete persisted Run tails."""
    request: object
    slug: str
    identity: dict
    lengths: dict
    consumed: bool = False

    label = 'Complete persisted stdout/stderr tails'
    content_type = 'text/plain; charset=utf-8'

    @property
    def run(self):
        return _public_run(self.identity)

    @property
    def filename(self):
        return f"run-{self.identity['id']}-persisted-tails.txt"

    def _query(self):
        # A stream can outlive the request's first role observation. Resolve
        # grants again before body delivery/chunks without retaining a new cache.
        request = _fresh_request(self.request)
        if not operations_relation(request).filter(pk=self.identity['report_id'], slug=self.slug, studio__org__is_active=True).exists():
            raise OperationsLogChanged('The selected report is no longer available.')
        query = _run_query(self.identity).annotate(
            stdout_bytes=ByteLength('stdout_tail'), stderr_bytes=ByteLength('stderr_tail'),
            stdout_characters=Length('stdout_tail'), stderr_characters=Length('stderr_tail'),
        ).filter(**self.lengths)
        return query

    def __iter__(self):
        if self.consumed:
            raise OperationsLogChanged('A persisted-tail stream cannot be replayed.')
        self.consumed = True
        if not self._query().exists():
            raise OperationsLogChanged('The selected persisted log changed before download.')
        yield (self.label + '\n').encode('utf-8')
        for name in STREAMS:
            yield ('\n--- ' + name + ' (complete persisted tail) ---\n').encode('utf-8')
            total = self.lengths[name + '_characters']
            for start in range(1, total + 1, STREAM_CHARACTERS):
                row = self._query().annotate(part=Substr(name + '_tail', start, STREAM_CHARACTERS)).values('part').first()
                if row is None:
                    raise OperationsLogChanged('The selected persisted log changed during download.')
                yield row['part'].encode('utf-8')
        if not self._query().exists():
            raise OperationsLogChanged('The selected persisted log changed during download.')


def operations_persisted_tail_stream(request, slug, *, run_id):
    """Authorize a stable terminal UUID and stream only its persisted DB tails.

    The caller supplies attachment/no-store headers. No disk download authority
    is introduced here. Closing/abandoning iteration holds no file or DB cursor.
    """
    run = _select(request, slug, 'last', _uuid(run_id))
    lengths = _run_query(run).annotate(
        stdout_bytes=ByteLength('stdout_tail'), stderr_bytes=ByteLength('stderr_tail'),
        stdout_characters=Length('stdout_tail'), stderr_characters=Length('stderr_tail'),
    ).values('stdout_bytes', 'stderr_bytes', 'stdout_characters', 'stderr_characters').first()
    if lengths is None:
        raise OperationsLogChanged('The selected persisted log changed before download.')
    return PersistedTailStream(request, slug, run, lengths)
