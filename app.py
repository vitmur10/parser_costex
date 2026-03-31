import os
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, send_file, jsonify, request, session, redirect, url_for
from dotenv import load_dotenv

# Завантажуємо змінні з .env
load_dotenv()

app = Flask(__name__)
# Секретний ключ потрібен для безпечної роботи сесій
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-costex-key")

# Дані для входу
ADMIN_USER = os.getenv("DASHBOARD_USER", "admin")
ADMIN_PASS = os.getenv("DASHBOARD_PASS", "admin123")

BASE_DIR = Path(__file__).resolve().parent
LATEST_XLSX = BASE_DIR / "costex_catalog_latest.xlsx"
LOG_FILE = BASE_DIR / "logs" / "costex_parser.log"

parser_process = None
status = {
    "is_running": False,
    "last_run": "Never",
    "result": "Waiting for action..."
}


# Декоратор для захисту сторінок
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # Якщо це API-запит від JavaScript, повертаємо помилку доступу
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            # Якщо це звичайний перехід, кидаємо на сторінку логіну
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def run_parser_task():
    global parser_process, status
    status["is_running"] = True
    status["result"] = "Running..."

    try:
        import sys
        parser_process = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=str(BASE_DIR)
        )
        parser_process.wait()

        if parser_process.returncode == 0:
            status["result"] = "Success"
        elif parser_process.returncode is not None and parser_process.returncode < 0:
            status["result"] = "Terminated by user"
        else:
            status["result"] = "Failed (check logs)"

    except Exception as e:
        status["result"] = f"Error: {str(e)}"
    finally:
        status["is_running"] = False
        status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parser_process = None


# --- МАРШРУТИ АВТОРИЗАЦІЇ ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USER and request.form['password'] == ADMIN_PASS:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'Невірний логін або пароль!'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))


# --- ЗАХИЩЕНІ МАРШРУТИ ---

@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/api/status')
@login_required
def get_status():
    return jsonify(status)


@app.route('/api/logs')
@login_required
def get_logs():
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                return jsonify({"logs": "".join(lines[-40:])})
        except Exception:
            return jsonify({"logs": "Error reading log file."})
    return jsonify({"logs": "Log file not found yet..."})


@app.route('/api/run', methods=['POST'])
@login_required
def run_now():
    if not status["is_running"]:
        thread = threading.Thread(target=run_parser_task, daemon=True)
        thread.start()
        return jsonify({"message": "Parser started"}), 202
    return jsonify({"error": "Parser is already running"}), 400


@app.route('/api/stop', methods=['POST'])
@login_required
def stop_now():
    global parser_process
    if status["is_running"] and parser_process:
        parser_process.terminate()
        return jsonify({"message": "Terminating process..."}), 200
    return jsonify({"error": "Parser is not running"}), 400


@app.route('/download')
@login_required
def download():
    if LATEST_XLSX.exists():
        return send_file(LATEST_XLSX, as_attachment=True)
    return "File not found. Please run the parser first.", 404


if __name__ == '__main__':
    from waitress import serve

    print("🚀 Server is running on port 5000...")
    serve(app, host='0.0.0.0', port=5000)