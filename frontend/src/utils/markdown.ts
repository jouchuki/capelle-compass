/**
 * Minimal Markdown → HTML converter for agent narratives.
 *
 * Handles only the subset used by the agent reports:
 * headings, bold, italic, unordered lists, line breaks.
 * Escapes HTML first to prevent injection.
 */
export function renderMarkdown(md: string): string {
  if (!md) return '';
  let html = md
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  const lines = html.split('\n');
  const out: string[] = [];
  let inList = false;
  for (const line of lines) {
    if (/^- (.+)$/.test(line)) {
      if (!inList) {
        out.push('<ul>');
        inList = true;
      }
      out.push('<li>' + line.replace(/^- /, '') + '</li>');
    } else {
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
      out.push(line);
    }
  }
  if (inList) out.push('</ul>');

  return out.join('\n').replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br/>');
}
