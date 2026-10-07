from pathlib import Path
from functools import wraps
import io
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from flask import (
    Flask,
    request,
    render_template,
    send_file,
    redirect,
    url_for,
    jsonify,
    session,
    flash,
    abort
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.exceptions import RequestEntityTooLarge

from dotenv import dotenv_values

from azure.storage.blob import (
    BlobServiceClient,
    generate_blob_sas,
    BlobSasPermissions,
    StandardBlobTier,
    RehydratePriority
)


# =========================================================
# PATHS / CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

ENV_FILE = BASE_DIR / ".env"

DATABASE = BASE_DIR / "cloud_workspace.db"


# =========================================================
# ENVIRONMENT
# =========================================================

config = dotenv_values(ENV_FILE)

AZURE_CONNECTION_STRING = config.get(
    "AZURE_STORAGE_CONNECTION_STRING"
)

AZURE_CONTAINER_NAME = config.get(
    "AZURE_CONTAINER_NAME",
    "files"
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = (
    102 * 1024 * 1024
)

app.secret_key = config.get(
    "FLASK_SECRET_KEY",
    "cloud-workspace-development-secret-change-this"
)


# =========================================================
# FILE LIMIT
# =========================================================

MAX_FILE_SIZE_MB = 100

MAX_FILE_SIZE = (
    MAX_FILE_SIZE_MB *
    1024 *
    1024
)


# =========================================================
# AZURE
# =========================================================

if not AZURE_CONNECTION_STRING:
    raise RuntimeError(
        "AZURE_STORAGE_CONNECTION_STRING is missing from .env"
    )


blob_service_client = BlobServiceClient.from_connection_string(
    AZURE_CONNECTION_STRING
)

container_client = (
    blob_service_client
    .get_container_client(AZURE_CONTAINER_NAME)
)


# =========================================================
# DATABASE
# =========================================================

def get_db():

    db = sqlite3.connect(
        DATABASE
    )

    db.row_factory = sqlite3.Row

    return db


def init_db():

    db = get_db()

    # -----------------------------------------------------
    # USERS TABLE
    # -----------------------------------------------------

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            username TEXT NOT NULL UNIQUE,

            email TEXT NOT NULL UNIQUE,

            password_hash TEXT NOT NULL,

            is_admin INTEGER NOT NULL DEFAULT 0,

            created_at TEXT NOT NULL

        )
    """)

    # -----------------------------------------------------
    # FILES TABLE
    # -----------------------------------------------------

    db.execute("""
        CREATE TABLE IF NOT EXISTS files (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            blob_name TEXT NOT NULL UNIQUE,

            original_name TEXT NOT NULL,

            owner_id INTEGER NOT NULL,

            visibility TEXT NOT NULL DEFAULT 'private',

            size_bytes INTEGER NOT NULL DEFAULT 0,

            created_at TEXT NOT NULL,

            FOREIGN KEY(owner_id)
                REFERENCES users(id)

        )
    """)

    # -----------------------------------------------------
    # ADD ACCESS TIER TO OLD DATABASES
    # -----------------------------------------------------

    columns = [
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(files)"
        ).fetchall()
    ]

    if "access_tier" not in columns:

        db.execute("""
            ALTER TABLE files
            ADD COLUMN access_tier TEXT
            NOT NULL DEFAULT 'Hot'
        """)

    db.commit()

    db.close()


# =========================================================
# DATABASE HELPERS
# =========================================================

def get_current_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    db = get_db()

    user = db.execute(
        """
        SELECT *
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    ).fetchone()

    db.close()

    return user


# =========================================================
# AUTH DECORATORS
# =========================================================

def login_required(function):

    @wraps(function)
    def decorated(*args, **kwargs):

        if not session.get("user_id"):

            if request.path.startswith(
                "/upload"
            ):

                return jsonify({
                    "success": False,
                    "message": "Please login first."
                }), 401

            return redirect(
                url_for("login")
            )

        return function(*args, **kwargs)

    return decorated


def admin_required(function):

    @wraps(function)
    def decorated(*args, **kwargs):

        user = get_current_user()

        if not user:

            return redirect(
                url_for("login")
            )

        if not user["is_admin"]:

            abort(403)

        return function(*args, **kwargs)

    return decorated


