// The Ceda logo: the wordmark with a pound sign in place of the "e". One place for it, so the
// app header and landing page always match. Screen readers hear "Ceda".

export function CedaLogo({ light = false, className = "" }: { light?: boolean; className?: string }) {
  return (
    <span
      role="img"
      aria-label="Ceda"
      className={`font-bold tracking-tight ${light ? "text-[#E9EEF4]" : "text-[#0C2135]"} ${className}`}
    >
      <span aria-hidden="true">
        C<span className={light ? "text-[#6FB497]" : "text-[#376D58]"}>£</span>da
      </span>
    </span>
  );
}
