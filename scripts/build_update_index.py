"""Build a validated static release catalog from published history and verified artifacts."""

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployctl.bootstrap import DEFAULT_REPOSITORY, MAX_INDEX_SIZE, parse_release_index
from deployctl.release_catalog import build_index, published_history, write_index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', type=Path, help='validated existing index or bundled seed')
    parser.add_argument('--refresh-history', action='store_true', help='publisher CI only, refresh published history before building image')
    parser.add_argument('--repo', default=DEFAULT_REPOSITORY)
    parser.add_argument('--exclude-version', help='exclude current candidate from pre-image published seed')
    parser.add_argument('--generated-at', required=True, help='explicit UTC RFC3339 timestamp for reproducible output')
    parser.add_argument('--candidate-version')
    parser.add_argument('--published-at', help='candidate publication timestamp, required with candidate')
    parser.add_argument('--artifacts', type=Path, default=ROOT / 'dist')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checksum', action='store_true', help='write SHA256 sidecar and refresh SHA256SUMS')
    args = parser.parse_args()
    if args.refresh_history == bool(args.history):
        parser.error('choose --history or --refresh-history')
    if args.candidate_version and args.refresh_history:
        parser.error('candidate assembly requires pre-image --history')
    if args.candidate_version and not args.published_at:
        parser.error('--candidate-version requires --published-at')
    if args.refresh_history:
        history = published_history(args.repo, os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'),
                                    args.generated_at, args.exclude_version)
    else:
        with args.history.open('rb') as stream:
            history = parse_release_index(stream.read(MAX_INDEX_SIZE + 1))
    generated_at = history['generated_at'] if args.refresh_history else args.generated_at
    value = build_index(history, generated_at, args.candidate_version, args.published_at, args.artifacts)
    write_index(args.output, value, checksum=args.checksum)
    print(f'Wrote {len(value["releases"])} validated release entries to {args.output}')


if __name__ == '__main__':
    main()
