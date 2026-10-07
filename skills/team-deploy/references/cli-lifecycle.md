# ctl / deployctl：定位、安装与工具升级

`ctl`与`deployctl`功能相同。工具自身更新使用`self-update`；`upgrade <application>`、`rollback <application>`用于业务服务。准备或更新CLI本身不执行服务部署。

## 命令缺失与PATH

在真正执行任务的环境检查命令及版本：

Linux使用`command -v`；Windows本地使用`Get-Command ctl,deployctl -ErrorAction SilentlyContinue`，命令缺失时优先用随附Python CLI完成本地接入。

```bash
command -v ctl || command -v deployctl || true
```

如果命令存在，执行对应的`--version`和必要的`--help`。如果只有deployctl，可直接使用它；不能将ctl别名缺失当成整个工具未安装。

如果命令不存在，检查用户指定的安装目录，以及`~/.local/bin`、`/usr/local/bin`。发现可执行文件后先用绝对路径运行`--version`，再将实际目录加入当前shell的PATH，例如：

```bash
"$HOME/.local/bin/ctl" --version
export PATH="$HOME/.local/bin:$PATH"
hash -r
ctl --version
```

自定义目录用其真实路径替换，不盲目重新安装或修改shell启动文件。命令存在但启动失败时，检查Python版本及安装时绑定的解释器是否仍存在；用有效Python重新执行安装器可修复解释器路径。

CLI安装要求Linux/Bash、Python>=3.10。工具自身安装与更新不需要Docker；业务服务运行另需Docker Engine、Compose>=2.30。本地Windows接入可以用`python "$SKILL_DIR/assets/deployctl.pyz" init/validate/package`，不用Linux安装器在Windows创建系统命令。

## 在线首次安装与旧版更新

真实平台`art-shier/deployctl`为私有仓库。使用已有的GH_TOKEN/GITHUB_TOKEN或已登录github.com且有平台读取权限的gh。需要登录时说明`gh auth login --hostname github.com`；不输出Token，不将其放进URL。

用户已要求准备/更新工具时，可以在其指定目录完成安装和验证，不额外安装系统依赖或自动提升权限。普通用户安装到用户目录：

```bash
bash "$SKILL_DIR/assets/install.sh" --repo art-shier/deployctl --version latest --user
export PATH="$HOME/.local/bin:$PATH"
ctl --version
deployctl --version
```

`latest`由真实GitHub Release API解析已发布版本，不用随附CLI版本号推断远程最新版。没有本地skill安装器但有gh时，也可通过平台入口：

```bash
set -o pipefail
gh api --hostname github.com 'repos/art-shier/deployctl/contents/install.sh?ref=main' \
  -H 'Accept: application/vnd.github.raw+json' | bash -s -- --user
```

生产需固定工具版本时，把`latest`改成用户选定的实际Release标签，如`v1.3.0`。有对应标签后也可固定Contents API的`ref`，并传同一`--version`。

旧版没有self-update，或用户要升级安装在自定义目录的命令时，重新执行安装器并保留原目录，例如：

```bash
bash "$SKILL_DIR/assets/install.sh" --repo art-shier/deployctl \
  --version latest --install-dir /srv/tools/bin
/srv/tools/bin/ctl --version
/srv/tools/bin/deployctl --version
```

先核对实际原目录和写权限；不要用`--user`把原来位于其他目录的命令留成旧版。安装器根据相邻`.deployctl-install.json`和文件校验值识别受管理命令。遇到不受管理、符号链接或同名其他程序时，核对原安装方式或选择新目录，不默认加`--force`。pip/wheel或源码安装沿用其原来源，不能假定属于此安装器管理。

安装器下载并检查SHA256、CLI版本与Release标签，再原子替换命令。重新运行安装器时，如果原来不用ctl别名，需要继续传`--no-alias`；self-update则直接沿用安装记录。`DEPLOYCTL_PYTHON=/实际路径/python3.12 bash ...`可指定Python，安装后的命令绑定该绝对路径。

## CLI自身升级（>=1.3.0）

先确认`--version`与`self-update --help`。安装器管理的CLI可以执行：

```bash
ctl self-update
ctl --version
deployctl --version
```

默认从安装记录中的原平台获取最新已发布Release，并在原目录更新受管理的命令。自定义目录、原命令名和是否安装ctl别名沿用安装记录。需要指定版本时：

```bash
ctl self-update --version v1.3.0
```

可用`--sha256 <独立取得的CLI摘要>`额外指定摘要。私有仓库沿用前面的读取凭证，目录需可写。当前直接运行的skill pyz、源码或pip入口没有安装记录时，self-update会拒绝；使用上面的安装器准备受管理的命令，不重写skill目录或Python环境。

执行失败就是工具更新失败；根据具体错误处理网络、凭证、写权限或安装记录，不改用业务`upgrade`。下载和校验失败保留旧命令；文件写入失败恢复已替换的命令。更新后用实际入口再次检查版本，不能只凭安装器打印的信息报告成功。

## 离线安装或指定版本回退

把`assets/install.sh`、`assets/deployctl.pyz`和相邻`.sha256`一起放到目标Linux主机，以实际路径执行：

```bash
bash "$SKILL_DIR/assets/install.sh" "$SKILL_DIR/assets/deployctl.pyz" --user
"$HOME/.local/bin/ctl" --version
```

自定义目录用`--install-dir /实际/原目录`替换`--user`。离线安装的是所携带产物的真实版本，不承诺为远程最新版；升级或回退到其他工具版本时先在联网环境取得相应Release的CLI及校验文件。工具版本回退也通过安装器或self-update的显式版本进行，不能调用业务rollback代替。
