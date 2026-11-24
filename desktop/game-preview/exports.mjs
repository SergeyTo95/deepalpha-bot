import { zipSync, strToU8 } from 'fflate';

const names = ['count', 'pickup', 'seconds', 'seed', 'speed', 'theme', 'title', 'v'];
const label = (value, limit) => typeof value === 'string' && value.trim() &&
  Array.from(value).length <= limit && !/[<>\p{Cc}\p{Cf}\p{Cs}]/u.test(value);
const integer = (value, min, max) => Number.isInteger(value) && value >= min && value <= max;

export function decodeGame(raw) {
  if (typeof raw !== 'string' || raw.length > 1700) throw Error('Invalid game');
  const spec = JSON.parse(raw);
  if (spec?.v === 2) {
    if (Object.keys(spec).sort().join(',') !== 'difficulty,seed,stages,theme,title,v'
        || !label(spec.title, 54) || !['industrial', 'crimson', 'ice'].includes(spec.theme)
        || !integer(spec.difficulty, 0, 2) || !integer(spec.stages, 1, 3)
        || !integer(spec.seed, 1, 99999)) throw Error('Invalid shooter');
    return spec;
  }
  if (!spec || Object.keys(spec).sort().join(',') !== names.join(',') || spec.v !== 1 ||
      !label(spec.title, 54) || !label(spec.pickup, 21) ||
      !['forest', 'neon', 'desert', 'ice'].includes(spec.theme) ||
      !integer(spec.count, 5, 12) || !integer(spec.seconds, 30, 120) ||
      !integer(spec.speed, 130, 230) || !integer(spec.seed, 1, 99999)) throw Error('Invalid game');
  return spec;
}

const setup = [
  "const fs=require('node:fs')",
  "const licenses=['phaser/LICENSE.md','eventemitter3/LICENSE'].map(path=>fs.readFileSync('node_modules/'+path,'utf8'))",
  "fs.writeFileSync('phaser.min.js','/*!\\n'+licenses.join('\\n').replaceAll('*/','* /')+'\\n*/\\n'+fs.readFileSync('node_modules/phaser/dist/phaser.min.js','utf8'))",
].join(';');
const manifest = {
  name: 'velia-phaser-game',
  private: true,
  dependencies: { phaser: '3.90.0' },
  overrides: { eventemitter3: '5.0.4' },
  scripts: { prepare: 'node -e "' + setup + '"' },
};
const readme = '# Игра из VELIA\n\nОткройте index.html в браузере. Если в папке отсутствует phaser.min.js, сначала выполните npm install.\n\n' +
  'Для изменения игры отредактируйте настройки в game.js. Управление: стрелки, WASD, экранные кнопки или касание поля.\n' +
  'Phaser 3.90 и EventEmitter3 лицензируются по MIT. Тексты лицензий включаются в phaser.min.js при установке.\n';

export function gameSources(spec, files) {
  const fps = spec?.v === 2;
  const index = files.get(fps ? '/fps' : '/').body.toString('utf8')
    .replace(/\.\/style\.css\?v=[\da-f]+/u, './style.css')
    .replace('<script type="module" src="./game.mjs"></script>', '<script src="./game.js" defer></script>')
    .replace(/^.*id="game-download".*\n/m, '');
  const original = files.get(fps ? '/fps.mjs' : '/game.mjs').body.toString('utf8');
  const game = spec ? original.replace(/^const demo = .*;$/m, () => 'const demo = ' + JSON.stringify(spec) + ';') : original;
  return {
    'index.html': index,
    'style.css': files.get('/style.css').body.toString('utf8'),
    'game.js': game,
    'README.md': fps ? '# Ретрошутер из VELIA\n\nОткройте index.html в браузере. WASD и стрелки для движения, пробел для атаки, цифры 1–4 для смены оружия. На телефоне используйте джойстик и свайп по игровому полю. Для атаки нажмите «Огонь». Найдите ключ и выход из сектора.\n' : readme,
    'package.json': JSON.stringify(fps ? {name: 'velia-retro-fps', private: true} : manifest, null, 2) + '\n',
    '.gitignore': 'node_modules/\nphaser.min.js\n',
  };
}

export function gameZip(spec, files) {
  const items = Object.fromEntries(Object.entries(gameSources(spec, files)).map(([name, value]) => [name, strToU8(value)]));
  if (spec?.v !== 2) items['phaser.min.js'] = files.get('/phaser.min.js').body;
  return zipSync(items, { level: 0 });
}
