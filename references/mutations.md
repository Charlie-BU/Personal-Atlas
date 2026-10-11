# 增删改与图同步

所有增删改使用 JSON 操作包，经 apply 发布。脚本锁住专用仓库，先验证再一次更新正文、图、manifest 和 Git 提交。图不单独手工维护。

## 操作包

先 status 取得 snapshot，read 取得完整 meta/body 和 sha256。操作包保存在当前任务临时目录，不放进数据仓库。用 JSON 序列化写文件，不将原文拼接为 shell 命令。

```json
{
  "operation_id":"op_unique_example",
  "base_commit":"<status.snapshot>",
  "changes":[
    {
      "action":"put",
      "meta":{
        "id":"pref_tea",
        "type":"preference",
        "title":"饮茶偏好",
        "summary":"茶饮口味与例外。",
        "domains":["life"],
        "temporal":{"mode":"none"},
        "basis":"user_statement",
        "evidence":[{"source":"<已导入来源ID>","locator":"lines:2-4"}]
      },
      "body":"# 饮茶偏好\n\n这里写来源支持的信息。"
    }
  ]
}
```

```text
python3 <skill>/scripts/atlas.py apply --plan <绝对路径的操作包.json>
```

- 新记录 put 不带 expected_sha256。
- 更新 put 必须带 read 返回的 expected_sha256；提供完整 meta/body。保留来源、旧限定条件、原有关系，除非本次修改明确改变它们。
- delete 形如 `{"action":"delete","id":"pref_tea","expected_sha256":"..."}`。
- 同操作每个 ID 只出现一次；同批新增可互相引用。
- operation_id 每次新操作唯一。结果不确定时原样重试同 ID、同内容；已成功会返回 already_applied。同 ID 不可用于别的修改。
- base_commit 过期则重新读取和重做操作包，不能只换成新版本号掩盖冲突。

## 操作判断

**新增**：查对象、别名和关系。同一事实增加独立证据；重复原文由 ingest 的 SHA-256 去重。相似事件可能发生过多次，不自动合并。同名人物需要证据区分。

**补充/修正**：保留有依据的原细节；用户明确纠错时调整旧事实状态。记录新纠正的 source。时间规则见 time.md。

**拆分/合并**：新建目标记录、更新所有 incoming 引用与 source units，再删除旧节点；如果历史查询仍需旧节点，改为 historical/retracted 并建立 supersedes，而不是丢失来源。稳定 ID 不重复使用。

**修改标题/类型**：标题变化保留 ID，必要时加旧标题 aliases。类型变化通过同 ID put 自动移动文件，图路径同步重建。

**删除**：先 neighbors 检查 incoming，尤其 derived_from、extracted_as、part_of、supports 和依赖总结。必须同事务移除/调整引用；悬空边会阻止整个修改发布。删除 source 也删除其当前 raw 文件，但前提是相关证据引用和 source units 已处理。

**用户要求忘记某内容**：普通删除记录不会自动擦掉原始来源里的相关句子。应定位 source 片段、引用事实、派生画像/总结，提出明确范围并在授权内处理。需保留来源剩余内容时，生成删去目标片段的新 source、重新定位保留事实的证据，并删除旧 source；不能声称保留旧原文时已忘记。当前实现不提供自动历史清洗，Git 历史另按用户授权处理。

## 来源覆盖与摘要

整理资料时，把新增记录和 source.units/coverage 更新放在同一操作包。processed 单元列出产出记录；不产出须 note。source complete 的结构校验通过仍不等于提取语义无遗漏，agent 要回查原文单元。

改正文时同步其 title/summary/aliases 和 relations。对派生总结使用 derived_from/depends_on 等记录依赖，修改证据后检查 incoming 并更新受影响总结。程序能发现悬空引用，不能发现“正文已改但摘要仍旧”的语义偏差。

## 事务与失败

1. 文件操作前锁、检查当前提交、验证记录哈希。
2. 构造完整提案，检查格式、时间、来源、覆盖单元、ID 和关系。
3. 确定性生成图，记录受影响文件的恢复日志。
4. 写入文件与 Git 对象，通过预期旧 HEAD 更新分支引用，作为提交边界。
5. 按既有策略同步远端。

普通失败回滚；进程中断留下恢复日志时读写均拒绝，运行 recover。不要手动删除日志。Git 提交后的中断会识别已提交状态；之前的中断恢复旧文件与索引。

锁是本机协作锁，外部编辑器不受它约束；勿在 apply/sync 运行期间手改文件。外部变更会在后续操作前检查，需 inspect + reindex，不允许假装当前图仍有效。

输出本地与远端状态分别解释。after_write 推送失败不会删除已完成本地修改，会返回 pending_or_conflicted 和原始原因；再次 sync 即可核验/重试。
