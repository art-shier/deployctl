"""Build the server bundle using an actual Registry manifest/index digest."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployctl.server_bundle import build_bundle

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', default='dist')
    args = parser.parse_args()
    print(build_bundle(ROOT, args.output, args.image, args.version))
