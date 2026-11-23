# Recycling granulator — operation and troubleshooting manual (demo)

Beside-the-press and central granulators that regrind sprues, runners and rejects for reuse.

## Models

| Model | capacity_kg_h | motor_kw | rotor_width_mm | screen_mm |
|---|---|---|---|---|
| GR-200 | 30 | 2.2 | 200 | 6 |
| GR-400 | 120 | 7.5 | 400 | 8 |
| GR-600 | 300 | 15 | 600 | 10 |

## Preventive maintenance

- Daily: check for metal contamination at the feed.
- Weekly: clean screen and cutting chamber.
- Every 500 hours: check blade gap (0.2–0.3 mm), re-sharpen or replace knives; check belts.

## Error codes and troubleshooting

Codes are shown on the controller display. Causes are listed from most to least likely.

### G1 — Motor overload (display: `MOTOR OL`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Overfeeding the granulator** (component: Drive motor; about 40% of cases). Action: Reduce feed rate; feed parts gradually; let motor cool before restart. Done by: customer. Typical time: 5 min.
2. **Blades dull or blade gap too wide** (component: Rotor and bed knives; about 35% of cases). Action: Re-sharpen or replace rotor and bed knives; reset blade gap to 0.2-0.3 mm. Done by: technician. Typical time: 120 min. Parts: MT-RCY-BLADESET (Rotor and bed knife set (Recycling granulator)).
3. **Drive motor overload** (component: Drive motor; about 25% of cases). Action: Check motor current and belts; reset overload; replace motor bearings if noisy. Done by: technician. Typical time: 90 min. Parts: MT-RCY-BELT (Drive belt set (Recycling granulator)).

### G2 — Safety interlock open (display: `INTERLOCK`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Safety interlock open (hopper or screen cradle not locked)** (component: Safety interlock switch; about 100% of cases). Action: Close and lock hopper and screen cradle until interlock clicks; never bypass interlocks. Done by: customer. Typical time: 5 min.

### G3 — Low output / fines (display: `LOW OUTPUT`)

Severity: **warning**.

1. **Blades dull or blade gap too wide** (component: Rotor and bed knives; about 60% of cases). Action: Re-sharpen or replace rotor and bed knives; reset blade gap to 0.2-0.3 mm. Done by: technician. Typical time: 120 min. Parts: MT-RCY-BLADESET (Rotor and bed knife set (Recycling granulator)).
2. **Screen clogged** (component: Screen; about 40% of cases). Action: Isolate and lock out, open cradle and clean screen; replace if holes are damaged. Done by: customer. Typical time: 20 min. Parts: MT-RCY-SCREEN (Granulator screen (Recycling granulator)).

### G4 — Phase fault (display: `PHASE`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Supply phase sequence reversed** (component: Phase protection relay; about 60% of cases). Action: Swap any two incoming phases at the main terminal (qualified electrician only). Done by: technician. Typical time: 20 min.
2. **Loss of one supply phase** (component: Phase protection relay; about 40% of cases). Action: Check incoming fuses/MCB on all three phases; restore supply before restart. Done by: customer. Typical time: 15 min.

## Safety

Isolate and lock out power before opening panels. See *General safety instructions*.
