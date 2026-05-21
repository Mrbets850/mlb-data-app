import Image from "next/image";
import Header from "./components/Header";
import CheckoutButton from "./components/CheckoutButton";
import FAQ from "./components/FAQ";

const features = [
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-6 h-6">
        <path d="M3 3v18h18" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M7 16l4-6 4 4 5-8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
    title: "Statcast-Powered Matchups",
    desc: "Every batter-vs-pitcher matchup scored with ISO, Barrel%, Exit Velocity, and xwOBA. See who has the edge before first pitch.",
  },
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-6 h-6">
        <circle cx="12" cy="12" r="10" />
        <path d="M12 6v6l4 2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
    title: "Real-Time Slate Updates",
    desc: "Lineups, pitcher changes, weather, and live game state — all refreshing in real time so you're never working with stale data.",
  },
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-6 h-6">
        <path d="M9 19V6l12-3v13" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="6" cy="19" r="3" />
        <circle cx="18" cy="16" r="3" />
      </svg>
    ),
    title: "Pitcher Vulnerability Scores",
    desc: "Identify weak spots in every starter's arsenal. Attack zones, pitch-type splits, and platoon gaps — all in one view.",
  },
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-6 h-6">
        <path d="M12 2L2 7l10 5 10-5-10-5z" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M2 17l10 5 10-5" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M2 12l10 5 10-5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
    title: "AI Parlay Builders",
    desc: "Data-driven HR, strikeout, and hits parlay generators that surface the highest-value combinations from each slate.",
  },
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-6 h-6">
        <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2v10z" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
    title: "Daily Edge Picks",
    desc: "Curated daily picks ranked by confidence level. No fluff, no filler — just the plays that matter most on today's slate.",
  },
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-6 h-6">
        <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
        <path d="M8 21h8" strokeLinecap="round" />
        <path d="M12 17v4" strokeLinecap="round" />
      </svg>
    ),
    title: "Works on Any Device",
    desc: "Full-featured on desktop, tablet, and mobile. No app download required — open your browser and get straight to the data.",
  },
];

const steps = [
  {
    num: "01",
    title: "Get instant access",
    desc: "One payment, no hoops. Pay $4.99 and you're in — full access to every tool in The MLB Edge, instantly.",
  },
  {
    num: "02",
    title: "Explore tonight's slate",
    desc: "Dive into matchup scores, pitcher breakdowns, prop targets, and AI-powered parlay picks for every game on the board.",
  },
  {
    num: "03",
    title: "Make sharper decisions",
    desc: "Use real data instead of gut feel. Whether it's a DFS lineup, a player prop, or a parlay — you'll have the edge.",
  },
];

