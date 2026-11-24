export type Contact = { name: string; company: string; email: string; serial: string; subject: string; message: string };
export type Errors = Partial<Record<keyof Contact, string>>;

const LIMITS: Record<keyof Contact, number> = { name: 100, company: 120, email: 254, serial: 40, subject: 300, message: 15000 };
const REQUIRED: (keyof Contact)[] = ["name", "company", "email", "subject", "message"];

export function validate(c: Contact): Errors {
  const e: Errors = {};
  for (const k of REQUIRED) if (!c[k].trim()) e[k] = "This field is required";
  for (const k of Object.keys(LIMITS) as (keyof Contact)[]) if (c[k].length > LIMITS[k]) e[k] = `Use at most ${LIMITS[k]} characters`;
  if (!e.email && !/^[^@\s<>,;:"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,}$/.test(c.email.trim())) e.email = "Enter a valid email address";
  if (c.serial.trim() && !/^MT-[A-Z0-9]{2,10}-\d{2}-\d{5}$/i.test(c.serial.trim())) e.serial = "Serial numbers look like MT-DHD150-23-00417";
  return e;
}

/** Body of POST /v1/email/inbound: the form becomes an ordinary inbound e-mail (FR-UI-03). */
export function toInbound(c: Contact) {
  return { from: c.email.trim(), name: c.name.trim(), subject: c.subject.trim(), body: `${c.message.trim()}\n\nCompany: ${c.company.trim()}`,
    serial_no: c.serial.trim().toUpperCase() || null };
}
