"""Exact intents (LLD-6, FR-RT-01): greeting, thanks, human, ticket status with an id, AMC status with an id,
reset. High precision only; everything else goes to the kNN/LLM router."""

import re

from app.kg.linker import Entities
from app.orchestrator.state import Intent

_GREETING = re.compile(r"^\s*(hi+|hello+|hey+|hii+|namaste|namaskar|good (morning|afternoon|evening)|hola)"
                       r"[\s!.,]*(there|team|matsui)?[\s!.,]*$", re.IGNORECASE)
_THANKS = re.compile(r"^\s*(thanks?( a lot| so much)?|thank you( so much| very much)?|thx|ty|dhanyavaa?d|shukriya|"
                     r"great,? thanks?|ok(ay)?,? thanks?)[\s!.,]*$", re.IGNORECASE)
_HUMAN = re.compile(r"\b(talk|speak|chat|connect)( me)? (to|with) (a |an |some )?(human|person|agent|someone|"
                    r"real person|executive|representative|engineer|customer care)\b|\b(human|live) agent\b|"
                    r"\b(insaan|aadmi|kisi) se baat\b|\bcall me back\b", re.IGNORECASE)
_STATUS = re.compile(r"\b(status|update|progress|where|when|kab|kya hua|any news|still|pending|follow[- ]?up|"
                     r"escalat\w*|nobody came|not come)\b", re.IGNORECASE)
_AMC_STATUS = re.compile(r"\b(active|valid|status|expir\w*|lapsed|still|cover\w*|running)\b", re.IGNORECASE)
_RESET = re.compile(r"^\s*(reset|start over|restart|new (chat|conversation)|clear( chat)?)[\s!.]*$", re.IGNORECASE)


def exact_intents(text: str, ents: Entities) -> list[Intent]:
    out: list[Intent] = []
    if _RESET.match(text):
        return [Intent(name="reset", confidence=1.0, source="rule")]
    if _GREETING.match(text):
        return [Intent(name="greeting", confidence=1.0, source="rule")]
    if _THANKS.match(text):
        return [Intent(name="thanks", confidence=1.0, source="rule")]
    if _HUMAN.search(text):
        out.append(Intent(name="human", confidence=1.0, source="rule", span=_HUMAN.search(text).group(0)))  # type: ignore[union-attr]
    if ents.ticket_ids and (_STATUS.search(text) or len(text.split()) <= 6):
        out.append(Intent(name="service.ticket_status", confidence=1.0, source="rule", span=ents.ticket_ids[0]))
    if ents.amc_ids and (_AMC_STATUS.search(text) or len(text.split()) <= 6):
        out.append(Intent(name="amc.status", confidence=1.0, source="rule", span=ents.amc_ids[0]))
    return out
