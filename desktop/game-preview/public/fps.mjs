(() => {
'use strict';

const demo = {"v":2,"title":"Сигнал из глубины","theme":"industrial","difficulty":1,"stages":3,"seed":3189};
const size = 15;
const fov = 0.69;
const weapons = [
  { name: 'Лом', range: 1.3, arc: .35, damage: 36, reload: .35, color: '#b67166' },
  { name: 'Винтовка', range: 13, arc: .065, damage: 26, reload: .25, color: '#d9bc8e' },
  { name: 'Дробовик', range: 6, arc: .25, damage: 52, reload: .62, color: '#ed985a' },
  { name: 'Импульсатор', range: 10, arc: .14, damage: 43, reload: .42, color: '#72d9d9' },
];
const monsters = [
  { name: 'Ползун', hp: 34, speed: 1.46, range: .78, damage: 7, reload: .86, color: '#d0df7b' },
  { name: 'Дрон', hp: 35, speed: 1.10, range: 5, damage: 8, reload: 1.65, color: '#80c8d4' },
  { name: 'Страж', hp: 74, speed: .85, range: .95, damage: 12, reload: 1.28, color: '#ca735b' },
  { name: 'Мимик', hp: 46, speed: 1.60, range: 1.1, damage: 10, reload: 1.15, color: '#cf99db' },
  { name: 'Призрак', hp: 52, speed: .98, range: 7, damage: 10, reload: 2.1, color: '#f2b86d' },
];
const tiers = [
  { pressure: .74, health: .8, damage: .7, supply: 1.45 },
  { pressure: 1, health: 1, damage: 1, supply: 1 },
  { pressure: 1.2, health: 1.24, damage: 1.45, supply: .8 },
];
const palettes = {
  industrial: { sky: '#1c2230', horizon: '#10121c', floor: '#382f2b', wall: [174,134,104], door: [78,172,160] },
  ice: { sky: '#203548', horizon: '#141e29', floor: '#354c5b', wall: [130,183,208], door: [113,242,204] },
  crimson: { sky: '#382130', horizon: '#231119', floor: '#4c312f', wall: [191,111,110], door: [225,179,80] },
};
const moves = [[1,0], [0,1], [-1,0], [0,-1]];
const angle = x => Math.atan2(Math.sin(x), Math.cos(x));
const distance = (a,b) => Math.hypot(a.x-b.x, a.y-b.y);
const element = id => document.getElementById('fps-' + id);

function random(seed) {
  let state = seed >>> 0;
  return n => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return Math.floor(state / 4294967296 * n);
  };
}

function walk(grid, x, y) {
  const values = Array.from({length: size}, () => Array(size).fill(-1));
  const queue = [ [x,y] ];
  values[y][x] = 0;
  for (let i = 0; i < queue.length; i++) {
    const [cx,cy] = queue[i];
    for (const [dx,dy] of moves) {
      const nx = cx + dx, ny = cy + dy;
      if (grid[ny]?.[nx] !== 0 || values[ny][nx] !== -1) continue;
      values[ny][nx] = values[cy][cx] + 1;
      queue.push([nx,ny]);
    }
  }
  return {values, queue};
}

function sector(seed, stage) {
  const next = random(seed + stage * 6187);
  const grid = Array.from({length:size}, () => Array(size).fill(1));
  const trail = [[1,1]];
  grid[1][1] = 0;
  while (trail.length) {
    const [x,y] = trail.at(-1);
    const choices = moves.filter(([dx,dy]) => {
      const nx = x + dx*2, ny = y + dy*2;
      return nx > 0 && nx < size-1 && ny > 0 && ny < size-1 && grid[ny][nx] === 1;
    });
    if (!choices.length) { trail.pop(); continue; }
    const [dx,dy] = choices[next(choices.length)];
    grid[y+dy][x+dx] = grid[y+2*dy][x+2*dx] = 0;
    trail.push([x+2*dx,y+2*dy]);
  }
  const openings = [];
  for (let y = 1; y < size-1; y++) for (let x = 1; x < size-1; x++) {
    if (grid[y][x] && (grid[y][x-1] === 0 && grid[y][x+1] === 0 ||
        grid[y-1][x] === 0 && grid[y+1][x] === 0)) openings.push([x,y]);
  }
  for (let i = 0; i < 16 && openings.length; i++) {
    const [x,y] = openings.splice(next(openings.length),1)[0];
    grid[y][x] = 0;
  }
  const {values,queue} = walk(grid, 1, 1);
  const exit = queue.at(-1);
  const reachable = queue.filter(([x,y]) => values[y][x] > 4 && (x !== exit[0] || y !== exit[1]));
  const point = ([x,y]) => ({x:x+.5,y:y+.5});
  const take = list => point(list.splice(next(list.length),1)[0]);
  const key = take(reachable);
  const foes = Array.from({length: 5 + stage*2}, (_,i) => ({kind:i%5, ...take(reachable)}));
  const pickups = Array.from({length:9}, (_,i) => ({...take(reachable), kind:i%4}));
  return {grid, key, exit:point(exit), foes, pickups, facing:grid[1][2] === 0 ? 0 : Math.PI/2};
}

function cast(grid, x, y, dx, dy) {
  let col = Math.floor(x), row = Math.floor(y);
  const stepX = dx < 0 ? -1 : 1, stepY = dy < 0 ? -1 : 1;
  const invX = Math.abs(1/dx), invY = Math.abs(1/dy);
  let sx = (dx < 0 ? x-col : col+1-x)*invX, sy = (dy < 0 ? y-row : row+1-y)*invY;
  for (let i = 0; i < 2 * size; i++) {
    if (sx < sy) {
      col += stepX;
      if (grid[row]?.[col] !== 0) return {depth:sx, side:0, u:(y+sx*dy)%1};
      sx += invX;
    } else {
      row += stepY;
      if (grid[row]?.[col] !== 0) return {depth:sy, side:1, u:(x+sy*dx)%1};
      sy += invY;
    }
  }
  return {depth:size,side:0,u:0};
}

function loadSpec() { return demo; }

function launch(spec) {
  document.title = spec.title;
  element('title').textContent = spec.title;
  const view = element('view'), ctx = view.getContext('2d', {alpha:false});
  const overlay = element('overlay'), start = element('start'), select = element('difficulty');
  const width = view.width, height = view.height, horizon = height / 2;
  const depth = new Float32Array(width);
  const palette = palettes[spec.theme];
  const sky = ctx.createLinearGradient(0,0,0,horizon);
  sky.addColorStop(0,palette.sky); sky.addColorStop(1,palette.horizon);
  const floor = ctx.createLinearGradient(0,horizon,0,height);
  floor.addColorStop(0,palette.horizon); floor.addColorStop(1,palette.floor);
  const input = new Map();
  const spray = document.querySelectorAll('[data-weapon]');
  const keys = {KeyW:'forward', ArrowUp:'forward', KeyS:'back', ArrowDown:'back', KeyA:'strafe-left',
    KeyD:'strafe-right', ArrowLeft:'left', ArrowRight:'right', Space:'fire'};
  const player = {x:1.5,y:1.5,a:0,hp:100,slot:1,ammo:[0,36,12,16],key:false,cooldown:0};
  let level, tier = tiers[spec.difficulty], stage = 0, active = false, routes, pathDelay = 0;
  let flash = 0, hit = 0, lastFrame = 0, bolts = [], drag = null, map = false;
  select.value = String(spec.difficulty);

  function hud() {
    element('stage').textContent = 'Сектор ' + (stage+1) + ' / ' + spec.stages;
    element('health').textContent = 'Здоровье ' + Math.max(0,Math.ceil(player.hp));
    element('ammo').textContent = weapons[player.slot].name + ' · ' + (player.slot ? player.ammo[player.slot] : '∞');
    element('key').textContent = player.key ? 'Ключ найден' : 'Ключ не найден';
    for (const button of spray) button.setAttribute('aria-pressed', String(Number(button.dataset.weapon) === player.slot));
  }
  function switchWeapon(slot) { player.slot = slot; hud(); }
  function notice(text) { element('notice').textContent = text; }
  function menu(title, detail, action) {
    active = false;
    input.clear();
    element('message').textContent = title;
    element('detail').textContent = detail;
    element('difficulty-label').hidden = false;
    start.textContent = action;
    overlay.hidden = false;
  }
  function newSector() {
    level = sector(spec.seed, stage);
    Object.assign(player,{x:1.5,y:1.5,a:level.facing,key:false,cooldown:0});
    level.foes.forEach(e => Object.assign(e,{hp:Math.ceil(monsters[e.kind].hp*tier.health),ready:.7,hurt:0}));
    bolts = []; routes = null; pathDelay = 0; flash = hit = 0;
    hud();
  }
  function open(x,y,r=.17) {
    return [-r,r].every(dx => [-r,r].every(dy => level.grid[Math.floor(y+dy)]?.[Math.floor(x+dx)] === 0));
  }
  function move(entity, dx, dy, radius) {
    if (open(entity.x+dx,entity.y,radius)) entity.x += dx;
    if (open(entity.x,entity.y+dy,radius)) entity.y += dy;
  }
  function seen(from,to) {
    const d = distance(from,to);
    return d < .05 || cast(level.grid,from.x,from.y,(to.x-from.x)/d,(to.y-from.y)/d).depth >= d-.06;
  }
  function fire() {
    if (player.cooldown > 0) return;
    const weapon = weapons[player.slot];
    if (player.slot && player.ammo[player.slot] === 0) {
      notice('Боеприпасы закончились. Выбери другое оружие или используй лом.');
      player.cooldown = .34;
      return;
    }
    if (player.slot) player.ammo[player.slot]--;
    player.cooldown = weapon.reload;
    flash = .09;
    const targets = level.foes.filter(e => e.hp > 0 && distance(player,e) <= weapon.range
      && Math.abs(angle(Math.atan2(e.y-player.y,e.x-player.x)-player.a)) <= weapon.arc+.14/Math.max(1,distance(player,e))
      && seen(player,e)).sort((a,b)=>distance(player,a)-distance(player,b));
    for (const enemy of targets.slice(0,player.slot === 2 ? 3 : 1)) {
      const data = monsters[enemy.kind];
      enemy.hp -= weapon.damage;
      enemy.hurt = .17;
      if (enemy.hp <= 0) {
        level.pickups.push({x:enemy.x,y:enemy.y,kind:1+enemy.kind%3});
        notice(data.name + ' повержен. Можно подобрать боеприпасы.');
      }
    }
    hud();
  }
  function hurt(amount) {
    player.hp -= amount*tier.damage;
    hit = .22;
    hud();
    if (player.hp <= 0) menu('Сигнал потерян','Попробуй другую сложность или экономь боеприпасы.','Начать заново');
  }
  function moveFoes(dt) {
    pathDelay -= dt;
    if (pathDelay <= 0) {
      routes = walk(level.grid,Math.floor(player.x),Math.floor(player.y)).values;
      pathDelay = .28;
    }
    for (const e of level.foes) {
      if (!active || e.hp <= 0) continue;
      const data = monsters[e.kind];
      e.hurt = Math.max(0,e.hurt-dt); e.ready -= dt;
      const d = distance(e,player);
      if (d < data.range && seen(e,player)) {
        if (e.ready > 0) continue;
        e.ready = data.reload/tier.pressure;
        if (data.range <= 1.1) hurt(data.damage);
        else {
          const a = Math.atan2(player.y-e.y,player.x-e.x);
          bolts.push({x:e.x,y:e.y,dx:Math.cos(a)*3.2,dy:Math.sin(a)*3.2,color:data.color,damage:data.damage,life:2.5});
        }
        continue;
      }
      const x = Math.floor(e.x), y = Math.floor(e.y), current = routes[y][x];
      if (current > 16) continue;
      const next = moves.find(([dx,dy]) => routes[y+dy]?.[x+dx] >= 0 && routes[y+dy][x+dx] < current);
      const target = next ? {x:x+next[0]+.5,y:y+next[1]+.5} : player;
      const length = distance(e,target);
      if (length > .03) {
        const stride = Math.min(length, dt*data.speed*tier.pressure);
        move(e,(target.x-e.x)/length*stride,(target.y-e.y)/length*stride,.12);
      }
    }
    bolts = bolts.filter(b => {
      b.x += b.dx*dt; b.y += b.dy*dt; b.life -= dt;
      if (!active || b.life <= 0 || !open(b.x,b.y,.03)) return false;
      if (distance(b,player) < .29) { hurt(b.damage); return false; }
      return true;
    });
  }
  function update(dt) {
    const pressed = action => [...input.values()].includes(action);
    player.a = angle(player.a + (Number(pressed('right'))-Number(pressed('left')))*dt*2.1);
    const straight = Number(pressed('forward'))-Number(pressed('back'));
    const strafe = Number(pressed('strafe-right'))-Number(pressed('strafe-left'));
    const stride = dt*2.9/Math.max(1,Math.hypot(straight,strafe));
    move(player,(Math.cos(player.a)*straight-Math.sin(player.a)*strafe)*stride,
      (Math.sin(player.a)*straight+Math.cos(player.a)*strafe)*stride);
    flash = Math.max(0,flash-dt); hit = Math.max(0,hit-dt); player.cooldown -= dt;
    if (pressed('fire')) fire();
    if (!player.key && distance(player,level.key)<.53) {
      player.key = true; notice('Ключ найден. Ищи выход.'); hud();
    }
    level.pickups = level.pickups.filter(p => {
      if (distance(player,p)>.51) return true;
      if (p.kind) {
        player.ammo[p.kind] += Math.ceil([0,12,5,7][p.kind]*tier.supply);
        notice('Боеприпасы · ' + weapons[p.kind].name);
      } else {
        if (player.hp >= 100) return true;
        player.hp = Math.min(100,player.hp+27); notice('Аптечка +27');
      }
      hud(); return false;
    });
    if (distance(player,level.exit)<.48) {
      if (!player.key) notice('Выход заперт. Сначала найди ключ.');
      else if (stage+1 === spec.stages) {
        menu('Ты вышел на поверхность','Все секторы пройдены.','Играть ещё');
        return;
      } else {
        stage++;
        player.hp = Math.min(100,player.hp+25);
        for (let i=1;i<4;i++) player.ammo[i] = Math.max(player.ammo[i],[0,18,6,8][i]);
        newSector(); notice('Следующий сектор. Найди новый ключ.');
      }
    }
    moveFoes(dt);
  }

  function wallSlice(x,y,h,ray) {
    const shade = Math.max(.23, 1/(1+ray.depth*.105))*(ray.side?.8:1)
      *(ray.u<.055 || ray.u>.945 ? .57 : 1);
    const color = palette.wall.map(c=>Math.floor(c*shade));
    ctx.fillStyle = 'rgb('+color.join(',')+')';
    ctx.fillRect(x,y,1,h);
    ctx.fillStyle = '#13151b42';
    for (let band=1;band<=3;band++) ctx.fillRect(x, y + h * band / 4, 1, Math.max(1,h/27));
  }
  function drawActor(actor) {
    const dx = actor.x-player.x, dy = actor.y-player.y, d = Math.hypot(dx,dy);
    const rel = angle(Math.atan2(dy,dx)-player.a);
    const z = d*Math.cos(rel);
    if (z<=.14 || Math.abs(rel)>.85) return;
    const x = Math.round(width/2*(1 + Math.tan(rel)/fov));
    if (x < 0 || x >= width || z>depth[x]+.12) return;
    const scale = width/(2*fov*z), ground = horizon+scale*.5;
    const h = Math.min(200, scale*actor.height);
    const y = actor.bolt ? horizon-h/2 : ground-h;
    const w = Math.max(3,h*(actor.enemy ? .7 : .65));
    ctx.save();
    ctx.beginPath();
    for (let px = Math.max(0,Math.floor(x-w/2)); px<Math.min(width,x+w/2); px++) {
      if (z<depth[px]+.1) ctx.rect(px,Math.round(y),1,Math.ceil(h));
    }
    ctx.clip();
    ctx.fillStyle = actor.hurt>0 ? '#fff2c3' : actor.color;
    if (actor.enemy) {
      ctx.fillRect(x-w*.34,y,w*.68,h*.28);
      const extent = (.70 + actor.kind*.075) * w;
      ctx.fillRect(x-extent/2,y+h*.28,extent,h*.52);
      ctx.fillRect(x-w*.5,y+h*.55,w*.2,h*.45);
      ctx.fillRect(x+w*.30,y+h*.55,w*.2,h*.45);
      ctx.fillStyle = '#1b1720';
      ctx.fillRect(x-w*.23,y+h*.13,w*.46,Math.max(2,h*.10));
    } else if (actor.door) {
      ctx.fillRect(x-w*.45,y,w*.9,h);
      ctx.fillStyle = '#111e23'; ctx.fillRect(x-w*.30,y+h*.08,w*.60,h*.92);
      ctx.fillStyle = actor.color; ctx.fillRect(x+w*.16,y+h*.54,w*.1,Math.max(1,h*.04));
    } else if (actor.bolt) {
      ctx.beginPath(); ctx.arc(x,y+h/2,h/2,0,Math.PI*2); ctx.fill();
    } else {
      ctx.fillRect(x-w*.5,y+h*.15,w,h*.72);
      ctx.fillStyle = '#262431'; ctx.fillRect(x-w*.12,y+h*.25,w*.24,h*.53);
    }
    ctx.restore();
  }
  function drawMap() {
    const unit=5;
    ctx.fillStyle='#11131be6'; ctx.fillRect(4,4,size*unit+6,size*unit+6);
    level.grid.forEach((row,y)=>row.forEach((cell,x)=>{
      if(cell){ctx.fillStyle='#949198';ctx.fillRect(7+x*unit,7+y*unit,unit,unit);}
    }));
    for(const [point,color] of [[level.exit,'#7ef1bf'],[player,'#fff'],...(!player.key?[[level.key,'#f2c578']]:[])]){
      ctx.fillStyle=color;ctx.fillRect(5+point.x*unit,5+point.y*unit,4,4);
    }
  }
  function render() {
    ctx.fillStyle=sky; ctx.fillRect(0,0,width,horizon);
    ctx.fillStyle=floor; ctx.fillRect(0,horizon,width,horizon);
    for (let x=0;x<width;x++) {
      const shift=Math.atan((2*x/width-1)*fov), a=player.a+shift;
      const ray=cast(level.grid,player.x,player.y,Math.cos(a),Math.sin(a));
      const z=Math.max(.1,ray.depth*Math.cos(shift));
      depth[x]=z;
      const h=Math.min(height*4,width/(2*fov*z));
      wallSlice(x,horizon-h/2,h,ray);
    }
    const actors = [
      ...level.foes.filter(e=>e.hp>0).map(e=>({...e,height:.65+e.kind*.06,enemy:true,color:monsters[e.kind].color})),
      ...level.pickups.map(p=>({...p,height:.21,color:p.kind?'#76c4d4':'#e5887b'})),
      ...(!player.key?[{...level.key,height:.28,color:'#f4cf79'}]:[]),
      {...level.exit,height:.88,color:player.key?'#7ef1bf':'#e59071',door:true},
      ...bolts.map(b=>({...b,height:.17,bolt:true})),
    ];
    actors.sort((a,b)=>distance(b,player)-distance(a,player));
    for (const actor of actors) drawActor(actor);
    const gun = weapons[player.slot], kick = flash>0 ? 8 : 0;
    const center = width/2;
    ctx.fillStyle = '#161823';
    ctx.fillRect(center-27,height-32+kick,54,32);
    ctx.fillStyle = gun.color;
    if (player.slot) {
      ctx.fillRect(center-10,height-55+kick,20,38);
      ctx.fillRect(center-16,height-41+kick,32,30);
    } else {
      ctx.lineWidth=7; ctx.strokeStyle=gun.color; ctx.beginPath();
      ctx.moveTo(center+32,height); ctx.lineTo(center+62,height-72+kick);
      ctx.lineTo(center+49,height-78+kick); ctx.stroke();
    }
    if(flash>0 && player.slot) {
      ctx.fillStyle='#ffe6ae';ctx.fillRect(center-7,height-68+kick,14,17);
    }
    ctx.fillStyle='#fff5d1';
    ctx.fillRect(center-5,horizon,11,1);ctx.fillRect(center,horizon-5,1,11);
    if (hit>0) {
      ctx.fillStyle='rgba(216,80,65,'+(hit*1.25)+')';ctx.fillRect(0,0,width,height);
    }
    if (map) drawMap();
  }
  function frame(time) {
    const dt = Math.min(.048,(time-lastFrame)/1000 || .016);
    lastFrame=time;
    if (active) { update(dt); render(); }
    requestAnimationFrame(frame);
  }

  start.onclick = () => {
    tier = tiers[Number(select.value)]; stage=0; map=false;
    Object.assign(player,{hp:100,slot:1,ammo:[0,36,12,16]});
    newSector();
    notice('Найди ключ и выход. M показывает карту.');
    overlay.hidden=true; active=true; start.blur();
  };
  for (const button of spray) button.onclick = () => { if(active) switchWeapon(Number(button.dataset.weapon)); };
  for (const button of document.querySelectorAll('[data-control]')) {
    button.addEventListener('pointerdown', e=>{
      if(!active)return;
      e.preventDefault();button.setPointerCapture(e.pointerId);
      input.set(e.pointerId,button.dataset.control);
    });
    for (const event of ['pointerup','pointercancel','lostpointercapture']) {
      button.addEventListener(event,e=>input.delete(e.pointerId));
    }
  }
  view.addEventListener('pointerdown', e=>{
    if(!active)return;
    view.setPointerCapture(e.pointerId);drag={id:e.pointerId,x:e.clientX};
  });
  view.addEventListener('pointermove', e=>{
    if(drag?.id!==e.pointerId)return;
    player.a=angle(player.a+(e.clientX-drag.x)*.012);drag.x=e.clientX;
  });
  for (const event of ['pointerup','pointercancel','lostpointercapture']) {
    view.addEventListener(event,e=>{ if(drag?.id===e.pointerId)drag=null; });
  }
  window.addEventListener('keydown', e=>{
    if(!active)return;
    if(keys[e.code]){e.preventDefault();input.set(e.code,keys[e.code]);}
    if(/^Digit[1-4]$/.test(e.code))switchWeapon(Number(e.code.at(-1))-1);
    if(e.code==='KeyM'&&!e.repeat)map=!map;
  });
  window.addEventListener('keyup', e=>input.delete(e.code));
  window.addEventListener('blur',()=>{input.clear();drag=null;});
  newSector(); render();
  menu(spec.title,'Найди ключ и выход из каждого сектора.','Начать игру');
  requestAnimationFrame(frame);
}

try { launch(loadSpec()); }
catch(error) {
  element('message').textContent = 'Не удалось запустить игру';
  element('start').disabled = true;
  throw error;
}
})();
