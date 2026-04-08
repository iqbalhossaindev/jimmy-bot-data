
import logging
import os
import threading
import tempfile
from collections import defaultdict
from datetime import datetime, time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytz
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    ConversationHandler,
    Filters,
    MessageHandler,
    Updater,
)

from config import (
    ADMIN_IDS,
    BOT_TOKEN,
    BRAND_NAME,
    BREAK_MENU,
    CONFIRM_RETRY_MENU,
    LOGOUT_MENU,
    MAIN_MENU,
    PRIVATE_GROUP_ID,
    STATUS_MENU,
    TIMEZONE,
    YES_NO_MENU,
)
from github_storage import GitHubStorage

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

storage = GitHubStorage()
tz = pytz.timezone(TIMEZONE)

REG_NAME, REG_MALL, REG_STORE, REG_CONFIRM = range(4)
LOGOUT_CUSTOMERS, LOGOUT_PRODUCT, LOGOUT_QTY, LOGOUT_VALUE, LOGOUT_CONFIRM = range(10, 15)
BREAK_PROBLEM = 20

ATTENDANCE_FIELDS = [
    "telegram_id", "name", "mall_name", "store_name", "date",
    "login_time", "logout_time", "work_seconds"
]

def md_escape(text):
    text = str(text)
    for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, '\\' + ch)
    return text

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
    total_seconds = int(total_seconds or 0)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h}:{m:02d}:{s:02d}"

def month_key(dt=None):
    dt = dt or now_local()
    return dt.strftime("%Y-%m")

def month_label(dt=None):
    dt = dt or now_local()
    return dt.strftime("%B %Y")

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

def get_breaks():
    return storage.read_json("break.json", [])

def save_breaks(data):
    storage.write_json("break.json", data, "Update break.json")

def get_attendance():
    return storage.read_csv_rows("attendance.csv")

def save_attendance(rows):
    storage.write_csv_rows("attendance.csv", ATTENDANCE_FIELDS, rows, "Update attendance.csv")

def find_employee(telegram_id):
    for item in get_employees():
        if str(item.get("telegram_id")) == str(telegram_id) and item.get("status") == "approved":
            return item
    return None

def get_pending_record(telegram_id):
    for item in get_pending():
        if str(item.get("telegram_id")) == str(telegram_id):
            return item
    return None

def get_today_attendance_row(telegram_id):
    today = date_str()
    for row in get_attendance():
        if str(row.get("telegram_id")) == str(telegram_id) and row.get("date") == today:
            return row
    return None

def user_month_summary(telegram_id):
    month = month_key()
    attendance = get_attendance()
    sales = get_sales()
    breaks = get_breaks()

    working_days = 0
    total_seconds = 0
    total_sale_value = 0
    total_qty = 0
    total_break = 0
    products_by_date = defaultdict(list)
    sales_by_date = {}
    attendance_map = {}

    for row in attendance:
        if str(row.get("telegram_id")) == str(telegram_id) and str(row.get("date", "")).startswith(month):
            attendance_map[row["date"]] = row
            if row.get("login_time"):
                working_days += 1
            try:
                total_seconds += int(float(row.get("work_seconds") or 0))
            except Exception:
                pass

    for entry in sales:
        if str(entry.get("telegram_id")) == str(telegram_id) and str(entry.get("date", "")).startswith(month):
            total_sale_value += int(entry.get("total_value", 0))
            total_qty += int(entry.get("total_qty", 0))
            sales_by_date[entry["date"]] = entry
            for item in entry.get("items", []):
                if item.get("product"):
                    products_by_date[entry["date"]].append(item["product"])

    for b in breaks:
        if str(b.get("telegram_id")) == str(telegram_id) and str(b.get("date", "")).startswith(month):
            total_break += int(b.get("seconds") or 0)

    return {
        "working_days": working_days,
        "total_seconds": total_seconds,
        "total_sale_value": total_sale_value,
        "total_qty": total_qty,
        "total_break": total_break,
        "products_by_date": dict(products_by_date),
        "sales_by_date": sales_by_date,
        "attendance_map": attendance_map,
    }

def user_today_break_used(telegram_id):
    total = 0
    today = date_str()
    for b in get_breaks():
        if str(b.get("telegram_id")) == str(telegram_id) and b.get("date") == today:
            total += int(b.get("seconds") or 0)
    return total

