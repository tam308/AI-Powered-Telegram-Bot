import datetime
import pytz
import os
import logging
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, filters, CallbackQueryHandler, MessageHandler
from google import genai
import asyncio
from types import SimpleNamespace


#job queue functions like a priority queue
#RESPOND to a message user typed like a slash command: await update.message.reply_text("")
#SEND a message using the bot: await context.bot.send_message(...)
#context.job_queue.jobs() returns a tuple of all scheduled jobs
#A single job object looks like this:
# Job(
#    data="Go to the gym",                  # message data
#    chat_id=1838348666,                    # user ID
#    next_t=datetime.datetime(2026, 6, 24, 15, 30, tzinfo=<UTC>), # The exact execution time
#    name="1838348666",                     # optional name (defaults to chat_id if not set)
#    removed=False                          # T/F tracking if the job was cancelled
#)


linebreak = "----------------------------------------"

#timezone used throughout the bot for scheduling and display
sg_tz = pytz.timezone("Asia/Singapore")

#day month year modified display format
DISPLAY_DATE = "%d %B %Y"             # 24 June 2026
DISPLAY_DATETIME = "%d %B %Y, %H:%M"  # 24 June 2026, 15:30

#task persistence
TASKS_FILE = "tasks.json"

#read the saved tasks back into a list; empty list if the file is missing or corrupt
def load_tasks():
    try:
        with open(TASKS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

#overwrite the file with the current list of tasks
def save_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, indent=2)

#append one task and persist
def add_task(user_id, item, time_str):
    tasks = load_tasks()
    tasks.append({"user_id": user_id, "item": item, "time": time_str})
    save_tasks(tasks)

#remove a task matching this user + item from the list and persist
def remove_task(user_id, item):
    tasks = [t for t in load_tasks() if not (t["user_id"] == user_id and t["item"] == item)]
    save_tasks(tasks)

