// Update the installed package after rebuilding the shared Rust renderer.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = path.join(root, 'rust/wasm/pkg');
fs.rmSync(path.join(source, '.gitignore'), { force: true });
const entry = require.resolve('opencut-wasm', { paths: [path.join(root, 'apps/web')] });
const target = fs.realpathSync(path.dirname(entry));
if (target !== fs.realpathSync(source)) {
 for (const file of fs.readdirSync(source)) {
  if (file !== '.gitignore') fs.copyFileSync(path.join(source, file), path.join(target, file));
 }
}
console.log('Local Rust renderer synchronized.');
