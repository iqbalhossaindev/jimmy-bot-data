import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from collections import defaultdict

import pytz
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

from config import (
    BOT_TOKEN,
    ADMIN_IDS,
    PRIVATE_GROUP_ID,
    PRODUCTS,
    MAIN_MENU,
    YES_NO_MENU,
    CONFIRM_RETRY_MENU,
    DONE_NONE_MENU,
    TIMEZONE,
)
from github_storage import GitHubStorage

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

storage = GitHubStorage()
tz = pytz.timezone(TIMEZONE)

(
    REG_NAME,
    REG_MALL,
    REG_STORE,
    REG_CONFIRM,
    LOGOUT_CUSTOMERS,
    LOGOUT_PRODUCT,
    LOGOUT_QTY,
    LOGOUT_VALUE,
    LOGOUT_CONFIRM,
) = range(9)

ATTENDANCE_FIELDS = [
    "telegram_id",
    "name",
    "mall_name",
    "store_name",
    "date",
    "login_time",
    "logout_time",
    "work_seconds",
]


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"JIMMY bot is running")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


def now_local():
    return datetime.now(tz)


def date_str(dt=None):
    dt = dt or now_local()
    return dt.strftime("%Y-%m-%d")


def pretty_date(dt=None):
    dt = dt or now_local()
    return dt.strftime("%d %B %Y")


def time_str(dt=None):
    dt = dt or now_local()
    return dt.strftime("%H:%M:%S")


def seconds_to_hms(total_seconds):
    total_seconds = int(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def month_key(dt=None):
    dt = dt or now_local()
    return dt.strftime("%Y-%m")


def is_admin(user_id):
    return user_id in ADMIN_IDS


def get_employees():
    return storage.read_json("employees.json", [])


def save_employees(data):
    storage.write_json("employees.json", data, "Update employees.json")


def get_pending():
    return storage.read_json("pending.json", [])


def save_pending(data):
    storage.write_json("pending.json", data, "Update pending.json")


def get_sales():
    return storage.read_json("sales.json", [])


def save_sales(data):
    storage.write_json("sales.json", data, "Update sales.json")


def get_attendance():
    return storage.read_csv_rows("attendance.csv")


def save_attendance(rows):
    storage.write_csv_rows(
        "attendance.csv",
        ATTENDANCE_FIELDS,
        rows,
        "Update attendance.csv",
    )


def find_employee(telegram_id):
    for item in get_employees():
        if str(item.get("telegram_id")) == str(telegram_id) and item.get("status") == "approved":
            return item
    return None


def user_month_summary(telegram_id):
    month = month_key()
    attendance = get_attendance()
    sales = get_sales()

    working_days = 0
    total_seconds = 0

    for row in attendance:
        if str(row.get("telegram_id")) == str(telegram_id) and str(row.get("date", "")).startswith(month):
            if row.get("login_time"):
                working_days += 1
            try:
                total_seconds += int(float(row.get("work_seconds") or 0))
            except Exception:
                pass

    total_sale_value = 0
    total_qty = 0
    products_by_date = defaultdict(list)

    for entry in sales:
        if str(entry.get("telegram_id")) == str(telegram_id) and str(entry.get("date", "")).startswith(month):
            total_sale_value += int(entry.get("total_value", 0))
            total_qty += int(entry.get("total_qty", 0))
            for item in entry.get("items", []):
                product_name = item.get("product", "")
                if product_name:
                    products_by_date[entry["date"]].append(product_name)

    return {
        "working_days": working_days,
        "total_seconds": total_seconds,
        "total_sale_value": total_sale_value,
        "total_qty": total_qty,
        "products_by_date": dict(products_by_date),
    }


def format_login_report(employee, dt, working_days):
    return (
        "Login\n\n"
        f'Name: {employee["name"]}\n'
        f'Mall Name: {employee["mall_name"]}\n'
        f'Store: {employee["store_name"]}\n'
        f"Time: {dt.strftime('%H:%M:%S')}\n"
        f"Date: {pretty_date(dt)}\n"
        f"This Month Total Working Day: {working_days}"
    )


def build_dsr_text(employee, report, today_hours_text):
    lines = []
    lines.append("Logout\n")
    lines.append(f'Name: {employee["name"]}')
    lines.append(f'Mall Name: {employee["mall_name"]}')
    lines.append(f'Store: {employee["store_name"]}')
    lines.append(f'Time: {report["logout_time"]}')
    lines.append(f'Date: {report["pretty_date"]}')
    lines.append(f"Total Working Hour Today: {today_hours_text}")
    lines.append(f'This Month Total Working Day: {report["month_working_days"]}')
    lines.append("")
    lines.append(f'DSR Dated {report["dsr_date"]}')
    lines.append(f'{employee["store_name"]} - {employee["mall_name"]}')
    lines.append("")
    lines.append(f'Total Customers Attend: {report["customers_attend"]}')
    lines.append("")
    lines.append("Model | Qty | Price | Total")

    for item in report["items"]:
        lines.append(f'{item["product"]} | {item["qty"]} | {item["price"]} | {item["total"]}')

    lines.append("")
    lines.append(f'Total Qty: {report["total_qty"]}')
    lines.append(f'Total Value: {report["total_value"]}')
    return "\n".join(lines)


def send_group_join_link(bot, user_id):
    try:
        link = bot.export_chat_invite_link(chat_id=PRIVATE_GROUP_ID)
        bot.send_message(
            chat_id=user_id,
            text=(
                "Your registration is approved.\n\n"
                "Please join the private group using this link:\n\n"
                f"{link}"
            ),
        )
    except Exception as e:
        logging.exception("Failed to send group join link: %s", e)
        bot.send_message(
            chat_id=user_id,
            text="Your registration is approved, but the group join link could not be created. Please contact admin.",
        )


def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    employee = find_employee(user_id)

    if employee:
        update.message.reply_text(
            "Welcome back to JIMMY.",
            reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
        )
        return ConversationHandler.END

    update.message.reply_text("Welcome to JIMMY.\n\nWhat is your name?")
    return REG_NAME


def reg_name(update: Update, context: CallbackContext):
    context.user_data["reg_name"] = update.message.text.strip()
    update.message.reply_text("Which mall are you working now?")
    return REG_MALL


def reg_mall(update: Update, context: CallbackContext):
    context.user_data["reg_mall"] = update.message.text.strip()
    update.message.reply_text("What is your shop name?")
    return REG_STORE


def reg_store(update: Update, context: CallbackContext):
    context.user_data["reg_store"] = update.message.text.strip()
    text = (
        "Please confirm your details:\n\n"
        f'Name: {context.user_data["reg_name"]}\n'
        f'Mall Name: {context.user_data["reg_mall"]}\n'
        f'Store: {context.user_data["reg_store"]}\n\n'
        "Are you confirm?"
    )
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(YES_NO_MENU, resize_keyboard=True),
    )
    return REG_CONFIRM


