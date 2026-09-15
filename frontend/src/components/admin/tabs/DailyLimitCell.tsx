import { useState } from 'react';
import { Pencil, Check, X, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { ApiError } from '../../../api/client';
import { updateUserDailyLimit } from '../adminFetch';

interface DailyLimitCellProps {
  userId: string;
  value: number | null;
  onSaved: (newValue: number | null) => void;
}

/**
 * Inline editor for the per-user ``daily_limit_override``.
 *
 *   null → "Standaard" (user falls back to the global default)
 *   0    → "Uitgeschakeld" (explicitly unlimited)
 *   N    → "N analyses/dag"
 *
 * Tab through "Standaard" / "Uitgeschakeld" / "Aangepast" radios, type
 * a number when "Aangepast" is picked, then Save. ApiError details
 * surface in-row so the operator never has to read the network tab.
 */
export default function DailyLimitCell({ userId, value, onSaved }: DailyLimitCellProps) {
  const [editing, setEditing] = useState(false);

  if (!editing) {
    return (
      <div className="flex items-center gap-2 justify-between">
        <span className="text-slate-700 tabular-nums">{renderValue(value)}</span>
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          aria-label="Wijzig dagelijkse limiet"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  }

  return (
    <DailyLimitEditor
      userId={userId}
      initial={value}
      onCancel={() => setEditing(false)}
      onSaved={(v) => {
        onSaved(v);
        setEditing(false);
      }}
    />
  );
}

function renderValue(value: number | null): string {
  if (value === null) return 'Standaard';
  if (value === 0) return 'Uitgeschakeld';
  return `${value}/dag`;
}

type Mode = 'default' | 'unlimited' | 'custom';

function initialMode(value: number | null): Mode {
  if (value === null) return 'default';
  if (value === 0) return 'unlimited';
  return 'custom';
}

interface EditorProps {
  userId: string;
  initial: number | null;
  onCancel: () => void;
  onSaved: (newValue: number | null) => void;
}

function DailyLimitEditor({ userId, initial, onCancel, onSaved }: EditorProps) {
  const [mode, setMode] = useState<Mode>(initialMode(initial));
  const [custom, setCustom] = useState<string>(
    initial !== null && initial > 0 ? String(initial) : '10',
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setError(null);
    let payload: number | null;
    if (mode === 'default') payload = null;
    else if (mode === 'unlimited') payload = 0;
    else {
      const parsed = parseInt(custom, 10);
      if (!Number.isFinite(parsed) || parsed < 1) {
        setError('Voer een geheel getal van 1 of hoger in.');
        return;
      }
      payload = parsed;
    }
    setSaving(true);
    try {
      await updateUserDailyLimit(userId, payload);
      onSaved(payload);
      toast.success('Limiet bijgewerkt');
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        const detail = err.detail;
        if (typeof detail === 'string') setError(detail);
        else if (detail && typeof detail === 'object' && 'message' in detail) {
          setError(String((detail as { message: unknown }).message));
        } else {
          setError(`HTTP ${err.status}`);
        }
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Onbekende fout');
      }
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <ModeRadio
          name={`limit-${userId}`}
          checked={mode === 'default'}
          onChange={() => setMode('default')}
          label="Standaard"
        />
        <ModeRadio
          name={`limit-${userId}`}
          checked={mode === 'unlimited'}
          onChange={() => setMode('unlimited')}
          label="Uitgeschakeld"
        />
        <ModeRadio
          name={`limit-${userId}`}
          checked={mode === 'custom'}
          onChange={() => setMode('custom')}
          label="Aangepast"
        />
        {mode === 'custom' ? (
          <input
            type="number"
            min={1}
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            className="w-16 rounded-md border border-slate-200 px-2 py-1 text-sm tabular-nums focus:border-blue-500 focus:outline-none"
          />
        ) : null}
        <div className="flex items-center gap-1 ml-auto">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="rounded-md bg-blue-600 px-2 py-1 text-white hover:bg-blue-700 disabled:opacity-50 inline-flex items-center gap-1"
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
            Opslaan
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="rounded-md border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-50 inline-flex items-center gap-1"
          >
            <X className="h-3 w-3" />
            Annuleer
          </button>
        </div>
      </div>
      {error ? <span className="text-[11px] text-rose-600">{error}</span> : null}
    </div>
  );
}

interface ModeRadioProps {
  name: string;
  checked: boolean;
  onChange: () => void;
  label: string;
}

function ModeRadio({ name, checked, onChange, label }: ModeRadioProps) {
  return (
    <label
      className={`inline-flex items-center gap-1 cursor-pointer rounded-md px-2 py-1 border ${
        checked
          ? 'border-blue-200 bg-blue-50 text-blue-700'
          : 'border-slate-200 text-slate-600 hover:bg-slate-50'
      }`}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        onChange={onChange}
        className="sr-only"
      />
      {label}
    </label>
  );
}
