import { Loader2 } from 'lucide-react';

interface SpinnerProps {
  label?: string;
  className?: string;
}

/** Tiny inline loader. The /admin surface uses it sparingly. */
export default function Spinner({ label, className }: SpinnerProps) {
  return (
    <div className={`flex items-center justify-center gap-2 text-slate-400 ${className ?? ''}`}>
      <Loader2 className="h-4 w-4 animate-spin" />
      {label ? <span className="text-xs uppercase tracking-widest font-bold">{label}</span> : null}
    </div>
  );
}
