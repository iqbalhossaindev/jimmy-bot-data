# FINAL main.py (Signup fixed for staff)

import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    ConversationHandler, CallbackContext
)
from config import BOT_TOKEN, ADMIN_IDS, PRIVATE_GROUP_ID
import json, os

logging.basicConfig(level=logging.INFO)

(
    ROLE,
    REG_NAME, REG_MALL, REG_STORE, REG_CONFIRM,
    LOGIN, LOGOUT,
    CLIENTS_ATTENDED, CLIENTS_CONVERTED,
    ADMIN_PANEL, PRODUCT_PANEL, DEL_PRODUCT
) = range(12)

# ---------- FILE HELPERS ----------
def load_json(file):
    if not os.path.exists(file):
        return {}
    with open(file, "r") as f:
        try:
            return json.load(f)
        except:
            return {}

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

def load_products():
    data = load_json("products.json")
    return data if isinstance(data, list) else []

def save_products(data):
    save_json("products.json", data)

# ---------- START ----------
def start(update: Update, context: CallbackContext):
    keyboard = [["1. Sales Employee"], ["2. Management"]]
    update.message.reply_text(
        "Welcome to JIMMY\n\nPlease choose your role:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return ROLE

# ---------- ROLE ----------
def role(update: Update, context: CallbackContext):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    if text.startswith("1"):
        update.message.reply_text("What is your name?")
        return REG_NAME

    if text.startswith("2"):
        if user_id not in ADMIN_IDS:
            update.message.reply_text("No problem, let's try again.")
            return start(update, context)
        return admin_panel(update, context)

    return ROLE

# ---------- REGISTRATION ----------
def reg_name(update: Update, context: CallbackContext):
    context.user_data["name"] = update.message.text.strip()
    update.message.reply_text("Which mall are you working now?")
    return REG_MALL

def reg_mall(update: Update, context: CallbackContext):
    context.user_data["mall"] = update.message.text.strip()
    update.message.reply_text("What is your shop name?")
    return REG_STORE

def reg_store(update: Update, context: CallbackContext):
    context.user_data["store"] = update.message.text.strip()

    msg = (
        "Please confirm your details:\n\n"
        f"Name: {context.user_data['name']}\n"
        f"Mall Name: {context.user_data['mall']}\n"
        f"Store: {context.user_data['store']}\n\n"
        "Are you confirm?"
    )
    update.message.reply_text(
        msg,
        reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], resize_keyboard=True)
    )
    return REG_CONFIRM

def reg_confirm(update: Update, context: CallbackContext):
    ans = (update.message.text or "").strip().lower()

    if ans != "yes":
        update.message.reply_text("No problem, let's try again.\n\nWhat is your name?")
        return REG_NAME

    user = update.effective_user
    employees = load_json("employees.json")

    employees[str(user.id)] = {
        "name": context.user_data.get("name", ""),
        "mall": context.user_data.get("mall", ""),
        "store": context.user_data.get("store", "")
    }
    save_json("employees.json", employees)

    # IMPORTANT FIX: directly show Login button after successful signup
    update.message.reply_text(
        "✅ Registration completed.\n\nPlease press Login.",
        reply_markup=ReplyKeyboardMarkup([["Login"]], resize_keyboard=True)
    )
    return LOGIN

# ---------- LOGIN ----------
def login(update: Update, context: CallbackContext):
    update.message.reply_text(
        "You are logged in.",
        reply_markup=ReplyKeyboardMarkup([["Logout"]], resize_keyboard=True)
    )
    return LOGOUT

# ---------- LOGOUT FLOW ----------
def logout(update: Update, context: CallbackContext):
    update.message.reply_text("How many clients did you attend today?")
    return CLIENTS_ATTENDED

def clients_attended(update: Update, context: CallbackContext):
    try:
        context.user_data["attended"] = int(update.message.text.strip())
    except:
        update.message.reply_text("Enter a valid number")
        return CLIENTS_ATTENDED

    update.message.reply_text("How many clients converted?")
    return CLIENTS_CONVERTED

def clients_converted(update: Update, context: CallbackContext):
    try:
        context.user_data["converted"] = int(update.message.text.strip())
    except:
        update.message.reply_text("Enter a valid number")
        return CLIENTS_CONVERTED

    user = update.effective_user
    emp = load_json("employees.json").get(str(user.id), {})

    msg = (
        "📋 DAILY REPORT\n\n"
        f"{emp.get('name','')}\n"
        f"{emp.get('mall','')} | {emp.get('store','')}\n\n"
        f"Clients: {context.user_data['attended']}\n"
        f"Converted: {context.user_data['converted']}"
    )

    context.bot.send_message(chat_id=PRIVATE_GROUP_ID, text=msg)
    update.message.reply_text("Logout completed.")
    return ConversationHandler.END

# ---------- ADMIN ----------
def admin_panel(update: Update, context: CallbackContext):
    keyboard = [["📦 Products"], ["⬅️ Back"]]
    update.message.reply_text(
        "Management Panel",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return ADMIN_PANEL

# ---------- PRODUCTS ----------
def product_panel(update: Update, context: CallbackContext):
    keyboard = [
        ["📋 List Products"],
        ["❌ Delist Product"],
        ["⬅️ Back"]
    ]
    update.message.reply_text(
        "Product Panel",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return PRODUCT_PANEL

def list_products(update: Update, context: CallbackContext):
    products = load_products()
    if not products:
        update.message.reply_text("No products available.")
        return
    msg = "📦 Product List\n\n" + "\n".join([f"• {p}" for p in products])
    update.message.reply_text(msg)

def del_product_start(update: Update, context: CallbackContext):
    update.message.reply_text("Enter product name to delete:")
    return DEL_PRODUCT

def del_product_confirm(update: Update, context: CallbackContext):
    name = update.message.text.strip()
    products = load_products()

    if name not in products:
        update.message.reply_text("Product not found.")
        return ConversationHandler.END

    products.remove(name)
    save_products(products)

    update.message.reply_text("Deleted successfully.")
    return ConversationHandler.END

# ---------- MAIN ----------
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ROLE: [MessageHandler(Filters.text & ~Filters.command, role)],

            REG_NAME: [MessageHandler(Filters.text & ~Filters.command, reg_name)],
            REG_MALL: [MessageHandler(Filters.text & ~Filters.command, reg_mall)],
            REG_STORE: [MessageHandler(Filters.text & ~Filters.command, reg_store)],
            REG_CONFIRM: [MessageHandler(Filters.text & ~Filters.command, reg_confirm)],

            LOGIN: [MessageHandler(Filters.regex("^(Login)$"), login)],
            LOGOUT: [MessageHandler(Filters.regex("^(Logout)$"), logout)],

            CLIENTS_ATTENDED: [MessageHandler(Filters.text & ~Filters.command, clients_attended)],
            CLIENTS_CONVERTED: [MessageHandler(Filters.text & ~Filters.command, clients_converted)],

            ADMIN_PANEL: [
                MessageHandler(Filters.regex("^📦 Products$"), product_panel),
                MessageHandler(Filters.regex("^⬅️ Back$"), start),
            ],

            PRODUCT_PANEL: [
                MessageHandler(Filters.regex("^📋 List Products$"), list_products),
                MessageHandler(Filters.regex("^❌ Delist Product$"), del_product_start),
                MessageHandler(Filters.regex("^⬅️ Back$"), admin_panel),
            ],

            DEL_PRODUCT: [MessageHandler(Filters.text & ~Filters.command, del_product_confirm)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    dp.add_handler(conv)

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