def send_md(bot, chat_id, text, **kwargs):
    return bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2", **kwargs)

def format_login_report(employee, dt, working_days):
    return (
        "✅ *LOGIN*\n\n"
        f"👤 *Name:* {md_escape(employee['name'])}\n"
        f"📍 *Mall:* {md_escape(employee['mall_name'])}\n"
        f"🏬 *Store:* {md_escape(employee['store_name'])}\n\n"
        f"🕒 *Time:* {md_escape(dt.strftime('%H:%M:%S'))}\n"
        f"📅 *Date:* {md_escape(pretty_date(dt))}\n\n"
        f"📊 *This Month Working Days:* {working_days}"
    )

def build_logout_text(employee, report, today_hours_text):
    lines = []
    lines.append("🚪 *LOGOUT*")
    lines.append("")
    lines.append(f"👤 *Name:* {md_escape(employee['name'])}")
    lines.append(f"📍 *Mall:* {md_escape(employee['mall_name'])}")
    lines.append(f"🏬 *Store:* {md_escape(employee['store_name'])}")
    lines.append("")
    lines.append(f"🕒 *Time:* {md_escape(report['logout_time'])}")
    lines.append(f"📅 *Date:* {md_escape(report['pretty_date'])}")
    lines.append("")
    lines.append(f"⏱ *Working Hours Today:* {md_escape(today_hours_text)}")
    lines.append(f"📊 *This Month Working Days:* {report['month_working_days']}")
    lines.append("")
    lines.append("────────────")
    lines.append("")
    lines.append("📦 *DSR REPORT*")
    lines.append(f"📅 *Date:* {md_escape(report['dsr_date'])}")
    lines.append(f"🏬 *Location:* {md_escape(employee['store_name'])} \\- {md_escape(employee['mall_name'])}")
    lines.append("")
    lines.append(f"👥 *Customers Attended:* {report['customers_attend']}")
    lines.append("")
    lines.append("*Sales Details*")
    for item in report["items"]:
        lines.append(f"• *{md_escape(item['product'])}*")
        lines.append(f"  Qty: {item['qty']} \\| Price: {item['price']} \\| Total: {item['total']}")
    if not report["items"]:
        lines.append("• *No Sale*")
    lines.append("")
    lines.append(f"📦 *Total Quantity:* {report['total_qty']}")
    lines.append(f"💰 *Total Value:* {report['total_value']}")
    return "\n".join(lines)

def build_status_text(employee, summary, today_row, today_break):
    lines = [
        "📊 *STATUS*",
        "",
        f"👤 *Name:* {md_escape(employee['name'])}",
        f"📍 *Mall:* {md_escape(employee['mall_name'])}",
        f"🏬 *Store:* {md_escape(employee['store_name'])}",
        "",
    ]
    if today_row and today_row.get("login_time") and not today_row.get("logout_time"):
        lines.append("🟢 *Today Status:* Logged In")
        lines.append(f"🕒 *Login Time:* {md_escape(today_row['login_time'])}")
        lines.append("")
    elif today_row and today_row.get("logout_time"):
        lines.append("⚪ *Today Status:* Logged Out")
        lines.append(f"🕒 *Login Time:* {md_escape(today_row['login_time'])}")
        lines.append(f"🕒 *Logout Time:* {md_escape(today_row['logout_time'])}")
        lines.append("")
    else:
        lines.append("🔴 *Today Status:* Not Logged In")
        lines.append("")
    lines += [
        f"📅 *Working Days:* {summary['working_days']}",
        f"⏱ *Total Hours:* {md_escape(seconds_to_hms(summary['total_seconds']))}",
        f"💰 *Total Sales:* {summary['total_sale_value']}",
        f"📦 *Total Quantity:* {summary['total_qty']}",
        f"☕ *Total Break Time:* {md_escape(seconds_to_hms(summary['total_break']))}",
        f"☕ *Break Used Today:* {md_escape(seconds_to_hms(today_break))}",
        "",
        "*Date Wise Product Sales*",
    ]
    if not summary["products_by_date"]:
        lines.append("• *No Sale*")
    else:
        for d in sorted(summary["products_by_date"].keys()):
            pretty = datetime.strptime(d, "%Y-%m-%d").strftime("%d %B %Y")
            lines.append("")
            lines.append(f"*{md_escape(pretty)}*")
            for product in summary["products_by_date"][d]:
                lines.append(f"• {md_escape(product)}")
    return "\n".join(lines)

