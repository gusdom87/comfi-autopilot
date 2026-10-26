# Judge sandbox
One command, no build step:
  JEV_SANDBOX_PORT=8791 python3 sandbox/server.py
Then open http://127.0.0.1:8791. The API key comes from the server
environment (see .env.example); it never reaches the browser. Example
messages are staged for the demo; no real guest data is used anywhere.
