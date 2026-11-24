import { AudioLines, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import {
  api,
  deleteApi,
  putJsonApi,
  type VoicePage,
  type VoiceReport,
  type VoiceReportAudio,
  type VoiceReports,
  type VoiceReportSummary,
  type VoiceSettingsRequest,
} from '../../api';
import { Button } from '../../components/Button';
import { Dialog, useConfirm } from '../../components/Dialog';
import { Card } from '../../components/Layout';
import { errorText } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { type ApiState, useApi } from '../../lib/useApi';

const KEEP_DAYS = [3, 7, 14, 30, 90];

const when = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });

const length = (seconds: number) => {
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return 'under a minute';
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
};

/**
 * Each call's transcript, timing and audio, kept for a few days so problems can be found. They hold
 * what people said at home: administrators only, every opening noted in the activity log.
 */
export function VoiceReportsCard({ api: page }: { api: ApiState<VoicePage> }) {
  const { data, reload } = page;
  const reports = useApi<VoiceReports>('/api/v1/ai/voice/reports');
  const { pending, run } = useAction();
  const [open, setOpen] = useState<VoiceReportSummary | null>(null);
  if (!data) return null;
  const settings = data.settings;
  const on = settings.reports ?? true;
  const change = async (key: string, body: VoiceSettingsRequest) => {
    if (await run(key, () => putJsonApi<VoicePage>('/api/v1/ai/voice/settings', body), 'Saved; the next call uses it'))
      void reload();
  };
  const list = reports.data?.reports ?? [];
  return (
    <Card
      title="Call reports"
      description="What was said on each call, with each reply’s timing, so problems can be found. Only administrators can see them, and each opening is noted in the activity log."
    >
      <div className="form">
        <label className="switch-field">
          <span className="switch-label">
            Keep call reports
            <small>
              Conversations are saved on this server for the days below, then deleted. They are never backed up.
            </small>
          </span>
          <input
            type="checkbox"
            role="switch"
            aria-label="Keep call reports"
            checked={on}
            disabled={Boolean(pending)}
            onChange={(event) => void change('reports', { reports: event.target.checked })}
          />
        </label>
        {on && (
          <>
            <label className="switch-field">
              <span className="switch-label">
                Keep the audio too
                <small>So a sound problem can be heard, not only read.</small>
              </span>
              <input
                type="checkbox"
                role="switch"
                aria-label="Keep the audio too"
                checked={settings.reports_audio ?? true}
                disabled={Boolean(pending)}
                onChange={(event) => void change('reports_audio', { reports_audio: event.target.checked })}
              />
            </label>
            <label className="field">
              <span>Keep them for</span>
              <select
                value={settings.reports_days ?? 14}
                disabled={Boolean(pending)}
                onChange={(event) => void change('reports_days', { reports_days: Number(event.target.value) })}
              >
                {[...new Set([...KEEP_DAYS, settings.reports_days ?? 14])]
                  .sort((a, b) => a - b)
                  .map((days) => (
                    <option key={days} value={days}>
                      {days} days
                    </option>
                  ))}
              </select>
            </label>
          </>
        )}
      </div>
      {reports.error && <p className="error-text">Could not load the reports: {reports.error}</p>}
      {list.length > 0 ? (
        <ul className="voice-reports">
          {list.map((report) => (
            <li key={report.id}>
              <button type="button" onClick={() => setOpen(report)}>
                <strong>{report.who}</strong>
                <span>{when(report.started_at)}</span>
                <span>{length(report.seconds)}</span>
                {report.audio && <AudioLines aria-label="Has audio" />}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        reports.data && <p className="muted">No call reports yet.</p>
      )}
      {open && (
        <ReportDialog
          summary={open}
          onClose={() => setOpen(null)}
          onDeleted={() => {
            setOpen(null);
            void reports.reload();
          }}
        />
      )}
    </Card>
  );
}

const LABEL: Record<VoiceReport['lines'][number]['kind'], string> = {
  said: '',
  tool: 'Asked an app',
  tool_result: 'The app answered',
  tool_error: 'The app refused',
};

/** One call, line by line, with its audio on request. */
function ReportDialog({
  summary,
  onClose,
  onDeleted,
}: {
  summary: VoiceReportSummary;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const path = `/api/v1/ai/voice/reports/${encodeURIComponent(summary.id)}`;
  const { data, error } = useApi<VoiceReport>(path);
  const confirm = useConfirm();
  const [audio, setAudio] = useState('');
  const [loading, setLoading] = useState(false);
  const listen = async () => {
    setLoading(true);
    try {
      setAudio((await api<VoiceReportAudio>(`${path}/audio`)).audio);
    } catch (cause) {
      toast.error(errorText(cause));
    } finally {
      setLoading(false);
    }
  };
  const remove = async () => {
    const yes = await confirm({
      title: 'Delete this call report?',
      description: 'Its transcript and audio are deleted now; this can’t be undone.',
      confirmLabel: 'Delete',
      tone: 'danger',
    });
    if (!yes) return;
    try {
      await deleteApi<VoiceReports>(path);
      toast.success('Call report deleted.');
      onDeleted();
    } catch (cause) {
      toast.error(errorText(cause));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      size="drawer"
      title={`${summary.who}, ${when(summary.started_at)}`}
      description={`${length(summary.seconds)}. Opening this is noted in the activity log.`}
      footer={
        <Button variant="danger" size="sm" icon={Trash2} onClick={() => void remove()}>
          Delete report
        </Button>
      }
    >
      {summary.audio &&
        (audio ? (
          <div className="voice-report-audio">
            <audio controls src={audio} />
            <a href={audio} download={`${summary.id}.ogg`}>
              Save the audio
            </a>
          </div>
        ) : (
          <Button size="sm" icon={AudioLines} loading={loading} onClick={() => void listen()}>
            Play the audio
          </Button>
        ))}
      {error && <p className="error-text">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && (
        <ol className="voice-report-lines">
          {data.lines.map((line, index) => (
            <li key={index} className={`is-${line.who} kind-${line.kind}${line.interrupted ? ' is-cut' : ''}`}>
              <small>
                {line.kind === 'said' ? (line.who === 'person' ? summary.who : 'Assistant') : LABEL[line.kind]}
                {line.interrupted && ' · cut off'}
                {line.reply_after ? ` · answered after ${line.reply_after.toFixed(1)} s` : ''}
              </small>
              <p>{line.text}</p>
            </li>
          ))}
          {data.lines.length === 0 && <li className="muted">Nothing was said on this call.</li>}
        </ol>
      )}
    </Dialog>
  );
}
