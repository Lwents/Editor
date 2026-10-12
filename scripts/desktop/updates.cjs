const {app,dialog}=require('electron');
const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');const {spawn}=require('node:child_process');
const repository='Lwents/Editor';
const assetPrefix=`https://github.com/${repository}/releases/download/`;
const compare=(a,b)=>{const x=a.split('.').map(Number),y=b.split('.').map(Number);for(let i=0;i<Math.max(x.length,y.length);i++){const d=(x[i]||0)-(y[i]||0);if(d)return d;}return 0;};
let busy=false;
function validate(manifest,assets){
 if(manifest.schema!==1||!/^\d+\.\d+\.\d+(?:\.\d+)?$/.test(manifest.version)||!/^\d+\.\d+\.\d+$/.test(manifest.runtimeVersion)||! /^[a-f0-9]{64}$/.test(manifest.runtimeHash)||! /^[a-f0-9]{64}$/.test(manifest.sha256)||!Number.isSafeInteger(manifest.size)||manifest.size<1||manifest.size>1500*1024*1024)throw new Error('Thông tin bản cập nhật không hợp lệ.');
 const asset=assets.find(a=>a.name==='Editor-Update-Windows-x64.zip');
 if(!asset||!asset.browser_download_url.startsWith(assetPrefix)||asset.size!==manifest.size)throw new Error('Không tìm thấy gói cập nhật hợp lệ.');
 return asset.browser_download_url;
}
async function json(url){const r=await fetch(url,{headers:{'User-Agent':'Lwent-Editor','Accept':'application/vnd.github+json'},signal:AbortSignal.timeout(15000)});if(r.status===404)return null;if(!r.ok)throw new Error('Không kết nối được máy chủ cập nhật.');const value=await r.text();if(value.length>256000)throw new Error('Thông tin cập nhật quá lớn.');return JSON.parse(value);}
function createUpdater(window,stop){
 async function check(manual=false){
  if(busy)return;busy=true;let accepted=false;
  try{
   const release=await json(`https://api.github.com/repos/${repository}/releases/latest`);
   const meta=release?.assets?.find(a=>a.name==='update.json');
   if(!meta){if(manual)await dialog.showMessageBox(window,{message:'Chưa có bản cập nhật mới.',detail:`Phiên bản hiện tại: ${app.getVersion()}`});return;}
   if(!meta.browser_download_url.startsWith(assetPrefix))throw new Error('Địa chỉ cập nhật không hợp lệ.');
   const manifest=await json(meta.browser_download_url);const url=validate(manifest,release.assets);
   if(compare(manifest.version,app.getVersion())<=0){if(manual)await dialog.showMessageBox(window,{message:'Bạn đang dùng phiên bản mới nhất.',detail:`Phiên bản: ${app.getVersion()}`});return;}
   const runtime=JSON.parse(fs.readFileSync(path.join(process.resourcesPath,'runtime.json'),'utf8'));
   if(runtime.version!==manifest.runtimeVersion||runtime.hash!==manifest.runtimeHash){const result=await dialog.showMessageBox(window,{message:'Bản mới cần nâng cấp thư viện AI.',detail:'Bản này cần cài bộ cài đầy đủ. Dự án và cấu hình đã lưu vẫn được giữ.',buttons:['Mở trang tải','Để sau'],cancelId:1});if(result.response===0)require('electron').shell.openExternal(`https://github.com/${repository}/releases/latest`);return;}
   const result=await dialog.showMessageBox(window,{type:'info',message:`Có bản cập nhật mới: ${manifest.version}`,detail:`Đang dùng ${app.getVersion()} · tải ${(manifest.size/1048576).toFixed(1)} MB.\nCập nhật giao diện/backend; giữ nguyên model, dự án và cấu hình.\nApp sẽ đóng và mở lại khi cài xong.`,buttons:['Cập nhật','Để sau'],defaultId:0,cancelId:1});
   if(result.response!==0)return;
   accepted=true;
   const active=await json('http://127.0.0.1:8101/api/jobs');if(active?.some(j=>['queued','processing'].includes(j.status))){await dialog.showMessageBox(window,{message:'Hãy đợi tác vụ xử lý video hoàn tất rồi cập nhật.'});return;}
   const free=fs.statfsSync(process.resourcesPath);if(Number(free.bavail)*Number(free.bsize)<manifest.size*4+104857600)throw new Error('Không đủ dung lượng trống để cập nhật và sao lưu.');
   const directory=path.join(app.getPath('userData'),'updates',crypto.randomUUID());fs.mkdirSync(directory,{recursive:true});
   const zip=path.join(directory,'update.zip');const response=await fetch(url,{signal:AbortSignal.timeout(600000)});if(!response.ok||!response.body)throw new Error('Không tải được gói cập nhật.');
   const hash=crypto.createHash('sha256');const file=fs.openSync(zip,'wx');let size=0;
   try{for await(const chunk of response.body){size+=chunk.length;if(size>manifest.size)throw new Error('Gói cập nhật vượt kích thước cho phép.');hash.update(chunk);fs.writeSync(file,chunk);window.setProgressBar(size/manifest.size);window.setTitle(`Đang tải cập nhật ${Math.floor(size/manifest.size*100)}% · Lwent Editor`);}}finally{fs.closeSync(file);}
   if(size!==manifest.size||hash.digest('hex')!==manifest.sha256)throw new Error('Gói tải về bị lỗi; chưa thay đổi app. Hãy thử lại.');
   // Recheck after download, since another job may have started meanwhile.
   const current=await json('http://127.0.0.1:8101/api/jobs');if(current?.some(j=>['queued','processing'].includes(j.status)))throw new Error('Có video đang xử lý. Hãy đợi hoàn tất rồi cập nhật.');
   const worker=path.join(directory,'apply-update.py');fs.copyFileSync(path.join(__dirname,'apply-update.py'),worker);
   const log=fs.openSync(path.join(directory,'update.log'),'a');
   const processWorker=spawn(path.join(process.resourcesPath,'bundle','python','python.exe'),[worker,'--zip',zip,'--sha256',manifest.sha256,'--version',manifest.version,'--resources',process.resourcesPath,'--exe',process.execPath,'--pid',String(process.pid),'--user-data',app.getPath('userData')],{detached:true,windowsHide:true,env:{...process.env,PYTHONNOUSERSITE:'1',PYTHONUTF8:'1'},stdio:['ignore',log,log]});
   await new Promise((resolve,reject)=>{processWorker.once('spawn',resolve);processWorker.once('error',reject);});fs.closeSync(log);processWorker.unref();
   await stop();app.exit(0);
  }catch(error){if(manual||accepted)await dialog.showMessageBox(window,{type:'error',message:'Chưa cập nhật được',detail:error.message});}
  finally{busy=false;if(!window.isDestroyed()){window.setProgressBar(-1);window.setTitle('Lwent Editor');}}
 }
 return {check};
}
module.exports={createUpdater,compare,validate};
