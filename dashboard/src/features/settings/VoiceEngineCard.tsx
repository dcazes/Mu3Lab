import { type FormEvent, useCallback, useState } from 'react';
import { type AiModels, type AiVoiceEngineRequest, type JobResponse, putJsonApi } from '../../api';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { useAction } from '../../lib/useAction';
import type { ApiState } from '../../lib/useApi';
import { JobLog } from './AiModelsCard';

type Engine = AiVoiceEngineRequest['engine'];

/** What switching on stops, in words for the person choosing. */
export function duplexStops(stops: string[]): string {
  if (!stops.length) return 'No other app uses the graphics card now.';
  return `These apps stop until you switch back: ${stops.join(', ')}.`;
}

/** Labs: one full-duplex model instead of separate steps; it holds the whole graphics card. */
export function VoiceEngineCard({ api, readOnly = false }: { api: ApiState<AiModels>; readOnly?: boolean }) {
  const { data, error, reload } = api;
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [draft, setDraft] = useState<Engine | null>(null);
  const [jobId, setJobId] = useState('');
  const [running, setRunning] = useState(false);
  const finished = useCallback(
    (lost: boolean) => {
      setRunning(false);
      if (lost) setJobId('');
      void reload();
    },
    [reload],
  );
  const title = (
    <>
      Labs: full-duplex mode <Badge tone="amber">Experimental</Badge>
    </>
  );
  const description =
    'One model listens and speaks at the same time, like a phone call, instead of separate steps. It cannot use your apps and needs the whole graphics card.';
  if (!data)
    return (
      <Card title={title} description={description} collapsible defaultOpen={false}>
        {error ? <p className="error-text">Could not load: {error}</p> : <p className="muted card-loading">Loading…</p>}
      </Card>
    );
  const saved = data.voice_engine;
  const chosen = draft ?? saved.engine;
  const busy = running || Boolean(data.active_job_id);
  const edited = chosen !== saved.engine;
  const unavailable = saved.engine === 'pipeline' ? saved.unavailable_reason : '';

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const on = chosen === 'duplex';
    const download = saved.downloaded
      ? ''
      : ` ${saved.model} is downloaded first (${saved.download_gb} GB), and its server is built for your graphics card, which can take half an hour the first time.`;
    if (
      !(await confirm({
        title: on ? `Switch to ${saved.model}?` : 'Switch back to separate steps?',
        description: on
          ? `${duplexStops(saved.stops)} Local chat models, speech in other apps and local search stop working meanwhile.${download} Nothing stops until the download and build succeed.`
          : `${saved.model} stops, then ${saved.stopped.length ? saved.stopped.join(', ') : 'the apps it stopped'} start again.`,
        confirmLabel: on ? 'Switch on' : 'Switch back',
      }))
    )
      return;
    const result = await run(
      'engine',
      () => putJsonApi<JobResponse>('/api/v1/ai/voice/engine', { engine: chosen }),
      on ? 'Mu3Lab is preparing the full-duplex model.' : 'Mu3Lab is switching back to separate steps.',
    );
    if (result) {
      setDraft(null);
      setJobId(result.job.id);
      setRunning(true);
    }
  };

  return (
    // Folded away unless it is on: most households never need it.
    <Card title={title} description={description} collapsible defaultOpen={saved.engine === 'duplex'}>
      {saved.engine === 'duplex' && (
        <Callout tone="success" title={`${saved.model} is on`}>
          It holds the graphics card.{' '}
          {saved.stopped.length ? `Stopped until you switch back: ${saved.stopped.join(', ')}.` : ''} Open Talk to use
          it; a call already open keeps the mode it started with.
        </Callout>
      )}
      <form className="form" onSubmit={(event) => void save(event)}>
        <fieldset className="choice-list" disabled={readOnly || busy}>
          <legend>How the voice assistant talks</legend>
          <label className="choice">
            <input
              type="radio"
              name="voice-engine"
              checked={chosen === 'pipeline'}
              onChange={() => setDraft('pipeline')}
            />
            <span>
              <strong>Off: separate steps</strong>
              <small>
                Whisper hears you, the model chosen in Answers replies, and Kokoro speaks. Tools and changes after a
                spoken yes work here.
              </small>
            </span>
          </label>
          <label className="choice">
            <input
              type="radio"
              name="voice-engine"
              checked={chosen === 'duplex'}
              disabled={Boolean(unavailable)}
              onChange={() => setDraft('duplex')}
            />
            <span>
              <strong>On: {saved.model}</strong>
              <small>
                One model on this server listens and speaks at once, so you can interrupt it naturally. It cannot use
                your apps or search the web yet. Your voice never leaves this server.
              </small>
            </span>
          </label>
        </fieldset>
        {unavailable && <p className="muted">{unavailable}</p>}
        {chosen === 'duplex' && saved.engine === 'pipeline' && (
          <Callout tone="warning" title="It takes the whole graphics card">
            {duplexStops(saved.stops)} Local chat models, speech in other apps and local search stop working meanwhile;
            chat answers from your outside providers if they are allowed. Starting those apps is refused until you
            switch back.
            {!saved.downloaded && ` The first switch downloads ${saved.download_gb} GB.`}
          </Callout>
        )}
        {!readOnly && (
          <div className="button-row">
            <Button type="submit" size="sm" loading={pending === 'engine'} disabled={!edited || busy}>
              Save and apply
            </Button>
            {edited && !busy && <small className="muted">Not saved yet: nothing changes until you save.</small>}
          </div>
        )}
      </form>
      {(jobId || data.active_job_id) && <JobLog id={jobId || data.active_job_id} finished={finished} />}
    </Card>
  );
}
