const { app, BrowserWindow, Notification, session } = require('electron');
const path = require('path');

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 480,
    minHeight: 360,
    title: 'OpenBell',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    backgroundColor: '#0a0a0a',
    icon: path.join(__dirname, '..', 'icon.png'),
  });

  // ── Network lockdown: block ALL external requests ──
  // Only allow connections to localhost, 127.x, 192.168.x.x, 10.x.x.x, file://
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
    const url = details.url;
    // Allow local files, devtools, and chrome internals
    if (url.startsWith('file://') || url.startsWith('devtools://') || url.startsWith('chrome')) {
      callback({});
      return;
    }
    try {
      const parsed = new URL(url);
      const host = parsed.hostname;
      const isLocal = (
        host === 'localhost' ||
        host === '127.0.0.1' ||
        host === '::1' ||
        host.startsWith('192.168.') ||
        host.startsWith('10.') ||
        host.startsWith('172.16.') ||
        host.startsWith('172.17.') ||
        host.startsWith('172.18.') ||
        host.startsWith('172.19.') ||
        host.startsWith('172.2') ||
        host.startsWith('172.3') ||
        host.endsWith('.local')
      );
      if (!isLocal) {
        console.warn(`[BLOCKED] External request: ${url}`);
        callback({ cancel: true });
        return;
      }
    } catch (e) {
      // Malformed URL — block it
      callback({ cancel: true });
      return;
    }
    callback({});
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));

  // Open DevTools in development
  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools();
  }
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
