"""Entry point.

python run.py                      # dev server on http://127.0.0.1:5000
flask --app run db upgrade         # CLI commands find ``app`` below
gunicorn "run:app"                 # production (set APP_ENV=production)
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=app.config["DEBUG"],
    )
