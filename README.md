# 🐴 Horsebot - Telegram Reminder Bot

This bot was built to schedule easy reminders without the need for heavy formatting, using Gemini to parse data provided.

## Available commands

- `/start` - Start the bot
- `/hello` - Say hello to the bot
- `/chat` - Chat with the AI
- `/generate_image` - Generate an image with AI
- `/schedule` - Schedule a reminder. Requires item, date and time in `YYYY-MM-DD` and `HH:MM` format
- `/ai_schedule` - Schedule a reminder with AI parsing, no strict format required
- `/list` - List all scheduled tasks organised by date and time
- `/delete` - Delete a scheduled task from the list
- `/help` - Show this help message

The bot can also be operated using the buttons after typing `/start`. Click on a button to execute the corresponding command.

Access to scheduling/chat commands is restricted to authorized Telegram user IDs
(see `ALLOWED_USERS` in [tele_reminder_bot.py](tele_reminder_bot.py)).

## Setup

1. Download or clone the repo, then install dependencies

   ```bash
   pip install -r requirements.txt
   ```

2. Configure API keys

   ```bash
   cp .env.example .env
   ```

   Then edit `.env` and fill in:
   - `TELEGRAM_TOKEN` from [@BotFather](https://t.me/BotFather)
   - `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey)
   - `ALLOWED_USERS` a comma-separated list of authorized Telegram user IDs. Use the [@userinfobot](https://t.me/userinfobot) to find your Telegram user ID.

## Run

```bash
python tele_reminder_bot.py
```

## Notes

- Timezone is currently set to `Asia/Singapore` timezone, update as required.
- Image generation requires a paid plan 
- AI scheduling may not be accurate, so double-check its contents.
