import sys
import uuid
import tempfile
import subprocess
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room

import select
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = 'siren-secret-key-123'

# Use threading async_mode for better compatibility
socketio = SocketIO(app, 
                   cors_allowed_origins="*",
                   async_mode='threading')

# Store sessions in memory
sessions = {}

@app.route("/")
def index():
    return render_template("home.html")

@app.route("/create_session", methods=["POST"])
def create_session():
    session_id = str(uuid.uuid4())[:8]
    sessions[session_id] = {
        "content": "# Welcome to SIREN Collaborative Editor\n# Start coding in Python...\nprint('Hello, World!')",
        "participants": {},
        "host_id": None,
        "writer_id": None
    }
    print(f"🎉 New session created: {session_id}")
    return jsonify({"session_id": session_id})

@app.route("/editor/<session_id>")
def editor(session_id):
    if session_id not in sessions:
        return "Session not found", 404
    return render_template("editor.html", session_id=session_id)
# Update your run_code function and add input handling

@app.route("/run_code", methods=["POST"])
def run_code():
    data = request.get_json()
    code = data.get("code", "")
    session_id = data.get("session_id")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py", mode='w', encoding='utf-8') as tmp:
            tmp.write(code)
            tmp.flush()

        # Run the process with pipes for input/output
        proc = subprocess.Popen(
            [sys.executable, "-u", tmp.name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Store the process for input handling
        process_id = str(uuid.uuid4())[:8]
        running_procs[process_id] = proc

        socketio.emit("code_output", {"output": "[Program started]\\n"}, room=session_id)

        def stream_output():
            try:
                while True:
                    # Check if process has terminated
                    if proc.poll() is not None:
                        break
                    
                    # Read output line by line
                    line = proc.stdout.readline()
                    if line:
                        socketio.emit("code_output", {"output": line}, room=session_id)
                    else:
                        # No more output, break
                        break
                        
            except Exception as e:
                socketio.emit("code_output", {"output": f"[Error: {e}]\\n"}, room=session_id)
            finally:
                # Cleanup
                if process_id in running_procs:
                    del running_procs[process_id]
                socketio.emit("code_output", {"output": "[Program finished]\\n"}, room=session_id)

        # Start output streaming in a separate thread
        threading.Thread(target=stream_output, daemon=True).start()
        
        return jsonify({
            "output": "[Program started - waiting for output...]\\n",
            "process_id": process_id
        })

    except Exception as e:
        return jsonify({"output": f"Error: {str(e)}\\n"})

@socketio.on("provide_input")
def handle_input(data):
    session_id = data.get("session_id")
    text = data.get("text", "")
    process_id = data.get("process_id")
    
    proc = running_procs.get(process_id)
    if proc and proc.poll() is None:
        try:
            # Send input to the process
            proc.stdin.write(text + "\\n")
            proc.stdin.flush()
            emit("code_output", {"output": f"[Input sent: {text}]\\n"}, room=session_id)
        except Exception as e:
            emit("code_output", {"output": f"Error sending input: {e}\\n"}, room=session_id)
    else:
        emit("code_output", {"output": "[No running process to send input]\\n"}, room=session_id)
@socketio.on("connect")
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on("disconnect")
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    handle_user_leave()

@socketio.on("join_session")
def handle_join(data):
    session_id = data.get("session_id")
    name = data.get("name", "Anonymous")
    sid = request.sid
    
    if session_id not in sessions:
        emit("error", {"msg": "Session not found"})
        return
    
    join_room(session_id)
    session = sessions[session_id]
    
    # Set as host and writer if first user
    if not session["participants"]:
        session["host_id"] = sid
        session["writer_id"] = sid
        print(f"👑 {name} is now host of session {session_id}")
    
    session["participants"][sid] = {
        "name": name,
        "sid": sid
    }
    
    # Send current code to new user
    emit("code_update", {"content": session["content"]})
    
    # Notify all users about updated participants
    emit_participants_update(session_id)
    
    print(f"👤 {name} joined session {session_id}")

def handle_user_leave():
    """Handle when a user leaves the session"""
    sid = request.sid
    for session_id, session in sessions.items():
        if sid in session["participants"]:
            user_name = session["participants"][sid]["name"]
            
            # Remove user from participants
            del session["participants"][sid]
            
            # Handle host transfer if host left
            if session["host_id"] == sid:
                if session["participants"]:
                    # Transfer host to first available participant
                    new_host_sid = next(iter(session["participants"].keys()))
                    session["host_id"] = new_host_sid
                    session["writer_id"] = new_host_sid
                    new_host_name = session["participants"][new_host_sid]["name"]
                    print(f"👑 Host transferred to {new_host_name} in session {session_id}")
                else:
                    # No participants left, clear host
                    session["host_id"] = None
                    session["writer_id"] = None
            
            # Update all clients
            emit_participants_update(session_id)
            
            print(f"👤 {user_name} left session {session_id}")
            break

def emit_participants_update(session_id):
    """Send updated participants list to all clients in the session"""
    if session_id in sessions:
        session = sessions[session_id]
        emit("participants_update", {
            "participants": session["participants"],
            "writer_id": session["writer_id"],
            "host_id": session["host_id"]
        }, room=session_id)

# Add these WebRTC signaling handlers to your existing app.py

@socketio.on("get_participants")
def handle_get_participants(data):
    """Get all participants in session"""
    session_id = data.get("session_id")
    sid = request.sid
    
    if session_id in sessions:
        session = sessions[session_id]
        # Notify about existing participants
        emit("participants_update", {
            "participants": session["participants"],
            "writer_id": session["writer_id"],
            "host_id": session["host_id"]
        })
@socketio.on("code_change")
def handle_code_change(data):
    session_id = data.get("session_id")
    content = data.get("content", "")
    sid = request.sid
    
    if session_id in sessions:
        session = sessions[session_id]
        # Only allow the current writer to make changes
        if session["writer_id"] == sid:
            session["content"] = content
            emit("code_update", {"content": content}, room=session_id, include_self=False)
            print(f"📝 Code updated by {session['participants'][sid]['name']} in session {session_id}")

@socketio.on("grant_write")
def handle_grant_write(data):
    """Grant write access to another user"""
    session_id = data.get("session_id")
    target_sid = data.get("target_sid")
    sid = request.sid
    
    if session_id in sessions:
        session = sessions[session_id]
        # Only host can grant write access
        if session["host_id"] == sid and target_sid in session["participants"]:
            session["writer_id"] = target_sid
            emit_participants_update(session_id)
            print(f"✏️ Write access granted to {session['participants'][target_sid]['name']}")

@socketio.on("revoke_write")
def handle_revoke_write(data):
    """Revoke write access (host becomes writer)"""
    session_id = data.get("session_id")
    sid = request.sid
    
    if session_id in sessions:
        session = sessions[session_id]
        # Only host can revoke write access
        if session["host_id"] == sid:
            session["writer_id"] = sid
            emit_participants_update(session_id)
            print(f"✏️ Write access revoked by {session['participants'][sid]['name']}")

# WebRTC signaling handlers (for future audio implementation)
@socketio.on("webrtc_offer")
def handle_webrtc_offer(data):
    target_sid = data.get("target")
    offer = data.get("sdp")
    if target_sid:
        emit("webrtc_offer", {
            "sdp": offer,
            "sid": request.sid
        }, to=target_sid)

@socketio.on("webrtc_answer")
def handle_webrtc_answer(data):
    target_sid = data.get("target")
    answer = data.get("sdp")
    if target_sid:
        emit("webrtc_answer", {
            "sdp": answer,
            "sid": request.sid
        }, to=target_sid)

@socketio.on("webrtc_ice_candidate")
def handle_webrtc_ice_candidate(data):
    target_sid = data.get("target")
    candidate = data.get("candidate")
    if target_sid:
        emit("webrtc_ice_candidate", {
            "candidate": candidate,
            "sid": request.sid
        }, to=target_sid)

if __name__ == "__main__":
    print("🚀 Starting SIREN Collaborative Editor...")
    print("📍 Local URL: http://localhost:5000")
    print("💡 Features: Real-time coding, Python execution, User management")
    print("🔧 Running with threading async_mode for better compatibility")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
