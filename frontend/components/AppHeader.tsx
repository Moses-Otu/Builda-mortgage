"use client";

import { Building2, Check, ChevronDown, FileCheck2, KeyRound, LogOut, UserRound, Users } from "lucide-react";
import Link from "next/link";

import { useSession } from "@/components/session";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";
import { roleLabel } from "@/lib/labels";

export function AppHeader() {
  const { session, chooseFirm, signOut } = useSession();

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-4 px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <span className="grid size-7 place-items-center rounded-md bg-brand-600 text-white">
            <FileCheck2 className="size-4" />
          </span>
          Evidence
        </Link>
        <Tooltip content="Only synthetic documents may be used until the retention decision (D10) is approved.">
          <Badge tone="synthetic" tabIndex={0}>
            Synthetic data only
          </Badge>
        </Tooltip>

        <div className="ml-auto flex items-center gap-2">
          {session.status === "ready" && (
            <>
              <DropdownMenu>
                <DropdownMenuTrigger className="flex h-9 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm shadow-xs hover:bg-slate-50">
                  <Building2 className="size-4 text-slate-500" />
                  <span className="max-w-56 truncate">{session.firm?.tenant_name ?? "Choose a firm"}</span>
                  <ChevronDown className="size-4 text-slate-400" />
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuLabel>Your firms</DropdownMenuLabel>
                  {session.me.memberships.map((m) => (
                    <DropdownMenuItem key={m.tenant_id} onSelect={() => void chooseFirm(m.tenant_id)}>
                      <span className="flex-1">
                        {m.tenant_name}
                        <span className="block text-xs text-slate-500">{roleLabel(m.role)}</span>
                      </span>
                      {session.firm?.tenant_id === m.tenant_id && <Check className="text-brand-600" />}
                    </DropdownMenuItem>
                  ))}
                  {session.me.memberships.length === 0 && (
                    <DropdownMenuLabel>You aren&apos;t a member of any firm yet.</DropdownMenuLabel>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>

              <DropdownMenu>
                <DropdownMenuTrigger
                  className="grid size-9 place-items-center rounded-full border border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                  aria-label="Account"
                >
                  <UserRound className="size-4" />
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuLabel>
                    Signed in
                    {session.firm && <span className="block font-normal">{roleLabel(session.firm.role)}</span>}
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  {session.firm?.role === "firm_admin" && (
                    <DropdownMenuItem onSelect={() => (window.location.href = "/team")}>
                      <Users /> Team
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onSelect={() => (window.location.href = "/change-password")}>
                    <KeyRound /> Change password
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => void signOut()}>
                    <LogOut /> Sign out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
