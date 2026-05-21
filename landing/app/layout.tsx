import type { Metadata } from "next";
import { Inter, DM_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const dmMono = DM_Mono({
  variable: "--font-dm-mono",
  weight: ["400", "500"],
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "The MLB Edge — Smarter MLB Insights, Faster Decisions",
  description:
    "Get the edge on every MLB slate. Fast, actionable baseball analytics for fans, bettors, and fantasy players. Statcast-powered matchup intelligence updated daily.",
  keywords: [
    "MLB analytics",
    "baseball insights",
    "MLB betting",
    "fantasy baseball",
    "Statcast data",
    "MLB matchups",
    "baseball data",
    "MLB picks",
  ],
  openGraph: {
    title: "The MLB Edge — Smarter MLB Insights, Faster Decisions",
    description:
      "Fast, actionable MLB analytics powered by Statcast data. Matchup intelligence, pitcher breakdowns, and daily picks for the modern baseball fan.",
    type: "website",
    url: "https://themlbedge.com",
    siteName: "The MLB Edge",
  },
  twitter: {
    card: "summary_large_image",
    title: "The MLB Edge — Smarter MLB Insights, Faster Decisions",
    description:
      "Fast, actionable MLB analytics powered by Statcast data. Matchup intelligence, pitcher breakdowns, and daily picks for the modern baseball fan.",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${dmMono.variable} h-full antialiased`}
    >
      <head>
        <meta name="theme-color" content="#03070f" />
        <link rel="icon" href="/logo.jpeg" type="image/jpeg" />
      </head>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
