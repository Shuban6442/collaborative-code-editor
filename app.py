from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
import uuid
import subprocess
import tempfile
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# sessions = {
#   session_id: {
#       "content": "",
#       "participants": {sid: {"name": str, "role": "host"/"participant"}},
#       "host_id": sid or None,
#       "writer_id": sid or None
#   }
# }
sessions = {}


@app.route("/")
def index():
    return render_template("home.html")



@app.route("/create_session", methods=["POST"])
def create_session():
    session_id = str(uuid.uuid4())[:6]
    sessions[session_id] = {
        "content": "",
        "participants": {},
        "host_id": None,       # will be set in join_session
        "writer_id": None      # will be set in join_session
    }
    return jsonify({"session_id": session_id})


@socketio.on("join_session")
def join_session(data):
    session_id = data.get("session_id")
    name = data.get("name")
    sid = request.sid

    if not session_id or session_id not in sessions:
        emit("error", {"msg": "Session not found"})
        return

    join_room(session_id)

    role = "participant"
    if sessions[session_id]["host_id"] is None:     # first joiner becomes host
        sessions[session_id]["host_id"] = sid
        sessions[session_id]["writer_id"] = sid     # host is first writer
        role = "host"

    sessions[session_id]["participants"][sid] = {"name": name, "role": role}

    emit("code_update", {"content": sessions[session_id]["content"]})
    emit("participants_update", {
        "participants": sessions[session_id]["participants"],
        "writer_id": sessions[session_id]["writer_id"],
        "host_id": sessions[session_id]["host_id"]
    }, room=session_id)



@app.route("/editor/<session_id>")
def editor(session_id):
    if session_id not in sessions:
        return "Session not found", 404
    return render_template("editor.html", session_id=session_id)


@socketio.on("code_change")
def handle_code_change(data):
    session_id = data.get("session_id")
    content = data.get("content", "")
    sid = request.sid

    if session_id not in sessions:
        emit("error", {"msg": "Session not found"})
        return

    if sessions[session_id]["writer_id"] != sid:
        emit("error", {"msg": "You are not the current writer"})
        return

    sessions[session_id]["content"] = content
    emit("code_update", {"content": content}, room=session_id, include_self=False)


@socketio.on("grant_write")
def grant_write(data):
    session_id = data.get("session_id")
    target_sid = data.get("target_sid")
    if session_id in sessions and target_sid in sessions[session_id]["participants"]:
        sessions[session_id]["writer_id"] = target_sid
        socketio.emit("participants_update", {
            "participants": sessions[session_id]["participants"],
            "host_id": sessions[session_id]["host_id"],
            "writer_id": sessions[session_id]["writer_id"]
        }, room=session_id)


@socketio.on("revoke_write")
def revoke_write(data):
    session_id = data.get("session_id")
    if session_id in sessions:
        # always fallback to host
        sessions[session_id]["writer_id"] = sessions[session_id]["host_id"]
        socketio.emit("participants_update", {
            "participants": sessions[session_id]["participants"],
            "host_id": sessions[session_id]["host_id"],
            "writer_id": sessions[session_id]["writer_id"]
        }, room=session_id)


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    for session_id, session in sessions.items():
        if sid in session["participants"]:
            del session["participants"][sid]
            if session["host_id"] == sid:
                session["host_id"] = None
                session["writer_id"] = None
            elif session["writer_id"] == sid:
                session["writer_id"] = session["host_id"]
            emit("participants_update", {
                "participants": session["participants"],
                "writer_id": session["writer_id"],
                "host_id": session["host_id"]
            }, room=session_id)
            break


@app.route("/run_code", methods=["POST"])
def run_code():
    data = request.get_json()
    code = data.get("code", "")

    temp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as tmp:
            temp_file_path = tmp.name
            tmp.write(code.encode("utf-8"))
            tmp.flush()
            result = subprocess.run(
                ["py", tmp.name],
                capture_output=True,
                text=True,
                timeout=5
            )
        output = result.stdout + result.stderr
    except Exception as e:
        output = str(e)
    finally:
        # Clean up temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)

    return jsonify({"output": output})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
