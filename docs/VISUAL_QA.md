# Bilingual visualization acceptance / 双语可视化验收

Verified in Chrome on 2026-10-18 before committing the change.

- Desktop: 1512 × 695; mobile breakpoint: 390 × 844.
- Chinese default; Chinese/English toggle immediately to the right of theme controls.
- Verified both language choices survive reload. Invalid preferences and disabled
  storage are covered by automated tests; disabled storage was not browser-tested.
- Checked light overview, dark relationship focus, search/filter and timeline in
  both languages against the same fictional snapshot: 57 records (56 personal
  records plus one source), 17 domains, 74 nodes, 227 relationships.
- Confirmed record titles and summaries remain unchanged; built-in domain, type,
  status and relationship labels follow the interface language.
- Confirmed switching language retains search, filter selection, selected record,
  theme and graph/timeline view. Header fits and toggle remains usable on mobile.
- Measured toggle to the right of theme group and no desktop header overflow;
  checked all marked static labels translated in English.
- Checked browser error logs: no errors recorded.
- All 18 Python tests passed, including Node DOM contracts for both languages,
  persisted/invalid preferences, unavailable storage and unchanged record data.

`docs/images/atlas-*.png` are actual browser captures, not generated mockups.
The English README uses English captures; the Chinese README uses Chinese captures.
Record text is intentionally Chinese in both sets. The fixture is fictional and
never reads a personal vault or connects to a Git provider.

本次已完成桌面与窄屏实际浏览器检查、两种语言各四个场景截图及刷新恢复检查。
禁用存储和非法值回退由自动测试覆盖，未冒称已在浏览器中禁用存储验收。
截图全部来自同一份虚构资料，不包含真实个人档案。