def reg_confirm(update: Update, context: CallbackContext):
    answer = update.message.text.strip().lower()

    if answer == "no":
        update.message.reply_text("No problem. Let's try again.\n\nWhat is your name?")
        return REG_NAME

    user = update.effective_user
    pending = get_pending()

    record = {
        "telegram_id": user.id,
        "username": user.username or "",
        "name": context.user_data["reg_name"],
        "mall_name": context.user_data["reg_mall"],
        "store_name": context.user_data["reg_store"],
        "status": "pending",
        "applied_at": now_local().isoformat(),
    }

    pending = [x for x in pending if str(x.get("telegram_id")) != str(user.id)]
    pending.append(record)
    save_pending(pending)

    for admin_id in ADMIN_IDS:
        try:
            context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "New registration request\n\n"
                    f'Name: {record["name"]}\n'
                    f'Mall Name: {record["mall_name"]}\n'
                    f'Store: {record["store_name"]}\n\n'
                    f"Reply with:\n/approve_{user.id}\nor\n/reject_{user.id}"
                ),
            )
        except Exception:
            logging.exception("Failed to send admin registration request")

    update.message.reply_text("Registration request sent to admin.\n\nPlease wait for approval.")
    return ConversationHandler.END


def approve_dynamic(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return

    user_id = int(update.message.text.strip().split("_", 1)[1])

    pending = get_pending()
    match = None
    rest = []

    for item in pending:
        if str(item.get("telegram_id")) == str(user_id):
            match = item
        else:
            rest.append(item)

    if not match:
        update.message.reply_text("Pending request not found.")
        return

    employees = get_employees()
    employees = [x for x in employees if str(x.get("telegram_id")) != str(user_id)]

    match["status"] = "approved"
    match["approved_at"] = now_local().isoformat()
    employees.append(match)

    save_employees(employees)
    save_pending(rest)

    update.message.reply_text("Registration approved successfully.")

    try:
        context.bot.send_message(
            chat_id=user_id,
            text="Registration approved successfully.\n\nCongratulations!\nWelcome to JIMMY.",
            reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
        )
        send_group_join_link(context.bot, user_id)
    except Exception:
        logging.exception("Failed to notify approved user")


def reject_dynamic(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return

    user_id = int(update.message.text.strip().split("_", 1)[1])

    pending = get_pending()
    rest = [x for x in pending if str(x.get("telegram_id")) != str(user_id)]
    save_pending(rest)

    update.message.reply_text("Registration rejected.")

    try:
        context.bot.send_message(
            chat_id=user_id,
            text=(
                "Registration rejected.\n\n"
                "Greetings from JIMMY.\n\n"
                "Your application was not approved.\n"
                "Please contact admin for assistance."
            ),
        )
    except Exception:
        logging.exception("Failed to notify rejected user")


def login(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    employee = find_employee(user_id)

    if not employee:
        update.message.reply_text("You are not approved yet. Please complete signup first with /start")
        return

    today = date_str()
    rows = get_attendance()

    for row in rows:
        if str(row["telegram_id"]) == str(user_id) and row["date"] == today and row.get("login_time"):
            update.message.reply_text("You already logged in today.")
            return

    dt = now_local()

    rows.append({
        "telegram_id": str(user_id),
        "name": employee["name"],
        "mall_name": employee["mall_name"],
        "store_name": employee["store_name"],
        "date": today,
        "login_time": time_str(dt),
        "logout_time": "",
        "work_seconds": "0",
    })
    save_attendance(rows)

    summary = user_month_summary(user_id)
    report = format_login_report(employee, dt, summary["working_days"])

    update.message.reply_text("Login successful.")
    update.message.reply_text("Thanks For Join Work.\nPlease Focus on Sales.\nBest of Luck")

    try:
        context.bot.send_message(chat_id=PRIVATE_GROUP_ID, text=report)
    except Exception:
        logging.exception("Failed to send login report to group")

    try:
        context.bot.send_message(chat_id=user_id, text=report)
    except Exception:
        logging.exception("Failed to send login report to user")


def logout_start(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)

    if not employee:
        update.message.reply_text("You are not approved yet. Please complete signup first with /start")
        return ConversationHandler.END

    rows = get_attendance()
    today = date_str()
    today_row = None

    for row in rows:
        if str(row["telegram_id"]) == str(update.effective_user.id) and row["date"] == today:
            today_row = row
            break

    if not today_row or not today_row.get("login_time"):
        update.message.reply_text("You did not login today.")
        return ConversationHandler.END

    if today_row.get("logout_time"):
        update.message.reply_text("You already logged out today.")
        return ConversationHandler.END

    context.user_data["logout_items"] = []
    context.user_data["logout_customers"] = 0

    update.message.reply_text("How many customers attended today?")
    return LOGOUT_CUSTOMERS


def logout_customers(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if not text.isdigit():
        update.message.reply_text("Please enter numbers only.")
        return LOGOUT_CUSTOMERS

    context.user_data["logout_customers"] = int(text)
    keyboard = [[p] for p in PRODUCTS] + DONE_NONE_MENU

    update.message.reply_text(
        "Select product.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )
    return LOGOUT_PRODUCT


def logout_product(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Done":
        return show_logout_summary(update, context)

    if text == "None":
        context.user_data["logout_items"] = []
        return show_logout_summary(update, context)

    if text not in PRODUCTS:
        update.message.reply_text("Please select a valid product.")
        return LOGOUT_PRODUCT

    context.user_data["current_product"] = text
    update.message.reply_text(f"Enter quantity for {text}")
    return LOGOUT_QTY


def logout_qty(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if not text.isdigit() or int(text) <= 0:
        update.message.reply_text("Please enter a valid quantity.")
        return LOGOUT_QTY

    context.user_data["current_qty"] = int(text)
    update.message.reply_text(f'Enter value for each product: {context.user_data["current_product"]}')
    return LOGOUT_VALUE


def logout_value(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if not text.isdigit() or int(text) < 0:
        update.message.reply_text("Please enter a valid value.")
        return LOGOUT_VALUE

    price = int(text)
    qty = context.user_data["current_qty"]
    total = price * qty

    context.user_data["logout_items"].append({
        "product": context.user_data["current_product"],
        "qty": qty,
        "price": price,
        "total": total,
    })

    update.message.reply_text(
        f'{context.user_data["current_product"]}\n'
        f'Qty: {qty}\n'
        f'Price: {price}\n'
        f'Total: {price} x {qty} = {total}'
    )

    keyboard = [[p] for p in PRODUCTS] + DONE_NONE_MENU
    update.message.reply_text(
        "Select another product or press Done.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )
    return LOGOUT_PRODUCT


def show_logout_summary(update: Update, context: CallbackContext):
    items = context.user_data.get("logout_items", [])
    lines = ["Today's Sales Summary\n"]

    total_qty = 0
    total_value = 0

    if not items:
        lines.append("No Sale")
    else:
        for item in items:
            lines.append(f'{item["product"]}: {item["qty"]} x {item["price"]} = {item["total"]}')
            total_qty += item["qty"]
            total_value += item["total"]

    lines.append("")
    lines.append(f'Total Customers Attend: {context.user_data.get("logout_customers", 0)}')
    lines.append(f"Total Qty: {total_qty}")
    lines.append(f"Total Value: {total_value}")
    lines.append("")
    lines.append("Please confirm this report.")

    context.user_data["summary_total_qty"] = total_qty
    context.user_data["summary_total_value"] = total_value

    update.message.reply_text(
        "\n".join(lines),
        reply_markup=ReplyKeyboardMarkup(CONFIRM_RETRY_MENU, resize_keyboard=True),
    )
    return LOGOUT_CONFIRM


def logout_confirm(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Retry":
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("No problem. Let's try again.\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if text != "Confirm":
        update.message.reply_text("Please press Confirm or Retry.")
        return LOGOUT_CONFIRM

    user_id = update.effective_user.id
    employee = find_employee(user_id)
    rows = get_attendance()
    today = date_str()
    logout_dt = now_local()
    work_seconds = 0

    for row in rows:
        if str(row["telegram_id"]) == str(user_id) and row["date"] == today and row.get("login_time"):
            login_dt = tz.localize(datetime.strptime(f'{today} {row["login_time"]}', "%Y-%m-%d %H:%M:%S"))
            work_seconds = int((logout_dt - login_dt).total_seconds())
            row["logout_time"] = time_str(logout_dt)
            row["work_seconds"] = str(work_seconds)
            break

    save_attendance(rows)

    sales = get_sales()
    report = {
        "telegram_id": user_id,
        "name": employee["name"],
        "mall_name": employee["mall_name"],
        "store_name": employee["store_name"],
        "date": today,
        "pretty_date": pretty_date(logout_dt),
        "dsr_date": logout_dt.strftime("%d/%m/%Y"),
        "logout_time": time_str(logout_dt),
        "customers_attend": context.user_data.get("logout_customers", 0),
        "items": context.user_data.get("logout_items", []),
        "total_qty": context.user_data.get("summary_total_qty", 0),
        "total_value": context.user_data.get("summary_total_value", 0),
    }
    sales.append(report)
    save_sales(sales)

    summary = user_month_summary(user_id)
    report["month_working_days"] = summary["working_days"]

    final_text = build_dsr_text(employee, report, seconds_to_hms(work_seconds))

    update.message.reply_text(
        "Logout successful.",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
    )

    try:
        context.bot.send_message(chat_id=PRIVATE_GROUP_ID, text=final_text)
    except Exception:
        logging.exception("Failed to send logout report to group")

    try:
        context.bot.send_message(chat_id=user_id, text=final_text)
    except Exception:
        logging.exception("Failed to send logout report to user")

    return ConversationHandler.END


def status(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)

    if not employee:
        update.message.reply_text("You are not approved yet. Please complete signup first with /start")
        return

    summary = user_month_summary(update.effective_user.id)

    lines = [
        "My Status\n",
        f'Name: {employee["name"]}',
        f'Mall Name: {employee["mall_name"]}',
        f'Store: {employee["store_name"]}',
        "",
        f'This Month Total Working Day: {summary["working_days"]}',
        f'This Month Total Working Hours: {seconds_to_hms(summary["total_seconds"])}',
        f'This Month Total Sale Value: {summary["total_sale_value"]}',
        f'This Month Total Quantity Sale: {summary["total_qty"]}',
        "",
        "Date Wise Product Sales",
    ]

    if not summary["products_by_date"]:
        lines.append("No Sale")
    else:
        for d in sorted(summary["products_by_date"].keys()):
            pretty = datetime.strptime(d, "%Y-%m-%d").strftime("%d %B %Y")
            lines.append("")
            lines.append(pretty)
            for product in summary["products_by_date"][d]:
                lines.append(product)

    update.message.reply_text("\n".join(lines))


def admin_history(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return

    query = " ".join(context.args).strip()
    if not query:
        update.message.reply_text("Usage: /history Full Name")
        return

    employees = get_employees()
    employee = None

    for item in employees:
        if item.get("name", "").lower() == query.lower():
            employee = item
            break

    if not employee:
        update.message.reply_text("Employee not found.")
        return

    attendance = get_attendance()
    sales = get_sales()

    lines = [
        f'History for: {employee["name"]}',
        "",
        f'Mall Name: {employee["mall_name"]}',
        f'Store: {employee["store_name"]}',
        "",
    ]

    sales_map = {
        x["date"]: x for x in sales if str(x.get("telegram_id")) == str(employee["telegram_id"])
    }

    found = False

    for row in attendance:
        if str(row["telegram_id"]) == str(employee["telegram_id"]):
            found = True
            pretty = datetime.strptime(row["date"], "%Y-%m-%d").strftime("%d %B %Y")
            lines.append(pretty)
            lines.append(f'Login: {row.get("login_time") or "-"}')
            lines.append(f'Logout: {row.get("logout_time") or "-"}')
            lines.append(f'Work Hour: {seconds_to_hms(int(float(row.get("work_seconds") or 0)))}')

            sale = sales_map.get(row["date"])
            if sale:
                lines.append(f'Total Customers: {sale.get("customers_attend", 0)}')
                lines.append(f'Total Qty: {sale.get("total_qty", 0)}')
                lines.append(f'Total Value: {sale.get("total_value", 0)}')

            lines.append("")

    if not found:
        lines.append("No history found.")

    update.message.reply_text("\n".join(lines))


def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def main():
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    registration = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REG_NAME: [MessageHandler(Filters.text & ~Filters.command, reg_name)],
            REG_MALL: [MessageHandler(Filters.text & ~Filters.command, reg_mall)],
            REG_STORE: [MessageHandler(Filters.text & ~Filters.command, reg_store)],
            REG_CONFIRM: [MessageHandler(Filters.text & ~Filters.command, reg_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    logout_flow = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Logout$"), logout_start)],
        states={
            LOGOUT_CUSTOMERS: [MessageHandler(Filters.text & ~Filters.command, logout_customers)],
            LOGOUT_PRODUCT: [MessageHandler(Filters.text & ~Filters.command, logout_product)],
            LOGOUT_QTY: [MessageHandler(Filters.text & ~Filters.command, logout_qty)],
            LOGOUT_VALUE: [MessageHandler(Filters.text & ~Filters.command, logout_value)],
            LOGOUT_CONFIRM: [MessageHandler(Filters.text & ~Filters.command, logout_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    dp.add_handler(registration)
    dp.add_handler(logout_flow)
    dp.add_handler(MessageHandler(Filters.regex("^Login$"), login))
    dp.add_handler(MessageHandler(Filters.regex("^Status$"), status))
    dp.add_handler(CommandHandler("history", admin_history))
    dp.add_handler(MessageHandler(Filters.regex(r"^/approve_\d+$"), approve_dynamic))
    dp.add_handler(MessageHandler(Filters.regex(r"^/reject_\d+$"), reject_dynamic))

    updater.start_polling(drop_pending_updates=True)
    updater.idle()


if __name__ == "__main__":
    main()
