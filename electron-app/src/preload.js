const { contextBridge } = require('electron');

// Expose minimal API to renderer
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  serverWsUrl: process.env.OPENBELL_WS_URL || 'ws://localhost:5000/ws',
});
