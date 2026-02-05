import logging
import os
import certifi

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext

os.environ['SSL_CERT_FILE'] = certifi.where()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = '8348440046:AAEBp-BoV3zUrmvOJyqiK2qYfy7ZvAhUuHg'

async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("Web-sahifani ochish", web_app={"url": "https://zingy-cajeta-64b23e.netlify.app/"})]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        'Salom! Web-sahifani ochish uchun quyidagi tugmani bosing.',
        reply_markup=reply_markup
    )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()
    
    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)
    
    application.run_polling()