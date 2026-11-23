import { cp, mkdir, copyFile } from 'node:fs/promises';

await mkdir('dist', { recursive: true });
await cp('public', 'dist', { recursive: true });
await copyFile('node_modules/phaser/dist/phaser.min.js', 'dist/phaser.min.js');
