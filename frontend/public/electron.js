const {app, BrowserWindow, ipcMain} = require("electron");
const path = require("path");
const {spawn} = require("child_process");
const fs = require("fs");
const axios = require("axios");
const si = require("systeminformation");

let mainWindow = null;
let backendProcess = null;
let llamaServerProcess = null;
let currentNgl = 0;
let currentModelPath = null;

const backendRelativePath = path.join("app", "backend", "backend.exe");
const llamaServerDirRelativePath = path.join("app", "llama-server");
const llamaServerExeName = "llama-server.exe";

const BACKEND_PORT = 9015;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const LLAMA_SERVER_PORT = 9016;

async function getNglValue() {
  console.log("[Electron NGL] Getting GPU info via systeminformation...");
  try {
    const gpuData = await si.graphics();
    if (gpuData && gpuData.controllers && gpuData.controllers.length > 0) {
      const nvidiaGpu = gpuData.controllers.find(gpu => gpu.vendor && gpu.vendor.toLowerCase().includes("nvidia"));
      if (nvidiaGpu && nvidiaGpu.vram && nvidiaGpu.vram > 0) {
        const total_mem = nvidiaGpu.vram;
        console.log(`[Electron NGL] Found GPU: ${nvidiaGpu.model}, VRAM: ${total_mem} MB`);
        if (total_mem >= 22000) {
          console.log("[Electron NGL] Setting NGL: -1");
          return -1;
        } else if (total_mem >= 15000) {
          console.log("[Electron NGL] Setting NGL: 40");
          return 40;
        } else if (total_mem >= 10000) {
          console.log("[Electron NGL] Setting NGL: 30");
          return 30;
        } else if (total_mem >= 7000) {
          console.log("[Electron NGL] Setting NGL: 20");
          return 20;
        } else if (total_mem >= 5000) {
          console.log("[Electron NGL] Setting NGL: 15");
          return 15;
        } else {
          console.log("[Electron NGL] Setting NGL: 10");
          return 10;
        }
      } else {
        console.log("[Electron NGL] NVIDIA GPU not found or VRAM info unavailable.");
      }
    } else {
      console.log("[Electron NGL] No graphics controllers found.");
    }
  } catch (error) {
    console.error("[Electron NGL] Error getting GPU info:", error);
  }
  console.log("[Electron NGL] Defaulting NGL to 0 (CPU).");
  return 0;
}

async function getFirstInstalledModelPath() {
  console.log("[Electron Model] Fetching model list from backend...");
  try {
    await new Promise(resolve => setTimeout(resolve, 4000));
    const response = await axios.get(`${BACKEND_URL}/api/models`, {timeout: 15000});
    if (response.status === 200 && Array.isArray(response.data)) {
      const installedModels = response.data.filter(m => m.installed && m.file_name);
      if (installedModels.length > 0) {
        const modelFileName = installedModels[0].file_name;
        const envPathsModule = await import("env-paths");
        const paths = envPathsModule.default("NeuraBox", {suffix: ""});
        const userDataDir = paths.data;
        if (!userDataDir) {
          console.error("[Electron Model] Could not determine user data directory via env-paths.");
          return null;
        }
        const modelDir = path.join(userDataDir, "models");
        const modelFullPath = path.join(modelDir, modelFileName);
        console.log(`[Electron Model] Checking for first installed model: ${modelFileName} at ${modelFullPath}`);
        if (fs.existsSync(modelFullPath)) {
          console.log(`[Electron Model] Found first installed model path: ${modelFullPath}`);
          return modelFullPath;
        } else {
          console.error(`[Electron Model] Model file path check failed: ${modelFullPath} does not exist.`);
          return null;
        }
      } else {
        console.log("[Electron Model] No installed models found via API.");
        return null;
      }
    } else {
      console.error(`[Electron Model] Failed to fetch models, status: ${response.status}`);
      return null;
    }
  } catch (error) {
    if (error.code === "ECONNREFUSED" || error.response?.status === 503) {
      console.warn(`[Electron Model] Backend not ready yet (${error.message}). Cannot get initial model.`);
    } else {
      console.error(`[Electron Model] Error fetching models from backend: ${error.message}`);
    }
    return null;
  }
}

