# 从管理端安装节点

在“部署节点 → SSH 安装节点”输入主机地址、SSH 端口和用户名。先探测 SSH 主机密钥，并与主机管理员提供的 SHA256 指纹核对，然后输入密码提交安装。中心必须可以连接节点 SSH，节点必须可以访问中心 HTTPS 地址和发行版/Docker 软件源。

账号必须为 root 或拥有无需终端交互的 sudo 权限；sudo 密码留空时使用登录密码。密码只保留在中心本次安装进程的内存，既不保存到数据库，也不写入命令行、审计或浏览器存储。关闭窗口不会终止安装。任务记录保留目标、主机指纹、阶段和结果；中心重启不会自动重放密码或安装，最迟在任务期限到达后显示“中断”。

节点侧安装器执行环境检查。最低要求为 Linux/systemd、Python 3.10、Docker Engine 20.10、Docker Compose 2.30。Ubuntu 22.04/24.04/26.04/26.10 和 Debian 12/13 可在缺少 Docker 时使用 Docker 官方签名 apt 仓库安装。其他系统需手动准备依赖。已有 Docker 合格则复用；低版本、服务异常、冲突容器运行时和已有 Docker 软件源均交由人工处理，不自动升级、卸载或重启现有 Docker。

第一版仅自动引导新主机：遇到已有 ctl 命令、节点身份或节点服务会停止，避免覆盖现有安装。已有 ctl 可使用“接入节点”的手动流程。安装成功意味着此次注册的节点已经向中心发送心跳；仍需在节点列表分配项目和环境授权。

最多同时执行四个任务，同一目标地址和端口只允许一个活动任务；节点上另有全局文件锁防止别名或多个中心同时安装。引导命令最多运行 20 分钟，后台任务最多 23 分钟，记录 25 分钟到期。失败后先核对主机状态再重试；已安装的系统软件包不会自动回滚。

页面只显示固定阶段及错误说明，不透传任意 SSH 输出。节点详细安装日志为 root 私有的 `/var/log/ctl-node-install.*.log`；Agent 运行问题可查看 `journalctl -u ctl-agent.service`。已有 Docker 异常时先检查 `docker info` 和 `docker compose version --short`。

安装器源文件是 `control-server/internal/nodeinstall/bootstrap.sh`。发布构建同步到 `skills/team-deploy/assets/node-bootstrap.sh`，公开资源地址为 `/agent/assets/node-bootstrap.sh`。中心从当前受校验的 CLI 资源构造安装包，不执行浏览器上传的脚本。手工调用安装器参数为 `bash node-bootstrap.sh HTTPS_CENTER CLI_PYZ TOKEN_FILE`，CLI 同目录必须包含对应 `.sha256`，注册文件必须 root 私有。
