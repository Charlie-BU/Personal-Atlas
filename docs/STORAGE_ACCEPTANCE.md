# Shared-repository storage acceptance

Verified on 2026-10-18 before publication:

- All 24 automated tests passed, including existing CRUD, source integrity,
  graph inventory, offline HTML safety and bilingual UI contracts.
- New tests cover initialization in a non-empty repository, file/directory
  conflicts, preservation of README, root configuration, plans and health data,
  namespace escape rejection, namespaced record/source URLs and export isolation.
- A disposable bare Git repository test publishes real Git objects and commits,
  preserves existing trees, rejects a stale compare-and-swap branch update after
  another skill writes, then rereads, validates and rebuilds before retrying.
- The rich fictional demo still exports 74 nodes and 227 relationships. Chrome
  smoke acceptance checked search, record details, timeline and language switching;
  no browser errors were recorded. The HTML UI template itself was unchanged.
- Tests use synthetic data. No live personal vault was read, written or migrated;
  no claim is made about real-account acceptance across Git hosting providers.

Atlas owns only atlas/. The repository HEAD remains the concurrency baseline.
Legacy root-level vaults require an explicitly scoped migration; no implicit
fallback or migration is performed by this change.
