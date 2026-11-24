/** Product families (tests/families.test.ts compares with mock-data/catalog). */
export const FAMILIES = [
  { id: "MTC", name: "Mold temperature controller", summary: "Holds a stable mold surface temperature with water or oil." },
  { id: "DHD", name: "Dehumidifying dryer", summary: "Low-dew-point drying for hygroscopic resins." },
  { id: "HAD", name: "Hot air dryer", summary: "Removes surface moisture from PP, PE and PS." },
  { id: "CHL", name: "Chiller", summary: "Chilled water for molds and oil coolers." },
  { id: "MDH", name: "Mold dehumidifier", summary: "Dry air around the mold to stop condensation." },
  { id: "CCS", name: "Conveying system", summary: "Moves resin from storage or dryers to machines." },
  { id: "RCY", name: "Recycling granulator", summary: "Regrinds sprues, runners and rejects for reuse." },
] as const;
export const familyName = (id: string | null) => FAMILIES.find((f) => f.id === id)?.name;
export const CENTRES = ["Noida", "Ludhiana", "Pune", "Mumbai", "Ahmedabad", "Vadodara", "Chennai", "Bengaluru", "Coimbatore", "Hyderabad", "Kolkata"];
