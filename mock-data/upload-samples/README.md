# Runtime upload samples

Use these to demonstrate dynamic knowledge updates during a live demo (KB admin -> Upload):

| File | Upload type | What should change |
|---|---|---|
| SB-2026-12_DHD_rotor_revision.md | document | New chunks + KG entities (E11, MT-DHD-ROTOR-R2, belts, motor); answers to eval Q 'requires_upload' |
| new_error_codes.csv | structured: error_codes | New ErrorCode nodes G5 and L7 wired to existing causes |
| faq_additions.csv | structured: faqs | New FAQ cache entries (invalidates semantic cache for similar questions) |

Before upload, the assistant must answer "I don't have information on that" for these questions (hallucination check).
