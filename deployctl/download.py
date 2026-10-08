"""Bounded HTTPS acquisition and private GitHub release authentication."""

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .release import MAX_PACKAGE


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or any(c in url for c in ('\r', '\n'))):
        raise ValueError('download URL must use HTTPS without credentials or fragment')
    return parsed


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = validate_url(newurl)
        old = validate_url(req.full_url)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and (new.hostname, new.port) != (old.hostname, old.port):
            redirected.remove_header('Authorization')
            redirected.remove_header('Cookie')
        return redirected


def read_response(response, limit, progress=None):
    header = response.headers.get('Content-Length')
    try:
        total = int(header) if header is not None else None
    except (ValueError, TypeError):
        total = None
    if total is not None and total < 0:
        total = None
    if total is not None and total > limit:
        raise ValueError('download exceeds size limit')
    if progress:
        progress.downloaded(0, total)
    content = bytearray()
    read = getattr(response, 'read1', response.read)
    while True:
        chunk = read(min(65536, limit + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > limit:
            raise ValueError('download exceeds size limit')
        if total is not None and len(content) > total:
            raise RuntimeError('download length mismatch')
        if progress:
            progress.downloaded(len(content), total)
    if total is not None and len(content) != total:
        raise RuntimeError('download incomplete')
    if progress and total is None:
        progress.downloaded(len(content), total, force=True)
    return bytes(content)


def fetch(request, limit, progress=None):
    try:
        with build_opener(SafeRedirect()).open(request, timeout=60) as response:
            return read_response(response, limit, progress)
    except HTTPError as exc:
        raise RuntimeError(f'download HTTP {exc.code}; check asset availability and credentials') from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError('download failed; check network and HTTPS certificate') from exc


def resolve_asset(url):
    parsed = validate_url(url)
    headers = {'User-Agent': 'team-deployctl/1.0.0'}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    parts = parsed.path.strip('/').split('/')
    if (token and parsed.hostname == 'github.com' and len(parts) == 6
            and parts[2:4] == ['releases', 'download']):
        owner, repo, _, _, tag, filename = map(unquote, parts)
        api = f'https://api.github.com/repos/{quote(owner, safe="")}/{quote(repo, safe="")}/releases/tags/{quote(tag, safe="")}'
        auth = dict(headers, Authorization=f'Bearer {token}', Accept='application/vnd.github+json',
                    **{'X-GitHub-Api-Version': '2022-11-28'})
        release = json.loads(fetch(Request(api, headers=auth), 1024 * 1024))
        matches = [asset for asset in release.get('assets', []) if asset.get('name') == filename]
        if len(matches) != 1:
            raise ValueError('GitHub Release asset not found or ambiguous')
        asset_url = matches[0]['url']
        target = validate_url(asset_url)
        prefix = f'/repos/{owner}/{repo}/releases/assets/'
        if target.hostname != 'api.github.com' or not target.path.startswith(prefix):
            raise ValueError('unexpected GitHub asset API URL')
        auth['Accept'] = 'application/octet-stream'
        return Request(asset_url, headers=auth)
    return Request(url, headers=headers)


def checksum(text):
    fields = text.strip().split()
    if not fields or not re.fullmatch(r'[a-fA-F0-9]{64}', fields[0]):
        raise ValueError('invalid SHA256 checksum')
    return fields[0].lower()


def acquire_release(source, cache, expected_sha256=None, progress=None):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    remote = '://' in source
    if remote:
        parsed = validate_url(source)
        if expected_sha256 is None:
            sidecar = urlunsplit(parsed._replace(path=parsed.path + '.sha256'))
            expected_sha256 = checksum(fetch(resolve_asset(sidecar), 4096).decode('utf-8'))
        request = resolve_asset(source)
        content = fetch(request, MAX_PACKAGE, progress=progress) if progress is not None else fetch(request, MAX_PACKAGE)
    else:
        path = Path(source).expanduser().resolve()
        if path.stat().st_size > MAX_PACKAGE:
            raise ValueError('release package is too large')
        if expected_sha256 is None:
            sidecar = path.with_name(path.name + '.sha256')
            if not sidecar.exists():
                raise ValueError('checksum sidecar is missing; provide .sha256 or --sha256')
            expected_sha256 = checksum(sidecar.read_text(encoding='utf-8'))
        content = path.read_bytes()
    expected = checksum(expected_sha256)
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ValueError('release checksum mismatch')
    result = cache / f'{actual}.tar.gz'
    result.write_bytes(content)
    return result
