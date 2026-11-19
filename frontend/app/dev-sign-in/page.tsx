import { KeyRound } from "lucide-react";
import { notFound } from "next/navigation";

import { Card, CardContent } from "@/components/ui/card";
import { devSignInEnabled } from "@/lib/server-config";

import { DevSignInForm } from "./form";

export const dynamic = "force-dynamic";

export default function DevSignInPage() {
  if (!devSignInEnabled()) notFound(); // fail-closed: absent unless explicitly enabled
  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <Card>
        <CardContent className="flex flex-col gap-5 p-6">
          <div className="grid size-10 place-items-center rounded-full bg-brand-50 text-brand-600">
            <KeyRound className="size-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">Development sign-in</h1>
            <p className="mt-1 text-sm text-slate-500">
              Local development only. Paste a token from{" "}
              <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">meip-admin dev-token</code>. It is kept in an
              httpOnly cookie and never stored in the browser.
            </p>
          </div>
          <DevSignInForm />
        </CardContent>
      </Card>
    </main>
  );
}
