import { cn } from "@/lib/utils";

export function Progress({ value, max, className, label }: { value: number; max: number; className?: string; label: string }) {
  const percent = max === 0 ? 0 : Math.round((value / max) * 100);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className={cn("h-2 w-full overflow-hidden rounded-full bg-slate-200", className)}
    >
      <div
        className={cn("h-full rounded-full transition-[width] duration-300", percent === 100 ? "bg-accept" : "bg-brand-600")}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}
