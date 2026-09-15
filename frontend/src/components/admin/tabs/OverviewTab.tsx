import { useEffect, useState } from 'react';
import { Link2, Eye, UserCheck } from 'lucide-react';
import {
  fetchAdminOverview,
  fetchCodexFailures,
  type AdminOverviewData,
  type CodexFailuresPerHourPoint,
} from '../adminFetch';
import { ApiError } from '../../../api/client';
import { formatInt, formatTokensCompact } from '../../../utils/adminFormat';
import StatCard from '../StatCard';
import Spinner from '../Spinner';
import ChartCard from '../charts/ChartCard';
import AdminLineChart from '../charts/AdminLineChart';
import AdminBarChart from '../charts/AdminBarChart';
import AdminStackedBarChart from '../charts/AdminStackedBarChart';
import AdminHourlyLineChart from '../charts/AdminHourlyLineChart';
import CodexHealthPanel from '../CodexHealthPanel';

const TOKEN_SERIES = [
  { key: 'input', color: '#2563eb', label: 'Input' },
  { key: 'output', color: '#60a5fa', label: 'Output' },
  { key: 'cache', color: '#bfdbfe', label: 'Cache' },
];

export default function OverviewTab() {
  const [data, setData] = useState<AdminOverviewData | null>(null);
  const [codexFailures, setCodexFailures] = useState<CodexFailuresPerHourPoint[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([fetchAdminOverview(), fetchCodexFailures()])
      .then(([overview, codex]) => {
        if (cancelled) return;
        setData(overview);
        setCodexFailures(codex.hourly);
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

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner label="Overzicht laden" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-rose-100 bg-rose-50 p-6 text-sm text-rose-700">
        {error}
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
        <StatCard label="Totaal accounts" value={formatInt(data.total_accounts)} />
        <StatCard label="Nieuwe accounts 24u" value={formatInt(data.new_accounts_24h)} />
        <StatCard label="Nieuwe accounts 30d" value={formatInt(data.new_accounts_30d)} />
        <StatCard label="Nieuwe feedback" value={formatInt(data.new_feedback)} />
        <StatCard label="Tokens 30d" value={formatTokensCompact(data.tokens_last_30d)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ChartCard title="Voltooide analyses per dag">
          <AdminLineChart data={data.analyses_per_day} color="#2563eb" seriesLabel="Analyses" />
        </ChartCard>
        <ChartCard title="Vragen per dag">
          <AdminBarChart data={data.questions_per_day} color="#2563eb" seriesLabel="Vragen" />
        </ChartCard>
        <ChartCard title="Token-gebruik per dag">
          <AdminStackedBarChart data={data.tokens_per_day} series={TOKEN_SERIES} />
        </ChartCard>
        <ChartCard title="Quota-limiet hits per dag">
          <AdminLineChart
            data={data.quota_limit_hits_per_day}
            color="#f59e0b"
            seriesLabel="Quota hits"
          />
        </ChartCard>
        <ChartCard
          title="Codex-token refresh-mislukkingen per uur"
          subtitle="Rollend venster, laatste 24 uur"
        >
          <AdminHourlyLineChart
            data={codexFailures ?? []}
            color="#dc2626"
            seriesLabel="Mislukkingen"
          />
        </ChartCard>
      </div>

      <CodexHealthPanel />

      <ShareFunnel funnel={data.share_funnel} />
    </div>
  );
}

interface ShareFunnelProps {
  funnel: { minted: number; opened: number; adopted: number };
}

function ShareFunnel({ funnel }: ShareFunnelProps) {
  return (
    <div className="bg-white border border-slate-100 artifact-shadow rounded-2xl p-5">
      <span className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">
        Deellinks-funnel (30 dagen)
      </span>
      <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
        <FunnelBox
          label="Gemaakt"
          value={funnel.minted}
          icon={<Link2 className="h-4 w-4 text-blue-600" />}
        />
        <FunnelBox
          label="Geopend"
          value={funnel.opened}
          icon={<Eye className="h-4 w-4 text-blue-600" />}
        />
        <FunnelBox
          label="Overgenomen"
          value={funnel.adopted}
          icon={<UserCheck className="h-4 w-4 text-blue-600" />}
        />
      </div>
    </div>
  );
}

function FunnelBox({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/40 p-4 flex items-center justify-between">
      <div className="flex flex-col">
        <span className="text-xs font-semibold text-slate-500">{label}</span>
        <span className="text-2xl font-bold text-slate-900 tabular-nums">{formatInt(value)}</span>
      </div>
      <div className="rounded-lg bg-white p-2 border border-slate-100">{icon}</div>
    </div>
  );
}
