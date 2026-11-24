"""General T3 agent: graph + document evidence (Text2Cypher when no template fits) and the strong model."""

from app.agents.base import AgentCtx, AgentResult
from app.agents.rag_answerer import answer
from app.retrieval.graphrag import graph_evidence


async def run(ctx: AgentCtx) -> AgentResult:
    ge = await graph_evidence(ctx.text, ctx.ents, "hybrid", ctx.deps)
    res = await answer(ctx.text, ge.evidence, ctx.llm, model=ctx.model_strong, timeout_ms=ctx.timeout_ms)
    return AgentResult(res.draft.text, evidence=ge.evidence, citations=res.draft.citations, abstained=res.draft.abstained,
                       generated=res.draft.generated)
