"use client";

import { useState, useEffect } from "react";
import Image from "next/image";

export default function Header() {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (menuOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [menuOpen]);

  const navLinks = [
    { label: "Features", href: "#features" },
    { label: "Pricing", href: "#pricing" },
    { label: "FAQ", href: "#faq" },
  ];

  return (
    <>
      <nav
        className={`fixed top-0 left-0 right-0 z-100 h-16 flex items-center justify-between px-[clamp(18px,5vw,80px)] transition-all duration-300 ${
          scrolled
            ? "bg-[rgba(3,7,15,0.96)] border-b border-border-gold"
            : "bg-[rgba(3,7,15,0.88)] border-b border-transparent"
        }`}
        style={{ backdropFilter: "blur(18px) saturate(180%)" }}
      >
        <a href="#" className="flex items-center gap-3 no-underline">
          <Image
            src="/logo.jpeg"
            alt="The MLB Edge"
            width={34}
            height={34}
            className="rounded-lg"
            priority
          />
          <span className="text-[0.82rem] font-extrabold tracking-[0.12em] uppercase text-text-primary">
            THE MLB <span className="text-gold">EDGE</span>
          </span>
        </a>

        <div className="hidden md:flex items-center gap-8">
          {navLinks.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-[0.82rem] font-semibold tracking-wide text-text-secondary hover:text-gold transition-colors duration-200"
            >
              {link.label}
            </a>
          ))}
          <a href="#pricing" className="cta-primary !py-[9px] !px-5 !text-[0.78rem] !animate-none">
            Get Access — $4.99 <span className="arrow">→</span>
          </a>
        </div>

        {/* Mobile hamburger */}
        <button
          className="md:hidden flex flex-col gap-[5px] p-2 bg-transparent border-none cursor-pointer"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
        >
          <span
            className={`block w-5 h-[2px] bg-text-primary transition-all duration-300 ${
              menuOpen ? "rotate-45 translate-y-[7px]" : ""
            }`}
          />
          <span
            className={`block w-5 h-[2px] bg-text-primary transition-all duration-300 ${
              menuOpen ? "opacity-0" : ""
            }`}
          />
          <span
            className={`block w-5 h-[2px] bg-text-primary transition-all duration-300 ${
              menuOpen ? "-rotate-45 -translate-y-[7px]" : ""
            }`}
          />
        </button>
      </nav>

      {/* Mobile menu */}
      <div
        className="mobile-menu"
        data-open={menuOpen}
        role="dialog"
        aria-modal={menuOpen}
        aria-label="Navigation menu"
      >
        {navLinks.map((link) => (
          <a
            key={link.href}
            href={link.href}
            className="text-xl font-bold tracking-wide text-text-primary hover:text-gold transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {link.label}
          </a>
        ))}
        <a
          href="#pricing"
          className="cta-primary mt-4"
          onClick={() => setMenuOpen(false)}
        >
          Get Access — $4.99 <span className="arrow">→</span>
        </a>
      </div>
    </>
  );
}
