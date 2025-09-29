import sys
import uuid
import tempfile
import threading
import subprocess
import os
from time import time
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'siren-secret-key-123')
socketio = SocketIO(app, 
                   cors_allowed_origins="*",
                   async_mode='threading')

# Store sessions in memory
sessions = {}
running_procs = {}   # store currently running processes by process_id

@app.route("/")
def index():
    return render_template("home.html")

@app.route("/create_session", methods=["POST"])
def create_session():
    session_id = str(uuid.uuid4())[:8]
    sessions[session_id] = {
        "content": "# Welcome to SIREN Collaborative Editor\n# Start coding in Python...\n\n# Example: Code with input and error handling\n'''\ntry:\n    num = int(input(\"Enter a number: \"))\n    print(\"100 divided by\", num, \"=\", 100 / num)\nexcept ZeroDivisionError:\n    print(\"Error: Cannot divide by zero\")\nexcept ValueError:\n    print(\"Error: Invalid input, please enter a number\")\n'''\n\nprint(\"Hello, World!\")",
        "participants": {},
        "host_id": None,
        "writer_id": None,
        "chat_messages": [],
        "created_at": datetime.now().isoformat(),
        "is_running_code": False,
        "current_process": None
    }
    print(f"🎉 New session created: {session_id}")
    return jsonify({"session_id": session_id})

@app.route("/editor/<session_id>")
def editor(session_id):
    if session_id not in sessions:
        return "Session not found", 404
    return render_template("editor.html", session_id=session_id)

@app.route("/run_code", methods=["POST"])
def run_code():
    data = request.get_json()
    code = data.get("code", "")
    session_id = data.get("session_id")

    if session_id not in sessions:
        return jsonify({"output": "❌ Error: Session not found\n"})

    # Set session as running code
    sessions[session_id]["is_running_code"] = True
    sessions[session_id]["current_process"] = None

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
        running_procs[process_id] = {
            'process': proc,
            'session_id': session_id
        }
        
        sessions[session_id]["current_process"] = process_id

        socketio.emit("code_output", {"output": "🚀 [Program started]\\n"}, room=session_id)
        socketio.emit("code_running_status", {"is_running": True, "process_id": process_id}, room=session_id)

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
                        # No more output, check if process ended
                        if proc.poll() is not None:
                            break
                        time.sleep(0.1)  # Small delay to prevent busy waiting
                        
            except Exception as e:
                socketio.emit("code_output", {"output": f"❌ [Error: {e}]\\n"}, room=session_id)
            finally:
                # Cleanup
                if process_id in running_procs:
                    del running_procs[process_id]
                
                sessions[session_id]["is_running_code"] = False
                sessions[session_id]["current_process"] = None
                
                socketio.emit("code_output", {"output": "✅ [Program finished]\\n"}, room=session_id)
                socketio.emit("code_running_status", {"is_running": False}, room=session_id)

        # Start output streaming in a separate thread
        thread = threading.Thread(target=stream_output, daemon=True)
        thread.start()
        
        return jsonify({
            "output": "[Program started - waiting for output...]\\n",
            "process_id": process_id
        })

    except Exception as e:
        sessions[session_id]["is_running_code"] = False
        sessions[session_id]["current_process"] = None
        return jsonify({"output": f"❌ Error: {str(e)}\\n"})

# ------------------ SocketIO Event Handlers ------------------

@socketio.on("connect")
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on("disconnect")
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    handle_user_leave()

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
        "sid": sid,
        "joined_at": datetime.now().isoformat()
    }
    
    # Send current code to new user
    emit("code_update", {"content": session["content"]})
    
    # Send chat history to new user
    if session["chat_messages"]:
        emit("chat_history", {"messages": session["chat_messages"][-50:]})
    
    # Send current code running status
    emit("code_running_status", {
        "is_running": session["is_running_code"],
        "process_id": session["current_process"]
    })
    
    # Notify all users about updated participants
    emit_participants_update(session_id)
    
    print(f"👤 {name} joined session {session_id}")

def emit_participants_update(session_id):
    """Helper to emit participants update to room"""
    if session_id in sessions:
        session = sessions[session_id]
        emit("participants_update", {
            "participants": session["participants"],
            "writer_id": session["writer_id"],
            "host_id": session["host_id"]
        }, room=session_id)

@socketio.on("code_change")
def handle_code_change(data):
    session_id = data.get("session_id")
    content = data.get("content", "")
    sid = request.sid
    
    if session_id in sessions:
        session = sessions[session_id]
        # Only allow code changes if no code is currently running
        if not session["is_running_code"] and session["writer_id"] == sid:
            session["content"] = content
            emit("code_update", {"content": content}, room=session_id, include_self=False)
            print(f"📝 Code updated by {session['participants'][sid]['name']} in session {session_id}")
        elif session["is_running_code"]:
            # Notify user that code cannot be changed while running
            emit("error", {"msg": "Cannot edit code while program is running"}, to=sid)

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

