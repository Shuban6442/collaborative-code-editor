import sys
import uuid
import tempfile
import threading
import select
import subprocess
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# --- In-memory session storage ---
sessions = {}
running_procs = {}   # store currently running processes per session


@app.route("/")
def index():
    return render_template("home.html")


@app.route("/create_session", methods=["POST"])
def create_session():
    """Create a new session with unique ID"""
    session_id = str(uuid.uuid4())[:6]
    sessions[session_id] = {
        "content": "",
        "participants": {},
        "host_id": None,
        "writer_id": None,
        "chat": []
    }
    return jsonify({"session_id": session_id})


@app.route("/editor/<session_id>")
def editor(session_id):
    if session_id not in sessions:
        return "Session not found", 404
    return render_template("editor.html", session_id=session_id)


# ------------------ SocketIO events ------------------

@socketio.on("join_session")
def join_session(data):
    session_id = data.get("session_id")
    name = data.get("name", "Anonymous")
    sid = request.sid

    if not session_id or session_id not in sessions:
        emit("error", {"msg": "Session not found"})
        return

    sess = sessions[session_id]
    join_room(session_id)

    role = "participant"
    if sess["host_id"] is None:
        sess["host_id"] = sid
        sess["writer_id"] = sid
        role = "host"

    sess["participants"][sid] = {"name": name, "role": role}

    emit("code_update", {"content": sess["content"]})
    emit("chat_history", {"messages": sess.get("chat", [])})
    emit("participants_update", {
        "participants": sess["participants"],
        "writer_id": sess["writer_id"],
        "host_id": sess["host_id"]
    }, room=session_id)


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


@socketio.on("send_message")
def handle_send_message(data):
    session_id = data.get("session_id")
    text = data.get("message", "").strip()
    name = data.get("name", "Anonymous")
    sid = request.sid

    if not session_id or session_id not in sessions:
        emit("error", {"msg": "Session not found"})
        return
    if not text:
        return

    from time import time
    msg = {"sid": sid, "name": name, "msg": text, "ts": time()}
    sess = sessions[session_id]
    chat_list = sess.setdefault("chat", [])
    chat_list.append(msg)
    if len(chat_list) > 200:
        del chat_list[0:len(chat_list)-200]

    socketio.emit("chat_message", msg, room=session_id)


# ------------------ Code execution ------------------
@app.route("/run_code", methods=["POST"])
def run_code():
    data = request.get_json()
    code = data.get("code", "")
    session_id = data.get("session_id")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as tmp:
            tmp.write(code.encode("utf-8"))
            tmp.flush()

        proc = subprocess.Popen(
            [sys.executable, "-u", tmp.name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        running_procs[session_id] = proc

        # Immediately notify that program started
        socketio.emit("code_output", {"output": "[Program started]\n"}, room=session_id)

        def stream_output():
            try:
                for line in proc.stdout:
                    if line == "":
                        break
                    socketio.emit("code_output", {"output": line}, room=session_id)
            except Exception as e:
                socketio.emit("code_output", {"output": f"[Error streaming output: {e}]\n"}, room=session_id)

        threading.Thread(target=stream_output, daemon=True).start()
        return jsonify({"output": ""})

    except Exception as e:
        return jsonify({"output": str(e)})


@socketio.on("provide_input")
def handle_input(data):
    session_id = data.get("session_id")
    text = data.get("text", "")
    proc = running_procs.get(session_id)
    if proc and proc.poll() is None:
        try:
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
            emit("code_output", {"output": f"[Input received: {text}]\n"}, room=session_id)
        except Exception as e:
            emit("code_output", {"output": f"Error sending input: {e}\n"}, room=session_id)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
