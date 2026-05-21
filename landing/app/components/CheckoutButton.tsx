"use client";

interface CheckoutButtonProps {
  className?: string;
  children?: React.ReactNode;
}

export default function CheckoutButton({
  className = "",
  children,
}: CheckoutButtonProps) {
  return (
    <a
      href="https://app.themlbedge.com"
      className={`cta-primary ${className}`}
    >
      {children || (
        <>
          Start Free Trial <span className="arrow">→</span>
        </>
      )}
    </a>
  );
}
