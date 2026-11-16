"""Build/mirror all declared images, then emit the standard immutable release."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployctl.contract import read_yaml
from deployctl.multiservice_build import ImageTools, deliver_images
from deployctl.release import build_release


def main():
    try:
        root=Path.cwd(); config=read_yaml(os.environ['DEPLOYMENT_FILE'])
        version=os.environ['RELEASE_VERSION']; repository=os.environ['IMAGE_REPOSITORY']
        authfile=Path(os.environ.get('DOCKER_CONFIG',str(Path.home()/'.docker')))/'config.json'
        tools=ImageTools(root,authfile)
        images=deliver_images(config,root,repository,version,tools)
        commit=subprocess.run(['git','rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
        build_release(config,images,version,root/'dist',commit=commit)
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as output:
            print('images='+json.dumps(images,sort_keys=True,separators=(',',':')),file=output)
        return 0
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
