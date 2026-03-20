from dotenv import load_dotenv

from caltrain_bot.telegram_bot import build_app

load_dotenv()


def main() -> None:
    app = build_app()
    # app.run_polling()


if __name__ == "__main__":
    main()
