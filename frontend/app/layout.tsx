import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Payslip review",
  description: "Review extracted payslip figures (synthetic data only)",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">
            Payslip review
          </Link>
          <span className="badge" title="Only synthetic documents may be used until D10 is approved">
            Synthetic data only
          </span>
        </header>
        {children}
      </body>
    </html>
  );
}
