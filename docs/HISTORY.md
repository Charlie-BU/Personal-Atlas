# Repository history / 仓库历史

This repository was extracted from `personal-atlas/` in
[Charlie-BU/PersonalSkills](https://github.com/Charlie-BU/PersonalSkills)
with `git subtree split --prefix=personal-atlas` in a disposable clone.
The original repository was not rewritten. The extraction preserves all 11
path-related commits on the source HEAD ancestry, including author/committer
identities, timestamps, commit messages, and each skill tree. Commits affecting
only other skills are omitted. The skill directory becomes the repository root,
so commit SHA values change.

[history-map.json](history-map.json) maps each original commit to its extracted
commit and records the shared tree ID. Every historical tree and commit metadata
was compared before publication. New open-source documentation and demo assets
are added in a subsequent commit. Historical documentation reflects behavior at
the time; use the current SKILL.md and references for current configuration.

本仓库通过 `git subtree split` 从原仓库抽离，保留源 HEAD 祖先中涉及该目录的
全部 11 次提交的作者、提交者、时间、说明和每个版本的文件树。
无关技能的提交不进入新仓库；目录提升到根目录，因此 SHA 会变化。
上述映射文件记录新旧提交和相同的文件树 ID；原仓库历史未被改写。
