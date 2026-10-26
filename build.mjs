import { mkdir, copyFile, readFile, writeFile } from 'node:fs/promises';
await mkdir('public/assets', { recursive: true });
for (const file of ['index.html', 'main.js', 'liquidity.html', 'liquidity.js', 'liquidity-loader.js', 'european-bonds.html', 'european-bonds.js', 'european-bonds.css', 'data.json', 'economic_calendar.json', '_headers']) {
  await copyFile(file, `public/${file}`);
}
await copyFile('assets/styles.css', 'public/assets/styles.css');
await copyFile('assets/pqg-three-phase-reference.png', 'public/assets/pqg-three-phase-reference.png');
await copyFile('node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js', 'public/assets/lightweight-charts.js');
await copyFile('node_modules/lightweight-charts/LICENSE', 'public/assets/lightweight-charts-LICENSE.txt');
const charts = await readFile('node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js', 'utf8');
const liquidity = await readFile('liquidity.js', 'utf8');
const snapshot = JSON.parse(await readFile('data.json', 'utf8'));
await writeFile('public/macro-data.json', JSON.stringify({
  schema_version: snapshot.schema_version,
  history_days: snapshot.history_days,
  price_basis: snapshot.price_basis,
  series: snapshot.series,
  generated_at: snapshot.generated_at,
}), 'utf8');
await writeFile('public/liquidity-runtime.js', `${charts}\n;(() => {\n${liquidity}\n})();\n`, 'utf8');
console.log('Static site ready in public/');
