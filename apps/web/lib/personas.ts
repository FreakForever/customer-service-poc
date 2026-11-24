/** Seed companies offered in the demo bar; ids match mock-data (tests/personas.test.ts). Synthetic (DR-03). */
export const PERSONAS = [
  { id: "CUST-0001", company: "Aarav Polymers" },
  { id: "CUST-0002", company: "Kaveri Packaging" },
  { id: "CUST-0003", company: "Orion Auto Components" },
  { id: "CUST-0004", company: "Lotus Medi-Plast" },
  { id: "CUST-0005", company: "Trident Industries" },
  { id: "CUST-0006", company: "Deccan Technoplast" },
  { id: "CUST-0008", company: "Navkar Precision Plastics" },
] as const;
export const PERSONA_COOKIE = "mcs_persona";

export function validPersona(v: string | undefined): string {
  return PERSONAS.some((p) => p.id === v) ? v! : "";
}
