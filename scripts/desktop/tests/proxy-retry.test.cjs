// Exercise the actual bridge handler, including its allowlist and origin guard.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const ts = require(process.argv[2] || '../../../fe/apps/web/node_modules/typescript');
(async () => {
 const source = path.resolve(__dirname, '../../../fe/apps/web/src/app/api/auto-translate/[...path]/route.ts');
 const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'editor-proxy-test-'));
 const file = path.join(directory, 'route.mjs');
 const originalFetch = global.fetch;
 try {
  fs.writeFileSync(file, ts.transpileModule(fs.readFileSync(source, 'utf8'), {compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText);
  const handler = await import(pathToFileURL(file).href);
  const calls = [];
  global.fetch = async (url, options) => {calls.push({url:String(url),options});return Response.json({job_id:'job-test',status:'queued'});};
  const context = {params:Promise.resolve({path:['api','jobs','job-test','retry']})};
  const request = origin => new Request('http://localhost:5176/api/auto-translate/api/jobs/job-test/retry', {method:'POST',headers:{host:'localhost:5176',origin}});
  const result = await handler.POST(request('http://localhost:5176'), context);
  assert.equal(result.status, 200);
  assert.equal((await result.json()).status, 'queued');
  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.endsWith('/api/jobs/job-test/retry'));
  assert.equal(calls[0].options.method,'POST');
  assert.equal((await handler.POST(request('https://foreign.example'), context)).status,403);
  assert.equal(calls.length,1);
  assert.equal((await handler.POST(request('http://localhost:5176'),{params:Promise.resolve({path:['api','jobs','job-test','delete']})})).status,404);
  console.log('Retry bridge: allowed POST reaches backend; foreign origins and unsupported routes rejected');
 } finally {global.fetch=originalFetch;fs.rmSync(directory,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
