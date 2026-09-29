import { describe, expect, it } from 'vitest';
import type { ProviderCatalogItem } from '../../api';
import { detectProvider } from './providerKeys';

const item = (id: string, prefix: string, key_pattern = ''): ProviderCatalogItem => ({
  id,
  name: id,
  key_hint: '',
  prefix,
  key_pattern,
  instructions: '',
  example_models: [],
});

const catalog = [
  item('groq', 'gsk_'),
  item('openrouter', 'sk-or-v1-'),
  item('mistral', '', '^[A-Za-z0-9]{32}$'),
  item('zhipu', '', '^[0-9a-f]{32}\\.[A-Za-z0-9]{16}$'),
];

describe('detectProvider', () => {
  it('is certain about distinctive prefixes', () => {
    expect(detectProvider(' gsk_abc ', catalog)).toEqual({ provider: catalog[0], certain: true });
    expect(detectProvider('sk-or-v1-abc', catalog)?.provider.id).toBe('openrouter');
  });

  it('only guesses from a key shape', () => {
    expect(detectProvider('A1b2'.repeat(8), catalog)).toEqual({ provider: catalog[2], certain: false });
    expect(detectProvider(`${'0123456789abcdef'.repeat(2)}.ABCDEFGHijklmnop`, catalog)?.provider.id).toBe('zhipu');
  });

  it('gives up on anything else', () => {
    expect(detectProvider('', catalog)).toBeNull();
    expect(detectProvider('mystery', catalog)).toBeNull();
  });
});
