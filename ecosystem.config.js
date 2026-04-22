const path = require("path");
const os = require("os");
const PROJECT_DIR = __dirname;
const VENV_PYTHON = path.join(PROJECT_DIR, "backend", ".venv", "bin", "python");
const CLOUDFLARED_CONFIG = path.join(os.homedir(), ".cloudflared", "config.yml");

module.exports = {
  apps: [
    {
      name: "chief-updater",
      script: path.join(PROJECT_DIR, "scripts", "watch-updates.sh"),
      autorestart: false,
      cron_restart: "*/5 * * * *",
      watch: false,
      log_file: path.join(PROJECT_DIR, "logs", "updater.log"),
      error_file: path.join(PROJECT_DIR, "logs", "updater-error.log"),
    },
    {
      name: "chief-build",
      script: "npm",
      args: "run build",
      cwd: path.join(PROJECT_DIR, "frontend"),
      autorestart: false,
      watch: false,
      log_file: path.join(PROJECT_DIR, "logs", "build.log"),
      error_file: path.join(PROJECT_DIR, "logs", "build-error.log"),
    },
    {
      name: "chief-tunnel",
      script: "cloudflared",
      args: `tunnel --config ${CLOUDFLARED_CONFIG} run voice-claude`,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
      log_file: path.join(PROJECT_DIR, "logs", "tunnel.log"),
      error_file: path.join(PROJECT_DIR, "logs", "tunnel-error.log"),
    },
    {
      name: "chief-backend",
      script: VENV_PYTHON,
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app --reload-dir services --reload-dir config",
      cwd: path.join(PROJECT_DIR, "backend"),
      autorestart: true,
      restart_delay: 2000,
      max_restarts: 20,
      log_file: path.join(PROJECT_DIR, "logs", "backend.log"),
      error_file: path.join(PROJECT_DIR, "logs", "backend-error.log"),
    },
  ],
};