# =========================================================
# FILE HELPERS
# =========================================================

VALID_TIERS = {
    "Hot",
    "Cool",
    "Cold",
    "Archive"
}

VALID_VISIBILITY = {
    "private",
    "public"
}


def get_file_record(file_id):

    db = get_db()

    file = db.execute(
        """
        SELECT
            files.*,
            users.username AS owner_name
        FROM files
        JOIN users
            ON users.id = files.owner_id
        WHERE files.id = ?
        """,
        (file_id,)
    ).fetchone()

    db.close()

    return file


def can_manage_file(file, user):

    if not user:
        return False

    if user["is_admin"]:
        return True

    return file["owner_id"] == user["id"]


def can_view_file(file, user):

    if not user:
        return False

    if user["is_admin"]:
        return True

    if file["owner_id"] == user["id"]:
        return True

    return file["visibility"] == "public"


def format_size(size):

    if size < 1024:
        return f"{size} B"

    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"

    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"

    return f"{size / 1024 / 1024 / 1024:.1f} GB"


# =========================================================
# SYNC EXISTING AZURE BLOBS
# =========================================================

def sync_existing_blobs():

    user_db = get_db()

    admin = user_db.execute(
        """
        SELECT *
        FROM users
        WHERE is_admin = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()

    if not admin:

        user_db.close()

        return

    blobs = container_client.list_blobs()

    for blob in blobs:

        exists = user_db.execute(
            """
            SELECT id
            FROM files
            WHERE blob_name = ?
            """,
            (blob.name,)
        ).fetchone()

        if exists:
            continue

        user_db.execute(
            """
            INSERT INTO files
            (
                blob_name,
                original_name,
                owner_id,
                visibility,
                size_bytes,
                created_at,
                access_tier
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blob.name,
                Path(blob.name).name,
                admin["id"],
                "private",
                blob.size or 0,
                datetime.now(
                    timezone.utc
                ).isoformat(),
                getattr(
                    blob,
                    "blob_tier",
                    None
                ) or "Hot"
            )
        )

    user_db.commit()

    user_db.close()


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(
    RequestEntityTooLarge
)
def file_too_large(error):

    if request.path.startswith(
        "/upload"
    ):

        return jsonify({
            "success": False,
            "message":
                "File is too large. Maximum size is 100 MB."
        }), 413

    return (
        "File too large. Maximum size is 100 MB.",
        413
    )


@app.errorhandler(403)
def forbidden(error):

    return render_template(
        "error.html",
        code=403,
        message="You do not have permission to access this page."
    ), 403


@app.errorhandler(404)
def not_found(error):

    return render_template(
        "error.html",
        code=404,
        message="The requested page was not found."
    ), 404


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    user = get_current_user()

    files = []

    if user:

        sync_existing_blobs()

        db = get_db()

        rows = db.execute(
            """
            SELECT
                files.*,
                users.username AS owner_name
            FROM files
            JOIN users
                ON users.id = files.owner_id
            WHERE
                files.owner_id = ?
                OR files.visibility = 'public'
            ORDER BY files.created_at DESC
            """,
            (user["id"],)
        ).fetchall()

        db.close()

        for file in rows:

            item = dict(file)

            item["size_display"] = format_size(
                item["size_bytes"]
            )

            item["is_owner"] = (
                item["owner_id"] == user["id"]
            )

            item["can_manage"] = can_manage_file(
                file,
                user
            )

            files.append(item)

    return render_template(
        "index.html",
        user=user,
        files=files,
        max_file_size_mb=MAX_FILE_SIZE_MB
    )


# =========================================================
# SIGNUP
# =========================================================

