import os
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, send_file, jsonify

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
LATEST_XLSX = BASE_DIR / "costex_catalog_latest.xlsx"
LOG_FILE = BASE_DIR / "logs" / "costex_parser.log"

# Глобальний стан
parser_process = None
status = {
    "is_running": False,
    "last_run": "Never",
    "result": "Waiting for action..."
}


def run_parser_task():
    global parser_process, status
    status["is_running"] = True
    status["result"] = "Running..."

    try:
        # Використовуємо Popen, щоб мати змогу зупинити процес
        parser_process = subprocess.Popen(
            ["python", "main.py"],
            cwd=str(BASE_DIR)
        )

        # Чекаємо завершення процесу
        parser_process.wait()

        if parser_process.returncode == 0:
            status["result"] = "Success"
        elif parser_process.returncode is not None and parser_process.returncode < 0:
            # Негативний код зазвичай означає примусову зупинку (terminate)
            status["result"] = "Terminated by user"
        else:
            status["result"] = "Failed (check logs)"

    except Exception as e:
        status["result"] = f"Error: {str(e)}"
    finally:
        status["is_running"] = False
        status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parser_process = None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def get_status():
    return jsonify(status)


@app.route('/api/logs')
def get_logs():
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                # Читаємо останні 40 рядків для консолі
                lines = f.readlines()
                return jsonify({"logs": "".join(lines[-40:])})
        except Exception:
            return jsonify({"logs": "Error reading log file."})
    return jsonify({"logs": "Log file not found yet..."})


@app.route('/api/run', methods=['POST'])
def run_now():
    if not status["is_running"]:
        thread = threading.Thread(target=run_parser_task, daemon=True)
        thread.start()
        return jsonify({"message": "Parser started"}), 202
    return jsonify({"error": "Parser is already running"}), 400


@app.route('/api/stop', methods=['POST'])
def stop_now():
    global parser_process
    if status["is_running"] and parser_process:
        parser_process.terminate()  # Надсилаємо сигнал завершення
        return jsonify({"message": "Terminating process..."}), 200
    return jsonify({"error": "Parser is not running"}), 400


@app.route('/download')
def download():
    if LATEST_XLSX.exists():
        return send_file(LATEST_XLSX, as_attachment=True)
    return "File not found. Please run the parser first.", 404


if __name__ == '__main__':
    # Запускаємо на 5000 порту
    app.run(host='0.0.0.0', port=5000, debug=True)