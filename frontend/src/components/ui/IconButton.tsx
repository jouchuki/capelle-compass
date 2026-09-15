import type { ButtonHTMLAttributes, ReactNode } from 'react';

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  label: string;
}

/**
 * Bare 32×32 icon button — used in the artifact header (download, share, x)
 * and other places where we want a hit target without visible chrome.
 */
export default function IconButton({
  children,
  label,
  className = '',
  ...rest
}: IconButtonProps) {
  return (
    <button
      {...rest}
      aria-label={label}
      title={label}
      className={`p-2 text-slate-400 hover:text-slate-900 transition-colors rounded-md ${className}`}
    >
      {children}
    </button>
  );
}
