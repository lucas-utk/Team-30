from dotenv import load_dotenv
import os
import json
import time
import base64
import sqlite3
import hashlib
import secrets
import html
import urllib.parse
import urllib.request
import urllib.error

from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import SimpleCookie


# Configuration
DB = "users.db"
HOST = "127.0.0.1"
PORT = 8080
load_dotenv()
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

spotify_token = None
spotify_token_expiry = 0


# Database functions
def db():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    return connection


def setup():
    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    connection.commit()
    connection.close()


# Password functions
def hash_password(password):
    salt = secrets.token_bytes(16)

    hashed = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1
    )

    return salt.hex() + ":" + hashed.hex()


def check_password(password, stored_password):
    try:
        salt_hex, hash_hex = stored_password.split(":")
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)

        actual_hash = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=16384,
            r=8,
            p=1
        )

        return secrets.compare_digest(actual_hash, expected_hash)

    except (ValueError, TypeError):
        return False


# Session functions
def new_session(user_id):
    token = secrets.token_urlsafe(32)

    connection = db()
    connection.execute(
        "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
        (token, user_id)
    )
    connection.commit()
    connection.close()

    return token


def get_user(token):
    if not token:
        return None

    connection = db()

    user = connection.execute("""
        SELECT users.id, users.username
        FROM users
        JOIN sessions ON users.id = sessions.user_id
        WHERE sessions.token = ?
    """, (token,)).fetchone()

    connection.close()

    if user is None:
        return None

    # Return (user ID, username)
    return (user["id"], user["username"])


# Spotify API functions
def get_spotify_token():
    global spotify_token, spotify_token_expiry

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError(
            "Spotify client ID or client secret is missing."
        )

    # Reuse the current token while it is valid.
    if spotify_token and time.time() < spotify_token_expiry:
        return spotify_token

    credentials = (
        SPOTIFY_CLIENT_ID + ":" + SPOTIFY_CLIENT_SECRET
    ).encode("utf-8")

    encoded_credentials = base64.b64encode(credentials).decode("utf-8")

    request = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=urllib.parse.urlencode({
            "grant_type": "client_credentials"
        }).encode("utf-8"),
        headers={
            "Authorization": "Basic " + encoded_credentials,
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    spotify_token = data["access_token"]
    spotify_token_expiry = time.time() + data["expires_in"] - 60

    return spotify_token


def search_spotify_albums(query="popular", limit=10):
    token = get_spotify_token()

    params = urllib.parse.urlencode({
        "q": query,
        "type": "album",
        "limit": min(max(limit, 1), 10),
        "market": "US"
    })

    request = urllib.request.Request(
        "https://api.spotify.com/v1/search?" + params,
        headers={
            "Authorization": "Bearer " + token
        }
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))

    albums = []

    for album in data.get("albums", {}).get("items", []):
        artists = [
            artist["name"]
            for artist in album.get("artists", [])
        ]

        images = album.get("images", [])

        albums.append({
            "id": album.get("id", ""),
            "title": album.get("name", ""),
            "artist": ", ".join(artists),
            "year": album.get("release_date", "")[:4],
            "cover": images[0]["url"] if images else "",
            "spotify_url": album.get(
                "external_urls", {}
            ).get("spotify", "")
        })

    return albums


# HTTP server
class Server(BaseHTTPRequestHandler):

    def cookies(self):
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie()

        try:
            cookie.load(cookie_header)

            if "session" in cookie:
                return cookie["session"].value

        except Exception:
            pass

        return None

    def form(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        return urllib.parse.parse_qs(body)

    def send_page(self, filename, username=None):
        try:
            with open(filename, "r", encoding="utf-8") as file:
                page = file.read()

            if username is not None:
                page = page.replace(
                    "{{username}}",
                    html.escape(username)
                )

            content = page.encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type", "text/html; charset=utf-8"
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        except FileNotFoundError:
            self.send_error(404, "Page not found")

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, data, status=200):
        content = json.dumps(data).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type", "application/json; charset=utf-8"
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        user = get_user(self.cookies())

        if path == "/":
            if user:
                self.redirect("/home")
            else:
                self.send_page("index.html")

        elif path == "/home":
            if user:
                self.send_page("home.html", user[1])
            else:
                self.redirect("/")

        elif path == "/api/albums":
            if not user:
                self.send_json(
                    {"error": "Please log in first."},
                    401
                )
                return

            params = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query
            )

            query = params.get("q", ["popular"])[0].strip()

            if not query:
                query = "popular"

            try:
                albums = search_spotify_albums(query, 10)
                self.send_json({"albums": albums})

            except Exception as error:
                print("Spotify API error:", error)
                self.send_json({
                    "error": (
                        "Could not retrieve albums from Spotify. "
                        "Check your API credentials and try again."
                    )
                }, 502)

        else:
            self.send_error(404, "Page not found")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        form = self.form()

        if path == "/register":
            username = form.get("username", [""])[0].strip()
            password = form.get("password", [""])[0]

            if not username or not password:
                self.send_error(
                    400, "Username and password are required"
                )
                return

            connection = db()

            try:
                cursor = connection.execute(
                    """
                    INSERT INTO users (username, password)
                    VALUES (?, ?)
                    """,
                    (username, hash_password(password))
                )
                connection.commit()
                user_id = cursor.lastrowid

            except sqlite3.IntegrityError:
                connection.close()
                self.send_error(409, "Username already exists")
                return

            connection.close()

            token = new_session(user_id)

            self.send_response(200)
            self.send_header(
                "Set-Cookie",
                "session=" + token +
                "; HttpOnly; SameSite=Strict; Path=/"
            )
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Registration successful")

        elif path == "/login":
            username = form.get("username", [""])[0].strip()
            password = form.get("password", [""])[0]

            connection = db()

            user = connection.execute(
                "SELECT id, password FROM users WHERE username = ?",
                (username,)
            ).fetchone()

            connection.close()

            if user is None or not check_password(
                password, user["password"]
            ):
                self.send_error(401, "Invalid username or password")
                return

            token = new_session(user["id"])

            self.send_response(200)
            self.send_header(
                "Set-Cookie",
                "session=" + token +
                "; HttpOnly; SameSite=Strict; Path=/"
            )
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Login successful")

        elif path == "/logout":
            token = self.cookies()

            if token:
                connection = db()
                connection.execute(
                    "DELETE FROM sessions WHERE token = ?",
                    (token,)
                )
                connection.commit()
                connection.close()

            self.send_response(200)
            self.send_header(
                "Set-Cookie",
                "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
            )
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Logged out")

        else:
            self.send_error(404, "Page not found")


# Start the server
if __name__ == "__main__":
    setup()
    server = HTTPServer((HOST, PORT), Server)

    print("Server running at http://127.0.0.1:8080")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