@app.route(
    "/signup",
    methods=["GET", "POST"]
)
def signup():

    if session.get("user_id"):

        return redirect(
            url_for("home")
        )

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        if not username or not email or not password:

            flash(
                "All fields are required.",
                "error"
            )

            return render_template(
                "signup.html"
            )

        if password != confirm_password:

            flash(
                "Passwords do not match.",
                "error"
            )

            return render_template(
                "signup.html"
            )

        if len(password) < 6:

            flash(
                "Password must be at least 6 characters.",
                "error"
            )

            return render_template(
                "signup.html"
            )

        db = get_db()

        existing = db.execute(
            """
            SELECT id
            FROM users
            WHERE email = ?
               OR username = ?
            """,
            (
                email,
                username
            )
        ).fetchone()

        if existing:

            db.close()

            flash(
                "Username or email already exists.",
                "error"
            )

            return render_template(
                "signup.html"
            )

        existing_users = db.execute(
            "SELECT COUNT(*) AS count FROM users"
        ).fetchone()["count"]

        # First registered account becomes admin
        is_admin = (
            1
            if existing_users == 0
            else 0
        )

        db.execute(
            """
            INSERT INTO users
            (
                username,
                email,
                password_hash,
                is_admin,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                username,
                email,
                generate_password_hash(password),
                is_admin,
                datetime.now(
                    timezone.utc
                ).isoformat()
            )
        )

        db.commit()

        user_id = db.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        db.close()

        session["user_id"] = user_id

        return redirect(
            url_for("home")
        )

    return render_template(
        "signup.html"
    )


# =========================================================
# LOGIN
# =========================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if session.get("user_id"):

        return redirect(
            url_for("home")
        )

    if request.method == "POST":

        # login.html sends "identifier"
        # It can contain either username or email.
        identifier = request.form.get(
            "identifier",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        db = get_db()

        user = db.execute(
            """
            SELECT *
            FROM users
            WHERE LOWER(email) = ?
               OR LOWER(username) = ?
            """,
            (
                identifier,
                identifier
            )
        ).fetchone()

        db.close()

        if (
            not user
            or not check_password_hash(
                user["password_hash"],
                password
            )
        ):

            flash(
                "Invalid username/email or password.",
                "error"
            )

            return render_template(
                "login.html"
            )

        session["user_id"] = user["id"]

        return redirect(
            url_for("home")
        )

    return render_template(
        "login.html"
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# =========================================================
# UPLOAD
# =========================================================

@app.route(
    "/upload",
    methods=["POST"]
)
@login_required
def upload_file():

    user = get_current_user()

    if "file" not in request.files:

        return jsonify({
            "success": False,
            "message": "No file selected."
        }), 400

    uploaded_file = request.files["file"]

    if not uploaded_file.filename:

        return jsonify({
            "success": False,
            "message": "No file selected."
        }), 400

    visibility = request.form.get(
        "visibility",
        "private"
    ).lower()

    if visibility not in VALID_VISIBILITY:

        visibility = "private"

    file_data = uploaded_file.read()

    if len(file_data) > MAX_FILE_SIZE:

        return jsonify({
            "success": False,
            "message":
                "File is too large. Maximum size is 100 MB."
        }), 413

    original_name = Path(
        uploaded_file.filename
    ).name

    blob_name = (
        f"{user['id']}/"
        f"{uuid.uuid4().hex}-"
        f"{original_name}"
    )

    blob_client = container_client.get_blob_client(
        blob_name
    )

    try:

        # New uploads always start in Hot tier.
        blob_client.upload_blob(
            io.BytesIO(file_data),
            overwrite=False,
            standard_blob_tier=StandardBlobTier.HOT
        )

        db = get_db()

        db.execute(
            """
            INSERT INTO files
            (
                blob_name,
                original_name,
                owner_id,
                visibility,
                size_bytes,
                created_at,
                access_tier
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blob_name,
                original_name,
                user["id"],
                visibility,
                len(file_data),
                datetime.now(
                    timezone.utc
                ).isoformat(),
                "Hot"
            )
        )

        db.commit()

        db.close()

        return jsonify({
            "success": True,
            "message": "File uploaded successfully."
        })

    except Exception as error:

        print(
            "UPLOAD ERROR:",
            repr(error)
        )

        try:

            blob_client.delete_blob()

        except Exception:
            pass

        return jsonify({
            "success": False,
            "message":
                "Upload failed. Check the Flask terminal."
        }), 500


# =========================================================
# DOWNLOAD
# =========================================================

