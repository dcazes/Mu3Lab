import { ArrowUpCircle, Copy, Download, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import {
  api,
  type McpServer,
  postJsonApi,
  type Service,
  type ServiceLogsResponse,
  type UpdateResponse,
} from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { copyText } from '../../components/CopyField';
import { useConfirm } from '../../components/Dialog';
import { Card } from '../../components/Layout';
import { StateBadge } from '../../components/Status';
import { useApi } from '../../lib/useApi';
import { useAction } from '../../lib/useAction';
import { useDashboard, useIsAdmin } from '../../state/dashboard';
import { McpConnectionCard } from '../integrations/McpPanel';
import { BackupsCard, Interrupted } from './BackupsCard';

function Logs({ service }: { service: Service }) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [container, setContainer] = useState('');
  const [tail, setTail] = useState(200);
  const { pending, run } = useAction();
  const load = () =>
    run('logs', async () => {
      const query = `tail=${tail}${container ? `&container=${encodeURIComponent(container)}` : ''}`;
      setLines((await api<ServiceLogsResponse>(`/api/v1/services/${service.id}/logs?${query}`)).lines);
    });
  const download = () => {
    const url = URL.createObjectURL(new Blob([(lines || []).join('\n')], { type: 'text/plain' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${service.id}-logs.txt`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Card
      title="Logs"
      description="Recent output with secrets redacted."
      actions={
        <>
          <select
            className="select-sm"
            aria-label="Container"
            value={container}
            onChange={(event) => setContainer(event.target.value)}
          >
            <option value="">All containers</option>
            {(service.containers || []).map((item) => (
              <option key={item.service} value={item.service}>
                {item.service}
              </option>
            ))}
          </select>
          <select
            className="select-sm"
            aria-label="Lines"
            value={tail}
            onChange={(event) => setTail(Number(event.target.value))}
          >
            {[50, 200, 500].map((value) => (
              <option key={value} value={value}>
                {value} lines
              </option>
            ))}
          </select>
          <Button size="sm" icon={RefreshCw} loading={pending === 'logs'} onClick={() => void load()}>
            {lines ? 'Refresh' : 'Load logs'}
          </Button>
        </>
      }
    >
      {lines && (
        <>
          <pre className="log log-tall">{lines.join('\n') || 'No recent log lines.'}</pre>
          <div className="button-row">
            <Button size="sm" icon={Copy} onClick={() => void copyText(lines.join('\n'), 'Logs copied')}>
              Copy
            </Button>
            <Button size="sm" icon={Download} onClick={download}>
              Download
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}

/** Updating an app, confirmed first; the Installed page's Update button and the Updates card share it. */
export function useApplyUpdate(service: Service) {
  const confirm = useConfirm();
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const apply = async (update: {
    approved_version: string;
    supporting_only: boolean;
    files_only?: boolean;
    source?: string;
  }) => {
    if (
      !(await confirm({
        title: update.files_only
          ? `Update ${service.name} with Mu3Lab’s changes?`
          : update.supporting_only
            ? `Update ${service.name}’s supporting services?`
            : `Update ${service.name} to ${update.approved_version}?`,
        description: [
          update.source === 'upstream' &&
            `${update.approved_version} comes straight from ${service.name}’s makers; Mu3Lab’s automatic checks found nothing in it that needs changes here.`,
          update.files_only
            ? `This version of Mu3Lab changed how ${service.name} is set up; ${service.name} itself stays ${update.approved_version}. Mu3Lab stops ${service.name}, saves a backup of its data, then starts it with the changes. If it doesn't start properly, Mu3Lab puts back the backup and the setup from before automatically. ${service.name} is unavailable for a few minutes.`
            : `Mu3Lab downloads the new version, stops ${service.name}, saves a backup of its data, then starts the new version. If it doesn't start properly, or sign-in, its chat connection or its widgets stop working, Mu3Lab puts back the backup and the current version automatically. ${service.name} is unavailable for a few minutes.`,
          // Core apps reach further than themselves while they are stopped (LiteLLM answers every chat).
          service.stop_warning,
        ]
          .filter(Boolean)
          .join(' '),
        confirmLabel: 'Update',
      }))
    )
      return;
    const result = await run(
      'update',
      () => postJsonApi(`/api/v1/services/${service.id}/actions`, { action: 'update' }),
      `Updating ${service.name}`,
    );
    if (result === undefined) return;
    void refresh();
  };
  return { run: apply, pending: pending === 'update' };
}

/** When the server last looked for a newer release, in words. */
function checkedAgo(when: string): string {
  const at = Date.parse(when);
  if (Number.isNaN(at)) return '';
  const minutes = Math.max(0, Math.round((Date.now() - at) / 60000));
  if (minutes < 2) return 'just now';
  if (minutes < 90) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  return hours < 36 ? `${hours} hours ago` : `${Math.round(hours / 24)} days ago`;
}

