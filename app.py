# 1. Apply Gevent monkey patching FIRST
from gevent import monkey
monkey.patch_all()

# 2. Standard imports follow
from flask import Flask
from routes_v2 import v2_blueprint

# Initialize Flask application
app = Flask(__name__)

# Register Blueprints
app.register_blueprint(v2_blueprint)

if __name__ == '__main__':
    from gevent.pywsgi import WSGIServer
    from geventwebsocket.handler import WebSocketHandler
    
    # Configure the Gevent WSGI server for REST and WebSockets
    http_server = WSGIServer(('0.0.0.0', 5000), app, handler_class=WebSocketHandler)
    print("[SYSTEM] Starting FYM Server on port 5000...")
    http_server.serve_forever()