#makes sure task names are unique for a given user, appending " (1)", " (2)", etc. if necessary
def unique_item_name(job_queue, user_id, item):
    existing = {job.data for job in job_queue.jobs() if job.chat_id == user_id}
    if item not in existing:
        return item
    n = 1
    while f"{item} ({n})" in existing:
        n += 1
    return f"{item} ({n})"

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
     #buttons for bot navigation appear after starting the bot.
    keyboard = [
        [InlineKeyboardButton("🙋 Hello", callback_data='hello'), InlineKeyboardButton("💬 Chat", callback_data='chat')],
        [InlineKeyboardButton("🗓️ Schedule", callback_data='schedule'), InlineKeyboardButton("📋 List Tasks", callback_data='list_tasks')],
        [InlineKeyboardButton("🗑️ Delete Task", callback_data='delete_helper'), InlineKeyboardButton("❓ Help", callback_data='help')],
        [InlineKeyboardButton("🌟 AI Schedule", callback_data='ai_schedule')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("YO YO YO It's ya boy the one and only HORSEBOT🐴\n" \
    "Only authorized users can use this bot!\nChoose an option below or type a slash command.", reply_markup = reply_markup)
   
#response to the buttons that require input
PROMPTS = {
    'chat': "🐴 Type your AI prompt.",
    'schedule': "🐴 Type your task in the following format:\n<item> <YYYY-MM-DD> <HH:MM>",
    'ai_schedule': "🐴 What would you like to schedule? Data is automatically parsed using AI.",
}

#function to handle all button presses
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # Acknowledge the callback query

    #only authorized users may drive the buttons (CallbackQueryHandler has no filter)
    if update.effective_user.id not in ALLOWED_USERS:
        await query.message.reply_text("🐴 You are not authorized to use this bot.")
        return

    #buttons that run straight away (no extra text needed from the user)
    immediate = {
        'help': help_command,
        'hello': hello,
        'list_tasks': list_tasks,
        'delete_helper': delete_helper,  # shows a numbered list, then waits for a number reply
    }

    if query.data in immediate:
        #build a minimal stand-in update so the original functions can use
        #update.message.reply_text() and update.effective_user as usual
        fake_update = SimpleNamespace(
            message=query.message,
            effective_user=update.effective_user,
        )
        await immediate[query.data](fake_update, context)
        await start(fake_update, context)  # return to the start menu when done
    elif query.data in PROMPTS:
        #remember which command we're collecting input for, then ask for it
        context.user_data['awaiting'] = query.data
        await query.message.reply_text(PROMPTS[query.data])

#explanation:

#catches the user's next text message after they press an input button, fakes
#context.args from that text, and hands off to the original command function.

#when you tap a prompt button, button_handler writes a note like context.user_data['awaiting'] = 'schedule'. .pop('awaiting', None) 
# does two things in one line:
#Reads the value of 'awaiting' (e.g. 'schedule') into the local variable awaiting.
#Deletes it from the dictionary.
#so if awaiting is none, the user is not in a prompt flow, and we just show the start menu again. If awaiting is not none, we look up the corresponding function in the handlers dictionary, and call it with a faked context.args built from the user's text message.
#if its in a flow, we call the original function with the faked context.args
#and we can run the functions we want

#so basically this function just bridges stuff between msg/button press to the actual functions
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.pop('awaiting', None)  # pop so it only fires once
    if awaiting is None:
        #default state: not in a prompt flow, so show the welcome message + buttons
        #happens if the user types text without pressing a button first, or after a command has finished
        await start(update, context)
        return

    handlers = {
        'chat': chat,
        'schedule': schedule,
        'ai_schedule': ai_schedule,
        'delete_main': delete_main,  # the number reply after delete_helper showed the list
    }
    func = handlers.get(awaiting)
    if func is None:
        return

    #the original functions read context.args, so populate it from the message text
    context.args = update.message.text.split()
    await func(update, context) #call the original function with the faked context.args
    await start(update, context)  # return to the start menu when done

#/help command, shows a list of available commands
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Available commands:\n/start - Start the bot\n" 
    "/hello - Say hello to the bot\n" 
    "/chat - Chat with the AI\n" 
    "/schedule - Schedule a reminder. Requires item, date and time in YYYY-MM-DD and HH:MM format\n"
    "/ai_schedule - Schedule a reminder with AI parsing, no strict format required\n"
    "/list - List all scheduled tasks\n"
    "/delete - Delete a scheduled task by picking its number from a list\n"
    "/help - Show this help message\n\n"
    "The bot can also be operated using the buttons after typing /start. Click on a button to execute the corresponding command."
    )

#/hello command, echoes back the user's first name
async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f'Hello {update.effective_user.first_name}, I\'m a horse. 🐴')

#/chat command, sends the user's message to the AI and returns the response
#behaves like typical AI chatbot.
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = " ".join(context.args) #extracts the message from the command arguments

    if not message:
        await update.message.reply_text("❗Please provide a message to send to the AI.")
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
        await context.bot.send_message(chat_id=user_id, text="🐴 Daily reminder of scheduled tasks!")
        await list_tasks(SimpleNamespace(message=SimpleNamespace(chat_id=user_id), effective_user=SimpleNamespace(id=user_id)), context)

#list all tasks in history sorted by date and time.
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    #fetch tasks from database, sorted by date and time
    jobs = context.job_queue.jobs()
    tasks = [{"item": job.data, "time": job.next_t.astimezone(sg_tz).strftime("%Y-%m-%d %H:%M:%S")}
             for job in jobs
             if job.data is not None and job.next_t is not None and job.chat_id == user_id]

    #tasks format {
    #    "item": "Go to the gym", 
    #    "time": "2026-06-24 15:30:00"
    #}
    if not tasks:
        await context.bot.send_message(chat_id=user_id, text="🐴 Wow no tasks at all, I'm going to go eat grass then...")
        return
    time_sorted_tasks = sorted(tasks, key=lambda x: x['time'])  # Sort tasks by time
    curr_date = None
    day_count = 0  #idx for numbering tasks within a day

    message = "🐴 All scheduled tasks: \n\n"

    for task in time_sorted_tasks:
        if task['item'] is None:
            continue

        task_date = task['time'].split(' ')[0]  # Extract the date part
        task_time = task['time'].split(' ')[1][:5]  # Extract the time part (HH:MM, drop seconds)

        if task_date != curr_date: #print date headers
            curr_date = task_date
            day_count = 0  # restart numbering for the new day
            #reformat YYYY-MM-DD into a friendlier "24 June 2026" for the header
            display_date = datetime.datetime.strptime(task_date, "%Y-%m-%d").strftime(DISPLAY_DATE)
            message += f"\n📄 Tasks for {display_date}:\n"

        day_count += 1
        message += f"{day_count}. {task['item']} ➡️ {task_time}\n"
 
    await context.bot.send_message(chat_id=user_id, text=message)

