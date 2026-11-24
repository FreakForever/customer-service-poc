"""Agent API + worker for the browser tests (apps/web/e2e): real graph, knowledge graph and mcs_test, scripted model and
classifier, demo mailbox. Rebuilds mcs_test on start. Needs `make proxy` (or DATABASE_URL_TEST) and NEO4J_*.

    cd services/agent && uv run python ../../scripts/e2e_server.py --port 8100
"""

import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "services" / "agent"
sys.path.insert(0, str(AGENT))

from app.config.settings import Settings

CLASSIFY = [  # (substring of the message, intents)
    ("warranty", ["faq.warranty"]), ("rotor", ["faq.warranty"]), ("lp trip", ["troubleshoot.error_code"]), ("complaint", ["complaint"]),
    ("motor ol", ["troubleshoot.error_code"]), ("dew point high", ["troubleshoot.symptom"]), ("bitcoin", ["out_of_scope"]),
    ("hot air dryer", ["faq.process"]), ("parts are common", ["troubleshoot.symptom"]), ("amc", ["amc.status"]),
]


def prepare(url: str) -> None:
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if name != "mcs_test":
        raise SystemExit(f"refusing to rebuild database {name!r}")
    for a in (("downgrade", "base"), ("upgrade", "head")):
        subprocess.run(["uv", "run", "alembic", "-x", f"url={url}", *a], cwd=AGENT, check=True, capture_output=True)
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.ingest.seed_kb import seed_kb
    from app.providers.fake import FakeProvider

    spec = importlib.util.spec_from_file_location("seed_script", ROOT / "scripts" / "seed.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = create_engine(url)
    with eng.begin() as c:
        mod.seed(c)
    eng.dispose()

    async def kb() -> None:
        aeng = create_async_engine(url)
        await seed_kb(aeng, FakeProvider(dim=768))
        await aeng.dispose()
    asyncio.run(kb())


def patch() -> None:
    from app.channels import chat
    from app.channels.email import pipeline
    from app.orchestrator.graph import build_graph
    from app.orchestrator.nodes import agents, rag, verify
    from app.orchestrator.state import Intent
    from app.providers import ResilientLLM
    from app.providers.fake import FakeProvider
    from app.retrieval.rerank import NoopReranker

    async def classify(state):
        if state.get("intents"):
            return {}
        t = state["env"].text.lower()
        names = next((n for k, n in CLASSIFY if k in t), ["faq.general"])
        return {"intents": [Intent(name=n, confidence=0.95 - i / 50, source="knn") for i, n in enumerate(names)]}

    script = {"t2.v1": lambda m: "The manual covers this [E1].", "summary.v1": lambda m: "Customer asked for a person.",
              "judge.v1": lambda m: json.dumps({"faithfulness": 1.0, "unsupported_claims": [], "answers_question": True})}
    for mod in (rag, agents, verify):
        mod.get_llm = lambda: ResilientLLM(FakeProvider(script=dict(script)))  # type: ignore[attr-defined]
    for mod in (rag, agents):
        mod.get_reranker = NoopReranker  # type: ignore[attr-defined]
    chat.graph = pipeline.graph = build_graph({"classify": classify})


async def serve(port: int) -> None:
    import uvicorn

    from app.config.settings import get_settings
    from app.db import get_engine, warm_pool
    from app.jobs.queue import run_once
    from app.main import create_app
    from app.rules.context import get_rule_context
    from app.worker import _handlers

    await warm_pool()
    await get_rule_context()
    _, handlers = _handlers(get_settings())

    async def worker() -> None:
        while True:
            try:
                if await run_once(get_engine(), handlers) is None:
                    await asyncio.sleep(0.5)
            except Exception as exc:  # noqa: BLE001
                print("worker:", type(exc).__name__, exc, file=sys.stderr)
                await asyncio.sleep(1)
    task = asyncio.create_task(worker())
    await uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")).serve()
    task.cancel()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    port = ap.parse_args().port
    s = Settings()
    url = os.environ.get("DATABASE_URL_TEST") or s.database_url_test.get_secret_value()
    if not url:
        raise SystemExit("DATABASE_URL_TEST is not set")
    os.environ.update(DATABASE_URL=url, ENV="dev", PROVIDER="fake", EMBED_PROVIDER="fake", MAIL_ADAPTER="demo", DEMO_PERSONAS="true", GCS_BUCKET="",
                      WARM_ON_STARTUP="false", NEXTAUTH_SECRET=os.environ.get("E2E_SECRET", "e2e-secret"))
    prepare(url)
    patch()
    asyncio.run(serve(port))


if __name__ == "__main__":
    main()
