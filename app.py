#!/usr/bin/env python3
"""
AttendanceIQ - Student Attendance Management System
Backend: Flask + mysql-connector-python
"""

from flask import Flask, render_template, request, jsonify, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import mysql.connector
from datetime import datetime
import os
import logging
import re
from functools import wraps

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":        os.environ.get("DB_HOST", "localhost"),
    "port":        int(os.environ.get("DB_PORT", 3306)),
    "user":        os.environ.get("DB_USER", "root"),
    "password":    os.environ.get("DB_PASS", "system"),
    "database":    os.environ.get("DB_NAME", "attendance_db"),
    "charset":     "utf8mb4",
    "autocommit":  True,
    "connection_timeout": 10,
    "use_pure": True
}

app = Flask(__name__)
CORS(app, origins=["http://localhost:5000", "http://127.0.0.1:5000"])
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1000 per hour", "100 per minute"],
    storage_uri="memory://"
)

# --- Input Validation Functions ---
def validate_roll_number(roll):
    """Validate roll number format (alphanumeric, 3-20 chars)"""
    if not roll or not isinstance(roll, str):
        return False, "Roll number is required"
    roll = roll.strip()
    if not re.match(r'^[A-Za-z0-9]{3,20}$', roll):
        return False, "Roll number must be 3-20 alphanumeric characters"
    return True, roll.strip()

def validate_name(name):
    """Validate student name (letters, spaces, hyphens, 2-100 chars)"""
    if not name or not isinstance(name, str):
        return False, "Name is required"
    name = name.strip()
    if not re.match(r'^[A-Za-z\s\-\'\.]{2,100}$', name):
        return False, "Name must be 2-100 characters with letters, spaces, hyphens, apostrophes, or periods"
    return True, name.strip().title()

def validate_date(date_str):
    """Validate date format (YYYY-MM-DD) and not future date"""
    if not date_str:
        return False, "Date is required"
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        today = datetime.now().date()
        if date_obj > today:
            return False, "Date cannot be in the future"
        return True, date_str
    except ValueError:
        return False, "Invalid date format. Use YYYY-MM-DD"

def validate_status(status):
    """Validate attendance status"""
    if status not in ['Present', 'Absent']:
        return False, "Status must be 'Present' or 'Absent'"
    return True, status

def handle_db_errors(f):
    """Decorator to handle database connection errors"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except mysql.connector.Error as e:
            logger.error(f"Database error: {e}")
            return jsonify({"error": "Database connection failed"}), 500
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return jsonify({"error": "An unexpected error occurred"}), 500
    return decorated_function

def get_db():
    if 'db' not in g:
        try:
            g.db = mysql.connector.connect(**DB_CONFIG)
            g.db.ping(reconnect=True)
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            g.db = None
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    try:
        base_config = DB_CONFIG.copy()
        db_name = base_config.pop("database")
        conn = mysql.connector.connect(**base_config)
        
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
            cur.execute(f"USE {db_name}")
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    roll_number VARCHAR(20) NOT NULL UNIQUE,
                    name VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    date DATE NOT NULL,
                    status ENUM('Present', 'Absent') NOT NULL,
                    UNIQUE KEY unique_attendance (student_id, date),
                    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# --- DB Helpers ---
def db_get_all_students(conn):
    with conn.cursor(dictionary=True) as cur:
        cur.execute("""
            SELECT id, roll_number, name, 
                   DATE_FORMAT(created_at, '%Y-%m-%d %H:%i') as created_at
            FROM students ORDER BY roll_number
        """)
        return cur.fetchall()

def db_add_student(conn, roll, name):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO students (roll_number, name) VALUES (%s, %s)",
                (roll, name)
            )
        return True, "Student added successfully"
    except mysql.connector.IntegrityError:
        return False, "Roll number already exists"
    except Exception as e:
        return False, f"Error: {str(e)}"

def db_delete_student(conn, student_id):
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM students WHERE id = %s", (student_id,))
        return True, "Deleted successfully"
    except Exception as e:
        return False, str(e)

def db_upsert_attendance_bulk(conn, attendance_data):
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO attendance (student_id, date, status) 
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE status = VALUES(status)
        """, attendance_data)

def db_get_attendance_for_date(conn, date):
    with conn.cursor(dictionary=True) as cur:
        cur.execute("""
            SELECT s.id, s.roll_number, s.name, 
                   COALESCE(a.status, 'Not Marked') as status
            FROM students s
            LEFT JOIN attendance a ON s.id = a.student_id AND a.date = %s
            ORDER BY s.roll_number
        """, (date,))
        return cur.fetchall()

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/health')
def api_health():
    conn = get_db()
    if conn is None: return jsonify({"status": "error"}), 500
    return jsonify({"status": "ok"})

@app.route('/api/students', methods=['GET', 'POST'])
@handle_db_errors
def api_students():
    conn = get_db()
    if conn is None: return jsonify({"error": "DB error"}), 500
    
    if request.method == 'GET':
        return jsonify(db_get_all_students(conn))
    
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400
    
    roll = data.get('roll_number')
    name = data.get('name')
    
    # Validate inputs
    valid_roll, roll_msg = validate_roll_number(roll)
    if not valid_roll:
        return jsonify({"error": roll_msg}), 400
    
    valid_name, name_msg = validate_name(name)
    if not valid_name:
        return jsonify({"error": name_msg}), 400
    
    success, msg = db_add_student(conn, roll_msg, name_msg)
    return jsonify({"message": msg} if success else {"error": msg}), 201 if success else 400

@app.route('/api/students/<int:student_id>', methods=['DELETE'])
@handle_db_errors
def api_delete_student(student_id):
    conn = get_db()
    if conn is None: return jsonify({"error": "DB error"}), 500
    
    success, msg = db_delete_student(conn, student_id)
    return jsonify({"message": msg} if success else {"error": msg})

@app.route('/api/attendance', methods=['GET', 'POST'])
@handle_db_errors
def api_attendance():
    conn = get_db()
    if conn is None: return jsonify({"error": "DB error"}), 500
    
    if request.method == 'GET':
        date = request.args.get('date')
        if not date:
            return jsonify({"error": "Date parameter is required"}), 400
        
        valid_date, date_msg = validate_date(date)
        if not valid_date:
            return jsonify({"error": date_msg}), 400
            
        return jsonify(db_get_attendance_for_date(conn, date_msg))
    
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400
    
    date = data.get('date')
    records = data.get('records', [])
    
    if not date:
        return jsonify({"error": "Date is required"}), 400
    
    valid_date, date_msg = validate_date(date)
    if not valid_date:
        return jsonify({"error": date_msg}), 400
    
    if not records:
        return jsonify({"error": "No attendance records provided"}), 400
    
    # Validate each record
    valid_records = []
    for record in records:
        student_id = record.get('student_id')
        status = record.get('status')
        
        if not student_id or not status:
            continue
            
        valid_status, status_msg = validate_status(status)
        if not valid_status:
            continue
            
        try:
            student_id = int(student_id)
        except ValueError:
            continue
            
        valid_records.append((student_id, date_msg, status_msg))
    
    if not valid_records:
        return jsonify({"error": "No valid attendance records"}), 400
    
    try:
        db_upsert_attendance_bulk(conn, valid_records)
        return jsonify({"message": f"Saved attendance for {len(valid_records)} students"})
    except Exception as e:
        logger.error(f"Error saving attendance: {e}")
        return jsonify({"error": "Failed to save attendance"}), 500

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000, host="0.0.0.0")
