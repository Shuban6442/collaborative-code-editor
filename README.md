# Collaborative Code Editor

A real-time collaborative code editor built with Flask and Socket.IO that allows multiple users to edit code together in sessions.

## Features

- 🚀 Real-time collaborative editing
- 👥 Session-based multi-user support
- 🎯 Host/participant role management  
- ▶️ Code execution functionality
- 💻 Monaco Editor integration
- 🔄 Live participant updates
- 🎨 Clean, modern UI

## Installation

1. Clone this repository:
```bash
git clone <repository-url>
cd collaborative-code-editor
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

1. Start the Flask application:
```bash
python app.py
```

2. Open your browser and go to `http://localhost:5000`

3. Create a new session or join an existing one

4. Start coding collaboratively!

## How it Works

- **Sessions**: Each coding session has a unique ID
- **Roles**: The first person to join becomes the host and initial writer
- **Writing Permission**: Only one person can edit at a time (the current writer)
- **Host Controls**: Hosts can grant/revoke write permissions
- **Real-time Updates**: All changes are synchronized instantly via WebSocket

## API Endpoints

- `GET /` - Home page
- `POST /create_session` - Create a new session
- `GET /editor/<session_id>` - Join a session
- `POST /run_code` - Execute Python code

## WebSocket Events

- `join_session` - Join a coding session
- `code_change` - Broadcast code changes
- `grant_write` - Grant write permission
- `revoke_write` - Revoke write permission

## Technologies Used

- **Backend**: Flask, Flask-SocketIO
- **Frontend**: HTML, CSS, JavaScript
- **Editor**: Monaco Editor
- **Real-time**: Socket.IO
- **Code Execution**: Python subprocess

## License

MIT License