import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

export async function copyText(value: string, label = 'Copied') {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(value);
    toast.success(label);
    return true;
  } catch {
    toast.error('Could not copy to the clipboard.');
    return false;
  }
}

export function CopyField({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy-field">
      <code title={value}>{value}</code>
      <button
        type="button"
        className="btn btn-ghost btn-sm btn-icon"
        aria-label={`Copy ${label}`}
        onClick={async () => {
          if (await copyText(value, `${label} copied`)) {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          }
        }}
      >
        {copied ? <Check /> : <Copy />}
      </button>
    </div>
  );
}
