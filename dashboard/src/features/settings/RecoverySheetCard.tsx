import { FileKey, Printer, ShieldAlert } from 'lucide-react';
import QRCode from 'qrcode';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { postJsonApi, type RecoverySheet, type RecoverySheetStatus } from '../../api';
import { Button } from '../../components/Button';
import { Dialog, useConfirm } from '../../components/Dialog';
import { Callout, Card, Facts } from '../../components/Layout';
import { relativeTime } from '../../lib/format';
import { useAction } from '../../lib/useAction';

const GUIDE = 'https://github.com/dcazes/Mu3Lab/blob/main/docs/backup-recovery.md';

/** Eight-character groups, so the password can be read aloud and typed back from paper. */
export function groups(password: string): string[] {
  return password.match(/.{1,8}/g) ?? [];
}

type Opened = RecoverySheet & { qr: string };

function SheetBody({ sheet }: { sheet: Opened }) {
  const generated = new Date(sheet.generated_at).toLocaleString();
  return (
    <div className="recovery-sheet">
      <header>
        <h1>Mu3Lab recovery sheet</h1>
        <p>
          {sheet.server || 'This server'} · printed {generated} · Mu3Lab {sheet.version}
        </p>
      </header>
      <p className="recovery-warning">
        Keep this sheet private and somewhere safe, with your other important papers. With it and a copy of the backups,
        anyone can read everything Mu3Lab stores.
      </p>
      <section>
        <h2>1. Backup password</h2>
        <p>Every backup is encrypted with this password. Without it, nobody can open them, you included.</p>
        <div className="recovery-password">
          <ol className="recovery-groups" aria-label="Backup password in groups of eight characters">
            {groups(sheet.password).map((group, index) => (
              <li key={index}>{group}</li>
            ))}
          </ol>
          {sheet.qr && (
            // Generated locally by the qrcode library from the password string, before printing.
            <div
              className="qr"
              role="img"
              aria-label="Backup password as a QR code"
              dangerouslySetInnerHTML={{ __html: sheet.qr }}
            />
          )}
        </div>
        <p className="muted">
          Type it without the spaces, or scan the code. Check code <b>{sheet.password_check}</b>: the rebuild guide
          shows how to confirm a typed password matches it.
        </p>
      </section>
      <section>
        <h2>2. Where the backups are</h2>
        <p>
          On this server, in <code>{sheet.repository}</code>.
        </p>
        {sheet.copies.length ? (
          <ul>
            {sheet.copies.map((copy) => (
              <li key={copy}>{copy}</li>
            ))}
          </ul>
        ) : (
          <p>
            <b>There is no copy on another computer yet.</b> If this server’s disk fails, the backups are lost with it.
          </p>
        )}
      </section>
      <section>
        <h2>3. What they hold</h2>
        <ul>
          <li>Mu3Lab’s settings, its people, and every app’s passwords and keys.</li>
          <li>Sign-in (Authentik), the password vault (Vaultwarden) and chat.</li>
          <li>Each app’s own data. Apps themselves are downloaded again; photos and other media only if you chose.</li>
        </ul>
      </section>
      <section>
        <h2>4. Other copies of the password</h2>
        <p>
          Each administrator’s Bitwarden holds it as <b>Mu3Lab backup password</b>, under Mu3Lab app logins. Bitwarden
          keeps it on their phone and computer even when this server is off.
        </p>
      </section>
      <section>
        <h2>5. If this server is lost</h2>
        <p>
          Follow “Rebuilding on a new computer” in the recovery guide: <code>{GUIDE}</code>
        </p>
        <p className="muted">
          Technical details: Kopia filesystem repository, image <code>{sheet.kopia_image}</code>.
        </p>
      </section>
    </div>
  );
}

/** The backup password on paper and in administrators' vaults: what rebuilding on a new computer needs. */
export function RecoverySheetCard({ status, onOpened }: { status?: RecoverySheetStatus | null; onOpened: () => void }) {
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [sheet, setSheet] = useState<Opened | null>(null);
  const [printing, setPrinting] = useState(false);

  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(false);
    window.addEventListener('afterprint', done);
    document.body.classList.add('printing-recovery-sheet');
    window.print();
    return () => {
      window.removeEventListener('afterprint', done);
      document.body.classList.remove('printing-recovery-sheet');
    };
  }, [printing]);

  const open = async () => {
    if (
      !(await confirm({
        title: 'Show the recovery sheet?',
        description:
          'It shows the backup password on this screen. Open it where nobody can look over your shoulder. Mu3Lab records that you opened it.',
        confirmLabel: 'Show it',
      }))
    )
      return;
    const result = await run('sheet', () => postJsonApi<RecoverySheet>('/api/v1/system/backups/recovery-sheet', {}));
    if (result) {
      // Drawn now, so printing never waits on it.
      const qr = await QRCode.toString(result.password, { type: 'svg', margin: 1, errorCorrectionLevel: 'M' }).catch(
        () => '',
      );
      setSheet({ ...result, qr });
      onOpened();
    }
  };
  // The password lives only in this component's memory, never in browser storage.
  const close = () => setSheet(null);

  return (
    <Card
      title="Recovery sheet"
      description="The backup password and where the backups are. A new computer needs both to open your backups."
      actions={
        <Button size="sm" icon={FileKey} loading={pending === 'sheet'} onClick={() => void open()}>
          Open recovery sheet
        </Button>
      }
    >
      {status ? (
        <Facts
          items={[
            { label: 'Last opened', value: `${relativeTime(status.opened_at)} by ${status.opened_by}` },
            { label: 'Vault copy', value: 'In every administrator’s Bitwarden, as “Mu3Lab backup password”' },
          ]}
        />
      ) : (
        <Callout tone="warning" icon={ShieldAlert} title="Print your recovery sheet">
          Mu3Lab keeps the backup password on this server. If the server is lost, you need the printed sheet or your
          Bitwarden copy to open the backups.
        </Callout>
      )}
      <Dialog
        open={Boolean(sheet)}
        onClose={close}
        size="lg"
        title="Recovery sheet"
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Close
            </Button>
            <Button icon={Printer} onClick={() => setPrinting(true)}>
              Print
            </Button>
          </>
        }
      >
        {sheet && <SheetBody sheet={sheet} />}
      </Dialog>
      {sheet && printing && createPortal(<SheetBody sheet={sheet} />, document.body)}
    </Card>
  );
}
