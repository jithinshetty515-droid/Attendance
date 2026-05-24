import os
import sqlite3
from datetime import date
from io import StringIO, BytesIO
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, Response, send_file, send_from_directory
from flask_mail import Mail, Message
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import qrcode

app = Flask(__name__)
app.secret_key = "change_this_secret_key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
QR_DIR = os.path.join(BASE_DIR, "qr_codes")
os.makedirs(QR_DIR, exist_ok=True)

app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "True") == "True"
app.config["MAIL_USE_SSL"] = os.environ.get("MAIL_USE_SSL", "False") == "True"
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", app.config["MAIL_USERNAME"])

mail = Mail(app)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roll_no TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            att_date TEXT NOT NULL,
            status TEXT NOT NULL,
            UNIQUE(student_id, att_date)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            marks INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            task TEXT NOT NULL,
            due_date TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    """)

    cur.execute("SELECT COUNT(*) AS c FROM admins")
    if cur.fetchone()["c"] == 0:
        cur.execute("INSERT INTO admins (username, password) VALUES (?, ?)", ("admin", "admin123"))

    conn.commit()
    conn.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "student_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    message = ""
    if request.method == "POST":
        name = request.form["name"].strip()
        roll_no = request.form["roll_no"].strip()
        password = request.form["password"].strip()

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO students (name, roll_no, password) VALUES (?, ?, ?)",
                (name, roll_no, password)
            )
            student_id = cur.lastrowid
            conn.commit()

            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(str(student_id))
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(os.path.join(QR_DIR, f"{student_id}.png"))

            message = f"Student registered successfully. QR saved for ID {student_id}."
        except sqlite3.IntegrityError:
            message = "Roll number already exists."
        finally:
            conn.close()

    return render_template("register.html", message=message)

@app.route("/test-css")
def test_css():
    return '''
    <html>
      <head>
        <link rel="stylesheet" href="/static/style.css">
      </head>
      <body>
        <h1>CSS test</h1>
      </body>
    </html>
    '''


@app.route("/login", methods=["GET", "POST"])
def login():
    message = ""
    if request.method == "POST":
        roll_no = request.form["roll_no"].strip()
        password = request.form["password"].strip()

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM students WHERE roll_no = ? AND password = ?", (roll_no, password))
        student = cur.fetchone()
        conn.close()

        if student:
            session["student_id"] = student["id"]
            session["student_name"] = student["name"]
            session["role"] = "student"
            return redirect(url_for("student_dashboard"))
        message = "Invalid student login."

    return render_template("login.html", message=message)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/student_dashboard")
@login_required
def student_dashboard():
    student_id = session["student_id"]
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM students WHERE id = ?", (student_id,))
    student = cur.fetchone()

    cur.execute("SELECT COUNT(DISTINCT att_date) AS total_days FROM attendance")
    total_days = cur.fetchone()["total_days"] or 0

    cur.execute("SELECT COUNT(*) AS present_days FROM attendance WHERE student_id = ? AND status = 'Present'", (student_id,))
    present_days = cur.fetchone()["present_days"] or 0

    attendance_percentage = round((present_days / total_days) * 100, 2) if total_days else 0

    cur.execute("SELECT * FROM marks WHERE student_id = ? ORDER BY id DESC", (student_id,))
    marks = cur.fetchall()

    cur.execute("SELECT * FROM homework ORDER BY due_date DESC")
    homework_list = cur.fetchall()

    conn.close()

    return render_template(
        "student_dashboard.html",
        student=student,
        present_days=present_days,
        total_days=total_days,
        attendance_percentage=attendance_percentage,
        marks=marks,
        homework_list=homework_list
    )


@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    message = ""
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM admins WHERE username = ? AND password = ?", (username, password))
        admin = cur.fetchone()
        conn.close()

        if admin:
            session["role"] = "admin"
            session["admin_name"] = username
            return redirect(url_for("admin_dashboard"))
        message = "Invalid admin login."

    return render_template("admin_login.html", message=message)


@app.route("/admin_dashboard")
@admin_required
def admin_dashboard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students ORDER BY id DESC")
    students = cur.fetchall()
    cur.execute("SELECT * FROM marks ORDER BY id DESC")
    marks = cur.fetchall()
    cur.execute("SELECT * FROM homework ORDER BY due_date DESC")
    homework_list = cur.fetchall()
    conn.close()
    return render_template("admin_dashboard.html", students=students, marks=marks, homework_list=homework_list)


@app.route("/scan")
def scan():
    return render_template("scan.html")


@app.route("/mark_attendance/<int:student_id>")
def mark_attendance(student_id):
    today = date.today().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students WHERE id = ?", (student_id,))
    student = cur.fetchone()
    if not student:
        conn.close()
        return "Student not found.", 404

    try:
        cur.execute(
            "INSERT INTO attendance (student_id, att_date, status) VALUES (?, ?, ?)",
            (student_id, today, "Present")
        )
        conn.commit()
        msg = "Attendance marked successfully."
    except sqlite3.IntegrityError:
        msg = "Attendance already marked today."
    conn.close()
    return f"{msg} Student ID: {student_id}, Date: {today}"


@app.route("/qr/<int:student_id>")
def qr_image(student_id):
    return send_from_directory(QR_DIR, f"{student_id}.png")


@app.route("/marks", methods=["GET", "POST"])
@admin_required
def marks():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        student_id = request.form["student_id"]
        subject = request.form["subject"].strip()
        marks_value = request.form["marks"].strip()
        cur.execute(
            "INSERT INTO marks (student_id, subject, marks) VALUES (?, ?, ?)",
            (student_id, subject, marks_value)
        )
        conn.commit()

    cur.execute("SELECT * FROM students ORDER BY name")
    students = cur.fetchall()

    cur.execute("""
        SELECT m.id, s.name, s.roll_no, m.subject, m.marks
        FROM marks m
        JOIN students s ON s.id = m.student_id
        ORDER BY m.id DESC
    """)
    mark_list = cur.fetchall()
    conn.close()
    return render_template("marks.html", students=students, mark_list=mark_list)


@app.route("/homework", methods=["GET", "POST"])
@admin_required
def homework():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        subject = request.form["subject"].strip()
        task = request.form["task"].strip()
        due_date = request.form["due_date"]
        cur.execute(
            "INSERT INTO homework (subject, task, due_date) VALUES (?, ?, ?)",
            (subject, task, due_date)
        )
        conn.commit()

    cur.execute("SELECT * FROM homework ORDER BY due_date DESC")
    homework_list = cur.fetchall()
    conn.close()
    return render_template("homework.html", homework_list=homework_list)


@app.route("/report")
@admin_required
def report():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(DISTINCT att_date) AS total_days FROM attendance")
    total_days = cur.fetchone()["total_days"] or 0

    cur.execute("""
        SELECT s.id, s.name, s.roll_no, COUNT(a.id) AS present_days
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.status = 'Present'
        GROUP BY s.id, s.name, s.roll_no
        ORDER BY s.name
    """)
    attendance_rows = cur.fetchall()

    attendance_report = []
    for row in attendance_rows:
        present_days = row["present_days"]
        percentage = round((present_days / total_days) * 100, 2) if total_days else 0
        attendance_report.append({
            "id": row["id"],
            "name": row["name"],
            "roll_no": row["roll_no"],
            "present_days": present_days,
            "total_days": total_days,
            "percentage": percentage
        })

    cur.execute("""
        SELECT s.name, s.roll_no, m.subject, m.marks
        FROM marks m
        JOIN students s ON s.id = m.student_id
        ORDER BY s.name
    """)
    marks_report = cur.fetchall()

    cur.execute("SELECT * FROM homework ORDER BY due_date DESC")
    homework_report = cur.fetchall()
    conn.close()

    return render_template(
        "report.html",
        attendance_report=attendance_report,
        marks_report=marks_report,
        homework_report=homework_report
    )


@app.route("/print_qr")
@admin_required
def print_qr():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students ORDER BY id")
    students = cur.fetchall()
    conn.close()
    return render_template("print_qr.html", students=students)


@app.route("/export/attendance.csv")
@admin_required
def export_attendance_csv():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.name, s.roll_no, a.att_date, a.status
        FROM attendance a
        JOIN students s ON s.id = a.student_id
        ORDER BY a.att_date DESC
    """)
    rows = cur.fetchall()
    conn.close()

    output = StringIO()
    output.write("name,roll_no,date,status\n")
    for r in rows:
        output.write(f'{r["name"]},{r["roll_no"]},{r["att_date"]},{r["status"]}\n')

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=attendance.csv"}
    )


