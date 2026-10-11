const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('editorSettings',{get:()=>ipcRenderer.invoke('editor:get-config'),save:c=>ipcRenderer.invoke('editor:save-config',c)});
