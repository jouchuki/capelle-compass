"""
Standardized output schema for all Capelle CLI tools.

Every tool wraps its output in ToolOutput so the dashboard can render
tables, charts, and search results uniformly without knowing which tool
produced the data.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class ColumnDef:
    """Describes one column in a ToolOutput data table."""
    key: str                          # field name in data rows
    label: str                        # display name
    type: str = "string"              # "string" | "number" | "year" | "url" | "text_snippet"
    unit: str | None = None           # "€k" | "%" | "per 1000 inwoners"


@dataclass
class ChartHint:
    """Suggested visualization for the data."""
    type: str                         # "bar" | "line" | "table" | "map" | "treemap"
    x: str                            # column key for x-axis
    y: str | list[str] = ""           # column key(s) for y-axis
    group_by: str | None = None       # optional grouping column
    title: str = ""


@dataclass
class ToolOutput:
    """Universal output wrapper emitted by every CLI tool."""
    tool: str                         # "cbs" | "budget" | "beleid" | "buitenbeter"
    query: str                        # what was asked
    result_type: str                  # "table" | "search_results" | "summary" | "time_series"
    data: list[dict[str, Any]]        # rows of data
    columns: list[ColumnDef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    chart_hints: list[ChartHint] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        d = asdict(self)
        # Clean None values from chart_hints
        for ch in d.get("chart_hints", []):
            if ch.get("group_by") is None:
                del ch["group_by"]
        for col in d.get("columns", []):
            if col.get("unit") is None:
                del col["unit"]
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass
class AnalysisSection:
    """One section of a multi-source analysis."""
    heading: str                      # e.g. "Crime Statistics (CBS)"
    source: str                       # tool name
    content: str                      # markdown narrative
    tool_output: ToolOutput | None = None  # raw data for dashboard rendering

    def to_dict(self) -> dict:
        d = {
            "heading": self.heading,
            "source": self.source,
            "content": self.content,
        }
        if self.tool_output:
            d["tool_output"] = self.tool_output.to_dict()
        return d


@dataclass
class AnalysisResult:
    """Complete multi-source analysis produced by the agent."""
    query: str                        # original question
    summary: str                      # agent's synthesis (markdown)
    sections: list[AnalysisSection] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "query": self.query,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections],
            "data_gaps": self.data_gaps,
            "follow_up": self.follow_up,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def save(self, output_dir: str | None = None) -> str:
        """Save to capelle_rag/analyses/ and return the file path."""
        from pathlib import Path
        if output_dir is None:
            output_dir = Path(__file__).parent / "analyses"
        else:
            output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        slug = self.query[:40].lower()
        slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
        slug = slug.strip().replace(" ", "_")
        filename = f"{slug}_{self.id}.json"
        path = output_dir / filename
        path.write_text(self.to_json(), encoding="utf-8")
        return str(path)
