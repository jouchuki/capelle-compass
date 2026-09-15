import { Component, type ErrorInfo, type ReactNode } from 'react';
import { RefreshCw } from 'lucide-react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

/**
 * Last-line error boundary for the entire app tree. Catches render errors
 * thrown by any descendant and renders a calm light-card fallback instead
 * of a white screen. Intentionally does NOT call ``recordEvent`` — a
 * telemetry failure during a render crash would risk a remount loop, so
 * we keep the boundary entirely client-local.
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Sanctioned console.error: this is the last point at which a render
    // crash can be observed before the boundary swallows it. Beaconing
    // to the server here would risk a feedback loop, so we log locally.
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="min-h-screen flex items-center justify-center bg-white px-6">
        <div className="max-w-md w-full bg-white rounded-2xl border border-slate-100 artifact-shadow p-10 text-center">
          <p className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">
            Er ging iets mis
          </p>
          <h1 className="mt-4 text-2xl font-bold text-slate-900 tracking-tight">
            We konden deze pagina niet laden
          </h1>
          <p className="mt-3 text-sm text-slate-500 leading-relaxed">
            Er trad een onverwachte fout op. Herlaad de pagina om opnieuw te beginnen — je sessie blijft bewaard.
          </p>
          <button
            type="button"
            onClick={this.handleReload}
            className="mt-8 inline-flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-3 px-6 text-xs font-bold uppercase tracking-wider transition-all"
          >
            <RefreshCw className="w-4 h-4" aria-hidden="true" />
            Herlaad pagina
          </button>
        </div>
      </div>
    );
  }
}
