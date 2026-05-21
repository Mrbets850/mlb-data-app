"use client";

import { useState } from "react";

const faqItems = [
  {
    q: "What do I get with The MLB Edge?",
    a: "Full access to every tool in the app: real-time matchup intelligence, pitcher breakdowns, HR and prop targets, AI-powered parlay builders, Statcast-driven leaderboards, and daily slate analysis — all updated for every MLB game day.",
  },
  {
    q: "Is it really free?",
    a: "Yes. Right now The MLB Edge is completely free to use. No credit card, no sign-up, no catch. Just open the app and start exploring.",
  },
  {
    q: "What data powers The MLB Edge?",
    a: "We pull from MLB's official Stats API and Baseball Savant's Statcast system — the same data used by MLB front offices. Every metric, matchup score, and recommendation is backed by real performance data, not guesswork.",
  },
  {
    q: "Is this for sports betting or fantasy baseball?",
    a: "Both — and more. The MLB Edge is built for anyone who wants sharper baseball insight. Whether you're building DFS lineups, evaluating player props, or just want to understand tonight's slate better, the tools work for your use case.",
  },
  {
    q: "How often is the data updated?",
    a: "Game-day data refreshes throughout the day. Lineups, pitcher probables, weather, and live game state update in real time. Statcast metrics refresh daily with the latest Baseball Savant data.",
  },
  {
    q: "Do I need to install anything?",
    a: "No. The MLB Edge runs in your browser on any device — phone, tablet, or desktop. No downloads, no app store. Just open the link and go.",
  },
];

export default function FAQ() {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  function toggle(i: number) {
    setOpenIndex(openIndex === i ? null : i);
  }

  return (
    <div className="flex flex-col gap-3 max-w-2xl mx-auto">
      {faqItems.map((item, i) => (
        <div key={i} className="faq-item bg-bg-card" data-open={openIndex === i}>
          <button className="faq-trigger" onClick={() => toggle(i)} aria-expanded={openIndex === i} aria-controls={`faq-answer-${i}`}>
            <span>{item.q}</span>
            <svg className="faq-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
          <div className="faq-answer" id={`faq-answer-${i}`} role="region">
            <p className="faq-answer-inner">{item.a}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
