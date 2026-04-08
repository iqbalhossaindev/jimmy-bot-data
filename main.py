import os
import io
import csv
import json
import time as time_module
import logging
import threading
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict
from datetime import datetime as dt, time as dtime

import pytz
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image,
)

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

BREAK_LIMIT_SECONDS = 3600


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
    return dt.now(tz)


def date_str(value=None):
    value = value or now_local()
    return value.strftime("%Y-%m-%d")


def pretty_date(value=None):
    value = value or now_local()
    return value.strftime("%d %B %Y")


def time_str(value=None):
    value = value or now_local()
    return value.strftime("%H:%M:%S")


def seconds_to_hms(total_seconds):
    total_seconds = int(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def month_key(value=None):
    value = value or now_local()
    return value.strftime("%Y-%m")


def is_admin(user_id):
    return user_id in ADMIN_IDS


def first_name(full_name):
    if not full_name:
        return ""
    return full_name.split()[0]


def employee_label(employee):
    return f'{first_name(employee.get("name", ""))} | {employee.get("mall_name", "")} | {employee.get("store_name", "")}'


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


def get_breaks():
    return storage.read_json("break.json", [])


def save_breaks(data):
    storage.write_json("break.json", data, "Update break.json")


def get_absence_state():
    return storage.read_json("absence.json", [])


def save_absence_state(data):
    storage.write_json("absence.json", data, "Update absence.json")


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


def get_today_attendance_row(telegram_id):
    today = date_str()
    for row in get_attendance():
        if str(row.get("telegram_id")) == str(telegram_id) and row.get("date") == today:
            return row
    return None


def break_used_today(telegram_id):
    today = date_str()
    total = 0
    for item in get_breaks():
        if str(item.get("telegram_id")) == str(telegram_id) and item.get("date") == today:
            total += int(item.get("seconds", 0) or 0)
    return total


def user_month_summary(telegram_id):
    month = month_key()
    attendance = get_attendance()
    sales = get_sales()
    breaks = get_breaks()

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

    total_break = 0
    for item in breaks:
        if str(item.get("telegram_id")) == str(telegram_id) and str(item.get("date", "")).startswith(month):
            total_break += int(item.get("seconds", 0) or 0)

    return {
        "working_days": working_days,
        "total_seconds": total_seconds,
        "total_sale_value": total_sale_value,
        "total_qty": total_qty,
        "products_by_date": dict(products_by_date),
        "total_break": total_break,
    }


def format_login_report(employee, value, working_days):
    return (
        "✅ *LOGIN*\n\n"
        f'👤 *Name:* {employee["name"]}\n'
        f'📍 *Mall:* {employee["mall_name"]}\n'
        f'🏬 *Store:* {employee["store_name"]}\n\n'
        f'🕒 *Time:* {value.strftime("%H:%M:%S")}\n'
        f'📅 *Date:* {pretty_date(value)}\n\n'
        f"📊 *This Month Working Days:* {working_days}"
    )


def build_dsr_text(employee, report, today_hours_text):
    lines = []
    lines.append("🚪 *LOGOUT*\n")
    lines.append(f'👤 *Name:* {employee["name"]}')
    lines.append(f'📍 *Mall:* {employee["mall_name"]}')
    lines.append(f'🏬 *Store:* {employee["store_name"]}')
    lines.append("")
    lines.append(f'🕒 *Time:* {report["logout_time"]}')
    lines.append(f'📅 *Date:* {report["pretty_date"]}')
    lines.append(f'⏱ *Working Hours Today:* {today_hours_text}')
    lines.append(f'This Month Working Days: {report["month_working_days"]}')
    lines.append("")
    lines.append("━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("📦 *DSR REPORT*")
    lines.append(f'📅 *Date:* {report["dsr_date"]}')
    lines.append(f'🏬 *{employee["store_name"]} - {employee["mall_name"]}*')
    lines.append("")
    lines.append(f'*Customers Attended:* {report["customers_attend"]}')
    lines.append("")
    lines.append("*Sales Details*")
    lines.append("")

    if not report["items"]:
        lines.append("No Sale")
    else:
        for item in report["items"]:
            lines.append(
                f'*{item["product"]}*\n'
                f'Qty: {item["qty"]} | Price: {item["price"]} | Total: {item["total"]}'
            )
            lines.append("")

    lines.append("━━━━━━━━━━━━━━")
    lines.append(f'*Total Quantity:* {report["total_qty"]}')
    lines.append(f'*Total Value:* {report["total_value"]}')
    return "\n".join(lines)


def send_group_join_link(bot, user_id):
    try:
        link = bot.export_chat_invite_link(chat_id=PRIVATE_GROUP_ID)
        bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 *APPROVED*\n\n"
                "Your registration is approved.\n\n"
                "Please join the private group using this link:\n\n"
                f"{link}"
            ),
            parse_mode="Markdown",
        )
    except Exception as exc:
        logging.exception("Failed to send group join link: %s", exc)
        bot.send_message(
            chat_id=user_id,
            text="Your registration is approved, but the group join link could not be created. Please contact admin.",
        )


