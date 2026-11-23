"""Write the orchestrator graph as Mermaid (BS-11, FR-OR-02): services/agent/app/orchestrator/graph.mmd.

    make graph   # = cd services/agent && uv run python ../../scripts/export_graph.py
A unit test fails if the committed file is stale.
"""

import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "services" / "agent"
sys.path.insert(0, str(AGENT))

from app.orchestrator.graph import mermaid

OUT = AGENT / "app" / "orchestrator" / "graph.mmd"

if __name__ == "__main__":
    OUT.write_text(mermaid())
    print(f"wrote {OUT.relative_to(AGENT.parents[1])}")
