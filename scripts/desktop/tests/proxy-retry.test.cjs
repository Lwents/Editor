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
  const recovery = new Request('http://localhost:5176/api/auto-translate/api/jobs/job-test/retry?text_only_gemini=true', {method:'POST',headers:{host:'localhost:5176',origin:'http://localhost:5176'}});
  assert.equal((await handler.POST(recovery, context)).status,200);
  assert.ok(calls[1].url.endsWith('/retry?text_only_gemini=true'));
  assert.equal((await handler.POST(request('https://foreign.example'), context)).status,403);
  assert.equal(calls.length,2);
  assert.equal((await handler.POST(request('http://localhost:5176'),{params:Promise.resolve({path:['api','jobs','job-test','delete']})})).status,404);
  for(const operation of ['backup','source']) {
   const url=`http://localhost:5176/api/auto-translate/api/jobs/job-test/${operation}`;
   assert.equal((await handler.GET(new Request(url),{params:Promise.resolve({path:['api','jobs','job-test',operation]})})).status,200);
  }
  const binary=Buffer.from(Array.from({length:65536},(_,index)=>index%256));
  const restore=new Request('http://localhost:5176/api/auto-translate/api/jobs/restore',{method:'POST',headers:{host:'localhost:5176',origin:'http://localhost:5176','content-type':'application/octet-stream','content-length':String(binary.length)},body:binary});
  assert.equal((await handler.POST(restore,{params:Promise.resolve({path:['api','jobs','restore']})})).status,200);
  assert.ok(calls.at(-1).url.endsWith('/api/jobs/restore'));
  assert.deepEqual(Buffer.from(await new Response(calls.at(-1).options.body).arrayBuffer()),binary);
  assert.equal(calls.at(-1).options.headers.get('content-length'),String(binary.length));
  console.log('Retry bridge: allowed POST reaches backend; foreign origins and unsupported routes rejected');
 } finally {global.fetch=originalFetch;fs.rmSync(directory,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
