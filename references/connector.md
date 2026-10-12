# 使用 agent 已有 Git 能力

本 Skill 不含远端客户端。发现宿主提供的连接器、插件或已授权 Git/CLI，选择与外部配置的主机、仓库和身份匹配的能力。例如 GitHub 可使用 ChatGPT GitHub plugin；GitLab、Gitee、自建服务使用其适配工具。未直接暴露时使用宿主工具发现功能，不把“未看到”直接视为不存在。能力仍缺失时提示用户连接/启用；不自写 HTTP 客户端作为兜底，也不要求仅为读取再提供 PAT。

## 连接与快照

1. 解析外部配置，包括当前上下文与上层 plugin 说明；必要信息齐全即可继续，本机 config.json 不可访问不阻塞。仅所有可用来源仍无仓库地址时提示补充。无分支先查询仓库默认分支。
2. 实际查询目标分支 HEAD，固定 commit 读取 vault.json 和 graph/root.json。
3. 解析实际工具返回，检查 schema_version=1、vault 非 initializing、vault.id=graph.vault_id。配置 expected_vault_id 时再匹配。错误标记、业务错误及缺失内容均算失败，不以工具完成提示代替成功。
4. 报告 snapshot、remote_checked=true、verification=entrypoint_only。仅入口可读不等于全库验证。查询继续读取相关记录。
5. 同轮分页/多跳固定 commit；需要最新状态时重读 HEAD，变化后复核。若工具无法读取固定版本，说明限制，不声称一致快照。

## 按图查询

- root.node_pages 是分页文件路径数组；读取每页取得节点 ID，再读 `graph/nodes/<id 中冒号替换为双短横线>.json`。节点含摘要、类型、状态、领域、正文 path 与 adjacency。
- domain 节点通常没有正文，沿 incoming 领域边选择相关记录；不能据此判断无资料。
- neighbors 按指定关系、方向读取对应 adjacency 页；timeline 按 root.timeline_pages 读取。处理全部相关分页与工具截断。
- read/source 读取节点实际指向的正文/原件，必要时按行分页。否定、条件、例外不能仅靠摘要回答。
- 路径不明确时用现有工具列固定 commit 的目录；不构造硬编码平台 API。
- 模糊问题多别名检索。完整查询须遍历相关范围至末页，说明未处理来源。
- 引用 record ID 或工具提供的固定 commit 文件链接；自建服务链接由其实际 URL 格式确定，不携带认证信息。

## 写入能力

先确认工具支持一次提交中增删多个文件、基于 base_commit 的并发保护和非强制分支更新，以及读取提交结果。查询能力不代表写能力。使用 mutations.md 的纯计算流程准备完整变更，再将内容交给现有工具发布；Skill 脚本的 prepared 状态不是远端保存成功。

不直接逐文件 update records 后补图，避免产生不一致版本。工具缺少安全提交能力或当前环境没有受信任的校验能力时，说明具体写入限制，仍继续授权的查询。不同工具账号/权限分别诊断，不静默切换身份。
