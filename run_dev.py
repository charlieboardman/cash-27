import sys
from cash.web.app import create_app

app = create_app("data-dev", admin_password="dev", testing=False)

if __name__ == "__main__":
    app.run(port=5051)