@app.route("/export/marks.csv")
@admin_required
def export_marks_csv():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.name, s.roll_no, m.subject, m.marks
        FROM marks m
        JOIN students s ON s.id = m.student_id
        ORDER BY s.name
    """)
    rows = cur.fetchall()
    conn.close()

    output = StringIO()
    output.write("name,roll_no,subject,marks\n")
    for r in rows:
        output.write(f'{r["name"]},{r["roll_no"]},{r["subject"]},{r["marks"]}\n')

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=marks.csv"}
    )


@app.route("/export/homework.csv")
@admin_required
def export_homework_csv():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT subject, task, due_date FROM homework ORDER BY due_date DESC")
    rows = cur.fetchall()
    conn.close()

    output = StringIO()
    output.write("subject,task,due_date\n")
    for r in rows:
        output.write(f'{r["subject"]},{r["task"]},{r["due_date"]}\n')

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=homework.csv"}
    )


@app.route("/export/report.pdf")
@admin_required
def export_report_pdf():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(DISTINCT att_date) AS total_days FROM attendance")
    total_days = cur.fetchone()["total_days"] or 0

    cur.execute("""
        SELECT s.name, s.roll_no, COUNT(a.id) AS present_days
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.status = 'Present'
        GROUP BY s.id, s.name, s.roll_no
        ORDER BY s.name
    """)
    rows = cur.fetchall()
    conn.close()

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 50

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, "Attendance Report")
    y -= 30

    p.setFont("Helvetica", 10)
    p.drawString(50, y, f"Total Working Days: {total_days}")
    y -= 20

    for r in rows:
        percentage = round((r["present_days"] / total_days) * 100, 2) if total_days else 0
        line = f'{r["name"]} | {r["roll_no"]} | Present: {r["present_days"]} | {percentage}%'
        p.drawString(50, y, line[:110])
        y -= 18
        if y < 50:
            p.showPage()
            y = height - 50

    p.showPage()
    p.save()
    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name="report.pdf", mimetype="application/pdf")


@app.route("/send_email_report", methods=["GET", "POST"])
@admin_required
def send_email_report():
    message = ""
    if request.method == "POST":
        recipient = request.form["recipient"].strip()

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM students")
        student_count = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM attendance")
        attendance_count = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM marks")
        marks_count = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM homework")
        homework_count = cur.fetchone()["c"]
        conn.close()

        body = (
            f"QR Attendance System Report\n"
            f"Students: {student_count}\n"
            f"Attendance entries: {attendance_count}\n"
            f"Marks records: {marks_count}\n"
            f"Homework records: {homework_count}\n"
        )

        if not app.config["MAIL_USERNAME"] or not app.config["MAIL_PASSWORD"]:
            message = "Mail not configured on server."
        else:
            msg = Message(
                subject="Attendance System Report",
                recipients=[recipient],
                body=body
            )
            mail.send(msg)
            message = "Email sent successfully."

    return render_template("email_report.html", message=message)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)