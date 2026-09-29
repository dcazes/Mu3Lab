import { describe, expect, it } from 'vitest';
import type { ProviderCatalogItem } from '../../api';
import { detectProvider, keyProblem } from './providerKeys';

const item = (id: string, prefix: string | string[], key_pattern = ''): ProviderCatalogItem => ({
  id,
  name: id,
  key_hint: '',
  prefixes: Array.isArray(prefix) ? prefix : prefix ? [prefix] : [],
  key_pattern,
  instructions: '',
});

const catalog = [
  item('groq', 'gsk_'),
  item('google', ['AIza', 'AQ.']),
  item('openrouter', 'sk-or-v1-'),
  item('mistral', '', '^[A-Za-z0-9]{32}$'),
  item('zhipu', '', '^[0-9a-f]{32}\\.[A-Za-z0-9]{16}$'),
];

describe('detectProvider', () => {
  it('is certain about distinctive prefixes', () => {
    expect(detectProvider(' gsk_abc ', catalog)).toEqual({ provider: catalog[0], certain: true });
    expect(detectProvider('sk-or-v1-abc', catalog)?.provider.id).toBe('openrouter');
  });

  it('knows every key format a provider has used', () => {
    expect(detectProvider('AIzaSyExample', catalog)?.provider.id).toBe('google');
    expect(detectProvider('AQ.Ab8RN6Example', catalog)?.provider.id).toBe('google');
  });

  it('only guesses from a key shape', () => {
    expect(detectProvider('A1b2'.repeat(8), catalog)).toEqual({ provider: catalog[3], certain: false });
    expect(detectProvider(`${'0123456789abcdef'.repeat(2)}.ABCDEFGHijklmnop`, catalog)?.provider.id).toBe('zhipu');
  });

  it('refuses text that cannot be a key, like a pasted sentence', () => {
    const sentence = 'compare these two products: https://example.com/item';
    expect(keyProblem(sentence)).toMatch(/doesn't look like an API key/);
    expect(detectProvider(sentence, catalog)).toBeNull();
    expect(keyProblem('gsk_abc')).toBe('');
  });

  it('gives up on anything else', () => {
    expect(detectProvider('', catalog)).toBeNull();
    expect(detectProvider('mystery', catalog)).toBeNull();
  });
});
