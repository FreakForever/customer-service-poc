# Hot air dryer — operation and troubleshooting manual (demo)

Removes surface moisture from non-hygroscopic resins (PP, PE, PS) using heated ambient air.

## Models

| Model | blower_kw | heater_kw | hopper_kg | max_temp_c |
|---|---|---|---|---|
| HAD-25 | 0.18 | 2.5 | 25 | 150 |
| HAD-50 | 0.25 | 3.5 | 50 | 150 |
| HAD-100 | 0.37 | 6 | 100 | 150 |

## Preventive maintenance

- Weekly: clean the inlet filter and hopper discharge.
- Monthly: check heater current and thermocouple.

## Error codes and troubleshooting

Codes are shown on the controller display. Causes are listed from most to least likely.

### E1 — Over-temperature (display: `OH`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Heater solid-state relay failed (shorted or open)** (component: Solid-state relay (heater); about 60% of cases). Action: Check SSR input/output with meter; replace SSR and verify heat-sink paste. Done by: technician. Typical time: 30 min. Parts: MT-HAD-SSR (Solid-state relay 40A (Hot air dryer)).
2. **Temperature sensor drifted out of calibration** (component: Thermocouple / RTD sensor; about 40% of cases). Action: Compare against reference thermometer; recalibrate offset or replace sensor. Done by: technician. Typical time: 30 min. Parts: MT-HAD-TC (Thermocouple type J / RTD PT100 (Hot air dryer)).

### E2 — Sensor open (display: `SE`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Temperature sensor open or loose connection** (component: Thermocouple / RTD sensor; about 100% of cases). Action: Re-seat sensor connector; if reading still open, replace thermocouple. Done by: technician. Typical time: 20 min. Parts: MT-HAD-TC (Thermocouple type J / RTD PT100 (Hot air dryer)).

### E3 — Heater not heating (display: `HE`)

Severity: **warning**.

1. **Heater element open circuit** (component: Process heater element; about 60% of cases). Action: Isolate power, measure heater resistance; replace heater element if open. Done by: technician. Typical time: 60 min. Parts: MT-HAD-HTR (Heater element (Hot air dryer)).
2. **Heater solid-state relay failed (shorted or open)** (component: Solid-state relay (heater); about 40% of cases). Action: Check SSR input/output with meter; replace SSR and verify heat-sink paste. Done by: technician. Typical time: 30 min. Parts: MT-HAD-SSR (Solid-state relay 40A (Hot air dryer)).

### E4 — Blower overload (display: `BL`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Blower motor overload (thermal trip)** (component: Process air blower; about 60% of cases). Action: Reset overload after cooling; check filter and duct for blockage; check motor current; replace blower if current high. Done by: technician. Typical time: 40 min. Parts: MT-HAD-BLW (Blower assembly (Hot air dryer)).
2. **Air filter clogged with fines or dust** (component: Air / dust filter; about 40% of cases). Action: Stop blower, remove filter, clean with compressed air from clean side or replace if torn. Done by: customer. Typical time: 10 min. Parts: MT-HAD-FLT (Filter element (Hot air dryer)).

### E5 — Hopper low material (display: `LO`)

Severity: **info**.

1. **Material bridging in hopper or source bin empty** (component: Material level / proximity sensor; about 60% of cases). Action: Refill source bin; agitate hopper; consider anti-bridging insert. Done by: customer. Typical time: 10 min.
2. **Level sensor dirty or mis-adjusted** (component: Material level / proximity sensor; about 40% of cases). Action: Clean sensor face; adjust sensitivity screw until LED switches with material. Done by: customer. Typical time: 10 min.

## Safety

Isolate and lock out power before opening panels. See *General safety instructions*.
