#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read a zip archive over HTTP(S) with Range requests instead of downloading it.

A macOS restore IPSW is a ~25 GB zip and a boot-chain extraction needs a few
hundred MB of it. A zip ends with its table of contents and stores every member
as one contiguous byte range, so zipfile.ZipFile can run on a seekable object
that fetches only the ranges it is asked for:

    remote = RangeFile(url, max_fetch=256 << 20)
    with open_zip(remote) as archive:
        manifest = archive.read('BuildManifest.plist')
    print(remote.summary())

    python3 tools/ipsw_remote.py [--max-fetch-mib N] <url>     # list the members

The bytes feed a boot chain, so every doubt is a refusal and not a guess. Each
has its own RemoteZipError subclass: a server that answers 200 to a Range request
(reading on would mean the whole archive); a different range or length than was
asked; a Content-Length that contradicts its Content-Range; a length, ETag or
Last-Modified that changes between requests; a body shorter or longer than its
range; an answer that is not a zip; a redirect that is not https (plain http is
followed only to 127.0.0.1 or localhost, and only from a loopback origin, which is
what the tests use); and more than the fetch cap in total. Transient failures
(network errors, HTTP 408/425/429/5xx, short bodies) are retried a bounded number
of times with the linear backoff of tools/fetch-ipsw.py; anything else stops at once.

Evidence: EXPERIMENTAL. Every path runs against an in-process fake transport and
a server on 127.0.0.1 (tools/test-ipsw-remote.py); no real CDN was contacted.
That Apple's CDN honours Range is INFERRED from tools/fetch-ipsw.py, which already
resumes with it. Its redirects and validators have not been observed.
"""
import argparse
import collections
import errno
import http.client
import io
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BLOCK = 1 << 20          # cache granule; every request is rounded out to whole blocks
CACHE_BLOCKS = 32        # LRU bound: the cache never holds more than 32 MiB
MAX_REQUEST = 8 * BLOCK  # one Range request covers at most this; a larger read is several
CHUNK = 1 << 20          # body read size, as in fetch-ipsw.py
TIMEOUT = 60             # seconds per socket operation, as in fetch-ipsw.py
ATTEMPTS = 6             # per request; fetch-ipsw.py allows 40 for one whole 27 GB transfer
BACKOFF_STEP = 5         # attempt n waits min(BACKOFF_STEP * n, BACKOFF_MAX) seconds, as in fetch-ipsw.py
BACKOFF_MAX = 60
DEFAULT_MAX_FETCH = 1 << 30
LOOPBACK = ('127.0.0.1', 'localhost')
TRANSIENT_STATUS = (408, 425, 429)   # every 5xx is transient too
ZIP_SIGNATURES = (b'PK\x03\x04', b'PK\x05\x06')   # a first local header, or an empty archive
LOCAL_HEADER = 30        # fixed part of a zip local file header (APPNOTE 4.3.7)
USER_AGENT = 'snapintosh-ipsw-remote'
CONTENT_RANGE = re.compile(r'bytes (\d+)-(\d+)/(\d+|\*)')


class RemoteZipError(Exception):
    """The remote archive was refused.

    Deliberately not an OSError: zipfile turns any OSError raised by seek() or
    read() while it looks for the end record into "File is not a zip file", which
    would hide every message below.
    """


class UrlRefusedError(RemoteZipError):
    """A URL or redirect target that is not https (or loopback http)."""


class RangeIgnoredError(RemoteZipError):
    """The server answered 200 to a Range request."""


class RangeMismatchError(RemoteZipError):
    """The answer is not the byte range that was asked for."""


class ObjectChangedError(RemoteZipError):
    """Length, ETag or Last-Modified differs from an earlier answer."""


class ShortBodyError(RemoteZipError):
    """The body stayed shorter than its range on every attempt."""


class FetchFailedError(RemoteZipError):
    """Retries ran out, or the server refused in a way that retrying cannot fix."""


class FetchCapExceededError(RemoteZipError):
    """The request would take total fetched bytes over the cap."""


class NotAZipError(RemoteZipError):
    """The object is not a readable zip archive."""


class _Transient(Exception):
    def __init__(self, reason, short=False):
        super().__init__(reason)
        self.short = short


def display(url):
    """The URL without credentials, query or fragment: a signed URL's token is in the query."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ''
    if ':' in host:
        host = f'[{host}]'
    try:
        port = f':{parts.port}' if parts.port else ''
    except ValueError:
        port = ''
    return f'{parts.scheme}://{host}{port}{parts.path}'


