const fs = require('fs');
const path = require('path');
const root = path.join('D:\\Program Files (x86)\\DeepSeek\\resources', 'app.asar', 'dsh', 'node_modules', '@deepseek-ai');

function walk(dir, out) {
  let ents;
  try { ents = fs.readdirSync(dir, { withFileTypes: true }); }
  catch (e) { return; }
  for (const e of ents) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, out);
    else if (/\.(js|cjs|mjs|ts)$/.test(e.name)) out.push(full);
  }
}

const files = [];
walk(root, files);

const needles = ['SKILL.md', '.dsh/skills', 'skills/aihot', 'skillsDir', 'skills-dir', 'skillDir', 'readSkills', 'loadSkills', 'discoverSkill'];
const hits = new Map();
for (const f of files) {
  let src;
  try { src = fs.readFileSync(f, 'utf8'); } catch (e) { continue; }
  for (const n of needles) {
    if (src.includes(n)) {
      if (!hits.has(n)) hits.set(n, []);
      const arr = hits.get(n);
      if (arr.length < 20) arr.push(path.relative(root, f));
    }
  }
}
for (const [n, arr] of hits) {
  console.log('\n### ' + n + ' (' + arr.length + ' shown)');
  for (const f of arr) console.log('  ' + f);
}
console.log('\nTOTAL_JS_FILES=' + files.length);
