"""
Unified Academic Paper Record Schema

Maintains a single record format across all extraction paths:
  - Path a: arXiv LaTeXML HTML (completeness: "full")
  - Path b: LaTeX Source -> LaTeXML (completeness: "full")
  - Path c: GROBID PDF TEI XML (completeness: "pdf-extracted", all parts marked "pdf-extracted, verify")
  - Path d: Crossref / OpenAlex Metadata (completeness: "metadata-only")

Bookkeeping only: No automated judgments or summaries.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from datetime import datetime
import json

@dataclass
class SectionRecord:
    id: str
    title: str
    text: str
    level: int = 1
    verified: str = "verified"  # "verified" | "pdf-extracted, verify" | "metadata-only"

@dataclass
class FigureRecord:
    id: str
    caption: str
    url_or_path: str = ""
    verified: str = "verified"

@dataclass
class TableRecord:
    id: str
    caption: str
    markdown: str
    verified: str = "verified"

@dataclass
class EquationRecord:
    id: str
    latex: str
    verified: str = "verified"

@dataclass
class ReferenceRecord:
    id: str
    title: str
    authors: List[str] = field(default_factory=list)
    venue: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    raw: str = ""
    verified: str = "verified"

@dataclass
class PartProvenance:
    tool: str
    tool_version: str
    status: str  # "extracted" | "pdf-extracted, verify" | "not-available"

@dataclass
class RecordProvenance:
    path_used: str  # "arxiv_html" | "latex_source" | "grobid_pdf" | "doi_metadata"
    tool: str
    tool_version: str
    source_uri: str
    extracted_at: str
    per_part: Dict[str, Dict[str, str]] = field(default_factory=dict)

@dataclass
class PaperRecord:
    source_id: str
    paper_id: int
    title: str
    authors: List[str]
    year: Optional[int]
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    urls: Dict[str, str] = field(default_factory=dict)
    completeness: str = "full"  # "full" | "pdf-extracted" | "metadata-only"
    provenance: Optional[RecordProvenance] = None
    abstract: str = ""
    sections: List[SectionRecord] = field(default_factory=list)
    figures: List[FigureRecord] = field(default_factory=list)
    tables: List[TableRecord] = field(default_factory=list)
    equations: List[EquationRecord] = field(default_factory=list)
    references: List[ReferenceRecord] = field(default_factory=list)
    human_notes: Dict[str, str] = field(default_factory=lambda: {
        "problem_formulation": "",
        "methodology_evaluation": "",
        "findings_credibility": "",
        "dissertation_gaps": ""
    })
    sha256: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperRecord":
        prov_data = data.get("provenance")
        prov = RecordProvenance(**prov_data) if prov_data and isinstance(prov_data, dict) else None

        sections = [SectionRecord(**s) for s in data.get("sections", [])]
        figures = [FigureRecord(**f) for f in data.get("figures", [])]
        tables = [TableRecord(**t) for t in data.get("tables", [])]
        equations = [EquationRecord(**e) for e in data.get("equations", [])]
        references = [ReferenceRecord(**r) for r in data.get("references", [])]

        return cls(
            source_id=data["source_id"],
            paper_id=data.get("paper_id", 0),
            title=data.get("title", "Untitled"),
            authors=data.get("authors", []),
            year=data.get("year"),
            doi=data.get("doi"),
            arxiv_id=data.get("arxiv_id"),
            urls=data.get("urls", {}),
            completeness=data.get("completeness", "full"),
            provenance=prov,
            abstract=data.get("abstract", ""),
            sections=sections,
            figures=figures,
            tables=tables,
            equations=equations,
            references=references,
            human_notes=data.get("human_notes", {
                "problem_formulation": "",
                "methodology_evaluation": "",
                "findings_credibility": "",
                "dissertation_gaps": ""
            }),
            sha256=data.get("sha256"),
            created_at=data.get("created_at", datetime.now().isoformat())
        )
