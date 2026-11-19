"use client";

import { Building2, ChevronRight, KeyRound, Loader2 } from "lucide-react";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";

import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { Membership } from "@/lib/types";
import { roleLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

export function PageContainer({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return <main className={cn("mx-auto w-full px-6 py-6", wide ? "max-w-[1600px]" : "max-w-6xl")}>{children}</main>;
}

export interface Crumb {
  label: ReactNode;
  href?: string;
}

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-2 flex items-center gap-1 text-sm text-slate-500">
      {items.map((item, i) => (
        <Fragment key={i}>
          {i > 0 && <ChevronRight className="size-3.5 text-slate-400" />}
          {item.href ? (
            <Link href={item.href} className="hover:text-slate-900 hover:underline">
              {item.label}
            </Link>
          ) : (
            <span className="max-w-72 truncate text-slate-700" aria-current="page">
              {item.label}
            </span>
          )}
        </Fragment>
      ))}
    </nav>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-16 text-sm text-slate-500 justify-center">
      <Loader2 className="size-4 animate-spin" /> {label}
    </div>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex max-w-md flex-col px-6 py-20">
      <Card>
        <CardContent className="flex flex-col gap-4 p-6">{children}</CardContent>
      </Card>
    </main>
  );
}

/** Renders the page only when signed in with a firm chosen; otherwise says what to do. */
export function RequireFirm({ children }: { children: (firm: Membership) => ReactNode }) {
  const { session, chooseFirm } = useSession();
  if (session.status === "loading") return <Loading />;
  if (session.status === "signed-out") {
    return (
      <Centered>
        <div className="grid size-10 place-items-center rounded-full bg-brand-50 text-brand-600">
          <KeyRound className="size-5" />
        </div>
        <div>
          <h1 className="text-lg font-semibold">Sign in to continue</h1>
          <p className="mt-1 text-sm text-slate-500">
            You&apos;re signed out, or your session has ended. Real sign-in isn&apos;t connected
            yet; in local development use the development sign-in.
          </p>
        </div>
        <Button asChild>
          <Link href="/dev-sign-in">Development sign-in</Link>
        </Button>
      </Centered>
    );
  }
  if (!session.firm) {
    return (
      <Centered>
        <div className="grid size-10 place-items-center rounded-full bg-brand-50 text-brand-600">
          <Building2 className="size-5" />
        </div>
        <div>
          <h1 className="text-lg font-semibold">Choose a firm to continue</h1>
          <p className="mt-1 text-sm text-slate-500">
            Choosing a firm only selects where you work. Access is checked by the server on every
            request.
          </p>
        </div>
        <div className="flex flex-col gap-2">
          {session.me.memberships.map((m) => (
            <button
              key={m.tenant_id}
              onClick={() => void chooseFirm(m.tenant_id)}
              className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3 text-left hover:border-brand-500 hover:bg-brand-50"
            >
              <span>
                <span className="block text-sm font-medium">{m.tenant_name}</span>
                <span className="block text-xs text-slate-500">{roleLabel(m.role)}</span>
              </span>
              <ChevronRight className="size-4 text-slate-400" />
            </button>
          ))}
          {session.me.memberships.length === 0 && (
            <p className="text-sm text-slate-500">You aren&apos;t a member of any firm yet.</p>
          )}
        </div>
      </Centered>
    );
  }
  // Keyed by firm, so switching firm reloads the page's data from scratch.
  return <Fragment key={session.firm.tenant_id}>{children(session.firm)}</Fragment>;
}