@app.route(
    "/download/<int:file_id>"
)
@login_required
def download_file(file_id):

    user = get_current_user()

    file = get_file_record(file_id)

    if not file:

        abort(404)

    if not can_view_file(
        file,
        user
    ):

        abort(403)

    # Archive files are offline.
    if file["access_tier"] == "Archive":

        return (
            "This file is in Azure Archive storage "
            "and must be rehydrated before it can be downloaded.",
            409
        )

    try:

        blob_client = container_client.get_blob_client(
            file["blob_name"]
        )

        stream = io.BytesIO()

        download = blob_client.download_blob()

        download.readinto(stream)

        stream.seek(0)

        return send_file(
            stream,
            as_attachment=True,
            download_name=file["original_name"]
        )

    except Exception as error:

        print(
            "DOWNLOAD ERROR:",
            repr(error)
        )

        return (
            "Unable to download the file.",
            500
        )


# =========================================================
# DELETE
# =========================================================

@app.route(
    "/delete/<int:file_id>",
    methods=["POST"]
)
@login_required
def delete_file(file_id):

    user = get_current_user()

    file = get_file_record(file_id)

    if not file:

        return jsonify({
            "success": False,
            "message": "File not found."
        }), 404

    if not can_manage_file(
        file,
        user
    ):

        return jsonify({
            "success": False,
            "message":
                "You can only delete your own files."
        }), 403

    try:

        blob_client = container_client.get_blob_client(
            file["blob_name"]
        )

        blob_client.delete_blob()

        db = get_db()

        db.execute(
            """
            DELETE FROM files
            WHERE id = ?
            """,
            (file_id,)
        )

        db.commit()

        db.close()

        return jsonify({
            "success": True,
            "message": "File deleted successfully."
        })

    except Exception as error:

        print(
            "DELETE ERROR:",
            repr(error)
        )

        return jsonify({
            "success": False,
            "message": "Unable to delete file."
        }), 500


# =========================================================
# SHARE
# =========================================================

@app.route(
    "/share/<int:file_id>"
)
@login_required
def share_file(file_id):

    user = get_current_user()

    file = get_file_record(file_id)

    if not file:

        return jsonify({
            "success": False,
            "message": "File not found."
        }), 404

    if not can_manage_file(
        file,
        user
    ):

        return jsonify({
            "success": False,
            "message":
                "You can only share your own files."
        }), 403

    # Archive files must be rehydrated first.
    if file["access_tier"] == "Archive":

        return jsonify({
            "success": False,
            "message":
                "Archived files must be rehydrated before they can be shared."
        }), 409

    try:

        blob_client = container_client.get_blob_client(
            file["blob_name"]
        )

        account_name = (
            blob_service_client.account_name
        )

        credential = (
            blob_service_client.credential
        )

        # Connection-string clients use an account key.
        account_key = getattr(
            credential,
            "account_key",
            None
        )

        if not account_key:

            return jsonify({
                "success": False,
                "message":
                    "Unable to generate SAS link."
            }), 500

        expiry = (
            datetime.now(timezone.utc)
            + timedelta(minutes=10)
        )

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=AZURE_CONTAINER_NAME,
            blob_name=file["blob_name"],
            account_key=account_key,
            permission=BlobSasPermissions(
                read=True
            ),
            expiry=expiry
        )

        url = (
            f"{blob_client.url}"
            f"?{sas_token}"
        )

        return jsonify({
            "success": True,
            "url": url,
            "expires_in": "10 minutes"
        })

    except Exception as error:

        print(
            "SHARE ERROR:",
            repr(error)
        )

        return jsonify({
            "success": False,
            "message": "Unable to create share link."
        }), 500


# =========================================================
# ADMIN PANEL
# =========================================================

