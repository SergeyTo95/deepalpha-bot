import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';

await mkdir('dist', { recursive: true });
await cp('public', 'dist', { recursive: true });

const [phaserLicense, eventsLicense, runtime] = await Promise.all([
  readFile('node_modules/phaser/LICENSE.md', 'utf8'),
  readFile('node_modules/eventemitter3/LICENSE', 'utf8'),
  readFile('node_modules/phaser/dist/phaser.min.js', 'utf8'),
]);
const notices = 'Phaser\n' + phaserLicense + '\nEventEmitter3\n' + eventsLicense;
await writeFile('dist/phaser.min.js', '/*!\n' + notices.replaceAll('*/', '* /') + '\n*/\n' + runtime);
