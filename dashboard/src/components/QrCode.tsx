import QRCode from 'qrcode';
import { useEffect, useState } from 'react';

export function QrCode({ value, label }: { value: string; label: string }) {
  const [svg, setSvg] = useState('');
  useEffect(() => {
    let active = true;
    QRCode.toString(value, { type: 'svg', margin: 1, errorCorrectionLevel: 'M' })
      .then((markup) => {
        if (active) setSvg(markup);
      })
      .catch(() => {
        if (active) setSvg('');
      });
    return () => {
      active = false;
    };
  }, [value]);
  if (!svg) return null;
  // The markup is generated locally by the qrcode library from a plain string.
  return <div className="qr" role="img" aria-label={label} dangerouslySetInnerHTML={{ __html: svg }} />;
}
