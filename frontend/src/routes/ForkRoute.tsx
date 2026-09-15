import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Loader2 } from 'lucide-react';
import { motion } from 'motion/react';
import { useAuth } from '../hooks/useAuth';
import { adoptFork, ApiError, getForkView, type ForkViewResponse } from '../api/client';
import type { AnalysisResult, ChatMessage } from '../types';
import ReportArtifact from '../components/artifact/ReportArtifact';

// Mock router returns an extra ``analysis`` field — the typed contract
// only guarantees messages, so we read it defensively.
type ForkViewWithAnalysis = ForkViewResponse & { analysis?: AnalysisResult };

function pickAnalysis(view: ForkViewWithAnalysis): AnalysisResult | null {
  if (view.analysis) return view.analysis;
  for (const m of view.messages) if (m.metadata?.analysis) return m.metadata.analysis;
  return null;
}

function pickOriginalQuestion(messages: ChatMessage[]): string | null {
  return messages.find((m) => m.role === 'user' && m.content.trim().length > 0)?.content ?? null;
}

const noop = (): void => undefined;

/**
 * Read-only shared analysis page. The ``:token`` IS the capability — no auth
 * to view, but adopting (cloning into the user's own sessions) gates on sign-in.
 */
export default function ForkRoute() {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const [view, setView] = useState<ForkViewWithAnalysis | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [adopting, setAdopting] = useState<boolean>(false);
  const [showQuestion, setShowQuestion] = useState<boolean>(false);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      setError({ status: 404, message: 'Geen token in de URL.' });
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getForkView(token)
      .then((response) => {
        if (cancelled) return;
        setView(response as ForkViewWithAnalysis);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setError({ status: err.status, message: typeof err.detail === 'string' ? err.detail : err.message });
        } else {
          setError({ status: 500, message: 'Kon de gedeelde analyse niet laden.' });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const handleAdopt = async () => {
    if (!token || adopting) return;
    setAdopting(true);
    try {
      const result = await adoptFork(token);
      navigate(`/?session=${encodeURIComponent(result.session_id)}`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? typeof err.detail === 'string'
            ? err.detail
            : err.message
          : 'Overnemen mislukt. Probeer het later opnieuw.';
      setError({ status: 500, message: msg });
      setAdopting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white">
        <Loader2 className="w-5 h-5 animate-spin text-slate-300" aria-label="Laden" />
      </div>
    );
  }

  if (!view) {
    return <ForkErrorState error={error} />;
  }

  const analysis = pickAnalysis(view);
  const originalQuestion = pickOriginalQuestion(view.messages);
  const adoptHref = `/login?return=${encodeURIComponent(`/f/${token ?? ''}`)}`;
  const btnClass =
    'inline-flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-5 py-2.5 text-xs font-bold uppercase tracking-wider transition-all disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0';

  return (
    <div className="min-h-screen bg-white">
      <div className="sticky top-0 z-20 bg-white/95 backdrop-blur border-b border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">Gedeelde analyse</p>
            <h1 className="mt-1 text-sm font-bold text-slate-900 truncate">{view.session.title}</h1>
          </div>
          {isAuthenticated ? (
            <button type="button" onClick={handleAdopt} disabled={adopting} className={btnClass}>
              {adopting ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Overneem deze analyse'}
            </button>
          ) : (
            <Link to={adoptHref} className={btnClass}>
              Inloggen om over te nemen
            </Link>
          )}
        </div>
      </div>

      <motion.main
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', damping: 30, stiffness: 200 }}
        className="max-w-4xl mx-auto px-6 py-10"
      >
        {/* ReportArtifact is a side-pane; we frame it inline at fixed height
         *  per spec. Fork is read-only so prev/next/follow-up are no-ops. */}
        {analysis ? (
          <div className="rounded-2xl overflow-hidden border border-slate-100 artifact-shadow h-[80vh]">
            <ReportArtifact
              analysis={analysis}
              sessionId={view.session.id}
              totalAnalyses={1}
              currentIndex={0}
              onPrev={noop}
              onNext={noop}
              onClose={() => navigate('/')}
              onAskFollowUp={noop}
            />
          </div>
        ) : (
          <div className="text-sm text-slate-500 italic">
            Deze sessie bevat nog geen voltooide analyse.
          </div>
        )}

        {originalQuestion ? (
          <section className="mt-12 border-t border-slate-100 pt-8">
            <button
              type="button"
              onClick={() => setShowQuestion((value) => !value)}
              aria-expanded={showQuestion}
              className="text-[11px] font-bold uppercase tracking-wider text-slate-500 hover:text-slate-900 transition-colors"
            >
              {showQuestion ? 'Verberg oorspronkelijke vraag' : 'Bekijk de oorspronkelijke vraag'}
            </button>
            {showQuestion ? (
              <p className="mt-3 text-sm text-slate-600 leading-relaxed bg-slate-50 border border-slate-100 rounded-xl p-4 whitespace-pre-wrap">
                {originalQuestion}
              </p>
            ) : null}
          </section>
        ) : null}
      </motion.main>
    </div>
  );
}

function ForkErrorState({ error }: { error: { status: number; message: string } | null }) {
  const isNotFound = error?.status === 404;
  return (
    <div className="min-h-screen flex items-center justify-center bg-white px-4">
      <div className="max-w-md w-full text-center">
        <p className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">
          {isNotFound ? 'Gedeelde analyse' : 'Er ging iets mis'}
        </p>
        <h1 className="mt-3 text-2xl font-bold text-slate-900 tracking-tight">
          {isNotFound ? 'Deze gedeelde analyse bestaat niet meer.' : 'Kon de gedeelde analyse niet laden.'}
        </h1>
        <p className="mt-3 text-sm text-slate-500">
          {isNotFound ? 'De link is verlopen of ingetrokken.' : error?.message ?? ''}
        </p>
        <Link
          to="/"
          className="mt-8 inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-blue-700 hover:text-blue-900 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Terug naar DataKompas
        </Link>
      </div>
    </div>
  );
}
