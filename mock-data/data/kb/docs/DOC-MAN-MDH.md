# Mold dehumidifier — operation and troubleshooting manual (demo)

Blankets the mold area with dry air to stop condensation when molds run with chilled water.

## Models

| Model | airflow_m3_h | compressor_kw | dew_point_c |
|---|---|---|---|
| MDH-500 | 500 | 1.1 | -5 |
| MDH-1000 | 1000 | 2.2 | -5 |

## Preventive maintenance

- Weekly: clean the air filter.
- Monthly: clean condenser coil; check drain line.

## Error codes and troubleshooting

Codes are shown on the controller display. Causes are listed from most to least likely.

### E01 — Dew point not reached (display: `DEW PT`)

Severity: **warning**.

1. **Condenser fins dirty or blocked airflow** (component: Condenser; about 35% of cases). Action: Clean condenser fins with low-pressure air or water; ensure 1 m clearance around unit. Done by: customer. Typical time: 30 min.
2. **Air filter clogged with fines or dust** (component: Air / dust filter; about 35% of cases). Action: Stop blower, remove filter, clean with compressed air from clean side or replace if torn. Done by: customer. Typical time: 10 min. Parts: MT-MDH-FLT (Filter element (Mold dehumidifier)).
3. **Refrigerant leak (low suction pressure)** (component: Refrigeration compressor; about 30% of cases). Action: Leak test with detector, repair joint, vacuum and recharge refrigerant per nameplate. Done by: technician. Typical time: 240 min. Parts: MT-MDH-REFKIT (Refrigerant recharge kit (Mold dehumidifier)).

### E02 — Coil frosting (display: `FROST`)

Severity: **warning**.

1. **Evaporator freezing due to low setpoint or low flow** (component: Evaporator / cooling coil; about 70% of cases). Action: Raise setpoint above 7 C, check flow; add glycol for sub-10 C operation. Done by: customer. Typical time: 30 min.
2. **Air filter clogged with fines or dust** (component: Air / dust filter; about 30% of cases). Action: Stop blower, remove filter, clean with compressed air from clean side or replace if torn. Done by: customer. Typical time: 10 min. Parts: MT-MDH-FLT (Filter element (Mold dehumidifier)).

### E03 — Compressor overload (display: `COMP OL`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Compressor overload or locked rotor** (component: Refrigeration compressor; about 60% of cases). Action: Check supply voltage and compressor current; reset overload; replace contactor or compressor if faulty. Done by: technician. Typical time: 180 min. Parts: MT-MDH-CONTACTOR (Compressor contactor (Mold dehumidifier)).
2. **Ambient temperature above rated 45 C** (component: Condenser; about 40% of cases). Action: Improve ventilation or relocate unit; reduce load until ambient is within rating. Done by: customer. Typical time: 30 min.

### E04 — Dew point sensor fault (display: `DPS`)

Severity: **warning**.

1. **Dew point sensor faulty or due for calibration** (component: Dew point sensor; about 100% of cases). Action: Verify against portable dew point meter; recalibrate or replace dew point sensor. Done by: technician. Typical time: 45 min. Parts: MT-MDH-DPS (Dew point sensor (Mold dehumidifier)).

## Safety

Isolate and lock out power before opening panels. See *General safety instructions*.
