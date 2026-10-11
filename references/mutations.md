# 增删改与图同步

所有增删改使用 JSON 操作包，经 apply 发布。脚本在内存验证提案，再经 GitHub API 将正文、图、manifest 与操作记录发布为一个远端提交。图不单独手工维护。

## 操作包

先 status 取得 snapshot，read 取得完整 meta/body 和 sha256。操作包只保存在内存，通过 stdin 传给脚本；不写临时文件，不将原文拼接为 shell 命令。

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
python3 -B <skill>/scripts/atlas.py apply --stdin
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

1. 从远端固定快照校验 base_commit、记录旧哈希和现有目录。
2. 在内存构造完整提案，检查格式、时间、来源、覆盖单元、ID 和关系。
3. 确定性生成图，上传变更 blob 和 tree；正文与图属于同一个 commit。
4. 再核对分支，使用 force=false 发布。并发变化时停止，不覆盖别人的提交。
5. 读取远端 HEAD 确认后报告 remote_saved；响应丢失时报告 remote_outcome_unknown，再按原 operation_id 与原内容核查重试。

没有本地锁、日志、恢复目录或待推送记录。远端未被分支引用的 Git 对象可能保留，但不会作为成功版本交给查询。失败后不要使用新 operation_id 盲目重复写。

远端人工编辑必须同样同步目录；reindex 仅重建图，不能补全人工删除的历史语义。普通增删改都使用 apply，不直接手改图。具体 API 行为与配置见 storage.md。
