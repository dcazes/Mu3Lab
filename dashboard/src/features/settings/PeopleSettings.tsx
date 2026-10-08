import { UserPlus, Users } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { postJsonApi } from '../../api';
import { Button } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Dialog, useConfirm } from '../../components/Dialog';
import { Callout, Card, EmptyState, PageHeader } from '../../components/Layout';
import { Menu } from '../../components/Menu';
import { QrCode } from '../../components/QrCode';
import { Badge } from '../../components/Status';
import { relativeTime } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

type Role = 'admin' | 'member';

interface Person {
  username: string;
  name: string;
  email: string;
  role: Role;
  active: boolean;
  last_login: string;
  /** Mu3Lab's own record of a removal or demotion while it revokes access. */
  access?: { kind: 'deactivated' | 'demoted'; state: 'pending' | 'complete'; pending: number; detail: string };
}

interface Invite {
  url: string;
  valid_hours: number;
}

const ROLE_TEXT: Record<Role, { label: string; detail: string }> = {
  member: {
    label: 'Household member',
    detail: 'Uses the dashboard, chat and every app, and can install apps. Cannot change system settings.',
  },
  admin: {
    label: 'Administrator',
    detail: 'Everything a member can do, plus system settings, AI providers, removing apps and managing people.',
  },
};

function InviteLink({ invite, name }: { invite: Invite; name: string }) {
  return (
    <div className="stack">
      <p>
        Send this link to {name}. It signs them in once, then they choose their own password in Authentik under{' '}
        <b>Settings → Change password</b>. It works once and expires in {invite.valid_hours} hours.
      </p>
      <CopyField value={invite.url} label="invite link" />
      <p className="muted">Or let them scan it with their phone (on your private network):</p>
      <QrCode value={invite.url} label={`Invite link for ${name}`} />
    </div>
  );
}