def is_loopback(url):
    return urllib.parse.urlsplit(url).hostname in LOOPBACK


def check_url(url, origin=None):
    """Refuse everything but https, and http to loopback from a loopback origin.

    origin is the URL a redirect chain began at. Plain http to loopback is a test
    convenience: letting a public https server bounce the tool to a service on
    this machine is not.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.hostname:
        if parts.scheme == 'https':
            return
        if parts.scheme == 'http' and parts.hostname in LOOPBACK and (origin is None or is_loopback(origin)):
            return
    what = 'URL' if origin is None else 'redirect to'
    raise UrlRefusedError(f'{what} {display(url)} is not allowed: only https is followed '
                          '(plain http only to 127.0.0.1 or localhost, from a loopback origin)')


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """urllib's redirect handling, with every hop vetted by check_url."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        origin = getattr(req, 'origin_url', req.full_url)
        check_url(newurl, origin)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.origin_url = origin
        return new


class _Reply:
    """A urllib response in the shape RangeFile expects from any transport."""

    def __init__(self, response):
        self._response = response
        self.status = getattr(response, 'status', None) or response.code
        self.headers = response.headers
        self.url = response.geturl()

    def read(self, size):
        return self._response.read(size)

    def close(self):
        self._response.close()


class UrllibTransport:
    """The default transport: one ranged GET per call, redirects vetted, any HTTP status returned.

    Loopback requests bypass proxy settings: a proxy must never see, and could not
    reach, the test server on this machine.
    """

    def __init__(self):
        self._public = urllib.request.build_opener(_GuardedRedirects())
        self._loopback = urllib.request.build_opener(urllib.request.ProxyHandler({}), _GuardedRedirects())

    def __call__(self, url, first, last, timeout):
        check_url(url)
        request = urllib.request.Request(url, headers={
            'Range': f'bytes={first}-{last}',
            # A compressed body would make the offsets refer to the encoding, not the archive.
            'Accept-Encoding': 'identity',
            'User-Agent': USER_AGENT})
        request.origin_url = url
        opener = self._loopback if is_loopback(url) else self._public
        try:
            return _Reply(opener.open(request, timeout=timeout))
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                error.close()
                raise UrlRefusedError(f'redirect from {display(url)} not followed (HTTP {error.code}): '
                                      'not https, a loop, or no Location') from None
            return _Reply(error)


def _certificate_failure(error):
    return isinstance(error, ssl.SSLCertVerificationError) or \
        isinstance(getattr(error, 'reason', None), ssl.SSLCertVerificationError)


