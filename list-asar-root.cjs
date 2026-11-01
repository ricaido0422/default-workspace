const fs = require('fs');
const path = require('path');
const asarRoot = 'D:\\Program Files (x86)\\DeepSeek\\resources\\app.asar';

function listDir(p, depth, maxDepth) {
  let entries;
  try { entries = fs.readdirSync(p, { withFileTypes: true }); }
  catch (e) { console.log('  '.repeat(depth) + 'ERR ' + p + ': ' + e.message); return; }
  for (const e of entries) {
    const full = path.join(p, e.name);
    if (e.isDirectory()) {
      console.log('  '.repeat(depth) + '[D] ' + e.name);
      if (depth < maxDepth) listDir(full, depth + 1, maxDepth);
    } else {
      let size = '';
      try { size = fs.statSync(full).size; } catch (_) {}
      console.log('  '.repeat(depth) + '[F] ' + e.name + (size !== '' ? ' (' + size + ')' : ''));
    }
  }
}

console.log('=== app.asar root (depth 2) ===');
listDir(asarRoot, 0, 2);