def send_markdown(bot, chat_id, text):
    bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


def build_monthly_pdf(employee, summary, year_month, output_path):
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleCenter",
        parent=styles["Title"],
        alignment=1,
        fontSize=20,
        spaceAfter=10,
    )
    normal_style = styles["Normal"]

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    elements = []

    if os.path.exists("logo.png"):
        try:
            elements.append(Image("logo.png", width=55 * mm, height=18 * mm))
            elements.append(Spacer(1, 6))
        except Exception:
            pass

    elements.append(Paragraph("EMPLOYEE TIMESHEET", title_style))
    elements.append(Spacer(1, 4))

    header_data = [
        ["Name", employee["name"], "Month", year_month],
        ["Brand", "JIMMY", "Location", f'{employee["store_name"]} - {employee["mall_name"]}'],
    ]
    header_table = Table(header_data, colWidths=[30 * mm, 75 * mm, 28 * mm, 55 * mm])
    header_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("BACKGROUND", (2, 0), (2, -1), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elements.append(header_table)
    elements.append(Spacer(1, 8))

    attendance = get_attendance()
    sales = get_sales()
    breaks = get_breaks()

    sales_by_date = {}
    for sale in sales:
        if str(sale.get("telegram_id")) == str(employee["telegram_id"]) and str(sale.get("date", "")).startswith(
            year_month.replace(" ", "-").replace("/", "-")
        ):
            sales_by_date[sale["date"]] = sale

    break_by_date = defaultdict(int)
    for item in breaks:
        if str(item.get("telegram_id")) == str(employee["telegram_id"]) and str(item.get("date", "")).startswith(
            year_month.replace(" ", "-").replace("/", "-")
        ):
            break_by_date[item["date"]] += int(item.get("seconds", 0) or 0)

    month_rows = [["Date", "Time In", "Time Out", "Break", "Work Hours", "Sales Qty", "Sales Value"]]

    target_month = None
    for row in attendance:
        if str(row.get("telegram_id")) == str(employee["telegram_id"]):
            target_month = row.get("date", "")[:7]
            if target_month:
                break

    if target_month is None:
        target_month = month_key()

    for day in range(1, 32):
        try:
            current = dt.strptime(f"{target_month}-{day:02d}", "%Y-%m-%d")
        except ValueError:
            continue

        date_key = current.strftime("%Y-%m-%d")
        row_match = None
        for row in attendance:
            if str(row.get("telegram_id")) == str(employee["telegram_id"]) and row.get("date") == date_key:
                row_match = row
                break

        if row_match:
            sale = sales_by_date.get(date_key, {})
            month_rows.append(
                [
                    current.strftime("%d/%m/%Y"),
                    row_match.get("login_time") or "-",
                    row_match.get("logout_time") or "-",
                    seconds_to_hms(break_by_date.get(date_key, 0)),
                    seconds_to_hms(int(float(row_match.get("work_seconds") or 0))),
                    str(sale.get("total_qty", 0)),
                    str(sale.get("total_value", 0)),
                ]
            )
        else:
            month_rows.append(
                [
                    current.strftime("%d/%m/%Y"),
                    "OFF",
                    "OFF",
                    "-",
                    "-",
                    "-",
                    "-",
                ]
            )

    timesheet_table = Table(
        month_rows,
        colWidths=[28 * mm, 24 * mm, 24 * mm, 22 * mm, 26 * mm, 20 * mm, 26 * mm],
        repeatRows=1,
    )
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B3D91")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    for idx, row in enumerate(month_rows[1:], start=1):
        if row[1] == "OFF":
            style_cmds.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#FFF2B2")))

    timesheet_table.setStyle(TableStyle(style_cmds))
    elements.append(timesheet_table)
    elements.append(Spacer(1, 8))

    summary_table = Table(
        [
            ["Total Working Days", str(summary["working_days"])],
            ["Total Working Hours", seconds_to_hms(summary["total_seconds"])],
            ["Total Sales Quantity", str(summary["total_qty"])],
            ["Total Sales Value", str(summary["total_sale_value"])],
            ["Total Break Time", seconds_to_hms(summary["total_break"])],
        ],
        colWidths=[65 * mm, 40 * mm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    elements.append(summary_table)
    elements.append(Spacer(1, 5))
    elements.append(Paragraph("Generated by JIMMY Management System", normal_style))

    doc.build(elements)


def send_user_monthly_pdf(update, context):
    employee = find_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("You are not approved yet.")
        return

    summary = user_month_summary(update.effective_user.id)
    ym = now_local().strftime("%B %Y")
    output_path = f"/tmp/monthly_{update.effective_user.id}.pdf"
    build_monthly_pdf(employee, summary, ym, output_path)

    with open(output_path, "rb") as file_obj:
        context.bot.send_document(
            chat_id=update.effective_user.id,
            document=file_obj,
            filename=f"{employee['name']}_monthly_timesheet.pdf",
            caption="📄 Monthly PDF Report",
        )


def send_admin_history_pdf(update, context):
    if not is_admin(update.effective_user.id):
        return

    query = " ".join(context.args).strip()
    if not query:
        update.message.reply_text("Usage: /historypdf Full Name")
        return

    employee = None
    for item in get_employees():
        if item.get("name", "").lower() == query.lower():
            employee = item
            break

    if not employee:
        update.message.reply_text("Employee not found.")
        return

    summary = user_month_summary(employee["telegram_id"])
    ym = now_local().strftime("%B %Y")
    output_path = f"/tmp/admin_history_{employee['telegram_id']}.pdf"
    build_monthly_pdf(employee, summary, ym, output_path)

    with open(output_path, "rb") as file_obj:
        context.bot.send_document(
            chat_id=update.effective_user.id,
            document=file_obj,
            filename=f"{employee['name']}_history.pdf",
            caption=f"📄 PDF History: {employee['name']}",
        )


def break_start(update, context):
    user_id = update.effective_user.id
    employee = find_employee(user_id)
    if not employee:
        update.message.reply_text("You are not approved yet.")
        return

    attendance_row = get_today_attendance_row(user_id)
    if not attendance_row or not attendance_row.get("login_time"):
        update.message.reply_text("Please login first.")
        return

    breaks = get_breaks()
    today = date_str()

    for item in breaks:
        if str(item.get("telegram_id")) == str(user_id) and item.get("date") == today and not item.get("end"):
            update.message.reply_text("⚠️ Break already started.")
            return

    now = now_local()
    breaks.append(
        {
            "telegram_id": user_id,
            "date": today,
            "start": time_str(now),
            "end": None,
            "seconds": 0,
        }
    )
    save_breaks(breaks)

    msg = (
        "☕ *BREAK STARTED*\n\n"
        f'👤 *Name:* {employee["name"]}\n'
        f'📍 *Mall:* {employee["mall_name"]}\n'
        f'🏬 *Store:* {employee["store_name"]}\n\n'
        f'🕒 *Time:* {time_str(now)}\n'
        f'📅 *Date:* {pretty_date(now)}'
    )

    update.message.reply_text(msg, parse_mode="Markdown")
    try:
        send_markdown(context.bot, PRIVATE_GROUP_ID, msg)
    except Exception:
        logging.exception("Failed to send break start to group")


def break_end(update, context):
    user_id = update.effective_user.id
    employee = find_employee(user_id)
    if not employee:
        update.message.reply_text("You are not approved yet.")
        return

    breaks = get_breaks()
    today = date_str()
    now = now_local()

    for item in breaks:
        if str(item.get("telegram_id")) == str(user_id) and item.get("date") == today and not item.get("end"):
            start_dt = tz.localize(dt.strptime(f'{today} {item["start"]}', "%Y-%m-%d %H:%M:%S"))
            seconds = int((now - start_dt).total_seconds())
            item["end"] = time_str(now)
            item["seconds"] = seconds
            save_breaks(breaks)

            used_today = break_used_today(user_id)

            msg = (
                "☕ *BREAK ENDED*\n\n"
                f'👤 *Name:* {employee["name"]}\n'
                f'📍 *Mall:* {employee["mall_name"]}\n'
                f'🏬 *Store:* {employee["store_name"]}\n\n'
                f'🕒 *Time:* {time_str(now)}\n'
                f'📅 *Date:* {pretty_date(now)}\n\n'
                f'⏱ *Break Used Today:* {seconds_to_hms(used_today)}'
            )

            update.message.reply_text(msg, parse_mode="Markdown")
            try:
                send_markdown(context.bot, PRIVATE_GROUP_ID, msg)
            except Exception:
                logging.exception("Failed to send break end to group")

            if used_today > BREAK_LIMIT_SECONDS:
                warning = (
                    "⚠️ *BREAK LIMIT EXCEEDED*\n\n"
                    f'👤 *Name:* {employee["name"]}\n'
                    f'📍 *Mall:* {employee["mall_name"]}\n'
                    f'🏬 *Store:* {employee["store_name"]}\n\n'
                    f'*Allowed Break:* {seconds_to_hms(BREAK_LIMIT_SECONDS)}\n'
                    f'*Used Today:* {seconds_to_hms(used_today)}'
                )
                try:
                    send_markdown(context.bot, user_id, warning)
                    for admin_id in ADMIN_IDS:
                        send_markdown(context.bot, admin_id, warning)
                except Exception:
                    logging.exception("Failed to send break limit warning")
            return

    update.message.reply_text("⚠️ No active break found.")


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
            send_markdown(
                context.bot,
                admin_id,
                (
                    "🆕 *NEW REGISTRATION REQUEST*\n\n"
                    f'*Employee:* {employee_label(record)}\n'
                    f'Name: {record["name"]}\n'
                    f'Mall: {record["mall_name"]}\n'
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
        send_markdown(
            context.bot,
            user_id,
            "🎉 *APPROVED*\n\nRegistration approved successfully.\n\nWelcome to JIMMY.",
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
        send_markdown(
            context.bot,
            user_id,
            (
                "❌ *REGISTRATION REJECTED*\n\n"
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

    value = now_local()

    rows.append(
        {
            "telegram_id": str(user_id),
            "name": employee["name"],
            "mall_name": employee["mall_name"],
            "store_name": employee["store_name"],
            "date": today,
            "login_time": time_str(value),
            "logout_time": "",
            "work_seconds": "0",
        }
    )
    save_attendance(rows)

    absence_state = get_absence_state()
    for item in absence_state:
        if str(item.get("telegram_id")) == str(user_id):
            item["alert_sent"] = False
            item["days_absent"] = 0
    save_absence_state(absence_state)

    summary = user_month_summary(user_id)
    report = format_login_report(employee, value, summary["working_days"])

    update.message.reply_text("Login successful.")
    update.message.reply_text("Thanks For Join Work.\nPlease Focus on Sales.\nBest of Luck")

    try:
        send_markdown(context.bot, PRIVATE_GROUP_ID, report)
    except Exception:
        logging.exception("Failed to send login report to group")

    try:
        send_markdown(context.bot, user_id, report)
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
    context.user_data["current_product"] = None
    context.user_data["current_qty"] = 0

    update.message.reply_text("How many customers attended today?")
    return LOGOUT_CUSTOMERS


def build_logout_keyboard():
    keyboard = [[p] for p in PRODUCTS]
    keyboard.append(["Done", "None"])
    keyboard.append(["Undo", "Reset"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def logout_customers(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("🔄 Reset done.\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if not text.isdigit():
        update.message.reply_text("⚠️ Invalid input.\nPlease enter customer count as a number only.")
        return LOGOUT_CUSTOMERS

    context.user_data["logout_customers"] = int(text)
    update.message.reply_text(
        "Select product.",
        reply_markup=build_logout_keyboard(),
    )
    return LOGOUT_PRODUCT


def show_product_prompt(update):
    update.message.reply_text(
        "Select another product or press Done.",
        reply_markup=build_logout_keyboard(),
    )


def logout_product(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("🔄 Reset done.\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if text == "Undo":
        items = context.user_data.get("logout_items", [])
        if items:
            items.pop()
            update.message.reply_text("↩️ Last item removed.")
        else:
            update.message.reply_text("⚠️ Nothing to undo.")
        show_product_prompt(update)
        return LOGOUT_PRODUCT

    if text == "Done":
        items = context.user_data.get("logout_items", [])
        if not items:
            update.message.reply_text("⚠️ Please add at least one product before finishing.")
            return LOGOUT_PRODUCT
        return show_logout_summary(update, context)

    if text == "None":
        context.user_data["logout_items"] = []
        return show_logout_summary(update, context)

    if text not in PRODUCTS:
        update.message.reply_text("Please select a valid product from the list.")
        return LOGOUT_PRODUCT

    context.user_data["current_product"] = text
    update.message.reply_text(f"Enter quantity for {text}")
    return LOGOUT_QTY


def logout_qty(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("🔄 Reset done.\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if not text.isdigit() or int(text) <= 0:
        update.message.reply_text("⚠️ Invalid input.\nPlease enter a valid quantity number.")
        return LOGOUT_QTY

    context.user_data["current_qty"] = int(text)
    update.message.reply_text(f'Enter value for each product: {context.user_data["current_product"]}')
    return LOGOUT_VALUE


def logout_value(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("🔄 Reset done.\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if not text.isdigit() or int(text) < 0:
        update.message.reply_text("⚠️ Invalid input.\nPlease enter a valid value number.")
        return LOGOUT_VALUE

    price = int(text)
    qty = context.user_data["current_qty"]
    total = price * qty

    context.user_data["logout_items"].append(
        {
            "product": context.user_data["current_product"],
            "qty": qty,
            "price": price,
            "total": total,
        }
    )

    update.message.reply_text(
        f'{context.user_data["current_product"]}\n'
        f'Qty: {qty}\n'
        f'Price: {price}\n'
        f'Total: {price} x {qty} = {total}'
    )

    show_product_prompt(update)
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

    keyboard = ReplyKeyboardMarkup([["Confirm", "Retry"], ["Reset"]], resize_keyboard=True)
    update.message.reply_text("\n".join(lines), reply_markup=keyboard)
    return LOGOUT_CONFIRM


def logout_confirm(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("🔄 Reset done.\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if text == "Retry":
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("No problem. Let's try again.\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if text != "Confirm":
        update.message.reply_text("Please press Confirm, Retry, or Reset.")
        return LOGOUT_CONFIRM

    user_id = update.effective_user.id
    employee = find_employee(user_id)
    rows = get_attendance()
    today = date_str()
    logout_value_dt = now_local()
    work_seconds = 0

    for row in rows:
        if str(row["telegram_id"]) == str(user_id) and row["date"] == today and row.get("login_time"):
            login_dt = tz.localize(dt.strptime(f'{today} {row["login_time"]}', "%Y-%m-%d %H:%M:%S"))
            work_seconds = int((logout_value_dt - login_dt).total_seconds())
            row["logout_time"] = time_str(logout_value_dt)
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
        "pretty_date": pretty_date(logout_value_dt),
        "dsr_date": logout_value_dt.strftime("%d/%m/%Y"),
        "logout_time": time_str(logout_value_dt),
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
        send_markdown(context.bot, PRIVATE_GROUP_ID, final_text)
    except Exception:
        logging.exception("Failed to send logout report to group")

    try:
        send_markdown(context.bot, user_id, final_text)
    except Exception:
        logging.exception("Failed to send logout report to user")

    return ConversationHandler.END


def status(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)

    if not employee:
        update.message.reply_text("You are not approved yet. Please complete signup first with /start")
        return

    summary = user_month_summary(update.effective_user.id)
    today_row = get_today_attendance_row(update.effective_user.id)
    current_status = "Logged Out"
    login_time_value = "-"

    if today_row and today_row.get("login_time") and not today_row.get("logout_time"):
        current_status = "Logged In"
        login_time_value = today_row.get("login_time")

    lines = [
        "📊 *STATUS*\n",
        f'👤 *Name:* {employee["name"]}',
        f'📍 *Mall:* {employee["mall_name"]}',
        f'🏬 *Store:* {employee["store_name"]}',
        "",
        f'🟢 *Status:* {current_status}',
        f'🕒 *Login Time:* {login_time_value}',
        "",
        f'📅 *Working Days:* {summary["working_days"]}',
        f'⏱ *Total Hours:* {seconds_to_hms(summary["total_seconds"])}',
        f'💰 *Total Sales:* {summary["total_sale_value"]}',
        f'📦 *Total Quantity:* {summary["total_qty"]}',
        f'☕ *Total Break Time:* {seconds_to_hms(summary["total_break"])}',
        "",
        "📋 *Date Wise Sales*",
    ]

    if not summary["products_by_date"]:
        lines.append("No Sale")
    else:
        for date_item in sorted(summary["products_by_date"].keys()):
            lines.append("")
            lines.append(dt.strptime(date_item, "%Y-%m-%d").strftime("%d %B %Y"))
            lines.append(", ".join(summary["products_by_date"][date_item]))

    lines.append("")
    lines.append("Use /statuspdf to print this month PDF.")

    update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
        "📁 *EMPLOYEE HISTORY*\n",
        f'*Employee:* {employee["name"]}',
        f'*Mall:* {employee["mall_name"]}',
        f'*Store:* {employee["store_name"]}',
        "",
    ]

    sales_map = {
        x["date"]: x for x in sales if str(x.get("telegram_id")) == str(employee["telegram_id"])
    }

    found = False

    for row in attendance:
        if str(row["telegram_id"]) == str(employee["telegram_id"]):
            found = True
            pretty = dt.strptime(row["date"], "%Y-%m-%d").strftime("%d %B %Y")
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

    lines.append("Use /historypdf Full Name to print PDF.")
    update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def cancel(update: Update, context: CallbackContext):
    context.user_data.clear()
    update.message.reply_text(
        "Cancelled.",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
    )
    return ConversationHandler.END


def check_absence(context: CallbackContext):
    attendance = get_attendance()
    employees = get_employees()
    state = get_absence_state()

    state_map = {str(item.get("telegram_id")): item for item in state}
    today = now_local().date()

    for emp in employees:
        user_id = str(emp.get("telegram_id"))
        last_date = None

        for row in attendance:
            if str(row.get("telegram_id")) == user_id and row.get("login_time"):
                row_date = dt.strptime(row["date"], "%Y-%m-%d").date()
                if last_date is None or row_date > last_date:
                    last_date = row_date

        if not last_date:
            continue

        absent_days = (today - last_date).days
        record = state_map.get(user_id, {"telegram_id": int(user_id), "days_absent": 0, "alert_sent": False})
        record["days_absent"] = absent_days

        if absent_days >= 2:
            staff_msg = (
                "⚠️ *ATTENDANCE REMINDER*\n\n"
                f'Dear *{emp["name"]}*,\n\n'
                f"You are absent for *{absent_days} days*.\n\n"
                "Please report to your manager and explain your absence.\n\n"
                "Thank you."
            )
            admin_msg = (
                "🚨 *ATTENDANCE ALERT*\n\n"
                f'👤 *Employee:* {emp["name"]}\n'
                f'📍 *Mall:* {emp["mall_name"]}\n'
                f'🏬 *Store:* {emp["store_name"]}\n\n'
                f'❗ *Absent for {absent_days} days*\n\n'
                "Please follow up with the staff member."
            )
            try:
                send_markdown(context.bot, int(user_id), staff_msg)
                for admin_id in ADMIN_IDS:
                    send_markdown(context.bot, admin_id, admin_msg)
                record["alert_sent"] = True
            except Exception:
                logging.exception("Failed to send absence alert")
        else:
            record["alert_sent"] = False

        state_map[user_id] = record

    save_absence_state(list(state_map.values()))


def is_last_day_of_month(value):
    tomorrow = value + datetime.timedelta(days=1)
    return tomorrow.month != value.month


def send_monthly_reports(context: CallbackContext):
    today = now_local().date()
    if not is_last_day_of_month(today):
        return

    employees = get_employees()
    month_text = now_local().strftime("%B %Y")

    for emp in employees:
        summary = user_month_summary(emp["telegram_id"])
        msg = (
            "📊 *MONTHLY REPORT*\n"
            f'📅 *{month_text}*\n\n'
            f'👤 *Name:* {emp["name"]}\n'
            f'📍 *Mall:* {emp["mall_name"]}\n'
            f'🏬 *Store:* {emp["store_name"]}\n\n'
            f'*Working Days:* {summary["working_days"]}\n'
            f'*Working Hours:* {seconds_to_hms(summary["total_seconds"])}\n'
            f'*Total Sales:* {summary["total_sale_value"]}\n'
            f'*Total Quantity:* {summary["total_qty"]}\n'
            f'*Total Break Time:* {seconds_to_hms(summary["total_break"])}'
        )
        try:
            send_markdown(context.bot, emp["telegram_id"], msg)
        except Exception:
            logging.exception("Failed to send monthly report")


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
    dp.add_handler(MessageHandler(Filters.regex("^Break Start$"), break_start))
    dp.add_handler(MessageHandler(Filters.regex("^Break End$"), break_end))
    dp.add_handler(CommandHandler("statuspdf", send_user_monthly_pdf))
    dp.add_handler(CommandHandler("history", admin_history))
    dp.add_handler(CommandHandler("historypdf", send_admin_history_pdf))
    dp.add_handler(MessageHandler(Filters.regex(r"^/approve_\d+$"), approve_dynamic))
    dp.add_handler(MessageHandler(Filters.regex(r"^/reject_\d+$"), reject_dynamic))

    updater.job_queue.run_daily(check_absence, time=dtime(hour=10, minute=0))
    updater.job_queue.run_monthly(send_monthly_reports, when=dtime(hour=18, minute=0), day=28)

    updater.start_polling(drop_pending_updates=True)
    updater.idle()


if __name__ == "__main__":
    main()
