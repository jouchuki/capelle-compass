import { useEffect, useMemo, useState } from 'react';
import { motion } from 'motion/react';
import { Link } from 'react-router-dom';
import { Clock, LogOut, Plus, Search } from 'lucide-react';
import { toast } from 'sonner';
import type { ChatMessage, ChatSession } from '../types';
import { deleteSession, listSessions, searchMessages } from '../api/client';

interface SidebarProps {
  activeSessionId: string | null;
  email: string | null;
  /** Triggered when the user wants to start a new session — parent owns the API call. */
  onNewSession: () => void;
  /** Open an existing session. */
  onSelectSession: (id: string) => void;
  /** A bumping counter the parent increments after creating / deleting sessions so this list refetches. */
  refreshKey: number;
  onLogout: () => void;
  /** Jump to a specific message in a session (used for server-side search hits). */
  onJumpToMessage?: (sessionId: string, messageId: string) => void;
}

function initialsFromEmail(email: string): string {
  const local = email.split('@')[0] ?? email;
  const parts = local.split(/[._-]+/).filter(Boolean);
  if (parts.length === 0) return email.slice(0, 2).toUpperCase();
  if (parts.length === 1) {
    const first = parts[0] ?? '';
    return first.slice(0, 2).toUpperCase();
  }
  const a = parts[0]?.[0] ?? '';
  const b = parts[1]?.[0] ?? '';
  return `${a}${b}`.toUpperCase();
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffMin = Math.round((now - then) / 60_000);
  if (diffMin < 1) return 'Net nu';
  if (diffMin < 60) return `${diffMin} min geleden`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH} uur geleden`;
  const diffD = Math.round(diffH / 24);
  if (diffD < 7) return `${diffD} dagen geleden`;
  return new Date(iso).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short' });
}

/**
 * Left rail: brand mark, "+ Nieuwe Analyse", session search, recent sessions
 * list, and the footer user-strip. Filters client-side on the typed query;
 * once the query has >2 chars also fires a server-side message search.
 */
export default function Sidebar({
  activeSessionId,
  email,
  onNewSession,
  onSelectSession,
  refreshKey,
  onLogout,
  onJumpToMessage,
}: SidebarProps) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [query, setQuery] = useState<string>('');
  const [serverHits, setServerHits] = useState<ChatMessage[]>([]);

  useEffect(() => {
    let cancelled = false;
    listSessions()
      .then((r) => {
        if (!cancelled) setSessions(r.sessions);
      })
      .catch(() => {
        if (!cancelled) setSessions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  useEffect(() => {
    if (query.trim().length <= 2) {
      setServerHits([]);
      return;
    }
    let cancelled = false;
    const handle = setTimeout(() => {
      searchMessages(query.trim())
        .then((r) => {
          if (!cancelled) setServerHits(r.messages);
        })
        .catch(() => {
          if (!cancelled) setServerHits([]);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query]);

  const filteredSessions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => s.title.toLowerCase().includes(q));
  }, [query, sessions]);

  const handleDelete = (id: string) => {
    const doDelete = async () => {
      try {
        await deleteSession(id);
        setSessions((prev) => prev.filter((s) => s.id !== id));
        toast.success('Sessie verwijderd');
      } catch {
        toast.error('Er ging iets mis. Probeer het opnieuw.');
      }
    };
    toast('Sessie verwijderen?', {
      description: 'Dit kan niet ongedaan worden gemaakt.',
      action: { label: 'Verwijderen', onClick: () => void doDelete() },
      cancel: { label: 'Annuleren', onClick: () => {} },
    });
  };

  return (
    <motion.aside
      initial={{ x: -340 }}
      animate={{ x: 0 }}
      exit={{ x: -340 }}
      transition={{ type: 'spring', damping: 25, stiffness: 200 }}
      className="w-[340px] h-full bg-white border-r border-slate-100 flex flex-col flex-shrink-0"
    >
      <div className="px-6 pt-6 pb-4 flex-shrink-0">
        <div className="mb-8">
          <Link
            to="/"
            className="text-sm font-bold tracking-[0.1em] text-slate-900 uppercase hover:text-blue-700 transition-colors"
          >
            DataKompas
          </Link>
        </div>

        <button
          type="button"
          onClick={onNewSession}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-slate-50 hover:bg-slate-100 text-slate-700 rounded-lg transition-all text-sm font-semibold mb-3"
        >
          <Plus className="w-4 h-4" />
          Nieuwe Analyse
        </button>

        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-300" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Zoek sessies..."
            className="w-full pl-9 pr-3 py-2 bg-white border border-slate-100 rounded-md text-xs text-slate-700 placeholder:text-slate-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 custom-scrollbar pb-4">
        <h2 className="px-2 mb-3 mt-2 text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em] flex items-center gap-2">
          <Clock className="w-3 h-3" />
          Recente Sessies
        </h2>
        <div className="space-y-1">
          {filteredSessions.length === 0 ? (
            <p className="px-2 py-3 text-xs text-slate-400 italic">
              Geen sessies gevonden.
            </p>
          ) : (
            filteredSessions.map((session) => {
              const active = session.id === activeSessionId;
              return (
                <div key={session.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelectSession(session.id)}
                    className={`w-full text-left pl-3 pr-8 py-2 rounded-md text-sm transition-all ${
                      active
                        ? 'bg-blue-50 text-blue-700 font-medium'
                        : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                    }`}
                  >
                    <div className="truncate">{session.title}</div>
                    <div className="text-[10px] opacity-60 mt-0.5">
                      {relativeTime(session.updated_at)}
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(session.id)}
                    className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 w-8 h-8 flex items-center justify-center text-lg leading-none text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-md transition-all"
                    aria-label="Sessie verwijderen"
                  >
                    ×
                  </button>
                </div>
              );
            })
          )}
        </div>

        {serverHits.length > 0 ? (
          <>
            <h2 className="px-2 mt-6 mb-3 text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em]">
              Matches in berichten
            </h2>
            <div className="space-y-1">
              {serverHits.slice(0, 10).map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() =>
                    onJumpToMessage
                      ? onJumpToMessage(m.session_id, m.id)
                      : onSelectSession(m.session_id)
                  }
                  className="w-full text-left px-3 py-2 rounded-md text-xs text-slate-600 hover:bg-slate-50 hover:text-slate-900 transition-all"
                >
                  <div className="line-clamp-2">{m.content}</div>
                  <div className="text-[10px] opacity-60 mt-1">
                    {relativeTime(m.created_at)}
                  </div>
                </button>
              ))}
            </div>
          </>
        ) : null}
      </div>

      <div className="p-4 border-t border-slate-100 flex-shrink-0">
        <div className="flex items-center gap-3 px-2 py-2">
          <div className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center text-xs font-medium text-slate-600">
            {email ? initialsFromEmail(email) : '··'}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs font-semibold text-slate-900 truncate">
              {email ?? 'Anoniem'}
            </div>
            <div className="text-[10px] text-slate-500 truncate">Beleidsadviseur</div>
          </div>
          <button
            type="button"
            onClick={onLogout}
            className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-slate-400 hover:text-slate-700 transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
            Uit
          </button>
        </div>
      </div>
    </motion.aside>
  );
}
