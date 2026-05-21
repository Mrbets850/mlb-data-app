"use client";

import { useState } from "react";

interface CheckoutButtonProps {
  className?: string;
  variant?: "primary" | "secondary";
  children?: React.ReactNode;
}

export default function CheckoutButton({
  className = "",
  variant = "primary",
  children,
}: CheckoutButtonProps) {
  const [loading, setLoading] = useState(false);

  async function handleCheckout() {
    setLoading(true);
    try {
      const res = await fetch("/api/create-checkout-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });

      const data = await res.json();

      if (data.url) {
        window.location.href = data.url;
      } else {
        console.error("Checkout error:", data.error);
        setLoading(false);
      }
    } catch (err) {
      console.error("Checkout error:", err);
      setLoading(false);
    }
  }

  const baseClass = variant === "primary" ? "cta-primary" : "cta-secondary";

  return (
    <button
      onClick={handleCheckout}
      disabled={loading}
      className={`${baseClass} ${className} ${loading ? "opacity-80 cursor-wait" : ""}`}
    >
      {loading ? (
        <>
          <span className="spinner" />
          <span>Redirecting…</span>
        </>
      ) : (
        children || (
          <>
            Start Free Trial <span className="arrow">→</span>
          </>
        )
      )}
    </button>
  );
}