def admin_employee_label(emp):
    first_name = emp.get("name", "").split()[0] if emp.get("name") else "Unknown"
    return f"{first_name} | {emp.get('mall_name','')} | {emp.get('store_name','')}"

def send_group_join_link(bot, user_id):
    try:
        link = bot.export_chat_invite_link(chat_id=PRIVATE_GROUP_ID)
        send_md(
            bot,
            user_id,
            "🎉 *REGISTRATION APPROVED*\n\nPlease join the private group using this link:\n\n" + md_escape(link),
        )
    except Exception as e:
        logging.exception("Failed to send group join link: %s", e)
        send_md(
            bot,
            user_id,
            "⚠️ *NOTICE*\n\nYour registration is approved, but the group link could not be created\\. Please contact admin\\."
        )

def create_monthly_timesheet_pdf(employee, summary):
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)

    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    margin = 20 * mm
    y = height - margin

    logo_candidates = ["logo.png", "logo.jpg", "logo.jpeg"]
    for logo in logo_candidates:
        if os.path.exists(logo):
            try:
                c.drawImage(logo, width/2 - 30*mm, y - 20*mm, width=60*mm, height=20*mm, preserveAspectRatio=True, mask='auto')
                y -= 25 * mm
                break
            except Exception:
                pass

    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width/2, y, "EMPLOYEE TIMESHEET")
    y -= 12 * mm

    def box(x, y0, w, h, text, bold=False):
        c.rect(x, y0-h, w, h)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9)
        c.drawString(x + 3*mm, y0 - h + 5*mm, text)

    info_h = 8 * mm
    left = margin
    right = width - margin
    col1, col2, col3, col4 = 30*mm, 70*mm, 28*mm, right - left - 30*mm - 70*mm - 28*mm

    box(left, y, col1, info_h, "Name")
    box(left + col1, y, col2, info_h, employee["name"])
    box(left + col1 + col2, y, col3, info_h, "Month")
    box(left + col1 + col2 + col3, y, col4, info_h, month_label())

    y -= info_h
    location = f"{employee['store_name']} - {employee['mall_name']}"
    box(left, y, col1, info_h, "Brand")
    box(left + col1, y, col2, info_h, BRAND_NAME)
    box(left + col1 + col2, y, col3, info_h, "Location")
    box(left + col1 + col2 + col3, y, col4, info_h, location)

    y -= 14 * mm

    cols = [
        ("Date", 28*mm),
        ("Time In", 22*mm),
        ("Time Out", 22*mm),
        ("Break", 18*mm),
        ("Work Hours", 22*mm),
        ("Sales Qty", 18*mm),
        ("Sales Value", 24*mm),
    ]
    x = left
    row_h = 8 * mm
    c.setFillColor(colors.HexColor("#0B3D91"))
    for title, w in cols:
        c.rect(x, y-row_h, w, row_h, fill=1, stroke=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x+2*mm, y-row_h+3*mm, title)
        c.setFillColor(colors.black)
        x += w
    y -= row_h

    month = month_key()
    days = sorted(set(list(summary["attendance_map"].keys()) + list(summary["sales_by_date"].keys())))
    if not days:
        days = []
    month_breaks = get_breaks()
    for d in days:
        x = left
        att = summary["attendance_map"].get(d)
        sale = summary["sales_by_date"].get(d)
        break_seconds = sum(
            int(b.get("seconds") or 0)
            for b in month_breaks
            if str(b.get("telegram_id")) == str(employee["telegram_id"]) and b.get("date") == d
        )
        login_time = att.get("login_time", "") if att else ""
        logout_time = att.get("logout_time", "") if att else ""
        work_h = seconds_to_hms(att.get("work_seconds", 0)) if att else ""
        sale_qty = str(sale.get("total_qty", 0)) if sale else "0"
        sale_val = str(sale.get("total_value", 0)) if sale else "0"

        off_day = not login_time and not logout_time
        cells = [
            datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m/%Y"),
            "OFF" if off_day else login_time,
            "OFF" if off_day else logout_time,
            "" if off_day else seconds_to_hms(break_seconds),
            "" if off_day else work_h,
            "" if off_day else sale_qty,
            "" if off_day else sale_val,
        ]
        fill = colors.HexColor("#F6E58D") if off_day else colors.white

        for (title, w), value in zip(cols, cells):
            c.setFillColor(fill)
            c.rect(x, y-row_h, w, row_h, fill=1, stroke=1)
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold" if off_day else "Helvetica", 8.5)
            c.drawString(x+2*mm, y-row_h+3*mm, str(value))
            x += w
        y -= row_h
        if y < 40*mm:
            c.showPage()
            y = height - margin

    y -= 8*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "MONTH SUMMARY")
    y -= 6*mm
    c.setFont("Helvetica", 9)
    summary_lines = [
        f"Total Working Days: {summary['working_days']}",
        f"Total Working Hours: {seconds_to_hms(summary['total_seconds'])}",
        f"Total Break Time: {seconds_to_hms(summary['total_break'])}",
        f"Total Sales Quantity: {summary['total_qty']}",
        f"Total Sales Value: {summary['total_sale_value']}",
    ]
    for line in summary_lines:
        c.drawString(left, y, line)
        y -= 5*mm

    c.save()
    return path

