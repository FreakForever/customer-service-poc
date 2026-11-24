"""Envelope and turn state (LLD-5, FR-OR-01).

Every channel adapter (chat SSE, email, voice later) converts its inbound message into an `Envelope` before
calling the orchestrator; nothing under app/orchestrator imports a channel adapter.
"""

import uuid
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from app.kg.linker import Entities
from app.orchestrator.policies import ChannelPolicy
from app.retrieval.evidence import Evidence

Channel = Literal["chat", "email", "voice"]
Tier = Literal["T0", "T1", "T2", "T3", "T4"]


class CustomerRef(BaseModel):
    customer_id: str
    contact_email: str | None = None
    trust: Literal["contact", "domain", "session"] = "session"


class AttachmentRef(BaseModel):
    filename: str
    mime: str
    storage_uri: str
    bytes: int = 0


class Envelope(BaseModel):
    channel: Channel
    conversation_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    customer: CustomerRef | None = None          # resolved id, or None for anonymous/unknown
    text: str
    attachments: list[AttachmentRef] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)   # email headers, chat client info


class Intent(BaseModel):
    name: str
    confidence: float
    source: Literal["rule", "knn", "llm", "stub"]
    span: str | None = None


class SubQuery(BaseModel):
    text: str
    intent: str | None = None
    depends_on: list[int] = Field(default_factory=list)


class Citation(BaseModel):
    marker: str
    evidence_id: str
    doc_id: str | None = None
    anchor: str | None = None


class Draft(BaseModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    form: dict[str, Any] | None = None
    abstained: bool = False
    generated: bool = False               # written by a model (needs coverage, regeneration and the judge)


class Verification(BaseModel):
    passed: bool
    coverage: float | None = None
    judge: float | None = None
    issues: list[str] = Field(default_factory=list)


class HandoffDecision(BaseModel):
    action: Literal["handoff", "draft_for_review"]
    reason: str
    priority: str | None = None
    summary: str | None = None            # note for the human; never sent to the customer


class TurnState(TypedDict, total=False):
    env: Envelope
    policy: ChannelPolicy
    trace_id: uuid.UUID
    entities: Entities                    # models, serials, error_codes, ticket_ids, amc_ids, part_nos, cities
    flags: set[str]                       # safety, injection, oos, complaint, human_requested
    intents: list[Intent]                 # [{name, confidence, source: rule|knn|llm}]
    subqueries: list[SubQuery]            # email decomposition / multi-intent
    active_agent: str | None              # sticky specialist (chat)
    evidence: list[Evidence]              # chunks + kg paths + tool results
    draft: Draft | None                   # text, citations, actions, form
    verification: Verification | None
    confidence: float
    confidence_parts: dict[str, float]
    blocked_draft: Draft | None           # failed verification: for the human only
    answered_tier: Tier
    handoff: HandoffDecision | None
    # orchestration bookkeeping (not in LLD-5's list; D-028)
    cache_hit: bool
    retrieval_score: float
    budget_exceeded: str | None           # node whose budget ran out → degrade
    deadline: float                       # time.monotonic() when the total budget ends
    path: list[str]                       # visited nodes, in order
    query_embedding: list[float]          # reused by the FAQ cache and retrieval (BS-14/15)
    knn: list[tuple[str, float]]          # top kNN intents (hint for the LLM router)
    language: str                         # en | hinglish | mixed — replies are always English (D-030)
    clarification: str | None             # router asks one clarifying question
    dialog: dict[str, Any]                # specialist memory, stored in conversations.state
    handback: bool                        # sticky agent returned the turn to the router
    response: dict[str, Any]
