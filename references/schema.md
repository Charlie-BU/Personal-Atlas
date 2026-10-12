# 记录与 JSON 图协议 v1

权威数据为远端仓库 vault.json、records/、sources/、operations/；用户要求归档时，skill/personal-atlas/ 可保存 Skill 静态副本并纳入清单；连接配置与凭据仍位于 Skill 外。graph/、manifests/ 为可完全重建的确定性生成物，无额外数据库作为唯一信息源。

## 记录

路径固定 `records/<type>/<id>.md`。ID 不随标题变化，以小写字母开头，只含小写字母/数字/下划线/连字符，最多 96 字符。引用用 ID。只有值得独立查改、边界或时间不同的信息才拆分，不必每个句子建文件。

文件是 `---` 包围的 JSON 对象与 Markdown 正文。JSON 是 YAML 子集，Python 标准库即可读取；手工编辑不能改为任意 YAML 语法。

```markdown
---
{
  "id": "pref_tea",
  "type": "preference",
  "title": "日常饮茶偏好",
  "summary": "茶饮口味和场景，不包含消费账单。",
  "domains": ["life", "food"],
  "aliases": ["茶口味"],
  "temporal": {"mode": "none"},
  "status": "active",
  "basis": "user_statement",
  "evidence": [{"source":"src_example","locator":"lines:2-4","claim":"c_taste"}],
  "relations": [{"type":"about","target":"person_self"}],
  "created_at": "2026-10-11T12:00:00+00:00",
  "updated_at": "2026-10-11T12:00:00+00:00"
}
---

# 日常饮茶偏好

## c_taste：口味
来源支持的具体偏好及其例外。
```

示例 ID 仅说明格式；source/person 必须已存在或在同一操作内创建。

必填 id/type/title/summary/domains/temporal/basis。status 默认 active，aliases/relations/evidence 默认空，系统时间由 apply 生成。更新使用完整 meta/body，不是隐式字段补丁。

- type：person、organization、place、profile、topic、preference、routine、state、event、decision、goal、project、asset、source。
- domains：非空领域 ID 列表，自由扩充如 work/education/health/finance/family/life，自动形成 domain 节点。
- basis：user_statement/document/third_party/inference/navigation。navigation 仅用于无事实断言的导航容器，不能用来绕过证据。
- inference 必须有 uncertainty 和 evidence；其余事实记录也须 evidence。source 自己不必引用另一个 source。
- title <=160 字符，summary <=500 字符；路由摘要通常 1–2 句，描述范围，不复制完整敏感细节。
- 正文通常 300–1,000 tokens，约 1,500 tokens 考虑拆分，16,000 字符为防事故硬上限。
- 扩展元数据可保留，但只有已定义字段有程序检索语义，不能假设支持任意字段过滤。

细粒度事实可用 `## c_name：标题`；evidence/relations 的 claim 可指向此稳定标题。删改标题时同步引用。locator 使用行号、消息 ID、页码、时间码。脚本验证来源与 claim 存在，agent 验证来源是否真正支持陈述。

## 关系

允许 about/part_of/participated_in/located_at/works_at/knows/supports/contradicts/supersedes/derived_from/depends_on/related_to。relations 为出边，target 必须存在；反向 incoming 自动生成。

有时限的任职/居住/关系使用 state 承载时间，再连接人、机构、地点。不要压缩为实体间的永久边。共现不等于 knows，相关不等于因果。related_to 只在关系确实不明时用。

程序生成 in_domain（记录→领域）、derived_from（记录→来源，含 locator/claim）、extracted_as（来源单元→整理记录）。

## 来源

source 除通用字段外还有：

```json
{
  "source": {
    "raw_path":"sources/src_example/original.txt",
    "sha256":"<ingest 计算>",
    "original_name":"input.txt",
    "coverage":"partial",
    "coverage_note":"已检查 1–100 行，其余待处理。",
    "scope":"原文件全部 180 行",
    "units":[
      {"id":"u1","locator":"lines:1-100","status":"processed","record_ids":["pref_tea"]},
      {"id":"u2","locator":"lines:101-180","status":"pending","record_ids":[]}
    ]
  }
}
```

coverage：unprocessed/partial/complete/failed。单元状态：pending/processed/failed。processed 单元须 record_ids，或 note 说明无个人事实、重复材料、用户要求排除等处理结果。complete 须 scope 和全部 processed 的 units；agent 还要核实单元覆盖全部 scope，脚本无法判断自然语言范围是否真正完整。

## 主目录：结构化图

```text
graph/root.json                    # 版本、领域种子、计数、分页位置
graph/nodes/<id>.json               # 节点摘要、正文路径、哈希、邻接页位置
graph/adjacency/<id>/outgoing-*.json # 有向带类型的出边，每页最多80条
graph/adjacency/<id>/incoming-*.json # 自动反向索引
graph/node-pages/*.json             # 全节点ID分页
graph/timeline/*.json               # 时间节点倒序，未知末尾
manifests/files/*.json              # 权威文件路径与SHA-256分片
```

领域 ID 如 domain:work，其文件名为 domain--work.json。邻接表按方向、类型筛选分页。content_revision 是权威文件清单哈希，snapshot 是 Git commit，不能混用。

不另维护一份 Markdown 总目录。图不含完整原文；高连接节点分页。一页邻居不代表该节点的全部邻居。