class RangeFile(io.BufferedIOBase):
    """A read-only, seekable file over an HTTP(S) object, fetched in cached blocks with Range requests.

    transport(url, first, last, timeout) is the whole HTTP layer and returns a reply
    with status, headers (case-insensitive .get), url (after redirects), read(n) and
    close(). It answers every HTTP status, and raises OSError or HTTPException for
    failures below HTTP. The default speaks urllib; tests pass a fake.

    Reads are served from block_size-aligned blocks (1 MiB by default) in a bounded LRU, so
    zipfile's many small reads cost one request per block, and a long read is fetched in
    coalesced requests of at most MAX_REQUEST. The first request (block 0) learns the length
    and the validators every later answer is compared with. max_fetch caps the bytes of all
    bodies received over the object's life, retries included; a read that cannot fit is
    refused before any request, so a whole-archive read is impossible by construction.
    """

    def __init__(self, url, *, max_fetch=DEFAULT_MAX_FETCH, block_size=BLOCK, cache_blocks=CACHE_BLOCKS,
                 timeout=TIMEOUT, attempts=ATTEMPTS, transport=None, sleep=time.sleep, require_zip=True):
        super().__init__()
        self._cache = collections.OrderedDict()
        self._pos = self._requests = self._retries = self._fetched = 0
        self._size = self._identity = None
        if min(max_fetch, block_size, cache_blocks, attempts) < 1:
            raise ValueError('max_fetch, block_size, cache_blocks and attempts must be at least 1')
        check_url(url)
        self.origin = self._url = url
        self.name = display(url)
        self._max_fetch, self._block, self._cache_blocks = max_fetch, block_size, cache_blocks
        self._timeout, self._attempts, self._sleep = timeout, attempts, sleep
        self._transport = transport or UrllibTransport()
        first_block = self._load(0, 0)[0]
        if require_zip and first_block[:4] not in ZIP_SIGNATURES:
            raise NotAZipError(f'{self.name} is not a zip archive: it starts with {first_block[:4]!r}, '
                               'not a zip signature')

    @property
    def size(self):
        return self._size

    @property
    def block_size(self):
        return self._block

    @property
    def max_fetch(self):
        return self._max_fetch

    @property
    def requests(self):
        """HTTP requests issued, retries included."""
        return self._requests

    @property
    def retries(self):
        return self._retries

    @property
    def bytes_fetched(self):
        """Body bytes received, including those of attempts that were then retried."""
        return self._fetched

    def summary(self):
        share = 100 * self._fetched / self._size if self._size else 0.0
        retried = f', {self._retries} retried' if self._retries else ''
        return f'{self._requests} requests{retried}, {self._fetched:,} bytes fetched ' \
               f'({share:.2f}% of {self._size:,})'

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        self._check_open()
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        self._check_open()
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._pos + offset
        elif whence == io.SEEK_END:
            target = self._size + offset
        else:
            raise ValueError(f'invalid whence ({whence})')
        if target < 0:
            # An OSError like a real file's, which zipfile relies on for archives smaller than an end record.
            raise OSError(errno.EINVAL, 'negative seek position')
        self._pos = target
        return target

    def read(self, size=-1):
        self._check_open()
        left = max(self._size - self._pos, 0)
        if size is None or size < 0 or size > left:
            size = left
        if size == 0:
            return b''
        start, stop = self._pos, self._pos + size
        first, last = start // self._block, (stop - 1) // self._block
        # Refuse an over-cap read before allocating its buffer or issuing any request.
        self._check_cap(sum(self._block_len(i) for i in range(first, last + 1) if i not in self._cache))
        out = bytearray(size)
        index = first
        while index <= last:
            data = self._cached(index)
            if data is not None:
                blocks = [data]
            else:
                end = index
                while (end < last and end + 1 not in self._cache and
                       end + 1 - index < max(MAX_REQUEST // self._block, 1)):
                    end += 1
                blocks = self._load(index, end)
            for i, data in enumerate(blocks, index):
                lo, hi = max(start, i * self._block), min(stop, i * self._block + len(data))
                out[lo - start:hi - start] = data[lo - i * self._block:hi - i * self._block]
            index += len(blocks)
        self._pos = stop
        return bytes(out)

    def close(self):
        self._cache.clear()
        super().close()

    def bytes_to_fetch(self, spans):
        """The bytes reading these [start, stop) spans would still fetch: whole blocks not already cached."""
        wanted = set()
        for start, stop in spans:
            start, stop = max(start, 0), min(stop, self._size)
            if start < stop:
                wanted.update(range(start // self._block, (stop - 1) // self._block + 1))
        return sum(self._block_len(i) for i in wanted if i not in self._cache)

    def _check_open(self):
        if self.closed:
            raise ValueError('I/O operation on closed file')

    def _block_len(self, index):
        return min(self._block, self._size - index * self._block)

    def _cached(self, index):
        data = self._cache.get(index)
        if data is not None:
            self._cache.move_to_end(index)
        return data

    def _store(self, index, data):
        self._cache[index] = data
        self._cache.move_to_end(index)
        while len(self._cache) > self._cache_blocks:
            self._cache.popitem(last=False)

    def _load(self, first, last):
        """Fetch blocks first..last as one range and cache them; returns them in order.

        The caller copies from the returned list, not the cache, because a long run
        can evict its own early blocks.
        """
        begin = first * self._block
        end = (last + 1) * self._block
        if self._size is not None:
            end = min(end, self._size)
        body = self._fetch(begin, end)
        blocks = [body[o:o + self._block] for o in range(0, len(body), self._block)]
        for index, data in enumerate(blocks, first):
            self._store(index, data)
        return blocks

    def _check_cap(self, nbytes):
        if self._fetched + nbytes > self._max_fetch:
            raise FetchCapExceededError(
                f'fetch cap exceeded: {self._fetched:,} bytes already fetched and {nbytes:,} more needed, over '
                f'the cap of {self._max_fetch:,} bytes ({self._max_fetch / (1 << 20):g} MiB); raise '
                '--max-fetch-mib only if reading that much is intended')

    def _fetch(self, start, stop):
        """Bytes [start, stop) of the object, with bounded retries and backoff."""
        failure = None
        for attempt in range(1, self._attempts + 1):
            self._check_cap(stop - start)
            self._requests += 1
            try:
                return self._attempt(start, stop)
            except _Transient as error:
                failure = error
            if attempt < self._attempts:
                self._retries += 1
                self._sleep(min(BACKOFF_STEP * attempt, BACKOFF_MAX))
        if failure.short:
            raise ShortBodyError(f'{self.name}: {failure}, on all {self._attempts} attempts') from failure
        raise FetchFailedError(f'{self.name}: request failed on all {self._attempts} attempts: {failure}') from failure

    def _attempt(self, start, stop):
        try:
            reply = self._transport(self._url, start, stop - 1, self._timeout)
            try:
                return self._accept(reply, start, stop)
            finally:
                reply.close()
        except (OSError, http.client.HTTPException) as error:
            # Below HTTP, whether on connect or part-way through a body: the CDN "drops connections".
            if _certificate_failure(error):
                raise FetchFailedError(f'TLS certificate verification failed for {self.name}: {error}; '
                                       'not retrying') from error
            raise _Transient(f'{type(error).__name__}: {error}') from error

    def _accept(self, reply, start, stop):
        if reply.status == 200:
            raise RangeIgnoredError(f'{self.name} answered 200 to a Range request: the server ignored Range, '
                                    'so reading on would mean downloading the whole archive')
        if reply.status != 206:
            raise self._status_failure(reply.status)
        final = reply.url or self._url
        check_url(final, self.origin)
        headers = reply.headers
        encoding = (headers.get('Content-Encoding') or 'identity').strip().lower()
        if encoding != 'identity':
            raise RangeMismatchError(f'{self.name} applied Content-Encoding {encoding} to a byte range: '
                                     'its offsets no longer refer to the archive')
        first, last, total = self._content_range(headers.get('Content-Range'), start, stop)
        length = last - first + 1
        declared = headers.get('Content-Length')
        if declared is not None and declared.strip() != str(length):
            raise RangeMismatchError(f'Content-Length {declared.strip()} contradicts the {length:,} bytes '
                                     f'of Content-Range {first}-{last}')
        self._remember(total, headers.get('ETag'), headers.get('Last-Modified'))
        self._url = final
        body = bytearray()
        while len(body) < length:
            piece = reply.read(min(CHUNK, length - len(body)))
            if not piece:
                break
            body += piece
            self._fetched += len(piece)
        if len(body) < length:
            raise _Transient(f'short body: {len(body):,} of the {length:,} bytes the range promised', short=True)
        if reply.read(1):
            raise RangeMismatchError(f'{self.name} sent more than the {length:,} bytes of its range')
        return bytes(body)

    def _content_range(self, value, start, stop):
        match = CONTENT_RANGE.fullmatch((value or '').strip())
        if not match or match[3] == '*':
            raise RangeMismatchError(f'answer to bytes={start}-{stop - 1} has no usable Content-Range ({value!r}): '
                                     'the total length is unknown')
        first, last, total = int(match[1]), int(match[2]), int(match[3])
        expected = min(stop - 1, total - 1)
        if (first, last) != (start, expected):
            kind = 'range' if first != start else 'length'
            raise RangeMismatchError(f'server answered a different {kind}: asked for bytes {start}-{expected}, '
                                     f'got bytes {first}-{last}')
        return first, last, total

    def _remember(self, total, etag, modified):
        seen = (total, etag, modified)
        if self._identity is None:
            self._identity, self._size = seen, total
            return
        for label, before, now in zip(('length', 'ETag', 'Last-Modified'), self._identity, seen):
            if before != now:
                raise ObjectChangedError(f'the object changed between requests: {label} was {before!r}, '
                                         f'now {now!r}')

    def _status_failure(self, status):
        if status in TRANSIENT_STATUS or status >= 500:
            return _Transient(f'HTTP {status}')
        if status == 416:
            if self._size is None:
                return NotAZipError(f'{self.name} is empty: HTTP 416 for the first range')
            return ObjectChangedError(f'HTTP 416 for a range inside the recorded length of {self._size:,} '
                                      'bytes: the object has changed')
        hint = ''
        if status in (401, 403, 404, 410):
            hint = ('; the redirect target stopped accepting requests (a signed URL expires), run again '
                    'to get a fresh one' if self._url != self.origin else '; the server rejected the request')
        return FetchFailedError(f'{self.name} answered HTTP {status}{hint}')


Member = collections.namedtuple('Member', 'name compressed_size offset')


def open_zip(source):
    """zipfile.ZipFile(source), with an unreadable archive reported as NotAZipError."""
    try:
        return zipfile.ZipFile(source)
    except zipfile.BadZipFile as error:
        raise NotAZipError(f'not a readable zip archive: {error}') from error


def describe(source):
    """The member table (name, compressed size, local header offset) from the central directory alone.

    source is a RangeFile or an open ZipFile. No member data is read.
    """
    archive = source if isinstance(source, zipfile.ZipFile) else open_zip(source)
    try:
        return [Member(info.filename, info.compress_size, info.header_offset) for info in archive.infolist()]
    finally:
        if archive is not source:
            archive.close()


def member_span(info):
    """The byte span [start, stop) zipfile reads to extract one member.

    The local header's extra field can differ in length from the central
    directory's (zip64 and timestamp fields do), so the stop is an estimate. An
    extra field is at most 64 KiB, so with blocks of 64 KiB or more the real read
    reaches at most one block beyond, or one short of, the estimate.
    """
    start = info.header_offset
    return start, start + LOCAL_HEADER + len(info.orig_filename.encode('utf-8')) + len(info.extra) + \
        info.compress_size


def main(argv=None):
    ap = argparse.ArgumentParser(description='List a remote zip (an IPSW) from its central directory only.')
    ap.add_argument('url')
    ap.add_argument('--max-fetch-mib', type=int, default=DEFAULT_MAX_FETCH >> 20,
                    help='refuse to fetch more than this many MiB in total (default %(default)s)')
    args = ap.parse_args(argv)
    if args.max_fetch_mib < 1:
        ap.error('--max-fetch-mib must be at least 1')
    try:
        with RangeFile(args.url, max_fetch=args.max_fetch_mib << 20) as remote:
            table = describe(remote)
            for member in table:
                print(f'{member.compressed_size:>16,}  {member.offset:>16,}  {member.name}')
            print(f'{len(table)} members; {remote.summary()}')
    except FetchCapExceededError as error:
        print(f'refused: {error}', file=sys.stderr)
        return 2
    except RemoteZipError as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
