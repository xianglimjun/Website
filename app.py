import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, send_from_directory

app = Flask(__name__)

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
    """Initializes the database table and seeds it from quiz.json if empty."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Create quizzes table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quizzes (
                    id SERIAL PRIMARY KEY,
                    question TEXT NOT NULL,
                    options JSONB NOT NULL,
                    answer TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'questions'
                );
            """)
            # Ensure type column exists for existing tables
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'questions';")
            conn.commit()

            # Check if empty
            cur.execute("SELECT COUNT(*) FROM quizzes;")
            count = cur.fetchone()[0]

            if count == 0:
                print("Database is empty. Seeding from quiz.json...")
                quiz_file_path = os.path.join(os.path.dirname(__file__), "quiz.json")
                
                # Load questions from JSON
                if os.path.exists(quiz_file_path):
                    with open(quiz_file_path, "r", encoding="utf-8") as f:
                        quizzes = json.load(f)
                else:
                    # Fallback default hardcoded questions in case file is missing
                    quizzes = [
                        {
                            "type": "questions",
                            "question": "Which HTTP status code represents a successful resource creation?",
                            "options": ["200 OK", "201 Created", "204 No Content", "400 Bad Request"],
                            "answer": "201 Created"
                        }
                    ]

                for quiz in quizzes:
                    cur.execute(
                        "INSERT INTO quizzes (question, options, answer, type) VALUES (%s, %s, %s, %s);",
                        (quiz["question"], json.dumps(quiz["options"]), quiz["answer"], quiz.get("type", "questions"))
                    )
                conn.commit()
                print(f"Successfully seeded {len(quizzes)} questions.")
            else:
                print(f"Database already contains {count} questions.")
    except Exception as e:
        print(f"Database initialization error: {e}")
    finally:
        if conn:
            conn.close()

@app.route('/.well-known/acme-challenge/<filename>')
def acme_challenge(filename):
    """ACME challenge route for SSL certificate verification."""
    # Try retrieving from environment variables
    env_token = os.environ.get("ACME_CHALLENGE_TOKEN")
    env_value = os.environ.get("ACME_CHALLENGE_VALUE")
    if env_token and filename == env_token:
        return env_value

    # Fallback to local files under the .well-known/acme-challenge directory
    well_known_dir = os.path.join(app.root_path, '.well-known', 'acme-challenge')
    return send_from_directory(well_known_dir, filename)

@app.route("/")
def index():
    """Homepage route. Fetches quiz questions and renders them."""
    conn = None
    quizzes = []
    error_message = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, question, options, answer, type FROM quizzes ORDER BY id LIMIT 5;")
            rows = cur.fetchall()
            for row in rows:
                quizzes.append({
                    "id": row["id"],
                    "question": row["question"],
                    "options": row["options"],
                    "answer": row["answer"],
                    "type": row.get("type", "questions")
                })
    except Exception as e:
        error_message = f"Failed to retrieve quiz questions: {e}"
        print(error_message)
    finally:
        if conn:
            conn.close()

    # Fallback to local quiz.json questions if database is unreachable or empty
    if not quizzes:
        quiz_file_path = os.path.join(os.path.dirname(__file__), "quiz.json")
        if os.path.exists(quiz_file_path):
            try:
                with open(quiz_file_path, "r", encoding="utf-8") as f:
                    local_quizzes = json.load(f)
                    for i, q in enumerate(local_quizzes[:5]):
                        quizzes.append({
                            "id": i + 1,
                            "question": q["question"],
                            "options": q["options"],
                            "answer": q["answer"],
                            "type": q.get("type", "questions")
                        })
            except Exception as e:
                print(f"Error loading local fallback: {e}")

    return render_template("index.html", quizzes=quizzes, error=error_message)

@app.route("/submit", methods=["POST"])
def submit():
    """Submission route. Evaluates user answers in-memory and shows results."""
    conn = None
    score = 0
    total = 0
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, answer, type FROM quizzes;")
            db_answers = {str(row["id"]): {"answer": row["answer"], "type": row.get("type", "questions")} for row in cur.fetchall()}
    except Exception as e:
        print(f"Database lookup error during submit: {e}")
        # Fallback dictionary matching local index fallback ids
        quiz_file_path = os.path.join(os.path.dirname(__file__), "quiz.json")
        db_answers = {}
        if os.path.exists(quiz_file_path):
            with open(quiz_file_path, "r", encoding="utf-8") as f:
                local_quizzes = json.load(f)
                for i, q in enumerate(local_quizzes):
                    db_answers[str(i + 1)] = {"answer": q["answer"], "type": q.get("type", "questions")}

    # Calculate in-memory score instantly
    for field_name, user_answer in request.form.items():
        if field_name.startswith("q_"):
            quiz_id = field_name.split("_")[1]
            correct_info = db_answers.get(quiz_id)
            if correct_info:
                correct_answer = correct_info["answer"]
                q_type = correct_info["type"]
                if q_type == "short-questions":
                    if user_answer.strip() == correct_answer.strip():
                        score += 1
                else:
                    if user_answer == correct_answer:
                        score += 1
                total += 1

    # In case total is 0 (e.g. no questions answered), default total to 5
    if total == 0:
        total = len(db_answers) if db_answers else 5

    return render_template("results.html", score=score, total=total)

if __name__ == "__main__":
    # Remove it from lines 81-82 and place it safely here:
    try:
        init_db()
    except Exception as e:
        print(f"Warning: Database pre-initialization failed: {e}")
        
    app.run(host="0.0.0.0", port=PORT)