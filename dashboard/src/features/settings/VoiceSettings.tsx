import { KeyRound, Mic, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { deleteApi, postApi, type VoiceKeyCreated, type VoiceKeyStatus } from '../../api';
import { Button, LinkButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card, EmptyState, Facts, PageHeader } from '../../components/Layout';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';

export function VoiceSettings() {
  const status = useApi<VoiceKeyStatus>('/api/v1/voice-key');
  const [created, setCreated] = useState<VoiceKeyCreated | null>(null);
  const { pending, run } = useAction();
  const confirm = useConfirm();
  const data = status.data;

  const create = async () => {
    if (
      data?.exists &&
      !(await confirm({
        title: 'Replace your voice key?',
        description: 'Devices using your current key stop working until you give them the new one.',
        confirmLabel: 'Replace key',
      }))
    )
      return;
    const result = await run('create', () => postApi<VoiceKeyCreated>('/api/v1/voice-key'), 'Voice key created');
    if (result) setCreated(result);
    void status.reload();
  };
  const revoke = async () => {
    if (
      !(await confirm({
        title: 'Turn off your voice key?',
        description: 'Every device and program using it stops reaching Mu3Lab speech.',
        confirmLabel: 'Turn off',
        tone: 'danger',
      }))
    )
      return;
    await run('revoke', () => deleteApi('/api/v1/voice-key'), 'Voice key turned off');
    setCreated(null);
    void status.reload();
  };

  return (
    <>
      <PageHeader
        title="Voice key"
        description="Use Mu3Lab's private speech on your own devices: dictate into any program, or have text read aloud. Your voice never leaves your server."
      />
      {data && !data.available ? (
        <EmptyState
          icon={Mic}
          title="Speech isn’t installed"
          action={<LinkButton to="/apps/speaches">View Speech</LinkButton>}
        >
          Install Speech first; your voice key uses it.
        </EmptyState>
      ) : (
        <Card
          title="Your personal key"
          description="A key works only for speech, only for you, and only while your device is connected to the Mu3Lab tailnet. Mu3Lab shows it once; keep it in your vault."
        >
          {created && (
            <Callout tone="success" icon={KeyRound} title="Copy your key now">
              It is shown only this once. Save it in Bitwarden, then paste it into your dictation app.
              <CopyField value={created.key} label="Voice key" />
            </Callout>
          )}
          {data && (
            <Facts
              items={[
                {
                  label: 'Address for apps',
                  value: data.base_url ? <CopyField value={data.base_url} label="Address" /> : '—',
                },
                { label: 'Speech to text model', value: data.models[0] ?? '—' },
                { label: 'Text to speech model', value: data.models[1] ?? '—' },
                {
                  label: 'Key',
                  value: data.exists
                    ? data.created_at
                      ? `Active since ${new Date(data.created_at).toLocaleDateString()}`
                      : 'Active'
                    : 'None yet',
                },
              ]}
            />
          )}
          <div className="button-row">
            <Button variant="primary" icon={KeyRound} loading={pending === 'create'} onClick={create}>
              {data?.exists ? 'Replace key' : 'Create key'}
            </Button>
            {data?.exists && (
              <Button variant="danger" icon={Trash2} loading={pending === 'revoke'} onClick={revoke}>
                Turn off key
              </Button>
            )}
          </div>
          <Callout tone="info" title="How to use it">
            Any app that supports an “OpenAI-compatible” speech service works: enter the address above, your key, and
            the model name. For example, a desktop dictation app types what you say into Google Docs or LibreOffice.
          </Callout>
        </Card>
      )}
    </>
  );
}
