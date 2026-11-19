import { notFound } from "next/navigation";

import { devSignInEnabled } from "@/lib/server-config";

import { DevSignInForm } from "./form";

export const dynamic = "force-dynamic";

export default function DevSignInPage() {
  if (!devSignInEnabled()) notFound(); // fail-closed: absent unless explicitly enabled
  return (
    <main className="narrow">
      <h1>Development sign-in</h1>
      <p className="note">
        Local development only. Paste a token from <code>meip-admin dev-token</code>. It is
        kept in an httpOnly cookie and never stored in the browser.
      </p>
      <DevSignInForm />
    </main>
  );
}
