const themes = {
  forest: { ground: 0x18362a, path: 0x214432, wall: 0x385c42, edge: 0x5e8251, item: 0xffdf70, player: 0xbaf46a, label: 'ЛЕС' },
  neon: { ground: 0x172342, path: 0x202d52, wall: 0x333b6c, edge: 0x6964b8, item: 0x9ffbff, player: 0xff8ae8, label: 'НЕОН' },
  desert: { ground: 0x52382a, path: 0x624533, wall: 0x80604a, edge: 0xbb9061, item: 0xffe495, player: 0xaedf92, label: 'ПУСТЫНЯ' },
  ice: { ground: 0x1b3e54, path: 0x255167, wall: 0x36637a, edge: 0x6ca9bb, item: 0xd4f9ff, player: 0xfacf69, label: 'ЛЁД' },
};
const demo = { v: 1, title: 'Сад светлячков', pickup: 'Светлячки', theme: 'forest', count: 9, seconds: 60, speed: 185, seed: 2719 };
const dimensions = { width: 480, height: 360 };
const element = id => document.getElementById(id);
const matchingInteger = (value, minimum, maximum) => Number.isInteger(value) && value >= minimum && value <= maximum;
const textAllowed = (value, length) => typeof value === 'string' && value.trim() && Array.from(value).length <= length
  && !/[<>\p{Cc}\p{Cf}]/u.test(value);
function loadSpec() {
  if (!location.hash) return demo;
  const fragment = location.hash.slice(1);
  if (!fragment.startsWith('v1=') || fragment.length > 1700) throw Error('Invalid game link');
  const spec = JSON.parse(decodeURIComponent(fragment.slice(3)));
  if (!spec || Object.keys(spec).length !== 8 || spec.v !== 1 || !Object.hasOwn(themes, spec.theme)
      || !textAllowed(spec.title, 54) || !textAllowed(spec.pickup, 21)
      || !matchingInteger(spec.count, 5, 12) || !matchingInteger(spec.seconds, 30, 120)
      || !matchingInteger(spec.speed, 130, 230) || !matchingInteger(spec.seed, 1, 99999)) throw Error('Invalid game fields');
  return spec;
}

