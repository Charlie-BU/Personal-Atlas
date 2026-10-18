# Contributing / 贡献指南

Issues and pull requests in English or Chinese are welcome. Describe the problem,
expected behavior, and a minimal reproduction using **synthetic data**. Discuss
large changes in an issue before implementation. Keep changes focused, preserve
source references and temporal semantics, and update both READMEs when behavior changes.

Run from the repository root (Python 3.10+; Node.js enables DOM contract checks):

```sh
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
python3 -B examples/build_demo.py --output /tmp/personal-atlas-demo.html
```

The tests use in-memory snapshots and a simulated commit receiver. Passing them
does not prove a live Git provider connection or rendered-browser behavior.
Never include personal vaults, credentials, screenshots of real records, or local
configuration in contributions. Preserve offline HTML export and atomic,
concurrency-protected writes through host-provided tools.

欢迎中文 Issue / PR。请用虚构资料提供复现，修改行为时同步更新中英文 README。
提交前运行上述检查；测试通过不代表真实远端读写验收完成。
请勿提交个人资料库、凭据、真实资料截图或本机配置。
