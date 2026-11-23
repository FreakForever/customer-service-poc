# Chiller — operation and troubleshooting manual (demo)

Supplies chilled water to molds and machine oil coolers using a refrigeration circuit.

## Models

| Model | compressor | condenser | cooling_tr | outlet_temp_range_c | refrigerant | tank_l |
|---|---|---|---|---|---|---|
| CHL-3A | scroll | air-cooled | 3 | 7-25 | R407C | 60 |
| CHL-5A | scroll | air-cooled | 5 | 7-25 | R407C | 100 |
| CHL-10W | scroll | water-cooled | 10 | 7-25 | R410A | 200 |
| CHL-20W | 2x scroll | water-cooled | 20 | 7-25 | R410A | 300 |

## Preventive maintenance

- Weekly: check tank level and outlet temperature stability.
- Monthly: clean condenser fins (air-cooled) or check cooling-tower flow (water-cooled).
- Yearly: check refrigerant pressures and compressor current; flush tank.

## Error codes and troubleshooting

Codes are shown on the controller display. Causes are listed from most to least likely.

### A01 — High refrigerant pressure (display: `HP TRIP`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Condenser fins dirty or blocked airflow** (component: Condenser; about 40% of cases). Action: Clean condenser fins with low-pressure air or water; ensure 1 m clearance around unit. Done by: customer. Typical time: 30 min.
2. **Insufficient cooling-tower water to condenser** (component: Condenser; about 30% of cases). Action: Check cooling tower pump, strainer and valves; ensure rated condenser water flow. Done by: customer. Typical time: 30 min.
3. **Ambient temperature above rated 45 C** (component: Condenser; about 20% of cases). Action: Improve ventilation or relocate unit; reduce load until ambient is within rating. Done by: customer. Typical time: 30 min.
4. **High-pressure switch tripped** (component: Pressure switch (HP/LP); about 10% of cases). Action: Find root cause (dirty condenser / low water flow) before resetting HP switch. Done by: technician. Typical time: 30 min. Parts: MT-CHL-HPSW (High-pressure switch (Chiller)).

### A02 — Low refrigerant pressure (display: `LP TRIP`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Refrigerant leak (low suction pressure)** (component: Refrigeration compressor; about 70% of cases). Action: Leak test with detector, repair joint, vacuum and recharge refrigerant per nameplate. Done by: technician. Typical time: 240 min. Parts: MT-CHL-REFKIT (Refrigerant recharge kit (Chiller)).
2. **Evaporator freezing due to low setpoint or low flow** (component: Evaporator / cooling coil; about 30% of cases). Action: Raise setpoint above 7 C, check flow; add glycol for sub-10 C operation. Done by: customer. Typical time: 30 min.

### A03 — Compressor overload (display: `COMP OL`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Compressor overload or locked rotor** (component: Refrigeration compressor; about 50% of cases). Action: Check supply voltage and compressor current; reset overload; replace contactor or compressor if faulty. Done by: technician. Typical time: 180 min. Parts: MT-CHL-CONTACTOR (Compressor contactor (Chiller)).
2. **Loss of one supply phase** (component: Phase protection relay; about 30% of cases). Action: Check incoming fuses/MCB on all three phases; restore supply before restart. Done by: customer. Typical time: 15 min.
3. **Condenser fins dirty or blocked airflow** (component: Condenser; about 20% of cases). Action: Clean condenser fins with low-pressure air or water; ensure 1 m clearance around unit. Done by: customer. Typical time: 30 min.

### A04 — Anti-freeze protection (display: `FREEZE`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Evaporator freezing due to low setpoint or low flow** (component: Evaporator / cooling coil; about 70% of cases). Action: Raise setpoint above 7 C, check flow; add glycol for sub-10 C operation. Done by: customer. Typical time: 30 min.
2. **Flow switch stuck or wiring fault** (component: Flow switch; about 30% of cases). Action: Check flow switch paddle and wiring; replace flow switch. Done by: technician. Typical time: 30 min. Parts: MT-CHL-FSW (Flow switch (Chiller)).

### A05 — No water flow (display: `FLOW`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Pump seized or impeller jammed** (component: Circulation pump; about 35% of cases). Action: Isolate power, rotate shaft manually; clear debris or replace pump seal kit / pump. Done by: technician. Typical time: 90 min. Parts: MT-CHL-PUMP (Circulation pump (Chiller)), MT-CHL-SEALKIT (Pump mechanical seal kit (Chiller)).
2. **Flow switch stuck or wiring fault** (component: Flow switch; about 35% of cases). Action: Check flow switch paddle and wiring; replace flow switch. Done by: technician. Typical time: 30 min. Parts: MT-CHL-FSW (Flow switch (Chiller)).
3. **Low water level in tank or no make-up water** (component: Level switch / float; about 30% of cases). Action: Open make-up water valve, check float; top up tank to the marked level. Done by: customer. Typical time: 10 min.

### A06 — Tank level low (display: `LEVEL`)

Severity: **warning**.

1. **Low water level in tank or no make-up water** (component: Level switch / float; about 80% of cases). Action: Open make-up water valve, check float; top up tank to the marked level. Done by: customer. Typical time: 10 min.
2. **Solenoid valve stuck** (component: Solenoid valve; about 20% of cases). Action: Check coil voltage; clean or replace solenoid valve. Done by: technician. Typical time: 30 min. Parts: MT-CHL-SOL (Solenoid valve (Chiller)).

### A07 — Phase fault (display: `PHASE`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Supply phase sequence reversed** (component: Phase protection relay; about 60% of cases). Action: Swap any two incoming phases at the main terminal (qualified electrician only). Done by: technician. Typical time: 20 min.
2. **Loss of one supply phase** (component: Phase protection relay; about 40% of cases). Action: Check incoming fuses/MCB on all three phases; restore supply before restart. Done by: customer. Typical time: 15 min.

### A08 — Outlet temperature sensor fault (display: `SENSOR`)

Severity: **critical**. The machine stops automatically; do not restart until the cause is fixed.

1. **Temperature sensor open or loose connection** (component: Thermocouple / RTD sensor; about 80% of cases). Action: Re-seat sensor connector; if reading still open, replace thermocouple. Done by: technician. Typical time: 20 min. Parts: MT-CHL-TC (Thermocouple type J / RTD PT100 (Chiller)).
2. **Controller communication fault or corrupted parameters** (component: Controller board / PLC; about 20% of cases). Action: Power-cycle unit; reload default parameters; if fault persists replace controller board. Done by: technician. Typical time: 60 min. Parts: MT-CHL-PLC (Controller board (Chiller)).

## Safety

Isolate and lock out power before opening panels. See *General safety instructions*.
