import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { Send } from 'lucide-react';

interface ChatInputProps {
  onSend: (content: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

const MIN_HEIGHT = 48;
const MAX_HEIGHT = 220;

/**
 * Auto-growing composer. Single-line by default; expands with content up
 * to MAX_HEIGHT then scrolls internally. Send tile pinned top-right so it
 * sits next to the first line of text regardless of how tall the textarea
 * grows. Enter submits; Shift+Enter inserts a newline.
 */
export default function ChatInput({
  onSend,
  disabled = false,
  placeholder = 'Stel een vraag over Capelle aan den IJssel...',
}: ChatInputProps) {
  const [value, setValue] = useState<string>('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const next = Math.min(Math.max(el.scrollHeight, MIN_HEIGHT), MAX_HEIGHT);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > MAX_HEIGHT ? 'auto' : 'hidden';
  }, [value]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue('');
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form onSubmit={onSubmit} className="max-w-screen-md mx-auto relative">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
        placeholder={placeholder}
        disabled={disabled}
        className="block w-full resize-none bg-white text-slate-900 pl-5 pr-14 py-3 rounded-2xl artifact-shadow border border-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-100 transition-shadow placeholder:text-slate-300 text-sm font-medium leading-6 disabled:opacity-60 align-top"
        style={{ height: `${MIN_HEIGHT}px` }}
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        className="absolute right-2 top-2 w-9 h-9 bg-blue-600 hover:bg-blue-700 text-white rounded-xl flex items-center justify-center transition-all shadow-md shadow-blue-500/20 active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none"
        aria-label="Verstuur bericht"
      >
        <Send className="w-4 h-4" />
      </button>
    </form>
  );
}
