import datetime
from email import message
import pytz
import os
import logging
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, filters
from google import genai
import asyncio

linebreak = "----------------------------------------"

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Load the hidden keys from your .env file
load_dotenv()
telegram_token = os.getenv("TELEGRAM_TOKEN")
gemini_key = os.getenv("GEMINI_API_KEY")

# GenAI API key here
client = genai.Client(api_key=gemini_key)

# List of authorized Telegram User IDs, loaded from .env (comma-separated)
# Use @userinfobot to get one's user ID
ALLOWED_USERS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USERS", "").split(",")
    if uid.strip()
]

#startup message
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("YO YO YO It's ya boy the one and only HORSEBOT🐴\nUse /help for a list of available commands.\n" \
    "Only authorized users can use this bot!")

#/help command, shows a list of available commands
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Available commands:\n/start - Start the bot\n" 
    "/hello - Say hello to the bot\n" 
    "/chat - Chat with the AI\n" 
    "/schedule - Schedule a reminder. Requires item,date and time in YYYY-MM-DD and HH:MM format\n"
    "/ai_sched - Schedule a reminder with AI parsing. No strict format required\n"
    "/help - Show this help message"
    )

#/hello command, echoes back the user's first name
async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f'Hello {update.effective_user.first_name}, I\'m a horse. 🐴')

#/chat command, sends the user's message to the AI and returns the response
#behaves like typical AI chatbot.
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = " ".join(context.args) #extracts the message from the command arguments

    if not message:
        await update.message.reply_text("Please provide a message to send to the AI.")
        return
    
    await update.message.reply_text("🐴 Horsing around...")
    await asyncio.sleep(1)
    try:
        chat = client.chats.create(model="gemini-flash-lite-latest")
        res = chat.send_message(message)
        await update.message.reply_text(res.text)
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")

#daily reminder function, sends a daily reminder to all authorized users
async def daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    for user_id in ALLOWED_USERS:
        await context.bot.send_message(chat_id=user_id, text="🐴 Scheduled tasks for today:")

#alarm module, sends a reminder to the user at the scheduled time
async def alarm(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(chat_id=job.chat_id, text=f"🐴 Reminder\n\n {job.data}")

#schedule the task using the /schedule command, takes in a message and a time in HH:MM format
#fallback if AI scheduling is down or does not work, requires strict formatting but is more likely to schedule correctly if the user follows the format
async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or len(context.args) < 3:
        await update.message.reply_text("Please provide a item to schedule in the following format:\n/schedule <item> <date> <time> <date in YYYY-MM-DD format> <time in HH:MM format>")
        return
    
    time = context.args[-1] #extracts the time from the command arguments
    date = context.args[-2] #extracts the date from the command arguments
    message = " ".join(context.args[:-2]) #extracts the message from the command arguments
    user_id = update.effective_user.id

    try: 

        naive_dt = datetime.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        sg_tz = pytz.timezone("Asia/Singapore")
        scheduled_time = sg_tz.localize(naive_dt)
        
        if scheduled_time < datetime.datetime.now(sg_tz):
            await update.message.reply_text("That exact date and time has already passed. Please choose a future time.")
            return
        
    except ValueError:
        await update.message.reply_text("Invalid date or time format. Please use YYYY-MM-DD for date and HH:MM for time.")
        return
    
    await update.message.reply_text(
                                    f"✅Scheduled {message} at {time} on {date}."
                                    f"\n\n"
                                    f"You will be reminded at the scheduled time."
                                    )
    context.job_queue.run_once(alarm, when=scheduled_time, chat_id=user_id, data=message)

#scheduling with AI parsing
async def ai_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = " ".join(context.args) #extracts the message from the command arguments
    user_id = update.effective_user.id

    if not message:
        await update.message.reply_text("Please provide a item to schedule with an item, time and date.")
        return
    
    try: 
        # Use AI to parse the message and extract the time
        sg_tz = pytz.timezone("Asia/Singapore")
        now = datetime.datetime.now(sg_tz)
        current_time_str = now.strftime("%Y-%m-%d %H:%M")
        
        #prompt demanding JSON output
        prompt = f"""
        You are a scheduling assistant. Extract the task and the exact target date/time from the user's message: "{message}"
        The current date and time is {current_time_str}.
        
        Rules:
        1. Extract the core task description (ignore the time/date words).
        2. Convert the requested time to a 24-hour format (YYYY-MM-DD HH:MM:SS).
        3. If no date is specified, assume today (or tomorrow if the requested time has already passed today).
        4. If no time is specified, default to 09:00:00.
        
        Return ONLY a valid JSON object matching this exact structure. Do not include markdown blocks or any other text:
        {{"task": "clean room", "target_datetime": "YYYY-MM-DD HH:MM:SS"}}
        """
        chat = client.chats.create(model="gemini-flash-lite-latest")
        res = chat.send_message(prompt)

        #clean data and parse JSON
        await update.message.reply_text("Parsing with Gemini...")
        text = res.text.strip().replace("```json", "").replace("```", "").strip()  # Clean up any markdown formatting
        data = json.loads(text)

        item = data.get("task")
        datetime_str = data.get("target_datetime")
        
        # Convert the extracted datetime string to a datetime object
        scheduled_time = datetime.datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
        actual_time = sg_tz.localize(scheduled_time)  # Localize timezone

        await update.message.reply_text(f""
                                        f"✅Scheduled {item} at {actual_time.strftime('%Y-%m-%d %H:%M:%S')} today.\n"
                                        f"\n\n"
                                        f"You will be reminded at the scheduled time.\n"
                                        f"AI may make mistakes, please double check the scheduled time and date. If it is wrong, please reschedule with a more specific time and date.")
        context.job_queue.run_once(alarm, when=actual_time, chat_id=user_id, data=item)

    except Exception as e:
        await update.message.reply_text("Something went wrong, please try again and be more specific. Make sure to include a time and a date for the AI to automatically format!")
        return

if __name__ == "__main__":
    #initialises the bot
    app = ApplicationBuilder().token(telegram_token).build()

    allowed_users = filters.User(user_id=ALLOWED_USERS)

    #basic commands that anyone can use
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command)) 

    #only allow authorized users for below commands
    #app.add_handler binds to slash commands, filters=allowed_users ensures only authorized users can use the command
    app.add_handler(CommandHandler("hello", hello, filters=allowed_users)) 
    app.add_handler(CommandHandler("chat", chat, filters=allowed_users))
    app.add_handler(CommandHandler("schedule", schedule, filters=allowed_users))
    app.add_handler(CommandHandler("ai_sched", ai_schedule, filters=allowed_users))

    #job queue for daily reminders
    job_queue = app.job_queue
    job_queue.run_daily(daily_reminder, time=datetime.time(hour=9, minute=0, second=0, tzinfo=pytz.timezone("Asia/Singapore")))  # Sends reminder at 9:00 AM every day
    
#run the bot
    print("Horsebot is up and running...")
    app.run_polling()