@app.route("/admin")
@admin_required
def admin_panel():

    db = get_db()

    users = db.execute(
        """
        SELECT *
        FROM users
        ORDER BY created_at DESC
        """
    ).fetchall()

    files = db.execute(
        """
        SELECT
            files.*,
            users.username AS owner_name
        FROM files
        JOIN users
            ON users.id = files.owner_id
        ORDER BY files.created_at DESC
        """
    ).fetchall()

    user_count = db.execute(
        "SELECT COUNT(*) AS count FROM users"
    ).fetchone()["count"]

    file_count = db.execute(
        "SELECT COUNT(*) AS count FROM files"
    ).fetchone()["count"]

    total_bytes = db.execute(
        """
        SELECT COALESCE(
            SUM(size_bytes),
            0
        ) AS total
        FROM files
        """
    ).fetchone()["total"]

    db.close()

    return render_template(
        "admin.html",
        users=users,
        files=files,
        user_count=user_count,
        file_count=file_count,
        total_storage=format_size(
            total_bytes
        )
    )


# =========================================================
# ADMIN UPDATE FILE
# =========================================================

@app.route(
    "/admin/update-file/<int:file_id>",
    methods=["POST"]
)
@admin_required
def admin_update_file(file_id):

    file = get_file_record(file_id)

    if not file:

        return jsonify({
            "success": False,
            "message": "File not found."
        }), 404

    data = request.get_json(
        silent=True
    ) or {}

    new_visibility = data.get(
        "visibility"
    )

    new_tier = data.get(
        "tier"
    )

    if new_visibility not in VALID_VISIBILITY:

        return jsonify({
            "success": False,
            "message": "Invalid visibility."
        }), 400

    if new_tier not in VALID_TIERS:

        return jsonify({
            "success": False,
            "message": "Invalid access tier."
        }), 400

    try:

        blob_client = container_client.get_blob_client(
            file["blob_name"]
        )

        current_tier = (
            file["access_tier"]
            or "Hot"
        )

        # -------------------------------------------------
        # CHANGE AZURE ACCESS TIER
        # -------------------------------------------------

        if new_tier != current_tier:

            # Archive -> another tier
            # requires rehydration.
            if (
                current_tier == "Archive"
                and new_tier != "Archive"
            ):

                blob_client.set_standard_blob_tier(
                    StandardBlobTier(new_tier),
                    rehydrate_priority=RehydratePriority.STANDARD
                )

            else:

                blob_client.set_standard_blob_tier(
                    StandardBlobTier(new_tier)
                )

        # -------------------------------------------------
        # UPDATE APPLICATION DATABASE
        # -------------------------------------------------

        db = get_db()

        db.execute(
            """
            UPDATE files
            SET
                visibility = ?,
                access_tier = ?
            WHERE id = ?
            """,
            (
                new_visibility,
                new_tier,
                file_id
            )
        )

        db.commit()

        db.close()

        # -------------------------------------------------
        # REHYDRATION MESSAGE
        # -------------------------------------------------

        if (
            current_tier == "Archive"
            and new_tier != "Archive"
        ):

            message = (
                f"File is being rehydrated to {new_tier}. "
                "It may take time before it becomes downloadable."
            )

        else:

            message = (
                "File settings updated successfully."
            )

        return jsonify({
            "success": True,
            "message": message,
            "visibility": new_visibility,
            "tier": new_tier
        })

    except Exception as error:

        print(
            "ADMIN UPDATE ERROR:",
            repr(error)
        )

        return jsonify({
            "success": False,
            "message":
                "Azure could not update this file. "
                "Check the Flask terminal."
        }), 500


# =========================================================
# ADMIN DELETE FILE
# =========================================================

@app.route(
    "/admin/delete-file/<int:file_id>",
    methods=["POST"]
)
@admin_required
def admin_delete_file(file_id):

    file = get_file_record(file_id)

    if not file:

        return jsonify({
            "success": False,
            "message": "File not found."
        }), 404

    try:

        blob_client = container_client.get_blob_client(
            file["blob_name"]
        )

        blob_client.delete_blob()

        db = get_db()

        db.execute(
            """
            DELETE FROM files
            WHERE id = ?
            """,
            (file_id,)
        )

        db.commit()

        db.close()

        return jsonify({
            "success": True,
            "message": "File deleted successfully."
        })

    except Exception as error:

        print(
            "ADMIN DELETE ERROR:",
            repr(error)
        )

        return jsonify({
            "success": False,
            "message":
                "Unable to delete file."
        }), 500


# =========================================================
# START
# =========================================================

init_db()


if __name__ == "__main__":

    app.run(
        debug=True
    )