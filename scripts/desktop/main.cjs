const {app,BrowserWindow,Menu,dialog,safeStorage,powerSaveBlocker}=require('electron');
const {spawn,execFile}=require('node:child_process');
const fs=require('node:fs');const path=require('node:path');const net=require('node:net');const crypto=require('node:crypto');
if(process.env.EDITOR_USER_DATA) app.setPath('userData',path.resolve(process.env.EDITOR_USER_DATA));
const root=path.join(process.resourcesPath,'bundle');
const children=[];let mainWindow,hardware,quitting=false;
const data=app.getPath('userData');const configPath=path.join(data,'config.json');
function config(){try{return JSON.parse(fs.readFileSync(configPath,'utf8'));}catch{return {};}}
function key(c,field='key'){try{return c[field]?safeStorage.decryptString(Buffer.from(c[field],'base64')):'';}catch{return '';}}
function child(exe,args,env,cwd,name){
 const log=fs.openSync(path.join(data,'logs',`${name}-${Date.now()}.log`),'a');
 const p=spawn(exe,args,{cwd,env:{...process.env,...env},windowsHide:true,stdio:['ignore',log,log]});fs.closeSync(log);children.push(p);p.on('error',e=>{dialog.showErrorBox('Editor',`${name}: ${e.message}`);});return p;
}
async function stop(){await Promise.all(children.filter(p=>p.pid&&p.exitCode===null).map(p=>new Promise(r=>execFile('taskkill.exe',['/PID',String(p.pid),'/T','/F'],{windowsHide:true},()=>r()))));children.length=0;}
async function free(port){return new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',()=>reject(new Error(`Cổng ${port} đang được ứng dụng khác sử dụng.`)));s.listen(port,'127.0.0.1',()=>s.close(resolve));});}
async function ready(url){const deadline=Date.now()+120000;while(Date.now()<deadline){try{const r=await fetch(url);if(r.ok)return;}catch{}await new Promise(r=>setTimeout(r,500));}throw new Error(`Dịch vụ chưa sẵn sàng: ${url}. Xem nhật ký trong ${path.join(data,'logs')}`);}
if(config().cpu){app.commandLine.appendSwitch('use-angle','swiftshader');app.commandLine.appendSwitch('use-webgpu-adapter','swiftshader');app.commandLine.appendSwitch('enable-unsafe-swiftshader');}else{app.commandLine.appendSwitch('force-high-performance-gpu');}
app.commandLine.appendSwitch('enable-unsafe-webgpu');
if(!app.requestSingleInstanceLock()){app.quit();}else{
app.on('second-instance',()=>{if(mainWindow){if(mainWindow.isMinimized())mainWindow.restore();mainWindow.focus();}});
app.on('before-quit',e=>{if(!quitting&&children.length){e.preventDefault();quitting=true;stop().finally(()=>app.quit());}});
app.on('window-all-closed',()=>app.quit());
app.whenReady().then(async()=>{
 fs.mkdirSync(path.join(data,'logs'),{recursive:true});
 mainWindow=new BrowserWindow({width:1440,height:930,minWidth:1000,minHeight:680,title:'Lwent Editor',backgroundColor:'#12141b',autoHideMenuBar:false,webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false}});
 mainWindow.loadFile(path.join(__dirname,'loading.html'));
 const updater=require('./updates.cjs').createUpdater(mainWindow,stop);
 mainWindow.webContents.setWindowOpenHandler(({url})=>{if(/^https?:\/\//.test(url))require('electron').shell.openExternal(url);return {action:'deny'};});
 mainWindow.webContents.on('will-navigate',(e,url)=>{if(!url.startsWith('http://127.0.0.1:5176/')&&!url.startsWith('blob:http://127.0.0.1:5176/')&&!url.startsWith('data:'))e.preventDefault();});
 Menu.setApplicationMenu(Menu.buildFromTemplate([{label:'Editor',submenu:[{label:'Kiểm tra cập nhật…',click:()=>updater.check(true)},{type:'separator'},{label:'Thông tin phần cứng',click:()=>dialog.showMessageBox(mainWindow,{title:'Phần cứng',message:hardware?`Chế độ: ${config().cpu?'CPU tương thích':'Tự động'}\nGPU: ${hardware.gpu||'CPU / GPU tích hợp'}\nMã hóa video: ${hardware.encoder}\nCPU: ${hardware.cpuThreads} luồng xử lý\nRAM: ${hardware.memoryGB} GB`:'Đang kiểm tra…'})},{type:'separator'},{role:'quit',label:'Thoát'}]},{label:'Chỉnh sửa',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},{label:'Hiển thị',submenu:[{role:'reload',label:'Tải lại giao diện'},{role:'togglefullscreen',label:'Toàn màn hình'}]}]));
 try{
 for(const port of [5176,8101,20131,20132])await free(port);
 const python=path.join(root,'python','python.exe');const ffmpeg=path.join(root,'tools','ffmpeg.exe');
 const base={PYTHONNOUSERSITE:'1',PYTHONUTF8:'1',PYTHONUNBUFFERED:'1',HF_HOME:path.join(root,'models','vieneu'),HF_MODULES_CACHE:path.join(data,'hf-modules'),HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',EDITOR_LAZY_MODELS:'1',PATH:`${path.join(root,'tools')};${process.env.PATH||''}`};
 hardware=await new Promise(resolve=>execFile(python,[path.join(__dirname,'hardware.py'),ffmpeg],{env:{...process.env,...base},windowsHide:true,timeout:70000},(error,out)=>{try{resolve(JSON.parse(out.trim()));}catch{resolve({cuda:false,cpuThreads:4,encoder:'libx264'});}}));
 const c=config();if(c.cpu){hardware.cuda=false;hardware.encoder='libx264';base.CUDA_VISIBLE_DEVICES='-1';}base.EDITOR_CPU_THREADS=String(hardware.cpuThreads);base.OMP_NUM_THREADS=String(hardware.cpuThreads);base.MKL_NUM_THREADS=String(hardware.cpuThreads);
 const backend=path.join(root,'backend');const cuda=hardware.cuda&&hardware.vramGB>=3;
 // A writable config outside the installed bundle survives app updates.
 const aiConfigFile=path.join(data,'ai.env');
 if(!fs.existsSync(aiConfigFile)){
  const quote=value=>"'"+String(value).replace(/\\/g,'\\\\').replace(/'/g,"\\'")+"'";
  const initial={AUTO_TRANSLATE_NINEROUTER_API_URL:c.url||'http://127.0.0.1:20128/v1',AUTO_TRANSLATE_AI_MODEL:c.model||'ag/gemini-3.8-flash-medium'};
  const previousKey=key(c);if(previousKey)initial.AUTO_TRANSLATE_NINEROUTER_API_KEY=previousKey;
  fs.writeFileSync(aiConfigFile,Object.entries(initial).map(([name,value])=>`${name}=${quote(value)}`).join('\n')+'\n',{mode:0o600});
 }

 const beEnv={...base,AUTO_TRANSLATE_STORAGE_DIR:path.join(data,'storage'),AUTO_TRANSLATE_FFMPEG_PATH:ffmpeg,AUTO_TRANSLATE_VIDEO_ENCODER:hardware.encoder,AUTO_TRANSLATE_VIDEO_PRESET:'medium',AUTO_TRANSLATE_CORS_ORIGINS:JSON.stringify(['http://127.0.0.1:5176']),EDITOR_AI_CONFIG_FILE:aiConfigFile,AUTO_TRANSLATE_VIENEU_API_URL:'http://127.0.0.1:20131',AUTO_TRANSLATE_ZEROTTS_API_URL:'http://127.0.0.1:20132',AUTO_TRANSLATE_WHISPER_MODEL:path.join(root,'models','whisper'),AUTO_TRANSLATE_WHISPER_FALLBACK_MODEL:path.join(root,'models','base.pt'),AUTO_TRANSLATE_WHISPER_DEVICE:cuda?'cuda':'cpu',AUTO_TRANSLATE_WHISPER_COMPUTE_TYPE:cuda?'int8_float16':'int8',AUTO_TRANSLATE_WHISPER_LOCAL_FILES_ONLY:'true',AUTO_TRANSLATE_YTDLP_AUTO_BROWSER_COOKIES:'false',ZEROTTS_MODEL_DIR:path.join(root,'models','zerotts'),VIENEU_DEVICE:cuda?'cuda':'cpu',VIENEU_BACKEND:'pytorch'};
 child(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8101'],beEnv,backend,'backend');
 child(python,['-m','uvicorn','app.services.ai.vieneu_server:app','--host','127.0.0.1','--port','20131'],beEnv,backend,'vieneu');
 child(python,['-m','uvicorn','app.services.ai.zerotts_server:app','--host','127.0.0.1','--port','20132'],beEnv,backend,'zerotts');
 const front=path.join(root,'frontend','apps','web');
 child(process.execPath,[path.join(front,'server.js')],{ELECTRON_RUN_AS_NODE:'1',EDITOR_DESKTOP_MODE:'1',NODE_ENV:'production',PORT:'5176',HOSTNAME:'127.0.0.1',NEXT_TELEMETRY_DISABLED:'1',RVP_BACKEND_URL:'http://127.0.0.1:8101',NEXT_PUBLIC_SITE_URL:'http://127.0.0.1:5176',NEXT_PUBLIC_MARBLE_API_URL:'https://api.marblecms.com',DATABASE_URL:'postgres://desktop:desktop@127.0.0.1:5432/desktop',BETTER_AUTH_SECRET:crypto.randomBytes(32).toString('hex'),UPSTASH_REDIS_REST_URL:'https://desktop.invalid',UPSTASH_REDIS_REST_TOKEN:'desktop-placeholder',MARBLE_WORKSPACE_KEY:'desktop-placeholder',FREESOUND_CLIENT_ID:'desktop-placeholder',FREESOUND_API_KEY:key(c,'soundKey')||'desktop-placeholder'},front,'frontend');
 await Promise.all([ready('http://127.0.0.1:8101/health'),ready('http://127.0.0.1:5176/projects')]);
 powerSaveBlocker.start('prevent-app-suspension');await mainWindow.loadURL('http://127.0.0.1:5176/projects');
 setTimeout(()=>updater.check(false),5000);
 }catch(e){await stop();await dialog.showMessageBox(mainWindow,{type:'error',title:'Không thể khởi động Editor',message:e.message});app.quit();}
});}
