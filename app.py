import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# Secure secret key generation on startup, fallback to static if configured in environment
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# Load port from environment variable, defaulting to 8080
PORT = int(os.environ.get("PORT", 8080))

# Database configuration from standard environment variables
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "postgres")

def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

def init_db():
    """Initializes the database by running schema.sql (includes tables and seed data)."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            if os.path.exists(schema_path):
                print("Running schema.sql...")
                with open(schema_path, "r", encoding="utf-8") as f:
                    cur.execute(f.read())
                conn.commit()
                print("Schema applied successfully.")
            else:
                print("Warning: schema.sql not found. Database tables may not exist.")
    except Exception as e:
        print(f"Database initialization error: {e}")
    finally:
        if conn:
            conn.close()

@app.route('/.well-known/acme-challenge/<filename>')
def acme_challenge(filename):
    """ACME challenge route for SSL certificate verification."""
    env_token = os.environ.get("ACME_CHALLENGE_TOKEN")
    env_value = os.environ.get("ACME_CHALLENGE_VALUE")
    if env_token and filename == env_token:
        return env_value
    well_known_dir = os.path.join(app.root_path, '.well-known', 'acme-challenge')
    return send_from_directory(well_known_dir, filename)


# ─── Public Routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Homepage: list all quiz sets."""
    conn = None
    quiz_sets = []
    error_message = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT qs.id, qs.title, qs.description,
                       COUNT(q.id) AS question_count
                FROM quiz_sets qs
                LEFT JOIN questions q ON q.quiz_set_id = qs.id
                GROUP BY qs.id
                ORDER BY qs.id;
            """)
            quiz_sets = list(cur.fetchall())
    except Exception as e:
        error_message = f"Failed to retrieve quiz sets: {e}"
        print(error_message)
    finally:
        if conn:
            conn.close()

    return render_template("index.html", quiz_sets=quiz_sets, error=error_message)


@app.route("/quiz/<int:quiz_set_id>")
def take_quiz(quiz_set_id):
    """Quiz-taking page for a specific quiz set."""
    conn = None
    quiz_set = None
    questions_list = []
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, title, description FROM quiz_sets WHERE id = %s;", (quiz_set_id,))
            quiz_set = cur.fetchone()
            if not quiz_set:
                return redirect(url_for("index"))
            cur.execute(
                "SELECT id, question, options, type FROM questions WHERE quiz_set_id = %s ORDER BY id;",
                (quiz_set_id,)
            )
            questions_list = list(cur.fetchall())
    except Exception as e:
        print(f"Error loading quiz {quiz_set_id}: {e}")
    finally:
        if conn:
            conn.close()

    return render_template("quiz.html", quiz_set=quiz_set, questions=questions_list)


@app.route("/quiz/<int:quiz_set_id>/submit", methods=["POST"])
def submit_quiz(quiz_set_id):
    """Grade submitted answers for a quiz set."""
    conn = None
    score = 0
    total = 0
    quiz_set_title = "Quiz"
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT title FROM quiz_sets WHERE id = %s;", (quiz_set_id,))
            row = cur.fetchone()
            if row:
                quiz_set_title = row["title"]
            cur.execute(
                "SELECT id, answer, type FROM questions WHERE quiz_set_id = %s;",
                (quiz_set_id,)
            )
            db_answers = {
                str(r["id"]): {"answer": r["answer"], "type": r.get("type", "questions")}
                for r in cur.fetchall()
            }
    except Exception as e:
        print(f"Database error during submit: {e}")
        db_answers = {}
    finally:
        if conn:
            conn.close()

    for field_name, user_answer in request.form.items():
        if field_name.startswith("q_"):
            q_id = field_name.split("_")[1]
            info = db_answers.get(q_id)
            if info:
                correct = info["answer"]
                if info["type"] == "short-questions":
                    if user_answer.strip() == correct.strip():
                        score += 1
                else:
                    if user_answer == correct:
                        score += 1
                total += 1

    if total == 0:
        total = len(db_answers) if db_answers else 1

    return render_template("results.html", score=score, total=total, quiz_set_title=quiz_set_title, quiz_set_id=quiz_set_id)


# ─── Admin Routes ─────────────────────────────────────────────────────────────

def require_admin():
    """Helper: redirect to login if not authenticated."""
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    return None


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        conn = None
        user = None
        try:
            conn = get_db_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE username = %s;", (username,))
                user = cur.fetchone()
        except Exception as e:
            print(f"Error checking user in DB: {e}")
        finally:
            if conn:
                conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["admin_logged_in"] = True
            session["username"] = user["username"]
            return redirect(url_for("admin_dashboard"))
        else:
            error = "Invalid username or password."

    return render_template("admin_login.html", error=error)


@app.route("/admin")
def admin_dashboard():
    guard = require_admin()
    if guard:
        return guard

    conn = None
    quiz_sets = []
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT qs.id, qs.title, qs.description,
                       COUNT(q.id) AS question_count
                FROM quiz_sets qs
                LEFT JOIN questions q ON q.quiz_set_id = qs.id
                GROUP BY qs.id
                ORDER BY qs.id;
            """)
            quiz_sets = list(cur.fetchall())
    except Exception as e:
        print(f"Error fetching quiz sets: {e}")
    finally:
        if conn:
            conn.close()

    return render_template("admin_dashboard.html", quiz_sets=quiz_sets)