export default function LandingPage() {
  return (
    <>
      <Header />

      <main className="relative z-1">
        {/* ─── HERO ─── */}
        <section
          id="hero"
          className="relative min-h-screen flex items-center pt-24 pb-16 px-[clamp(18px,5vw,80px)]"
        >
          <div className="absolute inset-0 pointer-events-none">
            <div
              className="absolute inset-0"
              style={{
                background:
                  "radial-gradient(ellipse 80% 70% at 20% 50%, rgba(124,58,237,0.12) 0%, transparent 60%), radial-gradient(ellipse 50% 50% at 80% 80%, rgba(250,204,21,0.06) 0%, transparent 55%)",
              }}
            />
          </div>

          <div className="relative z-1 max-w-[1200px] mx-auto w-full grid grid-cols-1 lg:grid-cols-2 gap-12 lg:gap-16 items-center">
            <div
              className="flex flex-col gap-7"
              style={{ animation: "fade-in-up 0.8s cubic-bezier(0.22,1,0.36,1) both" }}
            >
              <div className="flex items-center gap-3">
                <span className="w-[7px] h-[7px] rounded-full bg-green" style={{ animation: "pulse-dot 1.7s ease infinite" }} />
                <span className="text-[0.67rem] font-extrabold tracking-[0.18em] uppercase text-gold">
                  Live for today&apos;s slate
                </span>
              </div>

              <h1 className="text-[clamp(2.6rem,6vw,4.6rem)] font-black leading-[0.97] tracking-[-0.035em]">
                Get the edge on{" "}
                <span className="text-gradient">every MLB slate</span>
              </h1>

              <p className="text-text-secondary text-lg leading-relaxed max-w-lg">
                Smarter insights, faster decisions. Statcast-powered matchup
                intelligence, pitcher breakdowns, and daily picks — built for MLB
                fans who want sharper data without the clutter.
              </p>

              <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 mt-2">
                <CheckoutButton>
                  Get Access — $4.99 <span className="arrow">→</span>
                </CheckoutButton>
                <a href="#features" className="cta-secondary">
                  See what&apos;s inside
                </a>
              </div>

              <p className="text-text-muted text-[0.76rem] leading-relaxed mt-1">
                One-time payment. Instant access. No subscriptions.
              </p>
            </div>

            <div
              className="relative hidden lg:flex items-center justify-center"
              style={{ animation: "fade-in-up 1s cubic-bezier(0.22,1,0.36,1) 0.2s both" }}
            >
              <div className="relative w-full max-w-[420px] aspect-square">
                <div
                  className="absolute inset-0 rounded-3xl"
                  style={{
                    background:
                      "linear-gradient(135deg, rgba(124,58,237,0.12), rgba(250,204,21,0.08))",
                    border: "1px solid rgba(250,204,21,0.12)",
                  }}
                />
                <div className="absolute inset-4 rounded-2xl overflow-hidden border border-border-gold">
                  <Image
                    src="/logo.jpeg"
                    alt="The MLB Edge"
                    fill
                    className="object-cover"
                    priority
                  />
                </div>
                <div
                  className="absolute -bottom-4 -right-4 bg-bg-card border border-border-gold rounded-xl px-5 py-3"
                  style={{ animation: "fade-in-up 1.2s cubic-bezier(0.22,1,0.36,1) 0.5s both" }}
                >
                  <div className="stat-number text-gold text-2xl">17+</div>
                  <div className="text-text-secondary text-xs font-semibold uppercase tracking-wider">
                    Analytics tools
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ─── PROOF BAR ─── */}
        <section id="proof" className="relative z-1 py-12 border-y border-border-mid">
          <div className="max-w-[1200px] mx-auto px-[clamp(18px,5vw,80px)]">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
              {[
                { value: "162", label: "Games tracked per team" },
                { value: "Statcast", label: "Powered by real data" },
                { value: "Daily", label: "Picks & analysis updated" },
                { value: "17+", label: "Built-in analytics tools" },
              ].map((stat, i) => (
                <div key={i} className="flex flex-col items-center gap-2">
                  <span className="stat-number text-gold text-2xl md:text-3xl">
                    {stat.value}
                  </span>
                  <span className="text-text-secondary text-xs md:text-sm font-medium tracking-wide uppercase">
                    {stat.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ─── FEATURES ─── */}
        <section id="features" className="relative z-1 py-20 md:py-28 px-[clamp(18px,5vw,80px)]">
          <div className="max-w-[1200px] mx-auto">
            <div className="text-center mb-16">
              <span className="text-[0.7rem] font-extrabold tracking-[0.2em] uppercase text-purple mb-3 block">
                What&apos;s Inside
              </span>
              <h2 className="text-3xl md:text-4xl font-black tracking-tight mb-4">
                Clear data, <span className="text-gradient">zero clutter</span>
              </h2>
              <p className="text-text-secondary text-lg max-w-xl mx-auto">
                Every tool is designed to surface the insight that matters — fast.
                No noise, no filler, no spreadsheet digging.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {features.map((f, i) => (
                <div
                  key={i}
                  className="card p-7 flex flex-col gap-4 hover:translate-y-[-2px]"
                >
                  <div className="w-11 h-11 rounded-xl bg-purple-dim flex items-center justify-center text-purple">
                    {f.icon}
                  </div>
                  <h3 className="text-lg font-bold tracking-tight">{f.title}</h3>
                  <p className="text-text-secondary text-[0.92rem] leading-relaxed">
                    {f.desc}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <div className="section-divider" />

        {/* ─── HOW IT WORKS ─── */}
        <section id="how-it-works" className="relative z-1 py-20 md:py-28 px-[clamp(18px,5vw,80px)]">
          <div className="max-w-[1200px] mx-auto">
            <div className="text-center mb-16">
              <span className="text-[0.7rem] font-extrabold tracking-[0.2em] uppercase text-purple mb-3 block">
                Getting Started
              </span>
              <h2 className="text-3xl md:text-4xl font-black tracking-tight mb-4">
                Get in. Get the edge.{" "}
                <span className="text-gradient">It&apos;s that simple.</span>
              </h2>
              <p className="text-text-secondary text-lg max-w-xl mx-auto">
                No setup, no configuration, no learning curve. Pay once and start
                using every tool on today&apos;s slate.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-4xl mx-auto">
              {steps.map((step, i) => (
                <div key={i} className="relative card p-8 text-center">
                  <div className="stat-number text-4xl text-gold opacity-30 mb-4">
                    {step.num}
                  </div>
                  <h3 className="text-lg font-bold tracking-tight mb-3">
                    {step.title}
                  </h3>
                  <p className="text-text-secondary text-[0.92rem] leading-relaxed">
                    {step.desc}
                  </p>
                  {i < steps.length - 1 && (
                    <div className="hidden md:block absolute top-1/2 -right-3 text-text-muted text-xl">
                      →
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </section>

        <div className="section-divider" />

        {/* ─── PRICING ─── */}
        <section id="pricing" className="relative z-1 py-20 md:py-28 px-[clamp(18px,5vw,80px)]">
          <div className="max-w-[1200px] mx-auto">
            <div className="text-center mb-16">
              <span className="text-[0.7rem] font-extrabold tracking-[0.2em] uppercase text-purple mb-3 block">
                Pricing
              </span>
              <h2 className="text-3xl md:text-4xl font-black tracking-tight mb-4">
                One price.{" "}
                <span className="text-gradient">Full access.</span>
              </h2>
              <p className="text-text-secondary text-lg max-w-xl mx-auto">
                No subscriptions, no recurring charges, no feature gates.
                Pay once and get everything.
              </p>
            </div>

            <div className="max-w-md mx-auto">
              <div className="pricing-card p-8 md:p-10">
                <div className="text-center mb-8">
                  <div className="inline-flex items-center gap-2 bg-[rgba(250,204,21,0.08)] border border-border-gold rounded-full px-4 py-1.5 mb-6">
                    <span className="w-2 h-2 rounded-full bg-green" style={{ animation: "pulse-dot 1.7s ease infinite" }} />
                    <span className="text-xs font-bold tracking-wider uppercase text-gold">
                      One-Time Payment
                    </span>
                  </div>

                  <h3 className="text-xl font-bold mb-1">The MLB Edge</h3>
                  <p className="text-text-secondary text-sm mb-6">
                    Full access to every tool
                  </p>

                  <div className="flex items-baseline justify-center gap-1 mb-2">
                    <span className="stat-number text-5xl text-text-primary">$4</span>
                    <span className="stat-number text-3xl text-text-primary">.99</span>
                  </div>
                  <p className="text-text-muted text-sm">
                    one-time payment — no recurring fees
                  </p>
                </div>

                <ul className="flex flex-col gap-3 mb-8 text-[0.92rem]">
                  {[
                    "Statcast-powered matchup scores",
                    "Pitcher breakdown & vulnerability analysis",
                    "AI parlay builders (HR, K, Hits)",
                    "Daily ranked picks & targets",
                    "Hot/cold batter leaderboards",
                    "Real-time lineups & game state",
                    "Ballpark weather intelligence",
                    "Works on every device",
                  ].map((item, i) => (
                    <li key={i} className="flex items-start gap-3">
                      <svg
                        className="w-5 h-5 text-green flex-shrink-0 mt-0.5"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      <span className="text-text-secondary">{item}</span>
                    </li>
                  ))}
                </ul>

                <CheckoutButton className="w-full !text-base !py-4">
                  Get Access — $4.99 <span className="arrow">→</span>
                </CheckoutButton>

                <p className="text-text-muted text-xs text-center mt-4 leading-relaxed">
                  Secure payment via Stripe. You will not be charged again.
                </p>
              </div>
            </div>
          </div>
        </section>

        <div className="section-divider" />

        {/* ─── FAQ ─── */}
        <section id="faq" className="relative z-1 py-20 md:py-28 px-[clamp(18px,5vw,80px)]">
          <div className="max-w-[1200px] mx-auto">
            <div className="text-center mb-16">
              <span className="text-[0.7rem] font-extrabold tracking-[0.2em] uppercase text-purple mb-3 block">
                FAQ
              </span>
              <h2 className="text-3xl md:text-4xl font-black tracking-tight mb-4">
                Questions?{" "}
                <span className="text-gradient">Answered.</span>
              </h2>
            </div>

            <FAQ />
          </div>
        </section>

        <div className="section-divider" />

        {/* ─── FINAL CTA ─── */}
        <section id="final-cta" className="relative z-1 py-20 md:py-28 px-[clamp(18px,5vw,80px)]">
          <div className="max-w-[800px] mx-auto text-center">
            <div
              className="relative rounded-2xl p-10 md:p-16 border border-border-gold overflow-hidden"
              style={{
                background:
                  "linear-gradient(135deg, rgba(124,58,237,0.08) 0%, rgba(8,15,32,1) 50%, rgba(250,204,21,0.05) 100%)",
              }}
            >
              <div
                className="absolute inset-0 pointer-events-none"
                style={{
                  background:
                    "radial-gradient(ellipse 60% 60% at 50% 0%, rgba(250,204,21,0.06) 0%, transparent 70%)",
                }}
              />
              <div className="relative z-1">
                <h2 className="text-3xl md:text-4xl font-black tracking-tight mb-4">
                  Ready to sharpen{" "}
                  <span className="text-gradient">your edge?</span>
                </h2>
                <p className="text-text-secondary text-lg mb-8 max-w-lg mx-auto">
                  Join the MLB fans who stopped guessing and started using real
                  data. Full access for just $4.99.
                </p>
                <CheckoutButton>
                  Get Access — $4.99 <span className="arrow">→</span>
                </CheckoutButton>
                <p className="text-text-muted text-xs mt-4">
                  One-time payment · Instant access · No subscriptions
                </p>
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* ─── FOOTER ─── */}
      <footer className="relative z-1 border-t border-border-mid py-12 px-[clamp(18px,5vw,80px)]">
        <div className="max-w-[1200px] mx-auto">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-3">
              <Image
                src="/logo.jpeg"
                alt="The MLB Edge"
                width={28}
                height={28}
                className="rounded-md"
              />
              <span className="text-sm font-bold tracking-wider uppercase text-text-secondary">
                The MLB <span className="text-gold">Edge</span>
              </span>
            </div>

            <div className="flex items-center gap-6 text-sm text-text-muted">
              <a href="#features" className="hover:text-text-secondary transition-colors">
                Features
              </a>
              <a href="#pricing" className="hover:text-text-secondary transition-colors">
                Pricing
              </a>
              <a href="#faq" className="hover:text-text-secondary transition-colors">
                FAQ
              </a>
            </div>

            <p className="text-xs text-text-muted text-center md:text-right">
              &copy; {new Date().getFullYear()} The MLB Edge. All rights
              reserved.
            </p>
          </div>
        </div>
      </footer>
    </>
  );
}