function Updates({ service }: { service: Service }) {
  const apply = useApplyUpdate(service);
  const isAdmin = useIsAdmin();
  const checking = useAction();
  const installed = service.update?.installed_version ?? '';
  // A local comparison with the release Mu3Lab approves, so it loads straight away.
  const { data: update, reload } = useApi<UpdateResponse>(
    service.update?.repository ? `/api/v1/services/${service.id}/updates` : null,
  );
  // An update, a restore or a newer Mu3Lab changed what is installed or approved.
  const approved = service.update?.approved_version ?? '';
  const seen = useRef(`${installed} ${approved}`);
  useEffect(() => {
    if (seen.current === `${installed} ${approved}`) return;
    seen.current = `${installed} ${approved}`;
    void reload();
  }, [installed, approved, reload]);
  if (!service.update?.repository) return null;
  const target = update?.files_only
    ? `Mu3Lab’s changes to how ${service.name} is set up (${service.name} stays ${update.approved_version})`
    : update?.supporting_only
      ? `updated supporting services for ${update.approved_version}`
      : update?.approved_version;
  // Only a new release of the app has a version to name and release notes to read.
  const sameVersion = Boolean(update?.supporting_only || update?.files_only);
  // Foundation apps (sign-in, passwords, web addresses) move only when ./install.sh runs.
  const foundation = service.stage === 'foundation';
  const fromMakers = update?.source === 'upstream';
  // A newer release from the app's makers that Mu3Lab's checks held back for review.
  const held = update?.upstream?.state === 'held' ? update.upstream : undefined;
  const checkNow = () =>
    checking.run(
      'check',
      async () => {
        await postJsonApi(`/api/v1/services/${service.id}/updates/check`, {});
        await reload();
      },
      `Checked ${service.name} for a newer release`,
    );
  return (
    <Card
      title="Updates"
      description={foundation ? `Version ${installed || 'unknown'}. Updated by ./install.sh.` : undefined}
    >
      {/* Core apps have no Backups card, so an interrupted update offers its choices here. */}
      {service.stage === 'core' && service.recovery?.kind === 'update' && (
        <Interrupted service={service} busy={apply.pending} />
      )}
      {update && !foundation && installed && !service.recovery && (
        <div className="stack">
          <p>
            {update.update_available && fromMakers
              ? `A newer release from ${service.name}’s makers is ready: ${target}. It passed Mu3Lab’s automatic checks.`
              : update.update_available
                ? `A tested update is ready: ${target}.`
                : `You’re on the latest version Mu3Lab can offer (${update.installed_version}).`}
          </p>
          {!update.update_available && held && (
            <div className="stack">
              <p className="muted">
                {service.name} {held.version} is out, but waits for Mu3Lab’s review because:
              </p>
              <ul className="muted">
                {(held.reasons ?? []).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
          {update.upstream?.checked_at && (
            <p className="muted">Checked for newer releases {checkedAgo(update.upstream.checked_at)}.</p>
          )}
          {update.update_available && (update.removed_services?.length ?? 0) > 0 && (
            <p className="muted">This update removes: {update.removed_services?.join(', ')}.</p>
          )}
          {update.update_available && (update.added_services?.length ?? 0) > 0 && (
            <p className="muted">This update adds: {update.added_services?.join(', ')}.</p>
          )}
          {update.update_available && !update.update_enabled && update.blocked_reason && (
            <p className="muted">{update.blocked_reason}</p>
          )}
          <div className="button-row">
            {update.update_enabled && (
              <Button
                variant="primary"
                icon={ArrowUpCircle}
                loading={apply.pending}
                onClick={() => update && void apply.run(update)}
              >
                {sameVersion ? 'Update' : `Update to ${update.approved_version}`}
              </Button>
            )}
            {update.update_available && update.release_url && !sameVersion && (
              <ExternalButton href={update.release_url}>Release notes</ExternalButton>
            )}
            {!update.update_available && held?.release_url && (
              <ExternalButton href={held.release_url}>Release notes</ExternalButton>
            )}
            {isAdmin && (
              <Button icon={RefreshCw} loading={checking.pending === 'check'} onClick={() => void checkNow()}>
                Check now
              </Button>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

export function AdvancedTab({
  service,
  mcp,
  reloadMcp,
}: {
  service: Service;
  mcp?: McpServer;
  reloadMcp?: () => void;
}) {
  return (
    <div className="stack">
      {service.compose_present && <Logs service={service} />}
      {mcp && <McpConnectionCard server={mcp} reload={reloadMcp || (() => undefined)} />}
      <Updates service={service} />
      {service.stage === 'optional' && <BackupsCard service={service} />}
      {Boolean(service.containers?.length) && (
        <Card title="Containers">
          <div className="rows compact">
            {(service.containers || []).map((container) => (
              <div className="row" key={container.name}>
                <span className="row-text">
                  <b>{container.service}</b>
                  <small>{container.image}</small>
                </span>
                <StateBadge
                  state={container.health && container.health !== 'unknown' ? container.health : container.state}
                />
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
