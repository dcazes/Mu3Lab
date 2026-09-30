import { Download, Laptop, Smartphone } from 'lucide-react';
import type { Service } from '../../api';
import { ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Card } from '../../components/Layout';
import { QrCode } from '../../components/QrCode';
import { companionFor } from '../../lib/companions';

export function DevicesSection({ service, address }: { service: Service; address: string }) {
  const companion = companionFor(service.id);
  return (
    <>
      <Card
        title="Server address"
        description={
          companion?.serverHint ||
          `Open this address on a device with access to your Tailscale network to use ${service.name}.`
        }
      >
        <div className="device-address">
          <div className="stack">
            <CopyField value={address} label="Server address" />
            <p className="muted">
              Connect your device to an authorized Tailscale network before opening this address. Scan the QR code with
              your phone’s camera.
            </p>
          </div>
          <QrCode value={address} label={`QR code for ${address}`} />
        </div>
      </Card>
      {companion ? (
        <Card title="Official apps" description={companion.note}>
          <div className="button-row">
            {companion.apps.map((app) => (
              <ExternalButton key={app.platform} icon={Download} href={app.url}>
                {app.platform}
              </ExternalButton>
            ))}
          </div>
        </Card>
      ) : (
        <Card
          title="Browser shortcuts"
          description={`Add a shortcut to ${service.name} for convenient access. Browser installation and window behavior depend on the app, browser, and device.`}
        >
          <ul className="steps">
            <li>
              <Laptop />
              <span>
                <b>Computer (Chrome or Edge)</b>Open {service.name} and use the browser’s install option if available,
                or bookmark the page.
              </span>
            </li>
            <li>
              <Smartphone />
              <span>
                <b>Android</b>Open it in Chrome, tap ⋮, then <i>Add to Home screen</i>.
              </span>
            </li>
            <li>
              <Smartphone />
              <span>
                <b>iPhone or iPad</b>Open it in Safari, tap Share, then <i>Add to Home Screen</i>.
              </span>
            </li>
          </ul>
        </Card>
      )}
    </>
  );
}
