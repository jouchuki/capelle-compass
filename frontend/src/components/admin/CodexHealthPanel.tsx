import { useEffect, useState, type ReactNode } from 'react';
import { ServerCog, AlertTriangle, CheckCircle2, XOctagon } from 'lucide-react';
import { fetchCodexHealth, type CodexHealthRow, type CodexHealthStatus } from './adminFetch';
import { ApiError } from '../../api/client';
import { formatDateTimeNL } from '../../utils/adminFormat';
import Spinner from './Spinner';

type ChipPalette = {
  badge: string;
  ring: string;
  icon: string;
  label: string;
};

const STATUS_PALETTE: Record<CodexHealthStatus, ChipPalette> = {
  in_sync: {
    badge: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    ring: 'ring-emerald-100',
    icon: 'text-emerald-600',
    label: 'In sync',
  },
  rotated: {
    badge: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    ring: 'ring-emerald-100',
    icon: 'text-emerald-600',
    label: 'Geroteerd',
  },
  near_expiry: {
    badge: 'bg-amber-50 text-amber-700 border-amber-100',
    ring: 'ring-amber-100',
    icon: 'text-amber-600',
    label: 'Bijna verlopen',
  },
  failed: {
    badge: 'bg-rose-50 text-rose-700 border-rose-100',
    ring: 'ring-rose-100',
    icon: 'text-rose-600',
    label: 'Mislukt',
  },
  auth_json_missing: {
    badge: 'bg-rose-50 text-rose-700 border-rose-100',
    ring: 'ring-rose-100',
    icon: 'text-rose-600',
    label: 'Configuratie ontbreekt',
  },
};

function statusIcon(status: CodexHealthStatus): ReactNode {
  const palette = STATUS_PALETTE[status];
  const cls = `h-4 w-4 ${palette.icon}`;
  if (status === 'in_sync' || status === 'rotated') return <CheckCircle2 className={cls} />;
  if (status === 'near_expiry') return <AlertTriangle className={cls} />;
  return <XOctagon className={cls} />;
}

function formatHoursLeft(h: number | null): string {
  if (h === null || !Number.isFinite(h)) return '—';
  if (h <= 0) return 'verlopen';
  if (h < 1) {
    const mins = Math.max(1, Math.round(h * 60));
    return `${mins} min`;
  }
  if (h < 24) return `${h.toFixed(1).replace('.', ',')} uur`;
  const days = Math.floor(h / 24);
  return `${days} d`;
}

export default function CodexHealthPanel() {
  const [rows, setRows] = useState<CodexHealthRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchCodexHealth()
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setError(typeof err.detail === 'string' ? err.detail : `HTTP ${err.status}`);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError('Onbekende fout');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="bg-white border border-slate-100 artifact-shadow rounded-2xl p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">
            Codex-token status per yuta
          </span>
          <span className="text-xs text-slate-400">
            Live refresh-status van de Codex-credentials op elke host
          </span>
        </div>
        <ServerCog className="h-4 w-4 text-slate-400" />
      </div>

      {loading ? (
        <div className="py-8">
          <Spinner label="Status laden" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-rose-100 bg-rose-50 p-4 text-sm text-rose-700">
          {error}
        </div>
      ) : rows && rows.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {rows.map((row) => {
            const palette = STATUS_PALETTE[row.status];
            return (
              <li
                key={row.hostname}
                className={`rounded-xl border border-slate-100 bg-slate-50/40 px-4 py-3 flex flex-col gap-2 md:flex-row md:items-center md:justify-between ring-1 ring-inset ${palette.ring}`}
              >
                <div className="flex flex-col gap-0.5">
                  <span className="text-sm font-semibold text-slate-900 tabular-nums">
                    {row.hostname}
                  </span>
                  <span className="text-[11px] text-slate-500">
                    Laatste sync: {formatDateTimeNL(row.last_success)}
                    {row.access_tail ? (
                      <span className="ml-2 text-slate-400">
                        · token …{row.access_tail}
                      </span>
                    ) : null}
                  </span>
                  {row.status === 'auth_json_missing' ? (
                    <span className="text-[11px] text-rose-600 mt-0.5">
                      Configuratie ontbreekt — voer{' '}
                      <code className="font-mono text-rose-700">codex login --device-auth</code>{' '}
                      uit op deze host.
                    </span>
                  ) : null}
                </div>
                <div className="flex items-center gap-3 md:flex-row-reverse">
                  <span
                    className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${palette.badge}`}
                  >
                    {statusIcon(row.status)}
                    {palette.label}
                  </span>
                  <span className="text-[11px] text-slate-500 tabular-nums">
                    {formatHoursLeft(row.hours_to_expiry)} resterend
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="py-8 text-center text-sm text-slate-400">Geen hosts geregistreerd.</div>
      )}
    </div>
  );
}
