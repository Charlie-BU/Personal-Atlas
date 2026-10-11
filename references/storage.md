# 固定入口、Git 同步与恢复

运行要求：Python 3.9+、Git，macOS/Linux（使用 POSIX flock）；Windows 原生锁适配尚未实现。所有资料在用户选择的专用 Git 仓库中，不能把一个无关现有项目当作信息库。

## 初始化

本地先用：

```text
python3 <skill>/scripts/atlas.py init --path <新建的专用目录>
```

接入指定 Git 远端（可以为空仓库，也可以是已有 Atlas 仓库）：

```text
python3 <skill>/scripts/atlas.py init --remote <用户指定的URL> --branch main --sync after_write
python3 <skill>/scripts/atlas.py sync
```

远端非空但不是 Atlas 格式时拒绝；分支存在则克隆、验证和绑定。init 不推送，首次 sync 才上传。用户明确授权绑定某个仓库并自动同步时后续沿用；不自行选择已有其他业务仓库。

默认本地检出 `<skill>/.local/store`，绑定 `<skill>/.local/config.json`。--path 可换位置。--config 可用于独立资料库/隔离测试；相对 checkout 相对于 config 所在目录。配置只存 URL、分支、vault_id、checkout、sync，不存密码/令牌。

```json
{
  "schema_version":1,
  "vault_id":"vault_...",
  "checkout":"/absolute/path/to/personal-vault",
  "remote":"git@host:owner/personal-vault.git",
  "branch":"main",
  "sync":"after_write"
}
```

支持 manual/after_write。已绑定的本地库后来增加远端时，先验证目标确为用户指定的空仓库或同一个 vault，添加 origin 并更新 config.remote/sync，再 sync；不得直接把不同 vault 历史合并。

初始运行无需安装依赖包。脚本不会自己创建托管平台上的远端仓库；目标仓库地址由用户配置。

## 查询新鲜度

status 只检查本地，不查询网络，返回 remote_checked=false。用户要最新远端数据时先 sync；已有 after_write 是写后同步，不是查询前自动联网。离线或同步失败时说明本地 snapshot；不能把本地检查说成远端确认。

sync 有拉取与推送效果。如果用户只授权本地查询，不额外调用 sync 上传待推送数据。沿用已授权的同步策略即可。

## 同步协议

1. 核验 vault_id、origin URL、分支、干净工作区与图。
2. fetch；远端领先则先验证其权威数据和派生图，再快进。
3. 双方分叉时，以共同祖先逐权威文件三方比较。不同文件的修改可合并；同一文件双方改动则停在冲突说明，不用文本最后写入获胜。
4. 合并正文后重建图、检查删除与引用冲突，形成双亲提交。
5. 普通 push，无强制覆盖；远端此时又更新则拒绝并等待下次协调。
6. ls-remote 核验远端分支确实等于本地提交后返回 remote_confirmed。网络结果不确定则保留本地，重试核验。

不同记录表达了同一语义事实的矛盾不一定形成 Git 文件冲突。agent 写入前查重，合并后对相关实体复核，程序不宣称自动消除语义冲突。

发生同文件冲突后，保留两边版本和祖先，在隔离位置比较并由 agent/用户明确结论。当前 CLI 自动处理的范围仅为不相交文件变更；不要反复 sync 期待冲突自行消失，也不要强推选边。

## 外部编辑与恢复

```text
python3 <skill>/scripts/atlas.py verify
python3 <skill>/scripts/atlas.py reindex
python3 <skill>/scripts/atlas.py recover
```

手工修改后先 inspect，reindex 解析权威文件、修复图/manifest并提交；不覆盖正文。发现非法日期、缺来源或悬空边则先修正文。同一操作里人工删除了来源时，reindex 也会移除其原文并记录删除标记。已经暂存的人工修改会阻止 reindex，先检查并解除暂存。

recover 用 .git/atlas-transaction 日志判断：HEAD 未发布则恢复之前的文件/索引；HEAD 已到目标提交则认可已完成提交。不能删除日志绕过检查；HEAD 被外部改动时要求人工检查。

进程中断恢复经过测试；不承诺硬盘故障、文件系统损坏或电源异常的端到端持久性。原生编辑器不会自动遵守脚本锁，应避免同时写。

## 打包与范围

只打包 SKILL.md、agents/、references/、scripts/ 和需要的静态资源；明确排除 .local/、测试数据和缓存。skill 安装与数据仓库绑定是不同状态，未给远端时不宣称已同步。

v1 支持 Git（含 SSH/HTTPS/本地路径）及离线本地 Git；没有 SFTP/WebDAV/S3 后端、向量数据库、自动 LFS 或历史清除命令。JSON 图、Markdown 正文和原件独立保存，为后续扩展保留接口，而不虚报未实现功能。

普通删除只影响当前版本；彻底清除历史需要另行授权并协调 Git 历史与其他副本。Git 历史和第三方副本的机制边界参见 [GitHub 文档](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。
