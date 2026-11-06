"""Deterministic release index assembly and authenticated publisher-only history bootstrap."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

from .bootstrap import (MAX_INDEX_SIZE, STABLE_TAG, digest_text,
                        fetch, parse_release_index, utc_timestamp)


def encode_index(value):
    validated = parse_release_index(json.dumps(value).encode('utf-8'))
    return (json.dumps(validated, sort_keys=True, indent=2) + '\n').encode('utf-8')


def artifact_digest(path):
    if not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError('missing or oversized release artifact')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest_text(path.with_name(path.name + '.sha256').read_text(encoding='ascii')) != digest:
        raise ValueError('release artifact checksum mismatch')
    return digest


def build_index(history, generated_at, candidate_version=None, published_at=None, artifacts=None):
    result = parse_release_index(json.dumps(history).encode('utf-8'))
    result['generated_at'] = utc_timestamp(generated_at)
    if candidate_version is not None:
        if not isinstance(candidate_version, str) or not STABLE_TAG.fullmatch(candidate_version):
            raise ValueError('candidate version must be an exact stable tag')
        row = dict(version=candidate_version, published_at=utc_timestamp(published_at),
                   cli_sha256=artifact_digest(Path(artifacts) / 'deployctl.pyz'),
                   server_sha256=artifact_digest(Path(artifacts) / f'ctl-platform-{candidate_version}.tar.gz'))
        existing = next((item for item in result['releases'] if item['version'] == candidate_version), None)
        if existing is not None:
            if any(existing[key] != row[key] for key in ('cli_sha256', 'server_sha256')):
                raise ValueError('immutable release checksum conflict')
            result['releases'].remove(existing)
        result['releases'].append(row)
    return parse_release_index(json.dumps(result).encode('utf-8'))


def is_not_found(exc):
    while exc is not None:
        if isinstance(exc, HTTPError):
            return exc.code == 404
        exc = exc.__cause__
    return False


def published_history(repository, token, generated_at, exclude_version=None):
    """Read static history, bootstrapping only a missing index with authenticated CI metadata."""
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('repository must be owner/repository')
    url = f'https://github.com/{repository}/releases/latest/download/update-index.json'
    try:
        result = parse_release_index(fetch(Request(url, headers={'User-Agent': 'deployctl-publisher'}), MAX_INDEX_SIZE))
    except (RuntimeError, HTTPError) as exc:
        if not is_not_found(exc):
            raise
        if not token:
            raise ValueError('publisher history bootstrap requires GH_TOKEN') from exc
        result = bootstrap_history(repository, token, generated_at, exclude_version)
    result['releases'] = [item for item in result['releases'] if item['version'] != exclude_version]
    # An older-only image seed must never outrank a newer successfully cached catalog.
    # Derive its chronology from included publications, independent of image rebuild time.
    stamps = [item['published_at'] for item in result['releases']]
    newest = max(stamps, key=lambda value: datetime.fromisoformat(value[:-1] + '+00:00')) if stamps else '1970-01-01T00:00:00Z'
    return build_index(result, newest)


def bootstrap_history(repository, token, generated_at, exclude_version):
    headers = {'User-Agent': 'deployctl-publisher', 'Accept': 'application/vnd.github+json',
               'Authorization': 'Bearer ' + token, 'X-GitHub-Api-Version': '2022-11-28'}
    rows = []
    for page in range(1, 11):
        url = f'https://api.github.com/repos/{repository}/releases?per_page=100&page={page}'
        releases = json.loads(fetch(Request(url, headers=headers), 8 * 1024 * 1024))
        if not isinstance(releases, list) or len(releases) > 100:
            raise ValueError('invalid publisher release metadata')
        for release in releases:
            if not isinstance(release, dict):
                raise ValueError('invalid publisher release metadata')
            tag = release.get('tag_name')
            if (release.get('draft') is not False or release.get('prerelease') is not False
                    or not isinstance(tag, str) or not STABLE_TAG.fullmatch(tag) or tag == exclude_version):
                continue
            packages = ('deployctl.pyz', f'ctl-platform-{tag}.tar.gz')
            names = tuple(name + '.sha256' for name in packages)
            assets = release.get('assets')
            if not isinstance(assets, list) or any(not isinstance(item, dict) for item in assets):
                raise ValueError('invalid publisher assets')
            # Releases preceding the integrated platform may not provide both packages.
            if any(not any(item.get('name') == name for item in assets) for name in (*packages, *names)):
                continue
            digests = []
            for name in names:
                matches = [item for item in assets if item.get('name') == name]
                package = [item for item in assets if item.get('name') == name.removesuffix('.sha256')]
                if len(matches) != 1 or len(package) != 1:
                    raise ValueError('ambiguous publisher checksum asset')
                asset_url = matches[0].get('url', '')
                target = urlsplit(asset_url)
                prefix = f'/repos/{repository}/releases/assets/'
                if (target.scheme != 'https' or target.netloc != 'api.github.com' or target.query
                        or target.fragment or not target.path.startswith(prefix)
                        or not re.fullmatch(r'[1-9][0-9]*', target.path[len(prefix):])):
                    raise ValueError('unexpected publisher asset URL')
                raw = fetch(Request(asset_url, headers=dict(headers, Accept='application/octet-stream')), 4096)
                digests.append(digest_text(raw.decode('ascii')))
            rows.append(dict(version=tag, published_at=release.get('published_at'),
                             cli_sha256=digests[0], server_sha256=digests[1]))
        if len(releases) < 100:
            break
    else:
        raise ValueError('publisher release history exceeds bounded pagination')
    return parse_release_index(json.dumps(dict(schema_version=1, generated_at=generated_at, releases=rows)).encode('utf-8'))


def write_index(output, value, checksum=False):
    output = Path(output)
    raw = encode_index(value)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    if checksum:
        digest = hashlib.sha256(raw).hexdigest()
        output.with_name(output.name + '.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
        sums = output.parent / 'SHA256SUMS'
        lines = sums.read_text(encoding='ascii').splitlines() if sums.exists() else []
        lines = [line for line in lines if line.split()[-1:] != [output.name]]
        lines.append(f'{digest}  {output.name}')
        sums.write_text('\n'.join(sorted(lines, key=lambda line: line.split()[-1])) + '\n', encoding='ascii')
