"""Rule-layer entity extraction (LLD-6, FR-RT-01): regexes for account ids + the KG alias dictionary for models,
error codes, display texts, parts, cities and plans. Pure CPU, no I/O (p95 well under 10 ms)."""

import re

from app.kg.linker import Entities, LinkedEntity, Linker

TICKET = re.compile(r"\bSR-\d{4}-\d{5}\b", re.IGNORECASE)
AMC = re.compile(r"\bAMC-\d{4}-\d{4}\b", re.IGNORECASE)
SERIAL = re.compile(r"\bMT-([A-Z0-9]+)-\d{2}-\d{5}\b", re.IGNORECASE)
PART = re.compile(r"\bMT-[A-Z]{3}-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)


def _uniq(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(xs))


def extract_entities(text: str, linker: Linker) -> Entities:
    ents = linker.link_dictionary(text)
    ents.ticket_ids = _uniq([m.upper() for m in TICKET.findall(text)])
    ents.amc_ids = _uniq([m.upper() for m in AMC.findall(text)])
    serials = [(m.group(0).upper(), m.group(1).upper(), m.start()) for m in SERIAL.finditer(text)]
    ents.serials = _uniq([s for s, _, _ in serials])
    ents.part_nos = _uniq([m.group(0).upper() for m in PART.finditer(text)
                           if not SERIAL.fullmatch(m.group(0))])
    if not ents.model:                      # MT-DHD150-23-00417 → DHD-150 (helps family filters before a tool lookup)
        compact = {nid.replace("-", ""): nid for nid, n in linker.nodes.items() if n["label"] == "Model"}
        for serial, token, start in serials:
            if token in compact:
                nid = compact[token]
                ents.items.insert(0, LinkedEntity(nid, "Model", serial, start, start + len(serial), "serial", 1.0,
                                                  linker.family_of.get(nid)))
                # a display text / code that was ambiguous may now resolve with this family
                if ents.ambiguous:
                    retry = linker.link_dictionary(f"{nid} {text}")
                    ents.items += [e for e in retry.items if e.label == "ErrorCode"
                                   and e.id not in {x.id for x in ents.items}]
                    ents.ambiguous = retry.ambiguous
                break
    return ents