function launch(spec) {
  const { width, height } = dimensions;
  const theme = themes[spec.theme];
  const obstacles = [[142, 105, 112, 22], [340, 95, 32, 72], [235, 192, 44, 96], [91, 220, 50, 28], [374, 246, 108, 24], [183, 300, 96, 22]];
  const cells = [[53, 71], [182, 51], [276, 70], [415, 54], [409, 151], [314, 162],
    [132, 157], [48, 151], [58, 291], [310, 298], [421, 303], [459, 218],
    [280, 246], [161, 237], [114, 287], [356, 205]];
  const directions = new Map();
  const pointerStarted = new Map();
  const taps = new Map();
  const score = element('score'), clock = element('clock'), overlay = element('overlay'),
    status = element('status'), play = element('play');
  element('world').textContent = location.hash ? theme.label : 'GECKOSHI';
  element('title').textContent = spec.title;
  document.title = spec.title;
  element('objective').textContent = 'Собери все предметы до истечения времени.';
  function frame(label, action) {
    status.textContent = label;
    play.textContent = action;
    overlay.hidden = false;
  }
  function clearInput() {
    directions.clear();
    pointerStarted.clear();
    taps.clear();
    for (const control of document.querySelectorAll('[data-direction]')) control.removeAttribute('data-pressed');
  }
  for (const control of document.querySelectorAll('[data-direction]')) {
    control.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      control.setPointerCapture(event.pointerId);
      directions.set(event.pointerId, control.dataset.direction);
      pointerStarted.set(event.pointerId, performance.now());
      control.setAttribute('data-pressed', '');
    });
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) control.addEventListener(event, pointer => {
      const start = pointerStarted.get(pointer.pointerId);
      const now = performance.now();
      if (event === 'pointerup' && start !== undefined && now - start < 125) {
        taps.set(control.dataset.direction, start + 125);
      }
      directions.delete(pointer.pointerId);
      pointerStarted.delete(pointer.pointerId);
      if (![...directions.values()].includes(control.dataset.direction)) control.removeAttribute('data-pressed');
    });
    control.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      directions.set(control.dataset.direction, control.dataset.direction);
    });
    control.addEventListener('keyup', () => directions.delete(control.dataset.direction));
    control.addEventListener('blur', () => directions.delete(control.dataset.direction));
  }
  window.addEventListener('blur', clearInput);
  frame('Управляй гекконом', 'Играть');
  score.textContent = spec.pickup+' · 0 / '+spec.count;
  clock.textContent = spec.seconds+' с';
  class GameScene extends Phaser.Scene {
    init({startAgain = false} = {}) {
      this.startAgain = startAgain;
    }
    startRound() {
      this.started = true;
      this.deadline = this.time.now + spec.seconds * 1000;
      overlay.hidden = true;
    }
    create() {
      this.started = false;
      this.ended = false;
      this.collected = 0;
      const field = this.add.graphics().fillStyle(theme.ground).fillRect(0, 0, width, height);
      field.lineStyle(1, theme.path, 0.7);
      for (let x = 0; x < width; x += 24) field.lineBetween(x, 0, x, height);
      for (let y = 0; y < height; y += 24) field.lineBetween(0, y, width, y);
      const rng = new Phaser.Math.RandomDataGenerator([String(spec.seed)]);
      field.fillStyle(theme.edge, 0.32);
      for (let i = 0; i < 110; i++) field.fillCircle(rng.between(8, width-8), rng.between(8, height-8), rng.between(1, 2));
      const walls = this.physics.add.staticGroup();
      for (const [x, y, w, h] of obstacles) {
        const wall = this.add.rectangle(x, y, w, h, theme.wall).setStrokeStyle(2, theme.edge);
        walls.add(wall);
      }
      const pickups = this.physics.add.staticGroup();
      const positions = [...cells];
      rng.shuffle(positions);
      for (const [x, y] of [[36, 281], ...positions.slice(0, spec.count - 1)]) {
        const glow = this.add.circle(x, y, 13, theme.item, 0.12);
        const item = this.add.star(x, y, 4, 3, 8, theme.item);
        item.setData('glow', glow);
        pickups.add(item);
      }
      this.player = this.add.container(36, 324, [
        this.add.ellipse(-12, 4, 15, 5, theme.player).setRotation(-0.45),
        this.add.ellipse(-2, 0, 20, 16, theme.player),
        this.add.ellipse(9, -1, 14, 13, theme.player),
        this.add.circle(11, -4, 2, 0x15261c),
        this.add.circle(11, 3, 2, 0x15261c),
      ]);
      this.physics.add.existing(this.player);
      this.player.body.setSize(26, 20).setOffset(-13, -10).setCollideWorldBounds(true);
      this.physics.add.collider(this.player, walls);
      this.physics.add.overlap(this.player, pickups, (_, pickup) => {
        if (!this.started || this.ended) return;
        pickup.getData('glow').destroy();
        pickup.destroy();
        score.textContent = spec.pickup+' · '+(++this.collected)+' / '+spec.count;
        if (this.collected === spec.count) this.finish('Все предметы собраны!');
      });
      this.cursors = this.input.keyboard.createCursorKeys();
      this.wasd = this.input.keyboard.addKeys('W,A,S,D');
      play.onclick = () => {
        if (this.ended) this.scene.restart({startAgain: true});
        else this.startRound();
      };
      element('restart').onclick = () => this.scene.restart();
      clearInput();
      score.textContent = spec.pickup+' · 0 / '+spec.count;
      clock.textContent = spec.seconds+' с';
      frame('Собери предметы', 'Играть');
      if (this.startAgain) this.startRound();
      document.documentElement.dataset.gameReady = 'true';
    }
    finish(message) {
      this.ended = true;
      this.player.body.setVelocity(0);
      clearInput();
      frame(message, 'Сыграть снова');
    }
    update(now) {
      if (!this.started || this.ended) return;
      const seconds = Math.max(0, Math.ceil((this.deadline - now) / 1000));
      clock.textContent = seconds+' с';
      if (seconds === 0) { this.finish('Время вышло'); return; }
      const held = [...directions.values()];
      for (const [direction, until] of taps) {
        if (until > performance.now()) held.push(direction);
        else taps.delete(direction);
      }
      const x = +(this.cursors.right.isDown || this.wasd.D.isDown || held.includes('right')) -
        +(this.cursors.left.isDown || this.wasd.A.isDown || held.includes('left'));
      const y = +(this.cursors.down.isDown || this.wasd.S.isDown || held.includes('down')) -
        +(this.cursors.up.isDown || this.wasd.W.isDown || held.includes('up'));
      const speed = spec.speed / (x && y ? Math.SQRT2 : 1);
      this.player.body.setVelocity(x * speed, y * speed);
      if (x || y) this.player.rotation = Math.atan2(y, x);
    }
  }
  new Phaser.Game({
    type: Phaser.CANVAS, parent: 'game', width, height,
    physics: { default: 'arcade' }, scene: GameScene, banner: false,
    render: { antialias: true },
  });
}

try { launch(loadSpec()); }
catch (error) {
  element('status').textContent = 'Не удалось открыть игру. Проверь ссылку.';
  element('play').hidden = true;
  element('restart').disabled = true;
  throw error;
}
