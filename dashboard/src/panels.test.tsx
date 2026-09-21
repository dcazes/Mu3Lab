import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppsPanel, HomePanel } from './panels';
import type { Service } from './api';

function service(id: string, name: string, stage: Service['stage']): Service {
  return {
    id, name, stage, category: stage, lifecycle: 'optional', https_port: 1,
    maturity: stage === 'blocked' ? 'planned' : 'supported', auth: 'excluded', profiles: [], dependencies: [],
    availability: stage === 'blocked' ? 'blocked' : 'available', blocked_reason: '', route: 'pending', routable: false,
    required: stage !== 'optional', identity_note: '', resource_guidance: '', setup_action: '', mcp: { exposed: false, risk: '' },
    state: 'not_installed', lifecycle_state: 'not_installed', health_state: 'unknown', setup_state: 'pending', route_state: 'pending',
    identity_mode: '', backup_state: '', last_job_id: '', last_error: '', user_action: '', detail: '', url: '', route_ready: false, compose_present: true,
  };
}

const services = [service('ingress', 'Caddy', 'foundation'), service('ollama', 'Ollama', 'core'), service('nextcloud', 'Nextcloud', 'optional'), service('firecrawl', 'Firecrawl', 'blocked')];

describe('dashboard organization', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ state: 'not_connected', events: [], error: '' }) })); });
  afterEach(() => vi.unstubAllGlobals());

  it('groups Home apps and removes the duplicate wiring panel', () => {
    render(<HomePanel services={services} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    expect(screen.getByRole('heading', { name: 'Infrastructure' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'AI Integration' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Productivity apps' })).toBeInTheDocument();
    expect(screen.queryByText('CORE WIRING')).not.toBeInTheDocument();
    expect(screen.queryByText('6/9')).not.toBeInTheDocument();
  });

  it('orders the app catalog with Productivity first', () => {
    render(<AppsPanel services={services} catalog={{ ok: true, profiles: [], services: {} }} />);
    const groups = document.querySelector('.apps-sections');
    expect(Array.from(groups?.querySelectorAll(':scope > section > h2') || []).map(item => item.textContent))
      .toEqual(['Productivity apps', 'AI Integration', 'Foundation', 'Blocked and planned']);
  });
});
