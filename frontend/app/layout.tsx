import type { Metadata } from "next";
import { Inter } from "next/font/google";
import type { ReactNode } from "react";

import { AppHeader } from "@/components/AppHeader";
import { Providers } from "@/components/Providers";

import "./globals.css";

// Self-hosted by Next.js at build time: the browser loads it from this app (CSP font-src 'self').
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: "Ceda | Pre-qualification and affordability agent",
  description: "Mortgage evidence, verified and assessed. The broker decides. (Pilot, synthetic data only.)",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB" className={inter.variable}>
      <body className="min-h-screen">
        <Providers>
          <AppHeader />
          {children}
        </Providers>
      </body>
    </html>
  );
}
