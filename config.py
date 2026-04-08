import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
PRIVATE_GROUP_ID = int(os.getenv("PRIVATE_GROUP_ID", "0"))

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Dubai")

PRODUCTS = [
    "F7",
    "HF9",
    "PW11",
    "PW11 Pro Max",
    "H10 Flex",
    "H9 Pro",
    "JV35",
    "BX6 Lite",
    "BX7 Pro Max",
    "F8 Hair Dryer",
    "JV9 Pro Aqua",
]

MAIN_MENU = [["Login", "Logout", "Status"]]
YES_NO_MENU = [["Yes", "No"]]
CONFIRM_RETRY_MENU = [["Confirm", "Retry"]]
DONE_NONE_MENU = [["Done", "None"]]
