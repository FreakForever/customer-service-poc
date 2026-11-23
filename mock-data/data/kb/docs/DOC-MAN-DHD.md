# Dehumidifying dryer — operation and troubleshooting manual (demo)

Dries hygroscopic resins (PA, PC, PET, PBT, ABS) with low-dew-point air from a desiccant rotor.

## Models

| Model | bins | blower_kw | dew_point_c | hopper_l | process_heater_kw | regen_heater_kw | throughput_kg_h |
|---|---|---|---|---|---|---|---|
| DHD-50 | - | 0.75 | -40 | 50 | 3 | 2.5 | 25 |
| DHD-100 | - | 1.1 | -40 | 100 | 4.5 | 3 | 50 |
| DHD-150 | - | 1.5 | -40 | 150 | 6 | 4 | 75 |
| DHD-300M | 3 | 2.2 | -40 | 300 | 9 | 6 | 150 |

## Preventive maintenance

- Weekly: clean the process filter with compressed air from the clean side.
- Monthly: check regeneration filter and verify dew point reading against a portable meter.
- Yearly: calibrate dew point sensor; inspect desiccant rotor and drive belt.
- Every 3–5 years: replace desiccant rotor if dew point stays above -20 C.

## Error codes and troubleshooting

Codes are shown on the controller display. Causes are listed from most to least likely.

### E01 — Process temperature low (display: `PROC TEMP LO`)

Severity: **warning**.

1. **Heater element open circuit** (component: Process heater element; about 50% of cases). Action: Isolate power, measure heater resistance; replace heater element if open. Done by: technician. Typical time: 60 min. Parts: MT-DHD-HTR (Heater element (Dehumidifying dryer)).
2. **Heater solid-state relay failed (shorted or open)** (component: Solid-state relay (heater); about 30% of cases). Action: Check SSR input/output with meter; replace SSR and verify heat-sink paste. Done by: technician. Typical time: 30 min. Parts: MT-DHD-SSR (Solid-state relay 40A (Dehumidifying dryer)).
3. **Temperature sensor drifted out of calibration** (component: Thermocouple / RTD sensor; about 20% of cases). Action: Compare against reference thermometer; recalibrate offset or replace sensor. Done by: technician. Typical time: 30 min. Parts: MT-DHD-TC (Thermocouple type J / RTD PT100 (Dehumidifying dryer)).

### E02 — Process over-temperature (display: `PROC OVER TEMP`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Heater solid-state relay failed (shorted or open)** (component: Solid-state relay (heater); about 55% of cases). Action: Check SSR input/output with meter; replace SSR and verify heat-sink paste. Done by: technician. Typical time: 30 min. Parts: MT-DHD-SSR (Solid-state relay 40A (Dehumidifying dryer)).
2. **Temperature sensor drifted out of calibration** (component: Thermocouple / RTD sensor; about 25% of cases). Action: Compare against reference thermometer; recalibrate offset or replace sensor. Done by: technician. Typical time: 30 min. Parts: MT-DHD-TC (Thermocouple type J / RTD PT100 (Dehumidifying dryer)).
3. **Setpoint or recipe entered incorrectly** (component: Controller board / PLC; about 20% of cases). Action: Check setpoint, material recipe and units on the HMI; restore recommended values. Done by: customer. Typical time: 5 min.

### E03 — Regeneration temperature low (display: `REGEN TEMP LO`)

Severity: **warning**.

1. **Regeneration heater open circuit** (component: Regeneration heater; about 60% of cases). Action: Isolate power, check regeneration heater resistance; replace if open. Done by: technician. Typical time: 75 min. Parts: MT-DHD-RHTR (Regeneration heater (Dehumidifying dryer)).
2. **Heater solid-state relay failed (shorted or open)** (component: Solid-state relay (heater); about 25% of cases). Action: Check SSR input/output with meter; replace SSR and verify heat-sink paste. Done by: technician. Typical time: 30 min. Parts: MT-DHD-SSR (Solid-state relay 40A (Dehumidifying dryer)).
3. **Temperature sensor open or loose connection** (component: Thermocouple / RTD sensor; about 15% of cases). Action: Re-seat sensor connector; if reading still open, replace thermocouple. Done by: technician. Typical time: 20 min. Parts: MT-DHD-TC (Thermocouple type J / RTD PT100 (Dehumidifying dryer)).

### E04 — Dew point high (display: `DEW PT HIGH`)

Severity: **warning**.

