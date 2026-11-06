"""Register a published, checksum-verified release with a configured center."""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def register(center, token, payload, opener=None, sleep=time.sleep):
    parsed = urllib.parse.urlsplit(center)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Center must be an HTTPS URL without credentials, query or fragment')
    if not token or any(c.isspace() for c in token):
        raise ValueError('Missing or invalid registration credential')
    opener = opener or urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(center.rstrip('/') + '/api/v1/system-updates/registrations',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
    for attempt in range(5):
        try:
            with opener.open(request, timeout=30) as response:
                if response.status not in (200, 201):
                    raise RuntimeError('Unexpected registration response')
                return
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429) and error.code < 500:
                raise RuntimeError(f'Registration rejected (HTTP {error.code})') from None
        except (urllib.error.URLError, TimeoutError):
            pass
        if attempt == 4:
            raise RuntimeError('Registration unavailable after 5 attempts; rerun Register released version')
        sleep(2 ** attempt)


def metadata(repo, version):
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not re.fullmatch(r'v\d+\.\d+\.\d+', version):
        raise ValueError('Invalid repository or stable version')
    def fetch(url, limit):
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ctl-version-registration'}), timeout=30) as response:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Release metadata too large')
        return raw
    release = json.loads(fetch(f'https://api.github.com/repos/{repo}/releases/tags/{version}', 2 * 1024 * 1024))
    if release.get('draft') or release.get('prerelease') or release.get('tag_name') != version or not release.get('published_at'):
        raise ValueError('Expected a published stable release')
    base = f'https://github.com/{repo}/releases/download/{version}/'
    raw = fetch(base + 'update-index.json', 2 * 1024 * 1024)
    checksum = fetch(base + 'update-index.json.sha256', 4096).decode('ascii').split()[0]
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError('Release catalog checksum mismatch')
    matches = [r for r in json.loads(raw)['releases'] if r['version'] == version]
    if len(matches) != 1:
        raise ValueError('Expected one matching release')
    item = matches[0]
    # Use catalog time, including for bundled seed imports and retry idempotency.
    return {key: item[key] for key in ('version', 'published_at', 'cli_sha256', 'server_sha256')} | {
        'cli_url': base + 'deployctl.pyz', 'server_url': base + f'ctl-platform-{version}.tar.gz'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    payload = metadata(args.repo, args.version)
    register(os.environ['CTL_CENTER_URL'], os.environ['CTL_VERSION_REGISTRATION_TOKEN'], payload)
    print('Registered ' + args.version + ' with center')


if __name__ == '__main__':
    main()
