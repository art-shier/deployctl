# 一键安装deployctl / ctl

当前平台仓库 `art-shier/deployctl` 为公开仓库，安装器和已发布资产支持匿名下载。CLI是内置依赖的Python zipapp，安装要求Linux/Bash、Python>=3.10，不需要服务器pip安装。安装CLI不会安装Docker、修改防火墙或启动业务服务。

## 公开仓库：安装CLI1.8.0

```bash
set -o pipefail
curl --fail --silent --show-error https://raw.githubusercontent.com/art-shier/deployctl/v1.8.0/install.sh \
  | sudo bash -s -- --version v1.8.0
ctl --version
```

系统安装到/usr/local/bin；已有安装器管理的命令使用sudo ctl self-update --version v1.8.0。用户安装沿用原目录和身份。安装后可执行ctl server install --release <服务端包URL>；这与ctl login --server <管理API地址>的Token登录分开。

## 已登录GitHub CLI：一键安装最新版

```bash
set -o pipefail
gh api --hostname github.com 'repos/art-shier/deployctl/contents/install.sh?ref=main' \
  -H 'Accept: application/vnd.github.raw+json' | bash -s -- --user
```

安装器自动使用 `gh auth token --hostname github.com`，从最新已发布Release取得CLI和外部校验文件。安装到 `~/.local/bin`，生成 `deployctl` 和 `ctl` 两个同功能入口。若当前shell尚未包含该目录：

```bash
export PATH="$HOME/.local/bin:$PATH"
ctl --version
ctl --help
```

首次使用GitHub CLI先执行 `gh auth login --hostname github.com`。安装器不读取或更改你的shell启动文件。

## 固定版本（推荐生产初始化）

```bash
set -o pipefail
gh api --hostname github.com 'repos/art-shier/deployctl/contents/install.sh?ref=v1.5.0' \
  -H 'Accept: application/vnd.github.raw+json' | bash -s -- --user --version v1.5.0
```

版本标签、CLI内报告版本和外部SHA256必须一致。也可添加 `--sha256 <独立取得的摘要>`；摘要来自同一Release能检查损坏，不代替独立发布者认证。

## 私有fork：只有curl和只读Token

将有本仓库Contents读取权限的Token通过受控渠道配置为导出的GH_TOKEN，不把实际值写到URL或仓库：

```bash
set -o pipefail
curl -fsSL --proto '=https' --tlsv1.2 \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H 'Accept: application/vnd.github.raw+json' \
  'https://api.github.com/repos/art-shier/deployctl/contents/install.sh?ref=v1.5.0' \
  | bash -s -- --user --version v1.5.0
```

Token仅用于GitHub API；下载资产重定向到外部存储时移除Authorization。以后若仓库可公开访问，可省略Token，公开的art-shier/deployctl无需GitHub Token。

## 本地、系统目录和其他选项

```bash
# 预先下载CLI及相邻deployctl.pyz.sha256，安装本地文件
bash install.sh ./deployctl.pyz --user

# 有目录写权限时安装到指定目录；root默认/usr/local/bin
bash install.sh --repo art-shier/deployctl --version v1.5.0 --install-dir /usr/local/bin

# 不需要ctl短命令时
bash install.sh ./deployctl.pyz --user --no-alias

bash install.sh --help
```

安装器检查Python版本、下载大小、校验和、实际CLI版本，全部通过后才替换命令文件。记录 `.deployctl-install.json` 以支持原目录再次执行命令升级工具；这是工具安装元数据，和业务服务的state.json不同。

已有同名非本安装器管理的程序会阻止安装；可以选择目录、关闭alias，或在确认替换目标后显式传--force。下载/校验失败保留原安装，不自动提升权限。Docker/Compose只在执行服务部署时需要，要求Compose>=2.30。

## 找不到ctl与工具自身升级

先检查`command -v ctl`和`command -v deployctl`。仅ctl缺失时可使用deployctl；若`~/.local/bin/ctl --version`可以运行，则将该目录加入PATH，无需重新安装。自定义目录同样先使用其绝对路径；不要误用默认目录留出另一份旧CLI。

CLI>=1.3.0、且由本安装器管理时：

```bash
# 更新到原平台的最新已发布Release
ctl self-update
ctl --version

# 固定工具版本；显式指定旧版本也可用于工具版本回退
ctl self-update --version v1.5.0
```

默认同时更新受管理的deployctl和ctl，保留原目录、自定义命令名及无别名安装选择。可额外指定`--sha256 <独立获得的CLI摘要>`，不需要Docker，也不操作业务服务。`ctl upgrade <application> --env ... --release ...`才是业务服务升级。

v1.2.0及更早版本、源码/直接运行pyz或pip入口没有该管理能力时，重新执行安装器。现有自定义安装目录必须显式传入，例如`bash install.sh --version latest --install-dir /srv/tools/bin`；原来不使用ctl别名时继续传`--no-alias`。不受管理的同名程序会被保留，不能默认用--force替换。

Agent的完整检测、在线/离线安装、旧版迁移与验证步骤见 [CLI安装与升级](../skills/team-deploy/references/cli-lifecycle.md)。

可以通过 `DEPLOYCTL_PYTHON=/path/to/python3.12 bash install.sh ...` 选择解释器，安装后的命令固定使用这个绝对路径。若后来移除该Python，需用新的解释器重新安装工具。`--release-id <数字ID>` 仅供发布CI验证草稿资产，须同时给出精确 `--version`；日常安装无需此参数。
