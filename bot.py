import logging
import os
import signal
import hashlib
import asyncio
import certifi

from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Conflict
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext

os.environ['SSL_CERT_FILE'] = certifi.where()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration (set these as environment variables in Railway) ---
TOKEN = os.environ.get('BOT_TOKEN', '8348440046:AAEBp-BoV3zUrmvOJyqiK2qYfy7ZvAhUuHg')
CLICK_SERVICE_ID = os.environ.get('CLICK_SERVICE_ID', '')
CLICK_MERCHANT_ID = os.environ.get('CLICK_MERCHANT_ID', '')
CLICK_SECRET_KEY = os.environ.get('CLICK_SECRET_KEY', '')
PORT = int(os.environ.get('PORT', 8080))

# In-memory order storage (orders reset on restart)
orders = {}
next_order_id = 1
next_prepare_id = 1

# Global reference to bot application (for sending messages from Click callbacks)
bot_app = None


# ===================== Telegram Bot Handlers =====================

async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("Web-sahifani ochish", web_app={"url": "https://zingy-cajeta-64b23e.netlify.app/"})],
        [InlineKeyboardButton("To'lov qilish (1000 so'm)", callback_data="pay")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Salom! Quyidagi tugmalardan birini tanlang:",
        reply_markup=reply_markup,
    )


