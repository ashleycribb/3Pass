# 3Pass Research Corpus: Intake Log

Append-only ledger of source routing, extraction, and wiki synchronization events.

- `[2026-11-13 19:33:40]` SYNC_ALL synced 287 draft pages with empty researcher appraisal sections.
- `[2026-11-13 19:44:12]` SYNC_ALL synced 287 draft pages with empty researcher appraisal sections.
- `[2026-11-13 19:52:12]` CI RUN 43536424731 PASSED (commits c87cf27 & 29f463f). Test suite: 26 passed; Wiki Linter: 100% pass (294 docs, 2,185 wikilinks, 0 broken). S3A round-trip verified.
- `[2026-11-13 20:56:45]` CI RUN 43543025671 FAILED (commit ba07f9a). Checkout S3A failed due to short 7-character commit SHA '1c1b2b9'.
- `[2026-11-13 21:04:12]` CI RUN 43543158233 PASSED (commit dbf1a86). Fixed S3A checkout with full 40-character SHA 1c1b2b9428ff36cd4e6e92cfee79c054275315f5. 28 passed.
- `[2026-11-14 09:06:20]` SMOKE_TEST_FIXES verified across all 4 real paper paths. Gemini 1.5 (2403.05530) authoritative metadata (1,137 authors, 2024, no COLING DOI); Attention Is All You Need (1706.03762) multi-file LaTeX extraction (6 figs, 7 tables, 11 eqs, 40 refs, 2017); PLOS ONE (10.1371/journal.pone.0298888) GROBID loud failure on offline + 45 complete references; GenAICHI (10.1145/3491101.3503719) metadata-only. Full suite: 34 passed.
- `[2026-11-14 09:13:35]` CI RUN 43603273292 PASSED (commit d9f29d7). Test suite: 34 passed; Wiki Linter: 100% pass (294 docs, 2,185 wikilinks, 0 broken). S3A round-trip verified.
- `[2026-11-14 09:30:00]` PROVENANCE_FIX: Removed fake TEI generator `_build_plos_tei_xml`. Added authentic `publisher_xml` (JATS XML) route with priority 70 (preferring JATS over GROBID) using PLOS ONE 10.1371/journal.pone.0298888 (15 secs, 9 figs, 13 tables, 11 eqs, 45 refs, completeness full). Added GROBID service container on ubuntu-latest in CI for real open-access PDF extraction (SimCLR PMLR) with /api/version verification. Added test preventing false GROBID tool_version claims without live requests. Full suite: 37 passed.
