import type { Mu3LabChangelog, Mu3LabUpdate } from '../../api';
import { ExternalButton, LinkButton } from '../../components/Button';
import { Card, PageHeader } from '../../components/Layout';
import { useApi } from '../../lib/useApi';

export const RELEASES_URL = 'https://github.com/dcazes/Mu3Lab/releases';

/** Release notes of the installed version and the few before it, plus what the next update brings. */
export function WhatsNew() {
  const changelog = useApi<Mu3LabChangelog>('/api/v1/system/changelog');
  const update = useApi<Mu3LabUpdate>('/api/v1/system/update').data;
  const releases = changelog.data?.releases ?? [];
  return (
    <>
      <PageHeader
        title="What’s new"
        description="What changed in this version of Mu3Lab and the ones before it."
        actions={<ExternalButton href={RELEASES_URL}>All releases on GitHub</ExternalButton>}
      />
      {update?.available && (
        <Card
          title={`Coming in the next update (${update.behind === 1 ? 'one change' : `${update.behind} changes`})`}
          actions={
            <LinkButton size="sm" variant="primary" to="/settings/system">
              Update
            </LinkButton>
          }
        >
          <ul className="change-list">
            {update.changes.map((change) => (
              <li key={change}>{change}</li>
            ))}
          </ul>
        </Card>
      )}
      {changelog.error && <p className="muted">{changelog.error}</p>}
      {!changelog.data && !changelog.error && <p className="muted">Loading release notes…</p>}
      {releases.map((release, index) => (
        <Card
          key={release.version || release.commit}
          title={
            release.version ? `Version ${release.version}${index === 0 ? ' (installed)' : ''}` : 'This development copy'
          }
          description={release.date || undefined}
        >
          {release.changes.length ? (
            <ul className="change-list">
              {release.changes.map((change, position) => (
                <li key={`${position}-${change}`}>{change}</li>
              ))}
            </ul>
          ) : (
            <p className="muted">No changes recorded.</p>
          )}
        </Card>
      ))}
    </>
  );
}
