"""The 10 synthetic sample e-mails (mock-data). Not in the container image yet; the image gets them with BS-32/33."""

import json
from pathlib import Path

from app.channels.email.adapter import InboundMail

SAMPLES = Path(__file__).resolve().parents[5] / "mock-data" / "data" / "channels" / "emails.json" if len(Path(__file__).resolve().parents) > 5 else Path("/nonexistent")


def sample_mails(suffix: str = "") -> list[InboundMail]:
    return [InboundMail(message_id=f"<{e['id']}{suffix}@samples.mcs>", from_addr=e["from"], subject=e["subject"], text=e["body"])
            for e in json.loads(SAMPLES.read_text(encoding="utf-8"))]
