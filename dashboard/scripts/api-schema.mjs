import { readFileSync, writeFileSync } from 'node:fs';
import openapiTS, { astToString } from 'openapi-typescript';
import prettier from 'prettier';

const input = new URL('../openapi.json', import.meta.url);
const output = new URL('../src/api/schema.ts', import.meta.url);
const schema = JSON.parse(readFileSync(input, 'utf8'));
const ast = await openapiTS(schema, { defaultNonNullable: false });
const content = await prettier.format(astToString(ast), {
  ...(await prettier.resolveConfig(output.pathname)),
  parser: 'typescript',
});
if (process.argv.includes('--check')) {
  if (readFileSync(output, 'utf8') !== content) {
    console.error('Generated API types are stale. Run make api-schema.');
    process.exitCode = 1;
  }
} else {
  writeFileSync(output, content);
}

const aliases = new URL('../src/api/models.ts', import.meta.url);
const aliasContent = await prettier.format(
  "import type { components } from './schema';\n\n" +
    Object.keys(schema.components.schemas)
      .sort()
      .map((name) => `export type ${name} = components['schemas']['${name}'];\n`)
      .join(''),
  { ...(await prettier.resolveConfig(aliases.pathname)), parser: 'typescript' },
);
if (process.argv.includes('--check')) {
  if (readFileSync(aliases, 'utf8') !== aliasContent) {
    console.error('Generated API aliases are stale. Run make api-schema.');
    process.exitCode = 1;
  }
} else writeFileSync(aliases, aliasContent);
