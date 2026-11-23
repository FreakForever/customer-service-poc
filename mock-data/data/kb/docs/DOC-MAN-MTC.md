# Mold temperature controller — operation and troubleshooting manual (demo)

Circulates temperature-controlled water or oil through the mold to hold a stable mold surface temperature.

## Models

| Model | flow_lpm | heater_kw | max_temp_c | medium | power | pump_kw |
|---|---|---|---|---|---|---|
| MTC-W60 | 30 | 6 | 95 | water | 415V 3ph 50Hz | 0.4 |
| MTC-W90 | 45 | 9 | 95 | water | 415V 3ph 50Hz | 0.75 |
| MTC-W120HP | 60 | 12 | 120 | pressurised water | 415V 3ph 50Hz | 1.1 |
| MTC-O150 | 40 | 9 | 150 | thermal oil | 415V 3ph 50Hz | 0.75 |

## Preventive maintenance

- Weekly: check water level, strainer and hose connections for leaks.
- Monthly: clean the Y-strainer; check pump current.
- Every 6–12 months (3 months with hard water): descale unit and mold channels.

## Error codes and troubleshooting

Codes are shown on the controller display. Causes are listed from most to least likely.

### E01 — Low water level (display: `LO LEVEL`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Low water level in tank or no make-up water** (component: Level switch / float; about 70% of cases). Action: Open make-up water valve, check float; top up tank to the marked level. Done by: customer. Typical time: 10 min.
2. **Flow switch stuck or wiring fault** (component: Flow switch; about 20% of cases). Action: Check flow switch paddle and wiring; replace flow switch. Done by: technician. Typical time: 30 min. Parts: MT-MTC-FSW (Flow switch (Mold temperature controller)).
3. **Solenoid valve stuck** (component: Solenoid valve; about 10% of cases). Action: Check coil voltage; clean or replace solenoid valve. Done by: technician. Typical time: 30 min. Parts: MT-MTC-SOL (Solenoid valve (Mold temperature controller)).

### E02 — Over-temperature (display: `OVER TEMP`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Heater solid-state relay failed (shorted or open)** (component: Solid-state relay (heater); about 50% of cases). Action: Check SSR input/output with meter; replace SSR and verify heat-sink paste. Done by: technician. Typical time: 30 min. Parts: MT-MTC-SSR (Solid-state relay 40A (Mold temperature controller)).
2. **Temperature sensor drifted out of calibration** (component: Thermocouple / RTD sensor; about 30% of cases). Action: Compare against reference thermometer; recalibrate offset or replace sensor. Done by: technician. Typical time: 30 min. Parts: MT-MTC-TC (Thermocouple type J / RTD PT100 (Mold temperature controller)).
3. **Setpoint or recipe entered incorrectly** (component: Controller board / PLC; about 20% of cases). Action: Check setpoint, material recipe and units on the HMI; restore recommended values. Done by: customer. Typical time: 5 min.

### E03 — Temperature sensor open (display: `SENSOR OPEN`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Temperature sensor open or loose connection** (component: Thermocouple / RTD sensor; about 90% of cases). Action: Re-seat sensor connector; if reading still open, replace thermocouple. Done by: technician. Typical time: 20 min. Parts: MT-MTC-TC (Thermocouple type J / RTD PT100 (Mold temperature controller)).
2. **Controller communication fault or corrupted parameters** (component: Controller board / PLC; about 10% of cases). Action: Power-cycle unit; reload default parameters; if fault persists replace controller board. Done by: technician. Typical time: 60 min. Parts: MT-MTC-PLC (Controller board (Mold temperature controller)).