#alarm module, sends a reminder to the user at the scheduled time
async def alarm(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(chat_id=job.chat_id, text=f"🐴 Task Reminder:\n {job.data}")
    remove_task(job.chat_id, job.data)  #remove task from the file after it has been triggered

#schedule the task using the /schedule command, takes in a message and a time in HH:MM format
#fallback if AI scheduling is down or does not work, requires strict formatting but is more likely to schedule correctly if the user follows the format
async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or len(context.args) < 3:
        await update.message.reply_text("❗Please provide a item to schedule in the following format:\n/schedule <item> <date in YYYY-MM-DD format> <time in HH:MM format>")
        return
    
    time = context.args[-1] #extracts the time from the command arguments
    date = context.args[-2] #extracts the date from the command arguments
    message = " ".join(context.args[:-2]) #extracts the message from the command arguments
    user_id = update.effective_user.id

    try: 

        naive_dt = datetime.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        scheduled_time = sg_tz.localize(naive_dt)
        
        if scheduled_time < datetime.datetime.now(sg_tz):
            await update.message.reply_text("❗That exact date and time has already passed. Please choose a future time.")
            return
        
    except ValueError:
        await update.message.reply_text("❗Invalid date or time format. Please use YYYY-MM-DD for date and HH:MM for time.")
        return
    
    #if an identical task already exists for this user, append " (1)", " (2)", ... to keep it distinct
    message = unique_item_name(context.job_queue, user_id, message)
    await update.message.reply_text(
                                    f"✅Scheduled {message} at {time} on {scheduled_time.strftime(DISPLAY_DATE)}."
                                    f"\n\n"
                                    f"You will be reminded at the scheduled time."
                                    )
    context.job_queue.run_once(alarm, when=scheduled_time, chat_id=user_id, data=message)
    add_task(user_id, message, scheduled_time.strftime("%Y-%m-%d %H:%M:%S"))

#scheduling with AI parsing
async def ai_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = " ".join(context.args) #extracts the message from the command arguments
    user_id = update.effective_user.id

    if not message:
        await update.message.reply_text("Please provide a item to schedule with an item, time and date.")
        return
    
    try: 
        # Use AI to parse the message and extract the time
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
        5. "task" must ALWAYS be a non-empty string. Never return null, None, or an empty value for "task"; if the task is unclear, use the word "reminder".
        
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

        #fallback in case the AI ignores the prompt and returns a null/empty task
        if not item:
            item = "reminder"

        # Convert the extracted datetime string to a datetime object
        scheduled_time = datetime.datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
        actual_time = sg_tz.localize(scheduled_time)  # Localize timezone

        if actual_time < datetime.datetime.now(sg_tz):
            await update.message.reply_text("❗That time has already passed. Please choose a future time and be more specific.")
            return

        #if an identical task already exists for this user, append " (1)", " (2)", ... to keep it distinct
        item = unique_item_name(context.job_queue, user_id, item)

        await update.message.reply_text(f""
                                        f"✅Scheduled {item} at {actual_time.strftime(DISPLAY_DATETIME)}.\n"
                                        f"\n"
                                        f"You will be reminded at the scheduled time.\n"
                                        f"⚠️AI may make mistakes, please double check the scheduled time and date. If it is wrong, please reschedule with a more specific time and date.")
        context.job_queue.run_once(alarm, when=actual_time, chat_id=user_id, data=item)
        add_task(user_id, item, actual_time.strftime("%Y-%m-%d %H:%M:%S"))

    except Exception as e:
        logging.warning(f"ai_schedule failed for user {user_id}: {e}")  # keep visibility into AI/JSON failures
        await update.message.reply_text("❗Something went wrong, please try again and be more specific. Make sure to include a time and a date for the AI to automatically format!")
        return

#delete helper function that shows a numbered list and waits for the user to reply with a number
async def delete_helper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    #only this user's live, scheduled jobs, sorted by time so the numbering is stable
    jobs = sorted(
        [job for job in context.job_queue.jobs()
         if job.data is not None and job.next_t is not None and job.chat_id == user_id],
        key=lambda j: j.next_t
    )

    if not jobs:
        await update.message.reply_text("🐴 No tasks to delete, I'm off to eat grass...")
        return

    #remember the displayed order so the reply number maps back to the right task
    #this is the hand off to delete_main, which will actually remove the task from the job queue and the file
    #the next time a message is recieved, it pops the awaiting flag, and calls delete_main
    context.user_data['delete_options'] = [job.data for job in jobs]
    context.user_data['awaiting'] = 'delete_main'

    message = "🐴 Which task do you want to delete? Reply with its number:\n\n"
    for i, job in enumerate(jobs, start=1):
        task_time = job.next_t.astimezone(sg_tz).strftime(DISPLAY_DATETIME)
        message += f"{i}. {job.data} ({task_time})\n"
    await update.message.reply_text(message)

#deletes the task the user picked by number from the list, main delete function that actually removes the task from the job queue and the file
async def delete_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    options = context.user_data.pop('delete_options', None)
    if not options:
        await update.message.reply_text("🐴 Nothing to delete right now. Type /delete to start again.")
        return

    choice = " ".join(context.args).strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(options)):
        await update.message.reply_text(f"❗Please reply with a number between 1 and {len(options)}. Type /delete to try again.")
        return

    task_to_delete = options[int(choice) - 1]
    user_id = update.effective_user.id

    for job in context.job_queue.jobs():
        if job.data == task_to_delete and job.chat_id == user_id:
            job.schedule_removal()
            remove_task(user_id, task_to_delete)  # also drop it from the file
            await update.message.reply_text(f"✅Deleted task: {task_to_delete}")
            return

    await update.message.reply_text(f"❗Task not found (it may have already fired): {task_to_delete}")

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
    app.add_handler(CommandHandler("ai_schedule", ai_schedule, filters=allowed_users))
    app.add_handler(CommandHandler("list", list_tasks, filters=allowed_users))
    app.add_handler(CommandHandler("delete", delete_helper, filters=allowed_users)) #delete_helper shows a numbered list and waits for a number reply, delete_main actually deletes the task

    #job queue for daily reminders
    job_queue = app.job_queue

    #restore tasks saved to file
    now = datetime.datetime.now(sg_tz)
    still_valid = []
    #build a list of still-valid tasks and re-schedule them in the job queue per user, dropping any that have already passed
    for t in load_tasks():
        try:
            scheduled_time = sg_tz.localize(datetime.datetime.strptime(t["time"], "%Y-%m-%d %H:%M:%S"))
            if scheduled_time > now:
                job_queue.run_once(alarm, when=scheduled_time, chat_id=t["user_id"], data=t["item"])
                still_valid.append(t)
        except (KeyError, TypeError, ValueError) as e:
            #skip bad entries
            logging.warning(f"Skipping bad task in {TASKS_FILE}: {t} ({e})")
            continue
    save_tasks(still_valid)

    #daily reminder job, runs at 9:00 AM Singapore time every day
    job_queue.run_daily(daily_reminder, time=datetime.time(hour=9, minute=0, second=0, tzinfo=sg_tz))  # Sends reminder at 9:00 AM every day
    
    #buttons for bot navigation appear after starting the bot.
    app.add_handler(CallbackQueryHandler(button_handler))

    #plain text from authorized users feeds button prompts (chat/schedule/ai_schedule)
    app.add_handler(MessageHandler(allowed_users & filters.TEXT & ~filters.COMMAND, text_input)) #not COMMAND so that it doesn't trigger on slash commands

    #run the bot
    print("Horsebot is up and running...")
    app.run_polling()
