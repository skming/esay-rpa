'use strict';

const { BrowserWindow, app } = require('electron');

// electron-updater is a runtime dep — safe to require unconditionally.
const { autoUpdater } = require('electron-updater');

/** Send a typed payload to the current main window's renderer. */
function sendToRenderer(channel, payload) {
  const wins = BrowserWindow.getAllWindows();
  if (wins.length > 0 && !wins[0].isDestroyed()) {
    wins[0].webContents.send(channel, payload);
  }
}

/**
 * Configure and wire up electron-updater.
 * Call once after app is ready. Returns controller methods for IPC handlers.
 */
function initUpdater() {
  // Never auto-download: let the user choose when to pull the update.
  autoUpdater.autoDownload = false;
  // Install silently on next quit if already downloaded.
  autoUpdater.autoInstallOnAppQuit = true;
  // Suppress the built-in dialog; we handle everything in the renderer.
  autoUpdater.allowPrerelease = false;

  autoUpdater.on('checking-for-update', () => {
    sendToRenderer('update:status', { status: 'checking' });
  });

  autoUpdater.on('update-available', (info) => {
    sendToRenderer('update:status', {
      status: 'available',
      version: info.version,
      releaseDate: info.releaseDate,
      releaseNotes: typeof info.releaseNotes === 'string' ? info.releaseNotes : null,
    });
  });

  autoUpdater.on('update-not-available', (info) => {
    sendToRenderer('update:status', {
      status: 'not-available',
      version: info.version,
    });
  });

  autoUpdater.on('download-progress', (progress) => {
    sendToRenderer('update:status', {
      status: 'downloading',
      percent: Math.round(progress.percent),
      bytesPerSecond: Math.round(progress.bytesPerSecond),
      transferred: progress.transferred,
      total: progress.total,
    });
  });

  autoUpdater.on('update-downloaded', (info) => {
    sendToRenderer('update:status', {
      status: 'ready',
      version: info.version,
    });
  });

  autoUpdater.on('error', (err) => {
    // "No publish configuration" is expected in dev / unconfigured deployments.
    const isUnconfigured = err.message?.includes('publish') || err.message?.includes('Cannot find');
    sendToRenderer('update:status', {
      status: 'error',
      error: isUnconfigured ? 'update-server-not-configured' : err.message,
    });
  });

  return {
    checkForUpdates() {
      if (!app.isPackaged) {
        sendToRenderer('update:status', { status: 'error', error: 'update-dev-mode' });
        return;
      }
      autoUpdater.checkForUpdates().catch((err) => {
        sendToRenderer('update:status', { status: 'error', error: err.message });
      });
    },
    downloadUpdate() {
      autoUpdater.downloadUpdate().catch((err) => {
        sendToRenderer('update:status', { status: 'error', error: err.message });
      });
    },
    quitAndInstall() {
      autoUpdater.quitAndInstall(false, true);
    },
  };
}

module.exports = { initUpdater };
