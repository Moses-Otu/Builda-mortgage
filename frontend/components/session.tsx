"use client";

// Who is signed in and which firm the session acts within, loaded once for every page.
// The firm is only a selector: the API checks membership on every request.

import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { get } from "@/lib/api";
import type { Me, Membership } from "@/lib/types";

export type SessionState =
  | { status: "loading" }
  | { status: "signed-out" }
  | { status: "ready"; me: Me; firm: Membership | null };

interface SessionValue {
  session: SessionState;
  chooseFirm: (tenantId: string) => Promise<boolean>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionState>({ status: "loading" });

  const load = useCallback(async () => {
    const me = await get<Me>("me");
    if (!me.ok) {
      if (me.status === 403 && me.problem?.type?.endsWith("password-change-required")) {
        // This provider wraps every page, the change-password page included: redirecting from
        // there would reload it forever.
        if (window.location.pathname !== "/change-password") {
          window.location.href = "/change-password?first=1";
          return;
        }
      }
      setSession({ status: "signed-out" });
      return;
    }
    const response = await fetch("/api/session/firm", { cache: "no-store" });
    const body = response.ok ? ((await response.json()) as { tenant_id: string | null }) : { tenant_id: null };
    const firm = me.data.memberships.find((m) => m.tenant_id === body.tenant_id) ?? null;
    setSession({ status: "ready", me: me.data, firm });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const chooseFirm = useCallback(
    async (tenantId: string) => {
      const response = await fetch("/api/session/firm", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tenant_id: tenantId }),
      });
      if (response.status !== 204) return false;
      await load();
      return true;
    },
    [load],
  );

  const signOut = useCallback(async () => {
    await fetch("/api/session", { method: "DELETE" });
    window.location.href = "/";
  }, []);

  const value = useMemo(() => ({ session, chooseFirm, signOut }), [session, chooseFirm, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession outside SessionProvider");
  return value;
}