1. **Desiccant rotor degraded or contaminated** (component: Desiccant rotor; about 40% of cases). Action: Inspect rotor for discoloration or oil contamination; replace desiccant rotor. Done by: technician. Typical time: 120 min. Parts: MT-DHD-ROTOR (Desiccant rotor assembly (Dehumidifying dryer)).
2. **Regeneration heater open circuit** (component: Regeneration heater; about 25% of cases). Action: Isolate power, check regeneration heater resistance; replace if open. Done by: technician. Typical time: 75 min. Parts: MT-DHD-RHTR (Regeneration heater (Dehumidifying dryer)).
3. **Air filter clogged with fines or dust** (component: Air / dust filter; about 20% of cases). Action: Stop blower, remove filter, clean with compressed air from clean side or replace if torn. Done by: customer. Typical time: 10 min. Parts: MT-DHD-FLT (Filter element (Dehumidifying dryer)).
4. **Dew point sensor faulty or due for calibration** (component: Dew point sensor; about 15% of cases). Action: Verify against portable dew point meter; recalibrate or replace dew point sensor. Done by: technician. Typical time: 45 min. Parts: MT-DHD-DPS (Dew point sensor (Dehumidifying dryer)).

### E05 — Blower overload (display: `BLOWER OL`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Blower motor overload (thermal trip)** (component: Process air blower; about 60% of cases). Action: Reset overload after cooling; check filter and duct for blockage; check motor current; replace blower if current high. Done by: technician. Typical time: 40 min. Parts: MT-DHD-BLW (Blower assembly (Dehumidifying dryer)).
2. **Air filter clogged with fines or dust** (component: Air / dust filter; about 30% of cases). Action: Stop blower, remove filter, clean with compressed air from clean side or replace if torn. Done by: customer. Typical time: 10 min. Parts: MT-DHD-FLT (Filter element (Dehumidifying dryer)).
3. **Loss of one supply phase** (component: Phase protection relay; about 10% of cases). Action: Check incoming fuses/MCB on all three phases; restore supply before restart. Done by: customer. Typical time: 15 min.

### E06 — Filter clogged (display: `FILTER`)

Severity: **info**.

1. **Air filter clogged with fines or dust** (component: Air / dust filter; about 90% of cases). Action: Stop blower, remove filter, clean with compressed air from clean side or replace if torn. Done by: customer. Typical time: 10 min. Parts: MT-DHD-FLT (Filter element (Dehumidifying dryer)).
2. **Level sensor dirty or mis-adjusted** (component: Material level / proximity sensor; about 10% of cases). Action: Clean sensor face; adjust sensitivity screw until LED switches with material. Done by: customer. Typical time: 10 min.

### E07 — Sensor open (display: `SENSOR OPEN`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Temperature sensor open or loose connection** (component: Thermocouple / RTD sensor; about 85% of cases). Action: Re-seat sensor connector; if reading still open, replace thermocouple. Done by: technician. Typical time: 20 min. Parts: MT-DHD-TC (Thermocouple type J / RTD PT100 (Dehumidifying dryer)).
2. **Controller communication fault or corrupted parameters** (component: Controller board / PLC; about 15% of cases). Action: Power-cycle unit; reload default parameters; if fault persists replace controller board. Done by: technician. Typical time: 60 min. Parts: MT-DHD-PLC (Controller board (Dehumidifying dryer)).

### E08 — Phase fault (display: `PHASE`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Supply phase sequence reversed** (component: Phase protection relay; about 60% of cases). Action: Swap any two incoming phases at the main terminal (qualified electrician only). Done by: technician. Typical time: 20 min.
2. **Loss of one supply phase** (component: Phase protection relay; about 40% of cases). Action: Check incoming fuses/MCB on all three phases; restore supply before restart. Done by: customer. Typical time: 15 min.

### E10 — Hopper low material (display: `HOPPER LOW`)

Severity: **info**.

1. **Material bridging in hopper or source bin empty** (component: Material level / proximity sensor; about 70% of cases). Action: Refill source bin; agitate hopper; consider anti-bridging insert. Done by: customer. Typical time: 10 min.
2. **Level sensor dirty or mis-adjusted** (component: Material level / proximity sensor; about 30% of cases). Action: Clean sensor face; adjust sensitivity screw until LED switches with material. Done by: customer. Typical time: 10 min.

## Safety

Isolate and lock out power before opening panels. See *General safety instructions*.