function AddPersonDialog({ open, onClose, added }: { open: boolean; onClose: () => void; added: () => void }) {
  const { pending, run } = useAction();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('member');
  const [invite, setInvite] = useState<Invite | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await run(
      'add',
      () => postJsonApi<{ invite: Invite }>('/api/v1/people', { name, email, role }),
      `${name || email} added`,
    );
    if (result) {
      setInvite(result.invite);
      added();
    }
  };
  const close = () => {
    setName('');
    setEmail('');
    setRole('member');
    setInvite(null);
    onClose();
  };
  return (
    <Dialog open={open} onClose={close} title={invite ? `Invite ${name || email}` : 'Add a person'}>
      {invite ? (
        <InviteLink invite={invite} name={name || email} />
      ) : (
        <form className="form" onSubmit={submit}>
          <label className="field">
            <span>Name</span>
            <input value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" />
          </label>
          <label className="field">
            <span>
              Email <em>required</em>
            </span>
            <input
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="off"
            />
          </label>
          <fieldset className="field">
            <legend>What they can do</legend>
            {(Object.keys(ROLE_TEXT) as Role[]).map((value) => (
              <label className="radio-row" key={value}>
                <input
                  type="radio"
                  name="role"
                  value={value}
                  checked={role === value}
                  onChange={() => setRole(value)}
                />
                <span>
                  <b>{ROLE_TEXT[value].label}</b>
                  <small>{ROLE_TEXT[value].detail}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <div>
            <Button type="submit" variant="primary" icon={UserPlus} loading={pending === 'add'}>
              Add and create invite link
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

export function PeopleSettings() {
  const confirm = useConfirm();
  const { data } = useDashboard();
  const { run } = useAction();
  const [revoking, setRevoking] = useState(false);
  // Poll quickly while a revocation is still running so its progress is visible.
  const people = useApi<{ people: Person[] }>('/api/v1/people', { interval: revoking ? 5000 : 30000 });
  const [adding, setAdding] = useState(false);
  const [invite, setInvite] = useState<{ invite: Invite; name: string } | null>(null);
  const list = people.data?.people || [];
  const pending = list.some((person) => person.access?.state === 'pending');
  if (pending !== revoking) setRevoking(pending);

  const change = async (person: Person, action: 'role' | 'invite' | 'deactivate' | 'reactivate', role?: Role) => {
    const who = person.name || person.username;
    if (
      action === 'deactivate' &&
      !(await confirm({
        title: `Remove ${who}?`,
        description:
          'Mu3Lab stops their access at once, then ends their sign-in and sessions and revokes their voice key and chat tools, retrying until each is done. Some apps’ own mobile logins can keep working until they expire. Their account and what they saved in apps are kept, so you can bring them back later.',
        confirmLabel: 'Remove',
        tone: 'danger',
      }))
    )
      return;
    const result = await run(
      `${person.username}-${action}`,
      () =>
        postJsonApi<{ invite?: Invite }>(`/api/v1/people/${encodeURIComponent(person.username)}/${action}`, {
          role,
        }),
      action === 'invite' ? undefined : `${who} updated`,
    );
    if (result?.invite) setInvite({ invite: result.invite, name: who });
    void people.reload();
  };

  return (
    <>
      <PageHeader
        title="People"
        description="Choose who can use this Mu3Lab. Everyone signs in with their own account."
        actions={
          <Button variant="primary" icon={UserPlus} onClick={() => setAdding(true)}>
            Add person
          </Button>
        }
      />
      <Callout title="What people can see">
        Apps keep each person's data separate where the app supports it (their own photos, files and documents). Things
        shared inside an app, like a shared album or folder, are visible to whoever it is shared with.
      </Callout>
      <Card title="People" flush>
        {people.error ? (
          <p className="error-text card-pad">Could not load people: {people.error}</p>
        ) : !people.data ? (
          <p className="muted card-pad">Loading…</p>
        ) : list.length === 0 ? (
          <EmptyState icon={Users} title="Nobody yet">
            Add the people in your household.
          </EmptyState>
        ) : (
          <div className="rows">
            {list.map((person) => {
              const self = person.username === data.identity.username;
              return (
                <div className="row" key={person.username}>
                  <span className="row-text">
                    <b>
                      {person.name || person.username}
                      {self && <Badge>You</Badge>}
                    </b>
                    <small>
                      {person.email}
                      {' · '}
                      {!person.active
                        ? 'removed'
                        : person.last_login
                          ? `last signed in ${relativeTime(person.last_login)}`
                          : 'has not signed in yet'}
                    </small>
                    {person.access?.state === 'pending' && (
                      <small className="warning-text" role="status">
                        {person.access.detail}
                      </small>
                    )}
                  </span>
                  <Badge tone={person.role === 'admin' ? 'blue' : 'gray'}>{ROLE_TEXT[person.role].label}</Badge>
                  <Menu
                    label={`${person.name || person.username} actions`}
                    items={[
                      ...(person.active
                        ? [
                            {
                              label: person.role === 'admin' ? 'Make household member' : 'Make administrator',
                              onSelect: () => void change(person, 'role', person.role === 'admin' ? 'member' : 'admin'),
                              disabled: self,
                            },
                            { label: 'Create a new invite link', onSelect: () => void change(person, 'invite') },
                            {
                              label: 'Remove',
                              danger: true,
                              disabled: self,
                              onSelect: () => void change(person, 'deactivate'),
                            },
                          ]
                        : [{ label: 'Bring back', onSelect: () => void change(person, 'reactivate') }]),
                    ]}
                  />
                </div>
              );
            })}
          </div>
        )}
      </Card>
      <AddPersonDialog open={adding} onClose={() => setAdding(false)} added={() => void people.reload()} />
      <Dialog open={Boolean(invite)} onClose={() => setInvite(null)} title={`Invite ${invite?.name || ''}`}>
        {invite && <InviteLink invite={invite.invite} name={invite.name} />}
      </Dialog>
    </>
  );
}