@app.route("/admin/quiz/new", methods=["GET", "POST"])
def admin_new_quiz():
    guard = require_admin()
    if guard:
        return guard

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if title:
            conn = None
            try:
                conn = get_db_connection()
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO quiz_sets (title, description) VALUES (%s, %s) RETURNING id;",
                        (title, description)
                    )
                    new_id = cur.fetchone()[0]
                    conn.commit()
                return redirect(url_for("admin_edit_quiz", quiz_set_id=new_id))
            except Exception as e:
                print(f"Error creating quiz set: {e}")
            finally:
                if conn:
                    conn.close()

    return render_template("admin_quiz_edit.html", quiz_set=None, questions=[])


@app.route("/admin/quiz/<int:quiz_set_id>/edit", methods=["GET", "POST"])
def admin_edit_quiz(quiz_set_id):
    guard = require_admin()
    if guard:
        return guard

    conn = None
    quiz_set = None
    questions_list = []

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE quiz_sets SET title = %s, description = %s WHERE id = %s;",
                    (title, description, quiz_set_id)
                )
                conn.commit()
        except Exception as e:
            print(f"Error updating quiz set: {e}")
        finally:
            if conn:
                conn.close()
            conn = None

    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, title, description FROM quiz_sets WHERE id = %s;", (quiz_set_id,))
            quiz_set = cur.fetchone()
            cur.execute(
                "SELECT id, question, options, answer, type FROM questions WHERE quiz_set_id = %s ORDER BY id;",
                (quiz_set_id,)
            )
            questions_list = list(cur.fetchall())
    except Exception as e:
        print(f"Error loading quiz set for edit: {e}")
    finally:
        if conn:
            conn.close()

    if not quiz_set:
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_quiz_edit.html", quiz_set=quiz_set, questions=questions_list)


@app.route("/admin/quiz/<int:quiz_set_id>/delete", methods=["POST"])
def admin_delete_quiz(quiz_set_id):
    guard = require_admin()
    if guard:
        return guard

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM quiz_sets WHERE id = %s;", (quiz_set_id,))
            conn.commit()
    except Exception as e:
        print(f"Error deleting quiz set: {e}")
    finally:
        if conn:
            conn.close()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/quiz/<int:quiz_set_id>/question/add", methods=["POST"])
def admin_add_question(quiz_set_id):
    guard = require_admin()
    if guard:
        return guard

    question = request.form.get("question", "").strip()
    q_type = request.form.get("type", "questions")
    answer = request.form.get("answer", "").strip()

    if q_type == "questions":
        options_raw = request.form.getlist("options[]")
        options = [o.strip() for o in options_raw if o.strip()]
    else:
        options = []

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO questions (quiz_set_id, question, options, answer, type) VALUES (%s, %s, %s, %s, %s);",
                (quiz_set_id, question, json.dumps(options), answer, q_type)
            )
            conn.commit()
    except Exception as e:
        print(f"Error adding question: {e}")
    finally:
        if conn:
            conn.close()

    return redirect(url_for("admin_edit_quiz", quiz_set_id=quiz_set_id))


@app.route("/admin/question/<int:question_id>/delete", methods=["POST"])
def admin_delete_question(question_id):
    guard = require_admin()
    if guard:
        return guard

    quiz_set_id = request.form.get("quiz_set_id")
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM questions WHERE id = %s;", (question_id,))
            conn.commit()
    except Exception as e:
        print(f"Error deleting question: {e}")
    finally:
        if conn:
            conn.close()

    return redirect(url_for("admin_edit_quiz", quiz_set_id=quiz_set_id))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


if __name__ == "__main__":
    try:
        init_db()
    except Exception as e:
        print(f"Warning: Database pre-initialization failed: {e}")

    app.run(host="0.0.0.0", port=PORT)