### E04 — Pump overload (display: `PUMP OL`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Pump seized or impeller jammed** (component: Circulation pump; about 50% of cases). Action: Isolate power, rotate shaft manually; clear debris or replace pump seal kit / pump. Done by: technician. Typical time: 90 min. Parts: MT-MTC-PUMP (Circulation pump (Mold temperature controller)), MT-MTC-SEALKIT (Pump mechanical seal kit (Mold temperature controller)).
2. **Scale build-up in mold channels or heat exchanger** (component: Circulation pump; about 35% of cases). Action: Descale mold channels and unit with a descaling pump and approved descaling agent; fit water treatment. Done by: customer. Typical time: 120 min. Parts: MT-MTC-DESCALER (Descaling agent (20 L) (Mold temperature controller)).
3. **Loss of one supply phase** (component: Phase protection relay; about 15% of cases). Action: Check incoming fuses/MCB on all three phases; restore supply before restart. Done by: customer. Typical time: 15 min.

### E05 — Slow heat-up (display: `HEAT SLOW`)

Severity: **warning**.

1. **Heater element open circuit** (component: Process heater element; about 50% of cases). Action: Isolate power, measure heater resistance; replace heater element if open. Done by: technician. Typical time: 60 min. Parts: MT-MTC-HTR (Heater element (Mold temperature controller)).
2. **Heater solid-state relay failed (shorted or open)** (component: Solid-state relay (heater); about 30% of cases). Action: Check SSR input/output with meter; replace SSR and verify heat-sink paste. Done by: technician. Typical time: 30 min. Parts: MT-MTC-SSR (Solid-state relay 40A (Mold temperature controller)).
3. **Scale build-up in mold channels or heat exchanger** (component: Circulation pump; about 20% of cases). Action: Descale mold channels and unit with a descaling pump and approved descaling agent; fit water treatment. Done by: customer. Typical time: 120 min. Parts: MT-MTC-DESCALER (Descaling agent (20 L) (Mold temperature controller)).

### E06 — Low flow (display: `LOW FLOW`)

Severity: **warning**.

1. **Scale build-up in mold channels or heat exchanger** (component: Circulation pump; about 60% of cases). Action: Descale mold channels and unit with a descaling pump and approved descaling agent; fit water treatment. Done by: customer. Typical time: 120 min. Parts: MT-MTC-DESCALER (Descaling agent (20 L) (Mold temperature controller)).
2. **Flow switch stuck or wiring fault** (component: Flow switch; about 25% of cases). Action: Check flow switch paddle and wiring; replace flow switch. Done by: technician. Typical time: 30 min. Parts: MT-MTC-FSW (Flow switch (Mold temperature controller)).
3. **Pump seized or impeller jammed** (component: Circulation pump; about 15% of cases). Action: Isolate power, rotate shaft manually; clear debris or replace pump seal kit / pump. Done by: technician. Typical time: 90 min. Parts: MT-MTC-PUMP (Circulation pump (Mold temperature controller)), MT-MTC-SEALKIT (Pump mechanical seal kit (Mold temperature controller)).

### E07 — Phase fault (display: `PHASE`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Supply phase sequence reversed** (component: Phase protection relay; about 60% of cases). Action: Swap any two incoming phases at the main terminal (qualified electrician only). Done by: technician. Typical time: 20 min.
2. **Loss of one supply phase** (component: Phase protection relay; about 40% of cases). Action: Check incoming fuses/MCB on all three phases; restore supply before restart. Done by: customer. Typical time: 15 min.

### E08 — High pressure (display: `HI PRESS`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Scale build-up in mold channels or heat exchanger** (component: Circulation pump; about 50% of cases). Action: Descale mold channels and unit with a descaling pump and approved descaling agent; fit water treatment. Done by: customer. Typical time: 120 min. Parts: MT-MTC-DESCALER (Descaling agent (20 L) (Mold temperature controller)).
2. **Solenoid valve stuck** (component: Solenoid valve; about 30% of cases). Action: Check coil voltage; clean or replace solenoid valve. Done by: technician. Typical time: 30 min. Parts: MT-MTC-SOL (Solenoid valve (Mold temperature controller)).
3. **Setpoint or recipe entered incorrectly** (component: Controller board / PLC; about 20% of cases). Action: Check setpoint, material recipe and units on the HMI; restore recommended values. Done by: customer. Typical time: 5 min.

## Safety

Isolate and lock out power before opening panels. See *General safety instructions*.
