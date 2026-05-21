import Image from "next/image";
import Link from "next/link";

export const metadata = {
  title: "Welcome to The MLB Edge",
  description: "Your payment is complete. Start exploring MLB analytics.",
};

export default function SuccessPage() {
  return (
    <div className="relative z-1 min-h-screen flex items-center justify-center px-6">
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 60% 50% at 50% 40%, rgba(34,197,94,0.06) 0%, transparent 70%)",
        }}
      />

      <div className="relative text-center max-w-md mx-auto">
        <div className="mb-8 flex justify-center">
          <Image
            src="/logo.jpeg"
            alt="The MLB Edge"
            width={64}
            height={64}
            className="rounded-2xl"
          />
        </div>

        <div className="w-16 h-16 mx-auto mb-6 rounded-full bg-[rgba(34,197,94,0.12)] border border-[rgba(34,197,94,0.3)] flex items-center justify-center">
          <svg
            className="w-8 h-8 text-green"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </div>

        <h1 className="text-3xl font-black tracking-tight mb-3">
          You&apos;re in.
        </h1>

        <p className="text-text-secondary text-lg leading-relaxed mb-2">
          Payment confirmed. You now have full access to every tool in
          The MLB Edge.
        </p>

        <p className="text-text-muted text-sm mb-8">
          Check your email for a receipt from Stripe.
        </p>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
          <a
            href="https://themlbedge.com"
            className="cta-primary"
          >
            Open The MLB Edge <span className="arrow">→</span>
          </a>
          <Link
            href="/"
            className="cta-secondary"
          >
            Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}
