import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from servidor.app import app

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