function startLlamaServer(modelFilePath, nglValue) {
  if (llamaServerProcess) {
    console.log("[Electron] Llama Server is already running or starting.");
    return;
  }
  if (!modelFilePath) {
    console.log("[Electron] No model path provided, skipping llama-server start.");
    return;
  }
  const serverDir = path.join(process.resourcesPath, llamaServerDirRelativePath);
  const serverExePath = path.join(serverDir, llamaServerExeName);
  const defaultCtxSize = 4096;
  const llamaServerPort = LLAMA_SERVER_PORT;
  const llamaServerArgs = ["-m", modelFilePath, "--port", llamaServerPort.toString(), "-ngl", nglValue.toString(), "-c", defaultCtxSize.toString(),];
  console.log("[Electron] Preparing to start llama-server...");
  console.log(`[Electron] Server Path: ${serverExePath}`);
  console.log(`[Electron] Working Dir: ${serverDir}`);
  console.log(`[Electron] Arguments: ${llamaServerArgs.join(" ")}`);
  if (!fs.existsSync(serverExePath)) {
    console.error(`[Electron] Llama Server executable not found at ${serverExePath}`);
    return;
  }
  try {
    llamaServerProcess = spawn(serverExePath, llamaServerArgs, {stdio: "pipe", cwd: serverDir});
    currentModelPath = modelFilePath;
    currentNgl = nglValue;
    const pid = llamaServerProcess.pid;
    console.log(`[Electron] Llama Server process spawned. PID: ${pid}`);
    llamaServerProcess.stdout.on("data", (data) => {
      console.log(`[LlamaServer STDOUT] ${data.toString().trim()}`);
    });
    llamaServerProcess.stderr.on("data", (data) => {
      console.error(`[LlamaServer STDERR] ${data.toString().trim()}`);
    });
    llamaServerProcess.on("close", (code) => {
      console.log(`[Electron] Llama Server process PID ${pid} exited with code ${code}`);
      if (llamaServerProcess && llamaServerProcess.pid === pid) {
        llamaServerProcess = null;
        currentModelPath = null;
      }
    });
    llamaServerProcess.on("error", (err) => {
      console.error("[Electron] Failed to start Llama Server process:", err);
      if (llamaServerProcess && llamaServerProcess.pid === pid) {
        llamaServerProcess = null;
        currentModelPath = null;
      }
    });
  } catch (spawnError) {
    console.error("[Electron] Error spawning llama-server process:", spawnError);
    llamaServerProcess = null;
    currentModelPath = null;
  }
}

function stopLlamaServer() {
  if (llamaServerProcess) {
    const pid = llamaServerProcess.pid;
    console.log(`[Electron Quit] Killing llama-server process (PID: ${pid})...`);
    try {
      const killed = llamaServerProcess.kill();
      console.log(`[Electron Quit] llamaServerProcess.kill() called. Result: ${killed}`);
      setTimeout(() => {
        try {
          process.kill(pid, 0);
          console.warn(`[Electron Quit] Llama server process ${pid} still alive after SIGTERM, trying taskkill.`);
          if (process.platform === "win32") {
            require("child_process").execSync(`taskkill /F /PID ${pid}`);
            console.log("[Electron Quit] Taskkill successful.");
          }
        } catch (e) {
          if (e.code === "ESRCH") {
            console.log(`[Electron Quit] Llama server process ${pid} confirmed terminated.`);
          } else {
            console.error("[Electron Quit] Error checking/killing process:", e.message);
          }
        }
      }, 1500);
    } catch (error) {
      console.error("[Electron Quit] Error sending kill signal to llama-server:", error);
    } finally {
      llamaServerProcess = null;
      currentModelPath = null;
    }
  } else {
    console.log("[Electron Quit] Llama Server process already stopped or was not running.");
  }
}

function startBackend() {
  if (backendProcess) {
    console.log("[Electron] Backend process is already running or starting.");
    return;
  }
  const backendPath = path.join(process.resourcesPath, backendRelativePath);
  console.log(`[Electron] Trying to start backend from: ${backendPath}`);
  if (!fs.existsSync(backendPath)) {
    console.error(`[Electron] Backend executable not found at ${backendPath}`);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("backend-status", {isRunning: false, error: `File not found: ${backendPath}`});
    }
    return;
  }
  try {
    backendProcess = spawn(backendPath, [], {stdio: "pipe"});
    const pid = backendProcess.pid;
    console.log(`[Electron] Backend process spawned. PID: ${pid}`);
    backendProcess.stdout.on("data", (data) => {
      console.log(`[Backend STDOUT] ${data.toString().trim()}`);
    });
    backendProcess.stderr.on("data", (data) => {
      console.error(`[Backend STDERR] ${data.toString().trim()}`);
    });
    backendProcess.on("close", (code) => {
      console.log(`[Electron] Backend process PID ${pid} exited with code ${code}`);
      if (backendProcess && backendProcess.pid === pid) {
        backendProcess = null;
      }
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("backend-status", {isRunning: false, error: `Process exited code ${code}`});
      }
    });
    backendProcess.on("error", (err) => {
      console.error("[Electron] Failed to start backend process:", err);
      if (backendProcess && backendProcess.pid === pid) {
        backendProcess = null;
      }
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("backend-status", {isRunning: false, error: `Spawn error: ${err.message}`});
      }
    });
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("backend-status", {isRunning: true});
    }
  } catch (spawnError) {
    console.error("[Electron] Error spawning backend process:", spawnError);
    backendProcess = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("backend-status", {
        isRunning: false,
        error: `Spawn catch: ${spawnError.message}`
      });
    }
  }
}

