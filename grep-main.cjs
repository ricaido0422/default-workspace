const fs = require('fs');
const p = 'D:\\Program Files (x86)\\DeepSeek\\resources\\app.asar\\lib\\main.js';
const src = fs.readFileSync(p, 'utf8');
const lines = src.split('\n');

const needles = ['spawn', 'restart', 'respawn', 'relaunch', 'shutdown', 'dsh-desktop-host', 'index.js', 'fork(', 'child_process', 'hostProcess', 'hostPid', 'relaunchHost', 'restartHost', 'kill'];

const hits = new Map();
for (let i = 0; i < lines.length; i++) {
  const line = lines[i];
  for (const n of needles) {
    if (line.includes(n)) {
      if (!hits.has(n)) hits.set(n, []);
      const arr = hits.get(n);
      if (arr.length < 30) arr.push((i + 1) + ': ' + line.trim());
    }
  }
}
for (const [n, arr] of hits) {
  console.log('\n### ' + n + ' (' + arr.length + ' shown)');
  for (const s of arr) console.log(s);
}
