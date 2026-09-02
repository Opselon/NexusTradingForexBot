// locate which literal matched 'tab' in both files under the collision regexes
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '..', 'Web');
const read = (f) => fs.readFileSync(path.join(WEB, f), 'utf8');
const created = (src) => {
  const s = new Set();
  for (const m of src.matchAll(/id=\\?['"]([a-zA-Z][\w-]{2,60})\\?['"]/g)) { s.add(m[1]); console.log('  id=-match:', m[1]); }
  for (const m of src.matchAll(/getElementById\(['"]([\w-]+)['"]\)/g)) { s.add(m[1]); console.log('  gEBI-match:', m[1]); }
  return s;
};
console.log('ux_palette.js:'); created(read('ux_palette.js'));
console.log('control_center.js:'); created(read('control_center.js'));
