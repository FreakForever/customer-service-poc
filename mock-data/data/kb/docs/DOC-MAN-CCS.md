# Conveying system — operation and troubleshooting manual (demo)

Vacuum loaders and centralized conveying that move resin from storage or dryers to machines.

## Models

| Model | blower_kw | capacity_kg_h | controller | max_distance_m | motor | power | stations |
|---|---|---|---|---|---|---|---|
| VL-1 | - | 150 | - | 4 | 1ph carbon-brush | 230V 1ph | - |
| VL-3 | 2.2 | 400 | - | 15 | - | 415V 3ph | - |
| CCS-8 | 5.5 | 900 | PLC with HMI | 60 | - | - | 8 |

## Preventive maintenance

- Daily: empty loader dust bins.
- Weekly: clean loader filters and check lid gaskets.
- Monthly: inspect hoses and couplings for wear; check blower filter.

## Error codes and troubleshooting

Codes are shown on the controller display. Causes are listed from most to least likely.

### L1 — No material conveyed (alarm after 3 cycles) (display: `NO MAT`)

Severity: **warning**.

1. **Material bridging in hopper or source bin empty** (component: Material level / proximity sensor; about 40% of cases). Action: Refill source bin; agitate hopper; consider anti-bridging insert. Done by: customer. Typical time: 10 min.
2. **Vacuum leak at hose, lid gasket or coupling** (component: Conveying hose and couplings; about 30% of cases). Action: Check hose clamps, lid gasket and couplings; replace gasket or hose if cracked. Done by: customer. Typical time: 15 min. Parts: MT-CCS-GASKET (Loader lid gasket (Conveying system)), MT-CCS-HOSE (Conveying hose 38 mm (per 5 m) (Conveying system)).
3. **Conveying hose blocked by lumps or long strands** (component: Conveying hose and couplings; about 30% of cases). Action: Disconnect hose and clear blockage; fit lump screen at pick-up. Done by: customer. Typical time: 15 min.

### L2 — Filter clogged (display: `FILTER`)

Severity: **warning**.

1. **Air filter clogged with fines or dust** (component: Air / dust filter; about 100% of cases). Action: Stop blower, remove filter, clean with compressed air from clean side or replace if torn. Done by: customer. Typical time: 10 min. Parts: MT-CCS-FLT (Filter element (Conveying system)).

### L3 — Blower / motor overload (display: `MOTOR OL`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Blower motor overload (thermal trip)** (component: Process air blower; about 60% of cases). Action: Reset overload after cooling; check filter and duct for blockage; check motor current; replace blower if current high. Done by: technician. Typical time: 40 min. Parts: MT-CCS-BLW (Blower assembly (Conveying system)).
2. **Air filter clogged with fines or dust** (component: Air / dust filter; about 40% of cases). Action: Stop blower, remove filter, clean with compressed air from clean side or replace if torn. Done by: customer. Typical time: 10 min. Parts: MT-CCS-FLT (Filter element (Conveying system)).

### L4 — Station valve fault (display: `VALVE`)

Severity: **warning**.

1. **Solenoid valve stuck** (component: Solenoid valve; about 80% of cases). Action: Check coil voltage; clean or replace solenoid valve. Done by: technician. Typical time: 30 min. Parts: MT-CCS-SOL (Solenoid valve (Conveying system)).
2. **Controller communication fault or corrupted parameters** (component: Controller board / PLC; about 20% of cases). Action: Power-cycle unit; reload default parameters; if fault persists replace controller board. Done by: technician. Typical time: 60 min. Parts: MT-CCS-PLC (Controller board (Conveying system)).

### L5 — Level sensor fault (display: `SENSOR`)

Severity: **info**.

1. **Level sensor dirty or mis-adjusted** (component: Material level / proximity sensor; about 100% of cases). Action: Clean sensor face; adjust sensitivity screw until LED switches with material. Done by: customer. Typical time: 10 min.

### L6 — Phase fault (display: `PHASE`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Supply phase sequence reversed** (component: Phase protection relay; about 60% of cases). Action: Swap any two incoming phases at the main terminal (qualified electrician only). Done by: technician. Typical time: 20 min.
2. **Loss of one supply phase** (component: Phase protection relay; about 40% of cases). Action: Check incoming fuses/MCB on all three phases; restore supply before restart. Done by: customer. Typical time: 15 min.

## Safety

Isolate and lock out power before opening panels. See *General safety instructions*.
