const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('veliaConnect', {
  openPairing: () => ipcRenderer.invoke('velia:open-pairing'),
  submit: code => ipcRenderer.invoke('velia:pair', code),
});
