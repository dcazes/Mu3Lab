import { ChevronRight } from 'lucide-react';
import type { Mu3LabChangelog, Mu3LabUpdate } from '../../api';
import { ExternalButton, LinkButton } from '../../components/Button';
import { Card, PageHeader } from '../../components/Layout';
import { useApi } from '../../lib/useApi';

const count = (n: number, word: string) => (n ? `${n} ${word}${n === 1 ? '' : 's'}` : '');

export const RELEASES_URL = 'https://github.com/dcazes/Mu3Lab/releases';

/**
 * Release notes of the installed version and the few before it, plus what the next update brings.
 * Each release folds open into its developments: one per merged branch or pull request.
 */
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
          description={[
            release.date,
            count(
              release.developments?.length
                ? release.developments.reduce((sum, development) => sum + development.changes.length, 0)
                : release.changes.length,
              'change',
            ),
          ]
            .filter(Boolean)
            .join(' · ')}
          // The installed version opens; older ones stay folded.
          collapsible
          defaultOpen={index === 0}
        >
          {release.developments?.length ? (
            <div className="developments">
              {release.developments.map((development, position) => (
                <details key={`${position}-${development.title}`} className="collapsible" open={position === 0}>
                  <summary>
                    <ChevronRight />
                    <span className="development-title">{development.title}</span>
                    <small className="muted">
                      {[development.branch, development.date, count(development.changes.length, 'change')]
                        .filter(Boolean)
                        .join(' · ')}
                    </small>
                  </summary>
                  <div className="collapsible-body">
                    <ul className="change-list">
                      {development.changes.map((change, item) => (
                        <li key={`${item}-${change}`}>{change}</li>
                      ))}
                    </ul>
                  </div>
                </details>
              ))}
            </div>
          ) : release.changes.length ? (
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
