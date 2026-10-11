# GitHub 连接器：无需 Python 的远端查询

本私有版本绑定 `Charlie-BU/Charlie-Archive`、`main`、vault ID `vault_1f6ef6d8ddb3430e8dc5dd263c052065`。使用当前已授权的 GitHub 插件；无需重新索取 PAT。连接器缺失时先发现工具，不自行浏览登录或把令牌发给其他服务。

## 连接检查

1. GitHub fetch 请求 `https://api.github.com/repos/Charlie-BU/Charlie-Archive/git/ref/heads/main`，取 object.sha。
2. fetch_file 参数 repository_full_name=`Charlie-BU/Charlie-Archive`、ref=该 commit，分别读取 `vault.json` 和 `graph/root.json`。
3. 解析工具 structuredContent.content（可能是 JSON 字符串；部分宿主在 structuredContent.result 或嵌套 structuredContent 返回）。检查 vault schema_version=1、无 phase=initializing；graph schema_version=1；vault.id、graph.vault_id 与绑定一致。任何工具 isError 或错误内容均算失败，不把“Action completed”当成功。
4. 报告实际 snapshot、remote_checked=true、verification=entrypoint_only。不运行全库 verify。固定 commit 的 Contents 读取为入口可读检查，不冒称 Blob 哈希或全库校验。
5. 全轮按该 commit 读取。最终需要最新状态时重新取 HEAD；变化后复核受影响记录，不能拼接不同快照。

## 查询映射

- graph/list/search：按 `graph/root.json` 的 node_pages 逐页 fetch_file。本 schema 的 node_pages 是节点 ID 数组；候选 ID 对应 `graph/nodes/<id 中的冒号替换为双短横线>.json`。节点文件包含摘要、type/status/domain、记录 path 及 adjacency；先读相关领域节点和其 incoming 邻接，或从 ID 与别名选择候选，再读节点指向的 records 路径。domain 节点通常没有正文 path，不能据此判断无记录。字段以实际返回为准。
- 若节点页缺少路径，用 fetch 请求 `https://api.github.com/repos/Charlie-BU/Charlie-Archive/git/commits/<commit>` 得 tree.sha，再请求 `.../git/trees/<tree>?recursive=1` 定位 records 与 graph 路径。truncated=true 时按子树继续，不能当完整目录。
- read/source：fetch_file 读取实际记录或来源路径，ref 固定 commit。必要时使用 start_line/end_line 分页并确认剩余内容；正文的否定、条件、例外及证据不能仅靠摘要回答。
- neighbors：读取 graph 下实际对应邻接页，限定领域、关系和方向；无需把所有个人资料加载进上下文。
- timeline：按 root 的 timeline_pages 读相关页，按现实时间解释；未知时间不补造。
- 模糊问题多别名检索；某次关键词无结果不证明没有。完整查询需遍历相关页至末页，说明未处理来源范围。
- 引用固定 commit 的 GitHub 文件 URL 或 record ID；不要在最终回复暴露无关个人信息。
- connector 模式不使用脚本的 search/list 命令，读图再读正文即可。查到资料后直接回答，不把“缺本地 config”当额外门槛。

## 写入边界

GitHub 连接器授权与本地 PAT 授权独立。连接器可以上传 tree/commit/ref，但 Atlas 资料写入仍需受信任程序完整验证并生成正文、来源、图、清单及去重操作记录。使用可执行 Python 或提供等价校验的 Atlas 服务，沿 mutations.md 的 apply 规则同提交发布并确认 HEAD。不得只用 update_file 改 records 或手编 graph，也不得因查询通道可用就谎报自动同步已完成。

仅归档静态 Skill 文件时可使用 GitHub Git Database 工具，明确范围，保留资料文件；若归档影响 canonical revision，按现有 derive 生成相应元数据，不做资料 CRUD。无写入执行环境时仍完成可用的查询，并准确报告写入阻塞；不要把局部写入限制扩展成所有查询不可用。
