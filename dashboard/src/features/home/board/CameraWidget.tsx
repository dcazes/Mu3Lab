import { Loader2, Play, Square } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { CameraSettings } from '../../../api';
import { Link } from '../../../lib/router';
import { useApi } from '../../../lib/useApi';
import type { WidgetProps } from './widgets';

type Quality = 'sd' | 'hd';

// The codecs go2rtc may send, as Media Source Extensions name them; the page offers only what it plays.
const CODECS = [
  'avc1.640029',
  'avc1.64002A',
  'avc1.640033',
  'hvc1.1.6.L153.B0',
  'mp4a.40.2',
  'mp4a.40.5',
  'flac',
  'opus',
];

type SourceCtor = typeof MediaSource;

function mediaSource(): SourceCtor | null {
  const scope = window as unknown as { ManagedMediaSource?: SourceCtor; MediaSource?: SourceCtor };
  return scope.ManagedMediaSource ?? scope.MediaSource ?? null;
}

/**
 * Plays go2rtc's stream through Mu3Lab: Media Source over a WebSocket where the browser has it
 * (computers, Android, iPhone from iOS 17.1), HLS otherwise. It runs only while on screen, so
 * leaving Home or switching tabs lets go of the camera.
 */
function useLive(quality: Quality, playing: boolean) {
  const video = useRef<HTMLVideoElement>(null);
  // What the current connection reported; a newer one (another quality) starts out connecting.
  const session = `${quality}:${playing}`;
  const [reported, setReported] = useState<{ session: string; status: 'live' | 'error'; error: string } | null>(null);
  useEffect(() => {
    const element = video.current;
    if (!playing || !element) return;
    let closed = false;
    const setState = (value: { status: 'live' | 'error'; error: string }) => setReported({ session, ...value });
    const fail = (error: string) => !closed && setState({ status: 'error', error });
    const Source = mediaSource();
    if (!Source) {
      // Older iPhones: the browser plays HLS itself.
      element.src = `/api/v1/home/camera/api/stream.m3u8?src=${quality === 'hd' ? 'front_door_hd' : 'front_door'}&mp4`;
      element.onplaying = () => !closed && setState({ status: 'live', error: '' });
      element.onerror = () => fail('The camera did not answer.');
      void element.play().catch(() => undefined);
      return () => {
        closed = true;
        element.removeAttribute('src');
        element.load();
      };
    }
    const source = new Source();
    const url = URL.createObjectURL(source);
    element.disableRemotePlayback = true;
    element.src = url;
    let socket: WebSocket | null = null;
    let buffer: SourceBuffer | null = null;
    const queue: ArrayBuffer[] = [];
    const pump = () => {
      if (!buffer || buffer.updating) return;
      const ranges = element.buffered;
      if (ranges.length) {
        const end = ranges.end(ranges.length - 1);
        // Stay at the live edge, and keep only the last few seconds.
        if (end - element.currentTime > 3) element.currentTime = end - 0.5;
        if (end - ranges.start(0) > 30) {
          buffer.remove(ranges.start(0), end - 15);
          return;
        }
      }
      const next = queue.shift();
      if (next) buffer.appendBuffer(next);
    };
    source.addEventListener('sourceopen', () => {
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(`${scheme}://${window.location.host}/api/v1/home/camera/live?quality=${quality}`);
      socket.binaryType = 'arraybuffer';
      socket.onopen = () => {
        const supported = CODECS.filter((codec) => Source.isTypeSupported(`video/mp4; codecs="${codec}"`));
        socket?.send(JSON.stringify({ type: 'mse', value: supported.join(',') }));
      };
      socket.onmessage = (event) => {
        if (typeof event.data === 'string') {
          const message = JSON.parse(event.data) as { type: string; value: string };
          if (message.type === 'mse' && !buffer) {
            try {
              buffer = source.addSourceBuffer(message.value);
              buffer.mode = 'segments';
              buffer.addEventListener('updateend', pump);
            } catch {
              fail('This browser cannot play the camera’s video format. Try the other quality.');
            }
          } else if (message.type === 'error') fail(message.value);
          return;
        }
        queue.push(event.data as ArrayBuffer);
        if (queue.length > 60) queue.splice(0, queue.length - 60);
        pump();
      };
      socket.onclose = (event) => {
        if (event.code === 4403) fail('Only administrators can watch the camera.');
        else if (!closed && element.readyState < 2) fail('The live view stopped. Try again.');
      };
    });
    element.onplaying = () => !closed && setState({ status: 'live', error: '' });
    void element.play().catch(() => undefined);
    return () => {
      closed = true;
      socket?.close();
      element.removeAttribute('src');
      element.load();
      URL.revokeObjectURL(url);
    };
  }, [quality, playing, session]);
  const status = !playing ? 'idle' : reported?.session === session ? reported.status : 'connecting';
  return { video, status, error: reported?.session === session ? reported.error : '' };
}

/** The front-door camera, live, for administrators. Nothing streams until someone presses play. */
export function CameraWidget(_props: WidgetProps) {
  const { data, error } = useApi<CameraSettings>('/api/v1/home/devices/camera');
  const [quality, setQuality] = useState<Quality>('sd');
  const [playing, setPlaying] = useState(false);
  const [hidden, setHidden] = useState(document.visibilityState === 'hidden');
  useEffect(() => {
    const changed = () => setHidden(document.visibilityState === 'hidden');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  const live = useLive(quality, playing && !hidden);
  const { video } = live;

  if (!data) return error ? <p className="widget-error">{error}</p> : <p className="muted">Loading…</p>;
  if (!data.configured)
    return (
      <div className="widget-empty">
        <p>No camera is set up yet.</p>
        <Link to="/settings/home-devices" className="widget-link">
          Add it in Settings
        </Link>
      </div>
    );
  return (
    <div className="camera">
      <div className="camera-frame">
        <video ref={video} muted playsInline aria-label={`${data.name} camera`} />
        {!playing ? (
          <button type="button" className="camera-play" onClick={() => setPlaying(true)}>
            <Play aria-hidden />
            Watch {data.name.toLowerCase()}
          </button>
        ) : live.status === 'connecting' ? (
          <span className="camera-note">
            <Loader2 className="spin" aria-hidden />
            Connecting…
          </span>
        ) : live.status === 'error' ? (
          <span className="camera-note camera-error">{live.error}</span>
        ) : null}
      </div>
      {playing && (
        <div className="camera-bar">
          <div className="segmented" role="radiogroup" aria-label="Picture quality">
            {(['sd', 'hd'] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={quality === value}
                onClick={() => setQuality(value)}
              >
                {value === 'sd' ? 'Clear' : 'HD'}
              </button>
            ))}
          </div>
          <button type="button" className="widget-link" onClick={() => setPlaying(false)}>
            <Square aria-hidden />
            Stop
          </button>
        </div>
      )}
    </div>
  );
}
