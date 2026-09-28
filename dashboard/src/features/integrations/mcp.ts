import { type McpRegistryResponse, type McpServer, postApi } from '../../api';
import type { Tone } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { navigate } from '../../lib/router';
import { useDashboard } from '../../state/dashboard';

export const setsUpAutomatically = (server: McpServer) => server.auth.auto_provision || server.auth.type === 'none';

export function mcpTone(state: McpServer['state']): Tone {
  if (state === 'live') return 'green';
  if (['degraded', 'failed', 'incompatible'].includes(state)) return 'red';
  if (state === 'starting') return 'blue';
  if (state === 'authentication_required') return 'amber';
  return 'gray';
}

export const mcpStateLabel: Record<McpServer['state'], string> = {
  live: 'Connected',
  degraded: 'Degraded',
  authentication_required: 'Needs credential',
  disabled: 'Not connected',
  prepared: 'Prepared',
  unavailable: 'Unavailable',
  starting: 'Connecting',
  incompatible: 'Incompatible',
  failed: 'Failed',
  stopped: 'App stopped',
};

/** A short, plain-language status line for a connection. */
export function mcpSummary(server: McpServer, appName: string): { label: string; tone: Tone; detail: string } {
  switch (server.state) {
    case 'live':
      return {
        label: 'Connected',
        tone: 'green',
        detail: `${server.tools.length} tool${server.tools.length === 1 ? '' : 's'} available in chat`,
      };
    case 'stopped':
      return { label: 'App stopped', tone: 'gray', detail: `Start ${appName} to use it from chat.` };
    case 'authentication_required':
      return server.auth.auto_provision
        ? { label: 'Ready to connect', tone: 'gray', detail: 'Mu3Lab creates the credential when you connect.' }
        : { label: 'Needs credential', tone: 'amber', detail: `Add a credential from ${appName} to connect.` };
    case 'disabled':
    case 'prepared':
      return { label: 'Not connected', tone: 'gray', detail: 'Connect to let chat use this app.' };
    case 'starting':
      return { label: 'Connecting', tone: 'blue', detail: 'Verifying the connection…' };
    case 'unavailable':
      return { label: 'Unavailable', tone: 'gray', detail: server.error || `Install ${appName} first.` };
    default:
      return { label: mcpStateLabel[server.state], tone: mcpTone(server.state), detail: server.error || '' };
  }
}

export function useMcpRegistry() {
  const { data } = useDashboard();
  return useApi<McpRegistryResponse>(data.identity.writes_enabled ? '/api/v1/mcp/servers' : null, {
    interval: 5000,
  });
}

export type McpAction = 'prepare' | 'install' | 'restart' | 'disable' | 'verify' | 'update';

export const queueMcp = (server: McpServer, action: McpAction) => postApi(`/api/v1/mcp/servers/${server.id}/${action}`);

/** The one thing a user should do next for this connection, if anything. */
export function nextMcpStep(server: McpServer): { action: McpAction | 'chat'; label: string } | null {
  if (server.state === 'live') return { action: 'chat', label: 'Open chat' };
  if (server.review?.status !== 'accepted') return null;
  if (server.state === 'stopped') return server.prepared ? null : { action: 'prepare', label: 'Prepare' };
  if (['unavailable', 'starting'].includes(server.state)) return null;
  if (!server.auth.configured && !server.auth.auto_provision) return null;
  return { action: 'install', label: server.auth.configured ? 'Connect' : 'Create credential and connect' };
}

export function openChatFor(server: McpServer) {
  window.sessionStorage.setItem(
    'mu3lab.expectedMcp',
    JSON.stringify({ name: server.name, serviceId: server.service_id }),
  );
  navigate('/chat');
}