def create_simple_report_pdf(title, employee, lines):
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    margin = 20 * mm
    y = height - margin

    logo_candidates = ["logo.png", "logo.jpg", "logo.jpeg"]
    for logo in logo_candidates:
        if os.path.exists(logo):
            try:
                c.drawImage(logo, width/2 - 25*mm, y - 18*mm, width=50*mm, height=18*mm, preserveAspectRatio=True, mask='auto')
                y -= 22*mm
                break
            except Exception:
                pass

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, y, title)
    y -= 12*mm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Employee Information")
    y -= 6*mm

    c.setFont("Helvetica", 9)
    info = [
        f"Name: {employee['name']}",
        f"Mall: {employee['mall_name']}",
        f"Store: {employee['store_name']}",
        f"Generated: {pretty_date()} {time_str()}",
    ]
    for row in info:
        c.drawString(margin, y, row)
        y -= 5*mm

    y -= 3*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Report Details")
    y -= 6*mm
    c.setFont("Helvetica", 9)
    for line in lines:
        c.drawString(margin, y, str(line))
        y -= 5*mm
        if y < 25*mm:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica", 9)

    c.save()
    return path

def check_absence(context: CallbackContext):
    employees = [e for e in get_employees() if e.get("status") == "approved"]
    attendance = get_attendance()
    today = now_local().date()

    for emp in employees:
        dates = []
        for row in attendance:
            if str(row.get("telegram_id")) == str(emp["telegram_id"]) and row.get("login_time"):
                try:
                    dates.append(datetime.strptime(row["date"], "%Y-%m-%d").date())
                except Exception:
                    pass
        if not dates:
            continue
        last_date = max(dates)
        diff = (today - last_date).days
        if diff >= 2:
            staff_msg = (
                "⚠️ *ATTENDANCE REMINDER*\n\n"
                f"Dear *{md_escape(emp['name'])}*,\n\n"
                f"Our record shows that you have not attended work for the last *{diff} days*\\.\n\n"
                "Please report to your manager and explain the reason for your absence\\.\n\n"
                "Thank you\\."
            )
            admin_msg = (
                "🚨 *ATTENDANCE ALERT*\n\n"
                f"👤 *Employee:* {md_escape(emp['name'])}\n"
                f"📍 *Mall:* {md_escape(emp['mall_name'])}\n"
                f"🏬 *Store:* {md_escape(emp['store_name'])}\n\n"
                f"❗ *Absent for {diff} consecutive days*\n\n"
                "Please follow up with the staff member\\."
            )
            try:
                send_md(context.bot, int(emp["telegram_id"]), staff_msg)
            except Exception:
                logging.exception("Failed to send staff absence warning")
            for admin_id in ADMIN_IDS:
                try:
                    send_md(context.bot, admin_id, admin_msg)
                except Exception:
                    logging.exception("Failed to send admin absence alert")