async def pay_command(update: Update, context: CallbackContext) -> None:
    """Handle /pay <amount> — create a Click payment link."""
    global next_order_id

    amount = 1000
    if context.args:
        try:
            amount = int(context.args[0])
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Noto'g'ri summa. Foydalanish: /pay <summa>")
            return

    order_id = str(next_order_id)
    next_order_id += 1

    orders[order_id] = {
        'user_id': update.effective_user.id,
        'chat_id': update.effective_chat.id,
        'amount': amount,
        'status': 'pending',
    }

    payment_url = (
        f"https://my.click.uz/services/pay"
        f"?service_id={CLICK_SERVICE_ID}"
        f"&merchant_id={CLICK_MERCHANT_ID}"
        f"&amount={amount}"
        f"&transaction_param={order_id}"
    )

    keyboard = [[InlineKeyboardButton("Click orqali to'lash", url=payment_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Buyurtma: #{order_id}\n"
        f"Summa: {amount} so'm\n\n"
        f"To'lov qilish uchun tugmani bosing:",
        reply_markup=reply_markup,
    )


async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(context.error, Conflict):
        logger.error("Conflict: another bot instance is running. Exiting.")
        os._exit(1)


# ===================== Click Payment Callbacks =====================

def verify_click_sign(data, action):
    """Verify the MD5 signature sent by Click."""
    if action == 0:  # Prepare
        sign_source = '{}{}{}{}{}{}{}'.format(
            data.get('click_trans_id', ''),
            data.get('service_id', ''),
            CLICK_SECRET_KEY,
            data.get('merchant_trans_id', ''),
            data.get('amount', ''),
            data.get('action', ''),
            data.get('sign_time', ''),
        )
    else:  # Complete
        sign_source = '{}{}{}{}{}{}{}{}'.format(
            data.get('click_trans_id', ''),
            data.get('service_id', ''),
            CLICK_SECRET_KEY,
            data.get('merchant_trans_id', ''),
            data.get('merchant_prepare_id', ''),
            data.get('amount', ''),
            data.get('action', ''),
            data.get('sign_time', ''),
        )

    expected = hashlib.md5(sign_source.encode('utf-8')).hexdigest()
    return expected == data.get('sign_string', '')


async def click_prepare(request):
    """Click Prepare callback (action=0) — verify order before payment."""
    global next_prepare_id
    try:
        data = dict(await request.post())
        logger.info("Click Prepare request: %s", data)

        click_trans_id = data.get('click_trans_id', '')
        merchant_trans_id = data.get('merchant_trans_id', '')
        amount = data.get('amount', '0')
        action = int(data.get('action', '0'))

        # 1. Verify signature
        if not verify_click_sign(data, action):
            return web.json_response({
                'click_trans_id': click_trans_id,
                'merchant_trans_id': merchant_trans_id,
                'error': -1,
                'error_note': 'SIGN CHECK FAILED',
            })

        # 2. Check if order exists
        if merchant_trans_id not in orders:
            return web.json_response({
                'click_trans_id': click_trans_id,
                'merchant_trans_id': merchant_trans_id,
                'error': -5,
                'error_note': 'Order not found',
            })

        order = orders[merchant_trans_id]

        # 3. Check if already paid
        if order['status'] == 'paid':
            return web.json_response({
                'click_trans_id': click_trans_id,
                'merchant_trans_id': merchant_trans_id,
                'error': -4,
                'error_note': 'Already paid',
            })

        # 4. Check amount
        if float(amount) != float(order['amount']):
            return web.json_response({
                'click_trans_id': click_trans_id,
                'merchant_trans_id': merchant_trans_id,
                'error': -2,
                'error_note': 'Incorrect amount',
            })

        # 5. Success — mark as preparing and return prepare_id
        prepare_id = next_prepare_id
        next_prepare_id += 1
        order['status'] = 'preparing'
        order['prepare_id'] = prepare_id

        return web.json_response({
            'click_trans_id': int(click_trans_id),
            'merchant_trans_id': merchant_trans_id,
            'merchant_prepare_id': prepare_id,
            'error': 0,
            'error_note': 'Success',
        })

    except Exception as e:
        logger.error("Click Prepare error: %s", e, exc_info=True)
        return web.json_response({'error': -7, 'error_note': 'Bad request'})


async def click_complete(request):
    """Click Complete callback (action=1) — confirm or cancel payment."""
    try:
        data = dict(await request.post())
        logger.info("Click Complete request: %s", data)

        click_trans_id = data.get('click_trans_id', '')
        merchant_trans_id = data.get('merchant_trans_id', '')
        merchant_prepare_id = data.get('merchant_prepare_id', '')
        amount = data.get('amount', '0')
        action = int(data.get('action', '1'))
        error = int(data.get('error', '0'))

        # 1. Verify signature
        if not verify_click_sign(data, action):
            return web.json_response({
                'click_trans_id': click_trans_id,
                'merchant_trans_id': merchant_trans_id,
                'merchant_confirm_id': merchant_prepare_id,
                'error': -1,
                'error_note': 'SIGN CHECK FAILED',
            })

        # 2. Check if order exists
        if merchant_trans_id not in orders:
            return web.json_response({
                'click_trans_id': click_trans_id,
                'merchant_trans_id': merchant_trans_id,
                'merchant_confirm_id': merchant_prepare_id,
                'error': -5,
                'error_note': 'Order not found',
            })

        order = orders[merchant_trans_id]

        # 3. Check if already paid
        if order['status'] == 'paid':
            return web.json_response({
                'click_trans_id': click_trans_id,
                'merchant_trans_id': merchant_trans_id,
                'merchant_confirm_id': merchant_prepare_id,
                'error': -4,
                'error_note': 'Already paid',
            })

        # 4. If Click reports an error, cancel
        if error < 0:
            order['status'] = 'cancelled'
            return web.json_response({
                'click_trans_id': int(click_trans_id),
                'merchant_trans_id': merchant_trans_id,
                'merchant_confirm_id': int(merchant_prepare_id) if merchant_prepare_id else 0,
                'error': -9,
                'error_note': 'Transaction cancelled',
            })

        # 5. Check amount
        if float(amount) != float(order['amount']):
            return web.json_response({
                'click_trans_id': click_trans_id,
                'merchant_trans_id': merchant_trans_id,
                'merchant_confirm_id': merchant_prepare_id,
                'error': -2,
                'error_note': 'Incorrect amount',
            })

        # 6. Success — mark as paid and notify user via Telegram
        order['status'] = 'paid'
        confirm_id = int(merchant_prepare_id) if merchant_prepare_id else 0

        try:
            await bot_app.bot.send_message(
                chat_id=order['chat_id'],
                text=(
                    f"To'lov qabul qilindi!\n\n"
                    f"Buyurtma: #{merchant_trans_id}\n"
                    f"Summa: {order['amount']} so'm"
                ),
            )
        except Exception as notify_err:
            logger.error("Failed to notify user: %s", notify_err)

        return web.json_response({
            'click_trans_id': int(click_trans_id),
            'merchant_trans_id': merchant_trans_id,
            'merchant_confirm_id': confirm_id,
            'error': 0,
            'error_note': 'Success',
        })

    except Exception as e:
        logger.error("Click Complete error: %s", e, exc_info=True)
        return web.json_response({'error': -7, 'error_note': 'Bad request'})


async def health_check(request):
    return web.json_response({'status': 'ok'})


# ===================== Main =====================

async def main():
    global bot_app

    # --- Telegram bot ---
    bot_app = ApplicationBuilder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler('start', start))
    bot_app.add_handler(CommandHandler('pay', pay_command))
    bot_app.add_error_handler(error_handler)

    # --- Web server for Click callbacks ---
    web_app = web.Application()
    web_app.router.add_post('/click/prepare', click_prepare)
    web_app.router.add_post('/click/complete', click_complete)
    web_app.router.add_get('/health', health_check)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info("Web server started on port %d", PORT)

    # --- Start bot polling ---
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot started polling")

    # --- Wait for shutdown signal ---
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    logger.info("Shutdown signal received, stopping...")

    await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()
    await runner.cleanup()
    logger.info("Shutdown complete")


if __name__ == '__main__':
    asyncio.run(main())