function stopBackend() {
  if (backendProcess) {
    const pid = backendProcess.pid;
    console.log(`[Electron Quit] Killing backend process (PID: ${pid})...`);
    try {
      const killed = backendProcess.kill();
      console.log(`[Electron Quit] backendProcess.kill() called. Result: ${killed}`);
      setTimeout(() => {
        try {
          process.kill(pid, 0);
          console.warn(`[Electron Quit] Backend process ${pid} still alive after SIGTERM, trying taskkill.`);
          if (process.platform === "win32") {
            require("child_process").execSync(`taskkill /F /PID ${pid}`);
            console.log("[Electron Quit] Taskkill successful for backend.");
          }
        } catch (e) {
          if (e.code === "ESRCH") {
            console.log(`[Electron Quit] Backend process ${pid} confirmed terminated.`);
          } else {
            console.error("[Electron Quit] Error checking/killing backend process:", e.message);
          }
        }
      }, 1500);
    } catch (error) {
      console.error("[Electron Quit] Error sending kill signal to backend:", error);
    } finally {
      backendProcess = null;
    }
  } else {
    console.log("[Electron Quit] Backend process already stopped or was not running.");
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    }
  });

  if (process.env.NODE_ENV === "development" || !app.isPackaged) {
    console.log("[Electron] Loading frontend from localhost:3000");
    mainWindow.loadURL("http://localhost:3000");
    mainWindow.webContents.openDevTools();
  } else {
    const indexPath = path.join(__dirname, "..", "build", "index.html");
    console.log(`[Electron] Loading production index from: ${indexPath}`);
    mainWindow.loadFile(indexPath);
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });

  if (app.isPackaged) {
    console.log("[Electron] Packaged mode detected. Starting background services...");
    startBackend();

    try {
      currentNgl = await getNglValue();
      const firstModelPath = await getFirstInstalledModelPath();
      if (firstModelPath) {
        startLlamaServer(firstModelPath, currentNgl);
      } else {
        console.log("[Electron] No initial model found or backend error. Llama Server will not be started automatically.");
      }
    } catch (error) {
      console.error("[Electron] Error during initial service startup sequence:", error);
    }

  } else {
    console.log("[Electron] Development mode. Start backend and llama-server manually if needed.");
  }
});

app.on("quit", () => {
  console.log("[Electron Quit] Application quitting trigger received...");
  stopLlamaServer();
  stopBackend();
  console.log("[Electron Quit] Finished cleanup attempts.");
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

ipcMain.on("switch-model", async (event, newModelFileName) => {
  console.log(`[Electron IPC] Received switch-model request for: ${newModelFileName}`);
  let modelDir = null;
  try {
    const envPathsModule = await import("env-paths");
    const paths = envPathsModule.default("NeuraBox", {suffix: ""});
    modelDir = path.join(paths.data, "models");
  } catch (e) {
    console.error("[Electron IPC] Failed to get user data path via env-paths:", e);
    modelDir = path.join(app.getPath("userData"), "..", "Local", "NeuraBoxTeam", "NeuraBox", "models");
    console.warn(`[Electron IPC] Falling back to estimated models path: ${modelDir}`);
  }

  const newModelPath = path.join(modelDir, newModelFileName);

  if (!fs.existsSync(newModelPath)) {
    console.error(`[Electron IPC] New model file not found: ${newModelPath}`);
    event.reply("switch-model-error", `File not found: ${newModelFileName}`);
    return;
  }

  if (llamaServerProcess && currentModelPath === newModelPath) {
    console.log(`[Electron IPC] Model ${newModelFileName} is already running.`);
    event.reply("switch-model-success", newModelFileName);
    return;
  }

  stopLlamaServer();
  await new Promise(resolve => setTimeout(resolve, 1500));
  startLlamaServer(newModelPath, currentNgl);
  event.reply("switch-model-success", newModelFileName);
});

ipcMain.on("start-server-first-time", async (event, modelFileName) => {
  console.log(`[Electron IPC] Received start-server-first-time request for: ${modelFileName}`);
  if (llamaServerProcess) {
    console.log("[Electron IPC] Server is already running.");
    return;
  }

  let modelDir = null;
  try {
    const envPathsModule = await import("env-paths");
    const paths = envPathsModule.default("NeuraBox", {suffix: ""});
    modelDir = path.join(paths.data, "models");
  } catch (e) {
    console.error("Failed to get user data dir:", e);
    modelDir = path.join(app.getPath("userData"), "..", "Local", "NeuraBoxTeam", "NeuraBox", "models");
  }

  const modelPath = path.join(modelDir, modelFileName);
  if (!fs.existsSync(modelPath)) {
    console.error(`[Electron IPC] Model file not found: ${modelPath}`);
    return;
  }

  if (currentNgl === undefined || currentNgl === 0) {
    console.log("[Electron IPC] Redetermining NGL before first server start...");
    currentNgl = await getNglValue();
  }
  startLlamaServer(modelPath, currentNgl);
});