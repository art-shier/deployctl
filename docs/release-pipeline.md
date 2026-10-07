# 构建与版本发布

## 检查

ci.yml在分支推送/PR时调用verify.yml。verify.yml也被平台发布流程复用，并检出传入的准确commit/tag。

- Linux Python3.10/3.12、Windows Python3.12运行完整unittest。
- 校验安装脚本是否由已测试的标准库Python源生成。
- 构建内置依赖CLI及skill资源，再验证分发CLI。
- Linux实际安装CLI、执行ctl/deployctl、重复安装、拒绝坏校验和，并运行init/validate。
- 独立Linux Docker集成任务验证服务安装、升级、失败恢复、回滚、日志和重启。

## 发布

更新deployctl/__init__.py、pyproject.toml、模板与skill版本后，推送对应v标签，例如v1.2.0。也可从Actions手动触发Release deployctl，version填写已经存在的标签。

平台发布流程先完成全部检查，再构建wheel和发布资产；标签、Python包版本、CLI版本不一致时停止。发布包包括：

```text
deployctl.pyz + .sha256
install.sh + .sha256
team-deploy-skill-v<version>.zip + .sha256
team_deployctl-<version>-py3-none-any.whl + .sha256
SHA256SUMS
```

构建产物同时作为Actions artifact保留14天。先创建草稿Release，在Linux Runner通过草稿ID读取真实私有资产，验证两个命令的版本，并测试GitHub API获取的一键安装脚本入口。全部通过后才发布正式Release，再检查正式标签下载路径。失败的草稿保留用于诊断，不会成为最新版。Release不覆盖已有版本；失败后排查草稿或发布新版本，不移动已有标签。

## 私有平台接入

业务项目执行init时添加--private-platform，生成的工作流会显式映射项目secret `PLATFORM_READ_TOKEN`。该Token需要读取art-shier/deployctl代码的权限；业务仓库的GITHUB_TOKEN通常不能checkout其他私有仓库。

私有reusable workflow还需平台仓库Settings → Actions → General → Access允许目标团队范围的调用。保持仓库私有；这项访问设置按团队实际范围配置，Token不能代替reusable workflow的可见性要求。
