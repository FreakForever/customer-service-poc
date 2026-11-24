import { ContactForm } from "@/components/help/ContactForm";

export const metadata = { title: "Email us" };

export default function Contact() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="mb-2 text-3xl">Email us</h1>
      <p className="mb-6 text-steel">For smoke, fire, electric shock or a refrigerant leak, switch the machine off, keep clear and call the breakdown line.</p>
      <ContactForm />
    </div>
  );
}
