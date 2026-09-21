import os

from cash.web.app import create_app

app = create_app("data-dev", admin_password="dev", user_password="dev",
                 testing=True, auth_env_file=os.environ.get("CASH_AUTH_ENV_FILE"))

if __name__ == "__main__":
    app.run(
        host=os.environ.get("CASH_DEV_HOST", "127.0.0.1"),
        port=int(os.environ.get("CASH_DEV_PORT", "5051")),
        use_reloader=False)
