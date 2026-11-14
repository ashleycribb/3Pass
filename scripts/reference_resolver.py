"""
Reference Resolver Module (Existence Check Only, No Judgments)

Provides authoritative reference resolution across four stages:
  1. Direct DOI verification (Crossref polite pool)
  2. Title + Year fuzzy search via Crossref API
  3. Title + Year fuzzy search via OpenAlex API
  4. Title + Year fuzzy search via Semantic Scholar API (no API key required)

Records status: 'resolved' | 'ambiguous' | 'not_found', the match score,
matched DOI/ID, and service used.
Never drops or rewrites a reference; stores resolution alongside the original text.
Caches lookups on disk and respects API rate limits.

Also provides duplicate paper merging:
Same paper arriving via two routes (normalized title + DOI / arxiv_id)
is merged into one record with both provenances.
"""

import os
import re
import json
import time
import hashlib
import difflib
from urllib.parse import quote
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any, Tuple
import requests

from scripts.models import (
    PaperRecord,
    ReferenceRecord,
    ReferenceResolution,
    RecordProvenance,
)

DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "cache", "reference_resolver"
)


def normalize_title(title: str) -> str:
    """Normalizes title for robust fuzzy matching."""
    if not title:
        return ""
    # Strip LaTeX commands: \textbf{foo} -> foo, \emph{bar} -> bar
    t = re.sub(r"\\[a-zA-Z]+(?:\[.*?\])?\{([^}]*)\}", r"\1", title)
    t = re.sub(r"\\[a-zA-Z]+", " ", t)
    # Strip URLs and embedded DOIs
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", "", t)
    # Remove non-alphanumeric punctuation and collapse whitespace
    t = re.sub(r"[^\w\s]", " ", t).lower()
    return re.sub(r"\s+", " ", t).strip()


def extract_doi_clean(doi_or_text: Optional[str]) -> Optional[str]:
    """Extracts a clean DOI string from a raw string or URL."""
    if not doi_or_text:
        return None
    m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", str(doi_or_text))
    if m:
        doi = m.group(1).rstrip(". ;,)")
        return doi.lower()
    return None


def fuzzy_title_score(title_a: str, title_b: str) -> float:
    """
    Computes similarity score [0.0, 1.0] between two paper titles.
    Combines character sequence similarity and token set containment.
    """
    norm_a = normalize_title(title_a)
    norm_b = normalize_title(title_b)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0

    seq_ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()

    words_a = set(norm_a.split())
    words_b = set(norm_b.split())
    if words_a and words_b:
        intersection = words_a & words_b
        jaccard = len(intersection) / len(words_a | words_b)
        min_len = min(len(words_a), len(words_b))
        containment = len(intersection) / min_len if min_len > 0 else 0
        if containment >= 0.90 and min_len >= 3:
            return max(seq_ratio, 0.85)
        return max(seq_ratio, jaccard)
    return seq_ratio


