"""Build judge_50.json: 25 supported and 25 unsupported answers from the synthetic catalog (deterministic).
Labels are CONSTRUCTED (the edit made is known), not human. FR-VF-02 asks for human labels: a person should fill
`human_label` (supported | unsupported) for each item; eval/judge.py then reports agreement against those instead."""
import json
import random
from pathlib import Path

D = Path(__file__).resolve().parents[2] / "mock-data" / "data" / "catalog"
E, R, P, C = (json.loads((D / f"{n}.json").read_text()) for n in ("error_codes", "resolutions", "spare_parts", "causes"))
R, P, C = {r["id"]: r for r in R}, {p["part_no"]: p for p in P}, {c["id"]: c["description"] for c in C}
rnd = random.Random(7)
items = []
pool = [e for e in E if any(R[c["resolution_id"]]["parts"] for c in e["causes"]) and len(e["causes"]) > 1]
rnd.shuffle(pool)
for n in range(50):
    e = pool[n % len(pool)]
    rows = []
    for c in e["causes"]:
        r = R[c["resolution_id"]]
        parts = "; ".join(f"{p} INR {P[p]['price_inr']}" for p in r["parts"]) or "none"
        rows.append(f"Cause: {C[c['cause_id']]} ({round(c['likelihood'] * 100)}% of cases). Action: {r['steps']} "
                    f"Done by: {r['performed_by']}. Typical time: {r['est_minutes']} min. Parts: {parts}.")
    ev = f"{e['family']} {e['code']} {e['title']} (display {e['display_text']}). Severity: {e['severity']}. " + " ".join(rows)
    c = next(c for c in e["causes"] if R[c["resolution_id"]]["parts"])
    r = R[c["resolution_id"]]
    part, pct, mins, who = r["parts"][0], round(c["likelihood"] * 100), r["est_minutes"], r["performed_by"]
    cause, price = C[c["cause_id"]], P[part]["price_inr"]
    other = rnd.choice([p for p in P if p not in ev])
    good = [f"{e['code']} means {e['title'].lower()}. One cause, in about {pct}% of cases, is: {cause}. The part is {part} (INR {price}).",
            f"For {e['code']}, a {who} can fix '{cause}' in about {mins} minutes; the part listed is {part}.",
            f"The severity of {e['code']} is {e['severity']}. If the problem is '{cause}', you would need {part} at INR {price}."]
    bad = [(f"For {e['code']}, '{cause}' is fixed with part {other}.", "wrong part"),
           (f"For {e['code']}, '{cause}' accounts for about {pct + 17}% of cases.", "wrong percentage"),
           (f"Part {part} costs INR {price + 1500}.", "wrong price"),
           (f"Fixing '{cause}' is typically done in {mins + 45} minutes.", "wrong duration"),
           (f"'{cause}' is fixed by the {'technician' if who == 'customer' else 'customer'}, with no need for anyone else.", "wrong person"),
           (f"For {e['code']}, replace {part} (INR {price}). It is covered free of charge under warranty.", "invented claim"),
           (f"{e['code']} can also be caused by lightning damage to the display. The part is {part}.", "invented cause")]
    ok = n % 2 == 0
    text, edit = (good[(n // 2) % 3], "none") if ok else bad[(n // 2) % len(bad)]
    items.append({"id": f"J-{n + 1:02}", "question": f"What should I do about {e['code']} on a {e['family']} machine?", "evidence": ev,
                  "answer": text, "edit": edit, "constructed_label": "supported" if ok else "unsupported", "human_label": None})
Path(__file__).with_name("judge_50.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
