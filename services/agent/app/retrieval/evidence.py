"""Evidence passed to answer generation and verification (LLD-5 `TurnState.evidence`, LLD-9).

kind=kg_path: a verbalized graph fact with the path ids it came from (citations `[kg:<path_id>]`).
kind=chunk: a knowledge-base chunk (citations `DOC-ID#anchor`). kind=tool: transactional tool output (BS-16).
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    id: str                                   # stable: "kg:T-DIAG:..." | "doc:DOC-MAN-DHD#e04-dew-point-high"
    kind: Literal["kg_path", "chunk", "tool"]
    text: str
    source: str                               # template id, "text2cypher", "documented_in", "hybrid", tool name
    score: float = 0.0
    title: str | None = None
    doc_id: str | None = None
    anchor: str | None = None
    path_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def citation(self) -> str:
        if self.kind == "kg_path":
            return f"kg:{self.path_ids[0]}" if self.path_ids else self.id
        return f"{self.doc_id}#{self.anchor}" if self.doc_id else self.id