def send_monthly_reports(context: CallbackContext):
    for emp in [e for e in get_employees() if e.get("status") == "approved"]:
        summary = user_month_summary(emp["telegram_id"])
        message = (
            "📊 *MONTHLY REPORT*\n"
            f"📅 *{md_escape(month_label())}*\n\n"
            f"👤 *Name:* {md_escape(emp['name'])}\n"
            f"📍 *Mall:* {md_escape(emp['mall_name'])}\n"
            f"🏬 *Store:* {md_escape(emp['store_name'])}\n\n"
            f"📅 *Working Days:* {summary['working_days']}\n"
            f"⏱ *Total Hours:* {md_escape(seconds_to_hms(summary['total_seconds']))}\n"
            f"💰 *Total Sales:* {summary['total_sale_value']}\n"
            f"📦 *Total Quantity:* {summary['total_qty']}\n"
            f"☕ *Total Break Time:* {md_escape(seconds_to_hms(summary['total_break']))}"
        )
        try:
            send_md(context.bot, int(emp["telegram_id"]), message)
        except Exception:
            logging.exception("Failed to send monthly report")

def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    employee = find_employee(user_id)
    if employee:
        kb = MAIN_MENU + ([["Admin Panel"]] if is_admin(user_id) else [])
        update.message.reply_text(
            "Welcome back to JIMMY.",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        )
        return ConversationHandler.END

    pending = get_pending_record(user_id)
    if pending:
        update.message.reply_text("Your registration is pending admin approval.")
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
        f"Name: {context.user_data['reg_name']}\n"
        f"Mall Name: {context.user_data['reg_mall']}\n"
        f"Store: {context.user_data['reg_store']}\n\n"
        "Are you confirm?"
    )
    update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(YES_NO_MENU, resize_keyboard=True))
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

    admin_text = (
        "🆕 *NEW REGISTRATION REQUEST*\n\n"
        f"👤 *Name:* {md_escape(record['name'])}\n"
        f"📍 *Mall:* {md_escape(record['mall_name'])}\n"
        f"🏬 *Store:* {md_escape(record['store_name'])}\n\n"
        f"Reply with:\n`/approve_{user.id}`\nor\n`/reject_{user.id}`"
    )
    for admin_id in ADMIN_IDS:
        try:
            send_md(context.bot, admin_id, admin_text)
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
        send_md(
            context.bot,
            user_id,
            "🎉 *REGISTRATION APPROVED*\n\nCongratulations\\!\nWelcome to JIMMY\\.",
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
        send_md(
            context.bot,
            user_id,
            "❌ *REGISTRATION REJECTED*\n\nYour application was not approved\\. Please contact admin\\.",
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
    send_md(context.bot, user_id, "💪 *WELCOME TO WORK*\n\nThanks for joining work\\. Please focus on sales\\. Best of luck\\.")
    send_md(context.bot, PRIVATE_GROUP_ID, report)
    send_md(context.bot, user_id, report)

def logout_start(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("You are not approved yet. Please complete signup first with /start")
        return ConversationHandler.END

    today_row = get_today_attendance_row(update.effective_user.id)
    if not today_row or not today_row.get("login_time"):
        update.message.reply_text("You did not login today.")
        return ConversationHandler.END
    if today_row.get("logout_time"):
        update.message.reply_text("You already logged out today.")
        return ConversationHandler.END

    context.user_data["logout_items"] = []
    context.user_data["logout_customers"] = 0
    context.user_data["state"] = "awaiting_customers"
    update.message.reply_text("How many customers attended today?")
    return LOGOUT_CUSTOMERS

def logout_customers(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    if text == "Reset":
        context.user_data.clear()
        update.message.reply_text("🔄 RESET\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS
    if not text.isdigit():
        update.message.reply_text("⚠️ Invalid input\nPlease enter numbers only.")
        return LOGOUT_CUSTOMERS

    context.user_data["logout_customers"] = int(text)
    context.user_data["state"] = "awaiting_product"
    update.message.reply_text(
        "Select product.",
        reply_markup=ReplyKeyboardMarkup(LOGOUT_MENU, resize_keyboard=True),
    )
    return LOGOUT_PRODUCT

def logout_product(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data.clear()
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("🔄 RESET\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if text == "Undo":
        items = context.user_data.get("logout_items", [])
        if items:
            items.pop()
            update.message.reply_text("↩️ Last item removed.")
        else:
            update.message.reply_text("⚠️ Nothing to undo.")
        return LOGOUT_PRODUCT

    if text == "Done":
        if not context.user_data.get("logout_items"):
            update.message.reply_text("⚠️ Please add at least one product before finishing, or press None.")
            return LOGOUT_PRODUCT
        return show_logout_summary(update, context)

    if text == "None":
        context.user_data["logout_items"] = []
        return show_logout_summary(update, context)

    valid_products = [row[0] for row in LOGOUT_MENU if len(row) == 1 and row[0] not in ("Done","None","Undo","Reset")]
    if text not in valid_products:
        update.message.reply_text("⚠️ Invalid input\nPlease select a product from the list.")
        return LOGOUT_PRODUCT

    context.user_data["current_product"] = text
    context.user_data["state"] = "awaiting_qty"
    update.message.reply_text(f"Enter quantity for {text}")
    return LOGOUT_QTY

def logout_qty(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data.clear()
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("🔄 RESET\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if not text.isdigit() or int(text) <= 0:
        update.message.reply_text("⚠️ Invalid input\nPlease enter a valid quantity (number only).")
        return LOGOUT_QTY

    context.user_data["current_qty"] = int(text)
    context.user_data["state"] = "awaiting_value"
    update.message.reply_text(f"Enter value for each product: {context.user_data['current_product']}")
    return LOGOUT_VALUE

def logout_value(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data.clear()
        context.user_data["logout_items"] = []
        context.user_data["logout_customers"] = 0
        update.message.reply_text("🔄 RESET\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if not text.isdigit() or int(text) < 0:
        update.message.reply_text("⚠️ Invalid input\nPlease enter a valid value (number only).")
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
        f"{context.user_data['current_product']}\nQty: {qty}\nPrice: {price}\nTotal: {price} x {qty} = {total}"
    )
    update.message.reply_text(
        "Select another product or press Done.",
        reply_markup=ReplyKeyboardMarkup(LOGOUT_MENU, resize_keyboard=True),
    )
    return LOGOUT_PRODUCT

def show_logout_summary(update: Update, context: CallbackContext):
    items = context.user_data.get("logout_items", [])
    total_qty = sum(item["qty"] for item in items)
    total_value = sum(item["total"] for item in items)

    lines = ["Today's Sales Summary", ""]
    if items:
        for item in items:
            lines.append(f"{item['product']}: {item['qty']} x {item['price']} = {item['total']}")
    else:
        lines.append("No Sale")
    lines += [
        "",
        f"Total Customers Attend: {context.user_data.get('logout_customers', 0)}",
        f"Total Qty: {total_qty}",
        f"Total Value: {total_value}",
        "",
        "Please confirm this report.",
    ]
    context.user_data["summary_total_qty"] = total_qty
    context.user_data["summary_total_value"] = total_value
    update.message.reply_text(
        "\n".join(lines),
        reply_markup=ReplyKeyboardMarkup(CONFIRM_RETRY_MENU, resize_keyboard=True),
    )
    return LOGOUT_CONFIRM

def logout_confirm(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text == "Reset":
        context.user_data.clear()
        update.message.reply_text("🔄 RESET\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if text == "Retry":
        context.user_data["logout_items"] = []
        update.message.reply_text("No problem. Let's try again.\n\nHow many customers attended today?")
        return LOGOUT_CUSTOMERS

    if text != "Confirm":
        update.message.reply_text("Please press Confirm, Retry, or Reset.")
        return LOGOUT_CONFIRM

    user_id = update.effective_user.id
    employee = find_employee(user_id)
    rows = get_attendance()
    today = date_str()
    logout_dt = now_local()
    work_seconds = 0

    for row in rows:
        if str(row["telegram_id"]) == str(user_id) and row["date"] == today and row.get("login_time"):
            login_dt = tz.localize(datetime.strptime(f"{today} {row['login_time']}", "%Y-%m-%d %H:%M:%S"))
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
    final_text = build_logout_text(employee, report, seconds_to_hms(work_seconds))

    update.message.reply_text("Logout successful.", reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True))
    send_md(context.bot, PRIVATE_GROUP_ID, final_text)
    send_md(context.bot, user_id, final_text)
    return ConversationHandler.END

def status(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("You are not approved yet. Please complete signup first with /start")
        return
    summary = user_month_summary(update.effective_user.id)
    today_row = get_today_attendance_row(update.effective_user.id)
    today_break = user_today_break_used(update.effective_user.id)
    text = build_status_text(employee, summary, today_row, today_break)
    update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=ReplyKeyboardMarkup(STATUS_MENU, resize_keyboard=True))

def print_pdf(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("You are not approved yet.")
        return
    summary = user_month_summary(update.effective_user.id)
    path = create_monthly_timesheet_pdf(employee, summary)
    with open(path, "rb") as f:
        context.bot.send_document(chat_id=update.effective_user.id, document=f, filename=f"{employee['name']}_timesheet.pdf")
    try:
        os.remove(path)
    except Exception:
        pass

def break_menu(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("You are not approved yet.")
        return
    update.message.reply_text("Break options", reply_markup=ReplyKeyboardMarkup(BREAK_MENU, resize_keyboard=True))

def break_start(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("You are not approved.")
        return

    today_row = get_today_attendance_row(update.effective_user.id)
    if not today_row or not today_row.get("login_time"):
        update.message.reply_text("You must login first.")
        return

    breaks = get_breaks()
    today = date_str()
    for b in breaks:
        if str(b.get("telegram_id")) == str(update.effective_user.id) and b.get("date") == today and not b.get("end"):
            update.message.reply_text("⚠️ Break already started")
            return

    now = now_local()
    breaks.append({
        "telegram_id": update.effective_user.id,
        "date": today,
        "start": time_str(now),
        "end": None,
        "seconds": 0,
    })
    save_breaks(breaks)

    msg = (
        "☕ *BREAK STARTED*\n\n"
        f"👤 *Name:* {md_escape(employee['name'])}\n"
        f"📍 *Mall:* {md_escape(employee['mall_name'])}\n"
        f"🏬 *Store:* {md_escape(employee['store_name'])}\n\n"
        f"🕒 *Time:* {md_escape(time_str(now))}\n"
        f"📅 *Date:* {md_escape(pretty_date(now))}"
    )
    send_md(context.bot, update.effective_user.id, msg)
    send_md(context.bot, PRIVATE_GROUP_ID, msg)

def break_end(update: Update, context: CallbackContext):
    employee = find_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("You are not approved.")
        return

    breaks = get_breaks()
    today = date_str()
    now = now_local()

    for b in breaks:
        if str(b.get("telegram_id")) == str(update.effective_user.id) and b.get("date") == today and not b.get("end"):
            start_dt = tz.localize(datetime.strptime(f"{today} {b['start']}", "%Y-%m-%d %H:%M:%S"))
            seconds = int((now - start_dt).total_seconds())
            b["end"] = time_str(now)
            b["seconds"] = seconds
            save_breaks(breaks)

            msg = (
                "☕ *BREAK ENDED*\n\n"
                f"👤 *Name:* {md_escape(employee['name'])}\n"
                f"📍 *Mall:* {md_escape(employee['mall_name'])}\n"
                f"🏬 *Store:* {md_escape(employee['store_name'])}\n\n"
                f"🕒 *Time:* {md_escape(time_str(now))}\n"
                f"📅 *Date:* {md_escape(pretty_date(now))}\n\n"
                f"⏱ *Break Used Today:* {md_escape(seconds_to_hms(user_today_break_used(update.effective_user.id)))}"
            )
            send_md(context.bot, update.effective_user.id, msg)
            send_md(context.bot, PRIVATE_GROUP_ID, msg)
            return

    update.message.reply_text("⚠️ No active break found")

def back_to_main(update: Update, context: CallbackContext):
    kb = MAIN_MENU + ([["Admin Panel"]] if is_admin(update.effective_user.id) else [])
    update.message.reply_text("Back to main menu.", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

def admin_panel(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    update.message.reply_text("Admin panel", reply_markup=ReplyKeyboardMarkup([["Pending Requests", "Employees List"], ["Today Reports", "Back to Main Menu"]], resize_keyboard=True))

def admin_pending(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    pending = get_pending()
    if not pending:
        update.message.reply_text("No pending requests.")
        return
    lines = ["Pending Requests", ""]
    for p in pending:
        lines.append(f"• {admin_employee_label(p)}")
        lines.append(f"  /approve_{p['telegram_id']}  or  /reject_{p['telegram_id']}")
    update.message.reply_text("\n".join(lines))

def admin_employees(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    employees = [e for e in get_employees() if e.get("status") == "approved"]
    if not employees:
        update.message.reply_text("No employees found.")
        return
    lines = ["Employees List", ""]
    for emp in employees:
        lines.append(f"• {admin_employee_label(emp)}")
    update.message.reply_text("\n".join(lines))

def admin_today(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    today = date_str()
    rows = [r for r in get_attendance() if r.get("date") == today]
    if not rows:
        update.message.reply_text("No reports for today.")
        return
    lines = ["Today Reports", ""]
    for row in rows:
        name = row.get("name", "")
        status = "Logged Out" if row.get("logout_time") else "Logged In"
        lines.append(f"• {name} - {status}")
    update.message.reply_text("\n".join(lines))

def admin_history(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    query = " ".join(context.args).strip()
    if not query:
        update.message.reply_text("Usage: /history Full Name")
        return

    employee = None
    for item in get_employees():
        if item.get("name", "").lower() == query.lower():
            employee = item
            break
    if not employee:
        update.message.reply_text("Employee not found.")
        return

    attendance = get_attendance()
    sales = get_sales()
    lines = [
        f"History for: {employee['name']}",
        f"Mall: {employee['mall_name']}",
        f"Store: {employee['store_name']}",
        "",
    ]
    sales_map = {x["date"]: x for x in sales if str(x.get("telegram_id")) == str(employee["telegram_id"])}
    found = False
    for row in attendance:
        if str(row["telegram_id"]) == str(employee["telegram_id"]):
            found = True
            pretty = datetime.strptime(row["date"], "%Y-%m-%d").strftime("%d %B %Y")
            lines.append(pretty)
            lines.append(f"Login: {row.get('login_time') or '-'}")
            lines.append(f"Logout: {row.get('logout_time') or '-'}")
            lines.append(f"Work Hour: {seconds_to_hms(int(float(row.get('work_seconds') or 0)))}")
            sale = sales_map.get(row["date"])
            if sale:
                lines.append(f"Total Qty: {sale.get('total_qty', 0)}")
                lines.append(f"Total Value: {sale.get('total_value', 0)}")
            lines.append("")
    if not found:
        lines.append("No history found.")
    update.message.reply_text("\n".join(lines))

def admin_history_pdf(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    query = " ".join(context.args).strip()
    if not query:
        update.message.reply_text("Usage: /history_pdf Full Name")
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
    lines = [
        f"Working Days: {summary['working_days']}",
        f"Total Hours: {seconds_to_hms(summary['total_seconds'])}",
        f"Total Sales: {summary['total_sale_value']}",
        f"Total Quantity: {summary['total_qty']}",
        f"Total Break: {seconds_to_hms(summary['total_break'])}",
    ]
    path = create_simple_report_pdf("EMPLOYEE HISTORY REPORT", employee, lines)
    with open(path, "rb") as f:
        context.bot.send_document(chat_id=update.effective_user.id, document=f, filename=f"{employee['name']}_history.pdf")
    try:
        os.remove(path)
    except Exception:
        pass

def cancel(update: Update, context: CallbackContext):
    context.user_data.clear()
    update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True))
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
    dp.add_handler(CommandHandler("history", admin_history))
    dp.add_handler(CommandHandler("history_pdf", admin_history_pdf))
    dp.add_handler(MessageHandler(Filters.regex(r"^/approve_\d+$"), approve_dynamic))
    dp.add_handler(MessageHandler(Filters.regex(r"^/reject_\d+$"), reject_dynamic))

    dp.add_handler(MessageHandler(Filters.regex("^Login$"), login))
    dp.add_handler(MessageHandler(Filters.regex("^Status$"), status))
    dp.add_handler(MessageHandler(Filters.regex("^Print PDF$"), print_pdf))
    dp.add_handler(MessageHandler(Filters.regex("^Break$"), break_menu))
    dp.add_handler(MessageHandler(Filters.regex("^Break Start$"), break_start))
    dp.add_handler(MessageHandler(Filters.regex("^Break End$"), break_end))
    dp.add_handler(MessageHandler(Filters.regex("^Back$"), back_to_main))
    dp.add_handler(MessageHandler(Filters.regex("^Back to Main Menu$"), back_to_main))
    dp.add_handler(MessageHandler(Filters.regex("^Admin Panel$"), admin_panel))
    dp.add_handler(MessageHandler(Filters.regex("^Pending Requests$"), admin_pending))
    dp.add_handler(MessageHandler(Filters.regex("^Employees List$"), admin_employees))
    dp.add_handler(MessageHandler(Filters.regex("^Today Reports$"), admin_today))

    updater.job_queue.run_daily(check_absence, dtime(hour=10, minute=0, second=0))
    updater.job_queue.run_monthly(send_monthly_reports, when=dtime(hour=18, minute=0), day=-1)

    updater.start_polling(drop_pending_updates=True)
    updater.idle()

if __name__ == "__main__":
    main()