# ------------------ Chat System ------------------

@socketio.on("send_chat_message")
def handle_chat_message(data):
    """Handle chat messages from clients"""
    session_id = data.get("session_id")
    message_text = data.get("message", "").strip()
    sid = request.sid
    
    if not session_id or session_id not in sessions:
        return
    
    if not message_text:
        return
    
    session = sessions[session_id]
    if sid not in session["participants"]:
        return
    
    # Get sender info
    sender_info = session["participants"][sid]
    sender_name = sender_info["name"]
    
    # Create chat message
    chat_message = {
        "id": str(uuid.uuid4())[:8],
        "sender_sid": sid,
        "sender_name": sender_name,
        "message": message_text,
        "timestamp": time(),
        "time_display": datetime.now().strftime("%H:%M"),
        "is_me": False  # This will be set on client side
    }
    
    # Store message in session chat history
    session["chat_messages"].append(chat_message)
    
    # Keep only last 100 messages
    if len(session["chat_messages"]) > 100:
        session["chat_messages"] = session["chat_messages"][-100:]
    
    # Broadcast to all participants in the session
    emit("new_chat_message", chat_message, room=session_id)
    
    print(f"💬 {sender_name} sent message in session {session_id}: {message_text[:50]}...")

@socketio.on("get_chat_history")
def handle_get_chat_history(data):
    """Send chat history to joining user"""
    session_id = data.get("session_id")
    sid = request.sid
    
    if session_id in sessions and sessions[session_id]["chat_messages"]:
        session = sessions[session_id]
        chat_history = session["chat_messages"][-50:]  # Last 50 messages
        emit("chat_history", {"messages": chat_history})

# ------------------ Code Input Handling ------------------

@socketio.on("provide_input")
def handle_input(data):
    session_id = data.get("session_id")
    text = data.get("text", "")
    process_id = data.get("process_id")
    
    if process_id in running_procs:
        proc_info = running_procs[process_id]
        proc = proc_info['process']
        
        if proc and proc.poll() is None:
            try:
                # Send input to the process
                proc.stdin.write(text + "\n")
                proc.stdin.flush()
                emit("code_output", {"output": f"[Input sent: {text}]\n"}, room=session_id)
                print(f"📥 Input provided to process {process_id}: {text}")
            except Exception as e:
                emit("code_output", {"output": f"❌ Error sending input: {e}\n"}, room=session_id)
        else:
            emit("code_output", {"output": "[No running process to send input]\n"}, room=session_id)
    else:
        emit("code_output", {"output": "[Process not found]\n"}, room=session_id)

# ------------------ WebRTC Signaling ------------------

@socketio.on("webrtc_offer")
def handle_webrtc_offer(data):
    """Handle WebRTC offer"""
    target = data.get("target")
    sdp = data.get("sdp")
    
    if target:
        emit("webrtc_offer", {
            "sdp": sdp, 
            "sid": request.sid
        }, to=target)

@socketio.on("webrtc_answer")
def handle_webrtc_answer(data):
    """Handle WebRTC answer"""
    target = data.get("target")
    sdp = data.get("sdp")
    
    if target:
        emit("webrtc_answer", {
            "sdp": sdp, 
            "sid": request.sid
        }, to=target)

@socketio.on("webrtc_ice_candidate")
def handle_webrtc_ice_candidate(data):
    """Handle ICE candidate exchange"""
    target = data.get("target")
    candidate = data.get("candidate")
    
    if target:
        emit("webrtc_ice_candidate", {
            "candidate": candidate, 
            "sid": request.sid
        }, to=target)

@socketio.on("webrtc_get_participants")
def handle_webrtc_get_participants(data):
    """Get all participants for WebRTC connections"""
    session_id = data.get("session_id")
    sid = request.sid
    
    if session_id in sessions:
        session = sessions[session_id]
        participants = []
        
        for participant_sid, info in session["participants"].items():
            if participant_sid != sid:
                participants.append({
                    "sid": participant_sid,
                    "name": info["name"]
                })
        
        emit("webrtc_participants_list", {"participants": participants})

if __name__ == "__main__":
    print("🚀 Starting SIREN Collaborative Editor...")
    print("📍 Local URL: http://localhost:5000")
    print("💬 Features: Real-time chat, Code execution with input, Voice chat")
    print("🔧 Running with threading async_mode for better compatibility")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
