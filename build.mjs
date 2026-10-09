import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('public/assets', { recursive: true });
for (const file of ['index.html', 'main.js', 'liquidity.html', 'liquidity.js', 'data.json', 'economic_calendar.json', 'cme_term_sofr_snapshot.json', 'macromicro_ois_snapshot.json', '_headers']) {
  await copyFile(file, `public/${file}`);
}
await copyFile('assets/styles.css', 'public/assets/styles.css');
await copyFile('assets/pqg-three-phase-reference.png', 'public/assets/pqg-three-phase-reference.png');
await copyFile('node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js', 'public/assets/lightweight-charts.js');
await copyFile('node_modules/lightweight-charts/LICENSE', 'public/assets/lightweight-charts-LICENSE.txt');
console.log('Static site ready in public/');
