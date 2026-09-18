import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('public/assets', { recursive: true });
for (const file of ['index.html', 'main.js', 'liquidity.html', 'liquidity.js', 'data.json', '_headers']) {
  await copyFile(file, `public/${file}`);
}
await copyFile('assets/styles.css', 'public/assets/styles.css');
await copyFile('node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js', 'public/assets/lightweight-charts.js');
await copyFile('node_modules/lightweight-charts/LICENSE', 'public/assets/lightweight-charts-LICENSE.txt');
console.log('Static site ready in public/');
