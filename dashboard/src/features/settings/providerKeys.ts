import type { ProviderCatalogItem } from '../../api';

export interface Detection {
  provider: ProviderCatalogItem;
  /** A distinctive prefix is certain; a shape match is a best guess the owner should confirm. */
  certain: boolean;
}

/**
 * A gentle heads-up for pastes that look unusual. It never blocks: the owner
 * decides, and the provider's live check has the final word.
 */
export function keyWarning(key: string): string {
  const value = key.trim();
  if (/\s/.test(value))
    return 'This contains spaces or line breaks, which API keys usually do not. Check it before connecting.';
  if (value.includes('://')) return 'This looks like a web address rather than a key. Check it before connecting.';
  return '';
}

/** Guess the provider from the key itself; mirrors ctl/provider_catalog.py `detect`. */
export function detectProvider(key: string, catalog: ProviderCatalogItem[]): Detection | null {
  const value = key.trim();
  if (!value) return null;
  const byPrefix = catalog
    .flatMap((provider) => (provider.prefixes ?? []).map((prefix) => ({ provider, prefix })))
    .filter(({ prefix }) => value.startsWith(prefix))
    .sort((a, b) => b.prefix.length - a.prefix.length);
  if (byPrefix.length) return { provider: byPrefix[0].provider, certain: true };
  const byShape = catalog.filter((item) => item.key_pattern && new RegExp(item.key_pattern).test(value));
  return byShape.length === 1 ? { provider: byShape[0], certain: false } : null;
}