class ReferenceResolver:
    """
    Resolves academic references through polite existence checks:
    DOI -> Crossref -> OpenAlex -> Semantic Scholar.
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        email: str = "scholar@antigravity.internal",
        rate_limit_delay: float = 0.05,
        session: Optional[requests.Session] = None
    ):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self.email = email
        self.rate_limit_delay = rate_limit_delay
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": f"3PassResearchCorpus/1.0 (mailto:{self.email}; https://github.com/ashleycribb/3Pass)"
        })

    def _cache_key(self, ref: ReferenceRecord) -> str:
        clean_doi = extract_doi_clean(ref.doi)
        if clean_doi:
            return f"doi:{clean_doi}"
        norm_t = normalize_title(ref.title)
        yr = str(ref.year) if ref.year else ""
        return f"title:{norm_t}|yr:{yr}"

    def _cache_path(self, key: str) -> str:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{h}.json")

    def get_cached(self, ref: ReferenceRecord) -> Optional[ReferenceResolution]:
        key = self._cache_key(ref)
        path = self._cache_path(key)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                return ReferenceResolution(**data)
            except Exception:
                pass
        return None

    def put_cached(self, ref: ReferenceRecord, res: ReferenceResolution) -> None:
        key = self._cache_key(ref)
        path = self._cache_path(key)
        try:
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({
                    "status": res.status,
                    "match_score": round(res.match_score, 4),
                    "matched_id": res.matched_id,
                    "matched_service": res.matched_service,
                    "resolved_at": res.resolved_at
                }, fp, indent=2)
        except Exception:
            pass

    def resolve_reference(self, ref: ReferenceRecord) -> ReferenceResolution:
        """
        Resolves a single reference, storing the result on ref.resolution.
        Never drops or rewrites the original reference text.
        """
        # 1. Check cache
        cached = self.get_cached(ref)
        if cached:
            ref.resolution = cached
            return cached

        res: Optional[ReferenceResolution] = None

        # 2. DOI path (from ref.doi or embedded in ref.raw)
        clean_doi = extract_doi_clean(ref.doi) or extract_doi_clean(ref.raw)
        if clean_doi:
            res = self._resolve_by_doi(clean_doi, ref.title, ref.year, ref_raw=ref.raw)

        # 3. ArXiv ID path (constructs canonical DataCite/Crossref DOI 10.48550/arxiv.<id>)
        if not res or res.status == "not_found":
            m_arx = re.search(
                r"(?:arxiv[:\s/]|abs/)(\d{4}\.\d{4,5})",
                f"{ref.arxiv_id or ''} {ref.doi or ''} {ref.raw} {ref.title}",
                re.I
            )
            if m_arx:
                arxiv_doi = f"10.48550/arxiv.{m_arx.group(1)}"
                res = self._resolve_by_doi(arxiv_doi, ref.title, ref.year, ref_raw=ref.raw)

        # 4. Title + Year path (Crossref -> OpenAlex -> Semantic Scholar)
        if not res or res.status != "resolved":
            clean_title = normalize_title(ref.title)
            if len(clean_title) >= 6 or len(ref.raw) >= 10:
                # Stage A: Crossref title & author search
                cr_res = self._search_crossref(clean_title, ref.year, authors=ref.authors, raw=ref.raw)
                if cr_res:
                    res = cr_res

                # Stage B: OpenAlex fallback if not yet resolved
                if not res or res.status != "resolved":
                    alex_res = self._search_openalex(clean_title, ref.year, raw=ref.raw)
                    if alex_res:
                        if alex_res.status == "resolved" or not res or res.status == "not_found":
                            res = alex_res

                # Stage C: Semantic Scholar fallback if not yet resolved
                if not res or res.status != "resolved":
                    s2_res = self._search_semanticscholar(clean_title, ref.year, raw=ref.raw)
                    if s2_res:
                        if s2_res.status == "resolved" or not res or res.status == "not_found":
                            res = s2_res

        if not res:
            res = ReferenceResolution(
                status="not_found",
                match_score=0.0,
                matched_id=None,
                matched_service=None,
                resolved_at=datetime.now().isoformat()
            )

        self.put_cached(ref, res)
        ref.resolution = res
        return res

    def _resolve_by_doi(
        self, doi: str, ref_title: str, ref_year: Optional[int], ref_raw: str = ""
    ) -> Optional[ReferenceResolution]:
        """Direct DOI lookup via polite Crossref pool."""
        url = f"https://api.crossref.org/works/{quote(doi)}"
        try:
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)
            r = self.session.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                item = data.get("message", {})
                cand_title = item.get("title", [""])[0] if item.get("title") else ""
                score = fuzzy_title_score(ref_title, cand_title) if ref_title else 1.0

                if ref_raw:
                    norm_cand = normalize_title(cand_title)
                    norm_raw = normalize_title(ref_raw)
                    if norm_cand and len(norm_cand) >= 12 and norm_cand in norm_raw:
                        score = max(score, 0.95)

                # If reference title provided and significantly conflicts, mark ambiguous
                if ref_title and score < 0.40 and len(ref_title) > 15 and not (ref_raw and normalize_title(cand_title) in normalize_title(ref_raw)):
                    return ReferenceResolution(
                        status="ambiguous",
                        match_score=round(score, 4),
                        matched_id=doi,
                        matched_service="doi",
                        resolved_at=datetime.now().isoformat()
                    )

                return ReferenceResolution(
                    status="resolved",
                    match_score=max(round(score, 4), 0.95),
                    matched_id=doi,
                    matched_service="doi",
                    resolved_at=datetime.now().isoformat()
                )
            elif r.status_code == 404:
                return None
        except Exception:
            return None
        return None

    def _search_crossref(
        self, title: str, year: Optional[int], authors: Any = None, raw: str = ""
    ) -> Optional[ReferenceResolution]:
        """Title + Year search on Crossref."""
        url = "https://api.crossref.org/works"
        params: Dict[str, Any] = {"query.title": title[:200], "rows": 3}
        if authors:
            auth_str = " ".join(authors) if isinstance(authors, list) else str(authors)
            clean_auth = re.sub(r"[^\w\s]", " ", auth_str).strip()
            if clean_auth:
                params["query.author"] = clean_auth[:100]
        elif raw and len(raw) > len(title) + 10 and len(title) < 15:
            params = {"query.bibliographic": raw[:250], "rows": 3}
        try:
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)
            r = self.session.get(url, params=params, timeout=10)
            if r.status_code == 200:
                items = r.json().get("message", {}).get("items", [])
                return self._evaluate_candidates(title, year, items, service="crossref", ref_raw=raw)
        except Exception:
            pass
        return None

    def _search_openalex(
        self, title: str, year: Optional[int], raw: str = ""
    ) -> Optional[ReferenceResolution]:
        """Title + Year search on OpenAlex."""
        url = "https://api.openalex.org/works"
        params = {"search": title if len(title) >= 6 else raw[:100], "per-page": 3}
        try:
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)
            r = self.session.get(url, params=params, timeout=10)
            if r.status_code == 200:
                results = r.json().get("results", [])
                candidates = []
                for res in results:
                    candidates.append({
                        "title": [res.get("display_name", "")],
                        "DOI": res.get("doi") or res.get("id"),
                        "issued": {"date-parts": [[res.get("publication_year")]]}
                    })
                return self._evaluate_candidates(title, year, candidates, service="openalex", ref_raw=raw)
        except Exception:
            pass
        return None

    def _search_semanticscholar(
        self, title: str, year: Optional[int], raw: str = ""
    ) -> Optional[ReferenceResolution]:
        """Title + Year search on Semantic Scholar (no API key required)."""
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {"query": title if len(title) >= 6 else raw[:100], "limit": 3, "fields": "title,year,externalIds"}
        try:
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)
            r = self.session.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data", [])
                candidates = []
                for paper in data:
                    ext = paper.get("externalIds") or {}
                    cand_id = ext.get("DOI") or (f"S2:{paper.get('paperId')}" if paper.get("paperId") else None)
                    candidates.append({
                        "title": [paper.get("title", "")],
                        "DOI": cand_id,
                        "issued": {"date-parts": [[paper.get("year")]]}
                    })
                return self._evaluate_candidates(title, year, candidates, service="semanticscholar", ref_raw=raw)
        except Exception:
            pass
        return None

    def _evaluate_candidates(
        self,
        ref_title: str,
        ref_year: Optional[int],
        items: List[Dict[str, Any]],
        service: str,
        ref_raw: str = ""
    ) -> Optional[ReferenceResolution]:
        """Scores candidate search results and assigns resolved/ambiguous/not_found status."""
        if not items:
            return None

        scored: List[Tuple[float, str, str]] = []
        for it in items:
            t_list = it.get("title") or []
            cand_title = t_list[0] if isinstance(t_list, list) and t_list else str(t_list)
            cand_id = it.get("DOI") or it.get("id") or ""
            score = fuzzy_title_score(ref_title, cand_title)

            # Check raw string containment
            if ref_raw:
                norm_cand = normalize_title(cand_title)
                norm_raw = normalize_title(ref_raw)
                if norm_cand and len(norm_cand) >= 10 and norm_cand in norm_raw:
                    score = max(score, 0.95)

            # Year alignment check
            cand_year = None
            try:
                parts = it.get("issued", {}).get("date-parts", [[]])[0]
                if parts and str(parts[0]).isdigit():
                    cand_year = int(parts[0])
            except Exception:
                pass

            if ref_year and cand_year:
                if abs(ref_year - cand_year) == 0:
                    score = min(1.0, score + 0.05)
                elif abs(ref_year - cand_year) > 4 and score < 0.95:
                    score = max(0.0, score - 0.15)

            scored.append((score, cand_id, cand_title))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_score, top_id, top_title = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0

        now_iso = datetime.now().isoformat()

        # Exact title match check
        is_exact = normalize_title(ref_title) == normalize_title(top_title)
        if not is_exact and ref_raw and normalize_title(top_title) in normalize_title(ref_raw) and len(normalize_title(top_title)) >= 12:
            is_exact = True

        # Thresholds
        if top_score >= 0.85:
            # An exact title match resolves cleanly
            if is_exact:
                return ReferenceResolution(
                    status="resolved",
                    match_score=round(top_score, 4),
                    matched_id=top_id,
                    matched_service=service,
                    resolved_at=now_iso
                )

            # Check for ambiguity if the 2nd candidate has a different title and close score
            if len(scored) > 1 and second_score >= 0.75 and (top_score - second_score) < 0.08:
                second_title = scored[1][2]
                if normalize_title(top_title) != normalize_title(second_title):
                    return ReferenceResolution(
                        status="ambiguous",
                        match_score=round(top_score, 4),
                        matched_id=top_id,
                        matched_service=service,
                        resolved_at=now_iso
                    )

            return ReferenceResolution(
                status="resolved",
                match_score=round(top_score, 4),
                matched_id=top_id,
                matched_service=service,
                resolved_at=now_iso
            )
        elif 0.60 <= top_score < 0.85:
            return ReferenceResolution(
                status="ambiguous",
                match_score=round(top_score, 4),
                matched_id=top_id,
                matched_service=service,
                resolved_at=now_iso
            )
        else:
            return ReferenceResolution(
                status="not_found",
                match_score=round(top_score, 4),
                matched_id=None,
                matched_service=service,
                resolved_at=now_iso
            )


def are_duplicate_papers(rec1: PaperRecord, rec2: PaperRecord) -> bool:
    """Checks whether two PaperRecords represent the same paper."""
    if rec1.doi and rec2.doi:
        d1 = extract_doi_clean(rec1.doi)
        d2 = extract_doi_clean(rec2.doi)
        if d1 and d2 and d1 == d2:
            return True

    if rec1.arxiv_id and rec2.arxiv_id:
        a1 = rec1.arxiv_id.strip().lower()
        a2 = rec2.arxiv_id.strip().lower()
        if a1 == a2:
            return True

    score = fuzzy_title_score(rec1.title, rec2.title)
    if score >= 0.90:
        if rec1.year and rec2.year:
            return abs(rec1.year - rec2.year) <= 1
        return True

    return False


def merge_paper_records(rec1: PaperRecord, rec2: PaperRecord) -> PaperRecord:
    """
    Merges duplicate paper records arriving via two routes into one record
    preserving both provenances.
    """
    # Completeness order: "full" > "pdf-extracted" > "metadata-only"
    rank = {"full": 3, "pdf-extracted": 2, "metadata-only": 1}
    r1_rank = rank.get(rec1.completeness, 0)
    r2_rank = rank.get(rec2.completeness, 0)

    if r1_rank > r2_rank:
        primary, secondary = rec1, rec2
    elif r2_rank > r1_rank:
        primary, secondary = rec2, rec1
    else:
        primary, secondary = (rec1, rec2) if len(rec1.sections) >= len(rec2.sections) else (rec2, rec1)

    # Merge provenances list
    all_provs = []
    seen = set()
    prov_sources = (
        (primary.provenances or ([primary.provenance] if primary.provenance else [])) +
        (secondary.provenances or ([secondary.provenance] if secondary.provenance else []))
    )
    for p in prov_sources:
        if p:
            key = (p.path_used, p.source_uri)
            if key not in seen:
                seen.add(key)
                all_provs.append(p)

    # Merge URLs
    merged_urls = dict(secondary.urls)
    merged_urls.update(primary.urls)

    # Merge human notes
    merged_notes = dict(primary.human_notes)
    for k, v in secondary.human_notes.items():
        if v and not merged_notes.get(k):
            merged_notes[k] = v

    # Merge references by title/id preserving resolutions
    ref_map = {}
    for r in secondary.references:
        norm_k = normalize_title(r.title) or r.id
        ref_map[norm_k] = r
    for r in primary.references:
        norm_k = normalize_title(r.title) or r.id
        if norm_k in ref_map:
            if not r.resolution and ref_map[norm_k].resolution:
                r.resolution = ref_map[norm_k].resolution
        ref_map[norm_k] = r

    merged_refs = list(ref_map.values()) if ref_map else primary.references

    return PaperRecord(
        source_id=primary.source_id,
        paper_id=primary.paper_id or secondary.paper_id,
        title=primary.title if len(primary.title) >= len(secondary.title) else secondary.title,
        authors=primary.authors if len(primary.authors) >= len(secondary.authors) else secondary.authors,
        year=primary.year or secondary.year,
        doi=primary.doi or secondary.doi,
        arxiv_id=primary.arxiv_id or secondary.arxiv_id,
        urls=merged_urls,
        completeness=primary.completeness,
        provenance=primary.provenance or secondary.provenance,
        provenances=all_provs,
        abstract=primary.abstract or secondary.abstract,
        sections=primary.sections if primary.sections else secondary.sections,
        figures=primary.figures if primary.figures else secondary.figures,
        tables=primary.tables if primary.tables else secondary.tables,
        equations=primary.equations if primary.equations else secondary.equations,
        references=merged_refs,
        human_notes=merged_notes,
        as_of=primary.as_of or secondary.as_of,
        sha256=primary.sha256 or secondary.sha256,
        created_at=primary.created_at
    )


def resolve_paper_references(
    paper: PaperRecord,
    resolver: Optional[ReferenceResolver] = None,
    max_workers: int = 4
) -> PaperRecord:
    """
    Resolves all references in paper in-place (or threaded) and returns paper.
    Never drops or rewrites original text.
    """
    if resolver is None:
        resolver = ReferenceResolver()

    if not paper.references:
        return paper

    # Threaded resolution for performance while respecting rate limits
    if max_workers > 1 and len(paper.references) > 3:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(executor.map(resolver.resolve_reference, paper.references))
    else:
        for ref in paper.references:
            resolver.resolve_reference(ref)

    return paper
