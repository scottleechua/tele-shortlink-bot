import logging
import os
import warnings

from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", message=".*per_message=False.*", category=PTBUserWarning)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from db import init_db
from handlers.welcome import show_welcome, show_menu
from handlers.start import start_handler
from handlers.domains import domains_handler
from handlers.podcasts import podcasts_handler
from handlers.users import users_handler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    init_db()

    app = Application.builder().token(token).build()

    # /start shows the reply keyboard
    app.add_handler(CommandHandler("start", show_welcome))

    # Conversation handlers (checked first for active conversations)
    app.add_handler(start_handler())
    app.add_handler(domains_handler())
    app.add_handler(podcasts_handler())
    app.add_handler(users_handler())

    # Standalone handlers (checked when no conversation is active)
    app.add_handler(MessageHandler(filters.Text(["☰ Menu"]) & ~filters.COMMAND, show_menu))

    logger.info("Bot started, polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
