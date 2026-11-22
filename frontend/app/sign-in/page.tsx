import { KeyRound } from "lucide-react";
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { devSignInEnabled, passwordSignInEnabled } from "@/lib/server-config";

import { SignInForm } from "./form";

export const dynamic = "force-dynamic";

export default function SignInPage() {
  const password = passwordSignInEnabled();
  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <Card>
        <CardContent className="flex flex-col gap-5 p-6">
          <div className="grid size-10 place-items-center rounded-full bg-brand-50 text-brand-600">
            <KeyRound className="size-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">Sign in</h1>
            <p className="mt-1 text-sm text-slate-500">
              {password
                ? "Use the email and password your firm administrator set up for you."
                : "Password sign-in isn't enabled on this server."}
            </p>
          </div>
          {password && <SignInForm />}
          {!password && devSignInEnabled() && (
            <Link href="/dev-sign-in" className="text-sm text-brand-700 hover:underline">
              Development sign-in
            </Link>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
