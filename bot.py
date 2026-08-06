
import os
import json
import asyncio
import re
import datetime
import time
import threading

from telethon import events
from telethon import TelegramClient, Button
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.sessions import StringSession

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

API_ID = 24188127
API_HASH = 'e0e2a70a885d1497c8feb47815bb3e36'
BOT_TOKEN = "8979389438:AAElu4h6HreASjk2MAvfrNX037OxILVqhZo"
REG_ID = 39058108
REG_HASH = '7ee08cec4b5b266482e839937bff0dfd'
PAY_TOKEN = ""

ACC_FILE = 'registered_accounts.json'
NUM_FILE = 'numbers_for_sale.json'
USER_FILE = 'user_data.json'
CONF_FILE = 'bot_settings.json'
STATS_FILE = 'stats.json'
FORCED_SUB_FILE = 'forced_subscriptions.json'

TERMS_TEXT = """
📜 **الشروط والأحكام**

مرحباً بك عزيزي 🤝، يرجى الالتزام بالقوانين التالية:

💳 **1. الشحن:**
شحن رصيدك داخل البوت يتم من المطور حصراً (الحساب المذكور في نبذة البوت). نحن غير مسؤولين عن أي حسابات أخرى.

🔐 **2. استلام الحساب:**
في حال استلامك حساباً من البوت وتسجيل الدخول إليه، تتم إخلاء مسؤولية المطورين بالكامل عن الحساب المُستلم.

📞 **3. الدعم الفني:**
في حال وجود أي مشكلة، يُرجى التواصل مع المطور مباشرة.

📖 **4. الموافقة:**
قراءتك لهذه القوانين تعني موافقتك التامة عليها. وفي حال عدم قراءتها، فأنت تتحمل المسؤولية كاملة.

🧊 **5. تجنب التجميد:**
لتجنب تجميد الأرقام، قم بالتسجيل في تطبيق التليجرام باستخدام نسخة "تيليجرام الاصلي".

⏳ **6. ضمان التجميد:**
يوجد ضمان على التجميد لمدة 8 ساعات فقط بعد الشراء. بعد مرور 8 ساعات، لا يوجد أي تعويض نهائياً.
"""

SUPPORT_TEXT = """
🆘 **قسم الدعم الفني والمساعدة**

اختر من القائمة أدناه:
"""

client = TelegramClient('BotSession', API_ID, API_HASH)
bot = telebot.TeleBot(BOT_TOKEN)
pay_token = PAY_TOKEN

u_clients = {}
code_reqs = {}
res_timers = {}
u_sessions = {}
avail_nums = {}
syyad_users = {}
stats = {}
forced_subs = []

syyad_conf = {
    'admin_ids': ['8379531283'],
    'chargeRates': [],
    'reservationTimeoutMinutes': 60,
    'publish_channel_id': None,
    'bot_channel_url': 'https://t.me/AF_R_O_TO'
}

CATEGORY_EMOJIS = {
    "سبام": "🪙",
    "سليم": "👑",
    "احتيالي": "🎭",
    "مزيف": "🏮"
}

def hide_number(phone):
    if len(phone) <= 6:
        return phone
    country_code = ""
    remaining = phone
    if phone.startswith('+'):
        country_code = phone[:4]
        remaining = phone[4:]
    else:
        return phone
    
    if len(remaining) <= 2:
        return phone
    
    first_digit = remaining[0]
    last_digit = remaining[-1]
    hidden = "....."
    return f"{country_code}{first_digit}{hidden}{last_digit}"

def load(fpath, d_val):
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as file:
            try:
                return json.load(file)
            except json.JSONDecodeError:
                return d_val
    return d_val

def save(fpath, data):
    with open(fpath, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

def load_all():
    global u_sessions, avail_nums, syyad_users, syyad_conf, stats, forced_subs
    u_sessions = load(ACC_FILE, {})
    avail_nums = load(NUM_FILE, {})
    syyad_users = load(USER_FILE, {})
    stats = load(STATS_FILE, {})
    forced_subs = load(FORCED_SUB_FILE, [])
    loaded_settings = load(CONF_FILE, {})

    syyad_conf.update(loaded_settings)
    if '8379531283' not in syyad_conf['admin_ids']:
        syyad_conf['admin_ids'].append('8379531283')
    
    stats.setdefault('total_sales', 0)
    stats.setdefault('total_revenue_points', 0)
    stats.setdefault('total_revenue_stars', 0)
    stats.setdefault('sales_history', [])
    stats.setdefault('daily_sales', {})

def save_all():
    save(ACC_FILE, u_sessions)
    save(NUM_FILE, avail_nums)
    save(CONF_FILE, syyad_conf)
    save(USER_FILE, syyad_users)
    save(STATS_FILE, stats)
    save(FORCED_SUB_FILE, forced_subs)

def get_syyad_bal(uid):
    uid_str = str(uid)
    if uid_str not in syyad_users:
        syyad_users[uid_str] = {}

    syyad_users[uid_str].setdefault('points', 0)
    syyad_users[uid_str].setdefault('stars', 0)
    syyad_users[uid_str].setdefault('purchases', 0)
    syyad_users[uid_str].setdefault('username', '')

    save(USER_FILE, syyad_users)
    return syyad_users[uid_str]

def is_adm(uid):
    return str(uid) in syyad_conf['admin_ids']

async def check_forced_subscription(user_id):
    if not forced_subs:
        return True
    for channel in forced_subs:
        try:
            participant = await client.get_participants(channel)
            user_found = False
            for p in participant:
                if p.id == user_id:
                    user_found = True
                    break
            if not user_found:
                return False
        except:
            return False
    return True

async def show_forced_subscription_message(event):
    channels_text = "\n".join([f"• {channel}" for channel in forced_subs])
    buttons = []
    for channel in forced_subs:
        buttons.append([Button.url(f"📢 اشترك في القناة", channel)])
    buttons.append([Button.inline("✅ تحقق من الاشتراك", "check_subscription")])
    
    await event.respond(
        f"⚠️ **عذراً عزيزي المستخدم**\n\n"
        f"يرجى الاشتراك في القنوات التالية لتتمكن من استخدام البوت:\n\n"
        f"{channels_text}\n\n"
        f"📌 بعد الاشتراك، اضغط على زر 'تحقق من الاشتراك'",
        parse_mode='markdown',
        buttons=buttons
    )

def run_poll():
    bot.delete_webhook() 
    bot.polling(none_stop=True)

async def run_timer(phone, uid, expiry):
    global avail_nums, res_timers

    rem_time = expiry - time.time()
    if rem_time <= 0:
        await end_resv(phone, notify=False)
        return

    task = asyncio.create_task(asyncio.sleep(rem_time))
    res_timers[phone] = task

    try:
        await task
        await end_resv(phone)
    except asyncio.CancelledError:
        pass
    finally:
        if phone in res_timers:
            del res_timers[phone]

async def end_resv(phone, notify=True):
    global avail_nums
    if phone in avail_nums and avail_nums[phone]['status'] == 'booked':
        booked_by = avail_nums[phone]['booked_by']
        avail_nums[phone].update({
            'status': 'available',
            'booked_by': None,
            'booking_time': None,
            'expiry_time': None,
            'deposit_paid_stars': None
        })
        save_all()

        if notify and booked_by:
            await client.send_message(
                int(booked_by),
                f"🚨 **انتهى حجز الرقم `{hide_number(phone)}`.**\n\n"
                f"لم يتم إتمام عملية الشراء في الوقت المحدد. الرقم متاح الآن للبيع مرة أخرى.",
                parse_mode='markdown'
            )

        await client.send_message(
            int(syyad_conf['admin_ids'][0]),
            f"🚨 **انتهى حجز الرقم `{phone}`.**\n"
            f"كان محجوزاً بواسطة `{booked_by}` ولم يتم إتمام الشراء.",
            parse_mode='markdown'
        )

    if phone in res_timers:
        res_timers[phone].cancel()
        del res_timers[phone]

async def init_resv():
    for phone, details in list(avail_nums.items()):
        if details.get('status') == 'booked' and details.get('expiry_time'):
            expiry = details['expiry_time']
            if expiry > time.time():
                asyncio.create_task(run_timer(phone, details['booked_by'], expiry))
            else:
                await end_resv(phone, notify=False)

async def init_acc(phone, api_id, api_hash, sess_str):
    if phone in u_clients:
        try:
            if u_clients[phone].is_connected():
                return
            else:
                await u_clients[phone].disconnect()
                del u_clients[phone]
        except:
            pass

    u_client = TelegramClient(StringSession(sess_str), api_id, api_hash)

    @u_client.on(events.NewMessage(incoming=True, chats=777000))
    async def proc_code_msg(event):
        global code_reqs
        code_match = re.search(r'Login code: (\d+)', event.message.text)
        if not code_match:
            code_match = re.search(r'\b(\d{5,})\b', event.message.text)

        if code_match:
            code = code_match.group(1)
            buyer_id = code_reqs.get(phone)

            if buyer_id:
                await client.send_message(
                    int(buyer_id),
                    f"**✅ تم استلام الكود بنجاح**\n\n"
                    f"📱 الرقم: `{phone}`\n"
                    f"🔑 الكود: `{code}`"
                )
                acc_details = u_sessions.get(phone, {})
                two_fa_pass = acc_details.get('two_factor_password', 'لا يوجد')
                if two_fa_pass and two_fa_pass != "لا يوجد":
                    await client.send_message(
                        int(buyer_id),
                        f"🔐 كلمة مرور التحقق بخطوتين: `{two_fa_pass}`"
                    )

                if phone in code_reqs:
                    del code_reqs[phone]
            raise events.StopPropagation

    try:
        await u_client.connect()
        if not await u_client.is_user_authorized():
            if phone in u_clients:
                del u_clients[phone]
            return
        u_clients[phone] = u_client
    except Exception:
        if phone in u_clients:
            del u_clients[phone]

async def run_accs():
    for phone, details in u_sessions.items():
        api_id = details.get('api_id')
        api_hash = details.get('api_hash')
        sess_str = details.get('session_str')
        if api_id and api_hash and sess_str:
            asyncio.create_task(init_acc(phone, api_id, api_hash, sess_str))

async def send_channel_message(phone, country, price_points, price_stars, category):
    if syyad_conf.get('publish_channel_id'):
        try:
            cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
            channel_msg = (
                f"🌟 **تم بيع رقم جديد !** 🌟\n\n"
                f"📞 **الرقم:** `{hide_number(phone)}`\n"
                f"🌍 **الدولة:** {country}\n"
                f"🏷️ **التصنيف:** {cat_emoji} {category}\n"
            )
            if price_points > 0:
                channel_msg += f"💰 **السعر:** {price_points} نقطة\n"
            if price_stars > 0:
                channel_msg += f"⭐ **السعر:** {price_stars} نجمة\n"
            channel_msg += f"\n✨ **BOT:*@AF_ROT_O_SMS_BOT* 🤍"
            
            await client.send_message(
                syyad_conf['publish_channel_id'],
                channel_msg,
                parse_mode='markdown'
            )
        except Exception as e:
            print(f"Error sending channel message: {e}")

async def edit_post(phone):
    if syyad_conf.get('publish_channel_id') and phone in avail_nums:
        num_details = avail_nums[phone]
        msg_id = num_details.get('publish_message_id')
        if msg_id:
            try:
                orig_msg = await client.get_messages(syyad_conf['publish_channel_id'], ids=msg_id)
                if orig_msg:
                    new_text = f"#تم_البيع\n\n{orig_msg.text}"
                    await client.edit_message(syyad_conf['publish_channel_id'], msg_id, new_text)
            except Exception:
                pass

async def add_num(event):
    async with client.conversation(event.sender_id, timeout=600) as conv:
        await conv.send_message("أرسل الرقم الذي تريد إضافته (مع رمز الدولة +):", buttons=[[Button.inline("إلغاء", data='cancel_op')]])
        phone_resp = await conv.get_response()

        if phone_resp.text == 'إلغاء':
             await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
             return None, None

        phone = phone_resp.text.strip()

        if not phone.startswith('+') or not phone[1:].isdigit():
            await conv.send_message("رقم الهاتف غير صالح.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
            return None, None

        if phone in u_sessions:
            await conv.send_message("هذا الرقم مسجل بالفعل.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
            return None, None

        new_client = None
        try:
            new_client = TelegramClient(StringSession(), REG_ID, REG_HASH)
            await new_client.connect()

            two_fa_pass = "لا يوجد"
            code_req_info = await new_client.send_code_request(phone)
            await conv.send_message("تم إرسال الكود إلى الرقم، يرجى إرسال الكود المستلم:", buttons=[[Button.inline("إلغاء", data='cancel_op')]])

            code_resp = await conv.get_response()
            if code_resp.text == 'إلغاء':
                await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
                return None, None

            ver_code = code_resp.text.strip()

            try:
                await new_client.sign_in(
                    phone=phone,
                    code=ver_code,
                    phone_code_hash=code_req_info.phone_code_hash
                )
            except SessionPasswordNeededError:
                await conv.send_message("الحساب محمي بكلمة مرور. يرجى إرسال كلمة المرور (التحقق بخطوتين):", buttons=[[Button.inline("إلغاء", data='cancel_op')]])

                pass_resp = await conv.get_response()
                if pass_resp.text == 'إلغاء':
                    await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
                    return None, None

                two_fa_pass = pass_resp.text.strip()
                await new_client.sign_in(password=two_fa_pass)

            sess_str = new_client.session.save()
            new_acc_details = {
                'api_id': REG_ID,
                'api_hash': REG_HASH,
                'session_str': sess_str,
                'two_factor_password': two_fa_pass
            }

            await conv.send_message("تم تسجيل الحساب بنجاح. الآن، أدخل تفاصيل البيع.")

            await conv.send_message("أرسل سعر الرقم بالنقاط (0 إذا لم يكن بالنقاط):", buttons=[[Button.inline("إلغاء", data='cancel_op')]])
            pts_price_resp = await conv.get_response()
            if pts_price_resp.text == 'إلغاء':
                await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
                return None, None
            try:
                pts_price = int(pts_price_resp.text.strip())
            except ValueError:
                await conv.send_message("السعر بالنقاط غير صالح.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
                return None, None

            await conv.send_message("أرسل سعر الرقم بالنجوم (0 إذا لم يكن بالنجوم):", buttons=[[Button.inline("إلغاء", data='cancel_op')]])
            star_price_resp = await conv.get_response()
            if star_price_resp.text == 'إلغاء':
                await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
                return None, None
            try:
                star_price = int(star_price_resp.text.strip())
            except ValueError:
                await conv.send_message("السعر بالنجوم غير صالح.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
                return None, None

            await conv.send_message("أرسل اسم الدولة:", buttons=[[Button.inline("إلغاء", data='cancel_op')]])
            ctry_resp = await conv.get_response()
            if ctry_resp.text == 'إلغاء':
                await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
                return None, None
            ctry_name = ctry_resp.text.strip()

            category = None
            while category is None:
                await conv.send_message(
                    "🏷️ **اختر تصنيف الرقم بإرسال اسم التصنيف:**\n\n"
                    "🪙 سبام\n"
                    "👑 سليم\n"
                    "🎭 احتيالي\n"
                    "🏮 مزيف\n\n"
                    "❗ **أرسل اسم التصنيف كما هو مكتوب بالضبط**",
                    buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]]
                )
                
                category_resp = await conv.get_response()
                if category_resp.text == 'إلغاء':
                    await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
                    return None, None
                
                category_text = category_resp.text.strip()
                
                if category_text in CATEGORY_EMOJIS:
                    category = category_text
                else:
                    await conv.send_message("❌ يرجى إرسال اسم تصنيف صحيح: سبام، سليم، احتيالي، أو مزيف")

            sale_info = {
                "price_points": pts_price,
                "price_stars": star_price,
                "country": ctry_name,
                "category": category,
                "status": "available",
                "added_by": str(event.sender_id),
                "buyer_id": None,
                "booked_by": None,
                "booking_time": None,
                "expiry_time": None,
                "deposit_paid_stars": None,
                "publish_message_id": None
            }
            
            if syyad_conf.get('publish_channel_id'):
                cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
                pub_text = (
                    f"**📢 رقم جديد متاح للبيع**\n\n"
                    f"📞 **الرقم:** `{hide_number(phone)}`\n"
                    f"🌍 **الدولة:** {ctry_name}\n"
                    f"🏷️ **التصنيف:** {cat_emoji} {category}\n"
                )
                if pts_price > 0:
                    pub_text += f"💰 **السعر بالنقاط:** {pts_price}\n"
                if star_price > 0:
                    pub_text += f"⭐ **السعر بالنجوم:** {star_price}\n"
                pub_text += f"\n✨ **BOT:@AF_ROT_O_SMS_BOT!** 🤍"

                try:
                    sent_msg = await client.send_message(
                        syyad_conf['publish_channel_id'],
                        pub_text,
                        parse_mode='markdown'
                    )
                    sale_info["publish_message_id"] = sent_msg.id
                except Exception as e:
                     await conv.send_message(f"لم يتمكن من النشر في القناة: {e}")

            await conv.send_message(
                f"تمت إضافة الرقم `{phone}` بنجاح وعرضه للبيع.",
                parse_mode='markdown',
                buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]]
            )

            return {phone: new_acc_details}, {phone: sale_info}

        except FloodWaitError as e:
            await conv.send_message(f"حدث خطأ فيضان. يرجى الانتظار {e.seconds} ثانية.", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
            return None, None
        except Exception as e:
            await conv.send_message(f"حدث خطأ: {str(e)}", buttons=[[Button.inline("العودة للقائمة الرئيسية", data='main_admin_menu')]])
            return None, None
        finally:
            if new_client and new_client.is_connected():
                await new_client.disconnect()

async def show_a_nums(event):
    if not avail_nums:
        await event.edit("لا توجد أرقام مضافة حالياً.", buttons=[[Button.inline("العودة لقسم الأرقام", data="admin_numbers_section")]])
        return

    lines = []
    buttons = []

    for phone, details in avail_nums.items():
        status = details.get('status', 'N/A')
        category = details.get('category', 'غير محدد')
        cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
        emoji = ""
        txt = ""

        if status == 'available':
            emoji = "🟢"
            txt = "متاح"
        elif status == 'booked':
            emoji = "🟡"
            booked_by = details.get('booked_by', 'N/A')
            expiry = details.get('expiry_time')
            if expiry:
                rem_sec = max(0, int(expiry - time.time()))
                mins = rem_sec // 60
                secs = rem_sec % 60
                txt = f"محجوز لـ `{booked_by}` ({mins:02d}:{secs:02d} متبقي)"
            else:
                txt = f"محجوز لـ `{booked_by}`"
        elif status == 'sold':
            emoji = "🔴"
            txt = f"مباع للمستخدم `{details.get('buyer_id', 'غير معروف')}`"

        lines.append(
            f"📞 الرقم: `{phone}`\n"
            f"🌍 الدولة: {details.get('country', 'N/A')}\n"
            f"🏷️ التصنيف: {cat_emoji} {category}\n"
            f"💰 السعر (نقاط): {details.get('price_points', 0)}\n"
            f"⭐ السعر (نجوم): {details.get('price_stars', 0)}\n"
            f"{emoji} الحالة: {txt}\n"
            f"--------------------"
        )
        buttons.append([Button.inline(f"{cat_emoji} {phone} ({txt})", data=f"view_specific_number:{phone}")])

    msg = "**📋 قائمة الأرقام المضافة:**\n\n" + "\n".join(lines)

    buttons.append([Button.inline("🔙 العودة لقسم الأرقام", data="admin_numbers_section")])
    await event.edit(msg, buttons=buttons, parse_mode='markdown')

async def show_a_del(event):
    if not avail_nums:
        await event.edit("لا توجد أرقام لحذفها حالياً.", buttons=[[Button.inline("العودة لقسم الأرقام", data="admin_numbers_section")]])
        return

    buttons = []
    for phone in avail_nums:
        category = avail_nums[phone].get('category', 'غير محدد')
        cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
        buttons.append([Button.inline(f"❌ حذف الرقم {cat_emoji} {phone}", data=f"delete_number_confirm:{phone}")])

    buttons.append([Button.inline("العودة لقسم الأرقام", data="admin_numbers_section")])
    await event.edit("اختر الرقم الذي تريد حذفه:", buttons=buttons)

async def show_a_list(event):
    adm_list = "\n".join([f"- `{adm_id}`" for adm_id in syyad_conf['admin_ids']]) if syyad_conf['admin_ids'] else "لا يوجد أدمنية حالياً."
    await event.edit(
        f"**👥 قائمة الأدمنية:**\n{adm_list}",
        buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data="admin_admins_section")]],
        parse_mode='markdown'
    )

async def show_stats(event):
    total_sales = stats.get('total_sales', 0)
    total_revenue_points = stats.get('total_revenue_points', 0)
    total_revenue_stars = stats.get('total_revenue_stars', 0)
    
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    daily_sales = stats.get('daily_sales', {}).get(today, 0)
    
    msg = (
        f"📊 **إحصائيات المبيعات**\n\n"
        f"📦 **إجمالي المبيعات:** `{total_sales}`\n"
        f"💰 **إجمالي الأرباح (نقاط):** `{total_revenue_points}`\n"
        f"⭐ **إجمالي الأرباح (نجوم):** `{total_revenue_stars}`\n"
        f"📅 **مبيعات اليوم:** `{daily_sales}`\n"
    )
    
    if total_sales > 0:
        avg_points = total_revenue_points / total_sales
        avg_stars = total_revenue_stars / total_sales
        msg += f"\n📊 **متوسط سعر البيع:**\n"
        msg += f"  - نقاط: `{avg_points:.1f}`\n"
        msg += f"  - نجوم: `{avg_stars:.1f}`"
    
    await event.edit(
        msg,
        parse_mode='markdown',
        buttons=[[Button.inline("🔙 العودة", data="admin_stats_back")]]
    )

async def show_a_sales_history(event):
    history = stats.get('sales_history', [])
    if not history:
        await event.edit("لا توجد مبيعات مسجلة.", buttons=[[Button.inline("🔙 العودة", data="admin_stats_back")]])
        return
    
    lines = []
    for sale in history[-10:]:
        category = sale.get('category', 'غير محدد')
        cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
        lines.append(
            f"📞 {sale.get('phone', 'N/A')}\n"
            f"  - الدولة: {sale.get('country', 'N/A')}\n"
            f"  - التصنيف: {cat_emoji} {category}\n"
            f"  - السعر: {sale.get('price_points', 0)} نقطة / {sale.get('price_stars', 0)} نجمة\n"
            f"  - المشتري: `{sale.get('buyer_id', 'N/A')}`\n"
            f"  - التاريخ: {sale.get('date', 'N/A')}\n"
            f"--------------------"
        )
    
    msg = "**📋 سجل المبيعات (آخر 10):**\n\n" + "\n".join(lines)
    await event.edit(
        msg,
        parse_mode='markdown',
        buttons=[[Button.inline("🔙 العودة", data="admin_stats_back")]]
    )

async def show_u_main(event):
    uid = str(event.sender_id)
    
    if not await check_forced_subscription(event.sender_id):
        await show_forced_subscription_message(event)
        return
    
    user_bal = get_syyad_bal(uid)
    
    try:
        user = await client.get_entity(int(uid))
        username = user.first_name or user.username or "مستخدم"
    except:
        username = "مستخدم"
    
    user_bal['username'] = username
    save_all()
    
    welcome_msg = (
        f"**👋 أهلاً بك عزيزي `{username}`**\n\n"
        f"🆔 **ايديتك:** `{uid}`\n"
        f"💰 **عدد نقاطك:** `{user_bal['points']}`\n"
        f"📦 **عدد مشترياتك:** `{user_bal['purchases']}`\n\n"
        f"✨ **مرحباً بك في بوت بيع الأرقام!**"
    )
    
    await event.respond(
        welcome_msg,
        parse_mode='markdown',
        buttons=[
            [
                Button.inline('🛒 شراء رقم', 'user_buy_number_menu'),
                Button.inline('💰 شحن نقاط', 'user_charge_points_menu')
            ],
            [
                Button.inline('📜 الشروط والأحكام', 'user_show_terms'),
                Button.inline('📢 قناة البوت', 'user_bot_channel')
            ],
            [
                Button.inline('🆘 الدعم الفني والمساعدة', 'user_support_menu')
            ]
        ]
    )

async def show_u_bot_channel(event):
    channel_url = syyad_conf.get('bot_channel_url', 'https://t.me/AF_R_O_TO')
    await event.edit(
        f"📢 **قناة البوت الرسمية**\n\n"
        f"انضم إلى قناتنا الرسمية للحصول على آخر التحديثات والأرقام الجديدة:\n\n"
        f"🔗 {channel_url}\n\n"
        f"✨ **لا تفوت الفرصة!**",
        parse_mode='markdown',
        buttons=[
            [Button.url('📢 انضم الآن', channel_url)],
            [Button.inline('🔙 العودة للقائمة الرئيسية', 'user_main_menu')]
        ]
    )

async def show_u_support(event):
    await event.edit(
        SUPPORT_TEXT,
        parse_mode='markdown',
        buttons=[
            [
                Button.url('📞 الدعم الفني', 'https://t.me/J_D_D_M')
            ],
            [
                Button.inline('ℹ️ المساعدة', 'user_help_info')
            ],
            [
                Button.inline('🔙 العودة للقائمة الرئيسية', 'user_main_menu')
            ]
        ]
    )

async def show_u_help_info(event):
    help_text = """
ℹ️ **طريقة استخدام البوت:**

1️⃣ **طريقة شحن الرصيد:**
يمكنك شحن رصيدك من خلال الضغط على زر '💰 شحن نقاط' واختيار وسيلة الدفع المناسبة لك (نجوم تليجرام أو أكواد الشحن).

2️⃣ **طريقة شراء الأرقام:**
اضغط على '🛒 شراء رقم'، ستظهر لك الأرقام المتاحة حسب الدولة، اختر الدولة المناسبة ثم اختر الرقم الذي تريده.

3️⃣ **استلام الأرقام:**
بعد إتمام عملية الشراء، سيظهر لك الرقم. قم بطلبه في التطبيق المخصص ثم اضغط على زر '📲 الحصول على الكود' لاستلام كود التفعيل.

📞 إذا واجهتك أي مشكلة أخرى، تواصل مع فريق الدعم الفني عبر زر '📞 الدعم الفني'.
"""
    await event.edit(
        help_text,
        parse_mode='markdown',
        buttons=[[Button.inline('🔙 العودة لقسم الدعم', 'user_support_menu')]]
    )

async def show_u_buy_menu(event):
    await event.edit(
        "🏷️ **اختر تصنيف الأرقام التي تريد شراءها:**\n\n"
        "🪙 **سبام:** أرقام بأسعار منخفضة\n"
        "👑 **سليم:** أرقام مميزة بأسعار مرتفعة\n"
        "🎭 **احتيالي:** أرقام قد تكون محفوفة بالمخاطر\n"
        "🏮 **مزيف:** أرقام غير حقيقية (استخدام مؤقت)",
        parse_mode='markdown',
        buttons=[
            [Button.inline("🪙 سبام", data="buy_category:سبام")],
            [Button.inline("👑 سليم", data="buy_category:سليم")],
            [Button.inline("🎭 احتيالي", data="buy_category:احتيالي")],
            [Button.inline("🏮 مزيف", data="buy_category:مزيف")],
            [Button.inline("🔙 العودة للقائمة الرئيسية", data="user_main_menu")]
        ]
    )

async def show_u_nums_by_category(event, category):
    user_id = str(event.sender_id)
    
    nums_in_category = {
        phone: details for phone, details in avail_nums.items()
        if details.get('category') == category and details.get('status') in ['available', 'booked']
    }
    
    if not nums_in_category:
        await event.respond(
            f"❌ لا توجد أرقام {category} متاحة حالياً.",
            buttons=[[Button.inline("🔙 العودة للتصنيفات", data="user_buy_number_menu")]]
        )
        return
    
    buttons = []
    cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
    
    for phone, details in nums_in_category.items():
        status = details.get('status')
        hidden_phone = hide_number(phone)
        
        if status == 'booked' and str(details.get('booked_by')) == user_id:
            expiry = details.get('expiry_time')
            rem_sec = max(0, int(expiry - time.time()))
            mins, secs = divmod(rem_sec, 60)
            btn_text = f"🔔 {cat_emoji} {hidden_phone} ({mins:02d}:{secs:02d})"
        elif status == 'available':
            btn_text = f"{cat_emoji} {hidden_phone}"
        else:
            continue

from telethon import Button, events
from telethon.tl.types import InputMediaInvoice, LabeledPrice


# 1. قسم شحن النقاط
async def show_u_chrg(event):
    uid = str(event.sender_id)
    try:
        user = await client.get_entity(int(uid))
        username = user.first_name or user.username or "مستخدم"
    except:
        username = "مستخدم"

    await event.respond(
        f"👋 **أهلاً بك عزيزي `{username}`**\n\n"
        f"💰 **في قسم شراء النقاط:**\n"
        f"📌 **سعر الـ 60 نقطة = 100 نجمة ⭐**\n\n"
        f"👇 **يمكنك الشحن التلقائي فوراً بالنجوم أو مراسلة المطور:**",
        parse_mode="markdown",
        buttons=[
            [
                Button.inline(
                    "⭐ كتابة عدد النقاط للشحن بالنجوم", "enter_points_amount"
                )
            ],
            [
                Button.url("📩 مراسلة المطور", "https://t.me/J_D_D_M"),
                Button.url(
                    "🚫 التواصل مع المحظورين",
                    "https://t.me/su_p_po_rt_3fro_to_sm_s_bot",
                ),
            ],
            [Button.inline("🔙 العودة للقائمة الرئيسية", "user_main_menu")],
        ],
    )


# 2. استقبال عدد النقاط من العميل وحساب النجوم وإرسال فاتورة التلجرام
@client.on(events.CallbackQuery(data="enter_points_amount"))
async def ask_for_points(event):
    async with client.conversation(event.sender_id) as conv:
        await conv.send_message("✍️ **أدخل عدد النقاط التي تريد شراءها:**")

        # استقبال الرقم من العميل
        response = await conv.get_response()

        if not response.text.isdigit():
            await conv.send_message("❌ **عفواً، يرجى كتابة رقم صحيح فقط.**")
            return

        points = int(response.text)
        if points <= 0:
            await conv.send_message("❌ **يرجى إدخال عدد أكبر من 0.**")
            return

        # حساب السعر بالنجوم (60 نقطة = 100 نجمة)
        stars_required = round((points / 60) * 100)
        if stars_required < 1:
            stars_required = 1  # الحد الأدنى نجمة واحدة

        # إرسال فاتورة نجوم التلجرام الرسمية للمستخدم
        invoice_title = f"شراء {points} نقطة"
        invoice_description = (
            f"شحن حسابك بـ {points} نقطة مقابل {stars_required} نجمة"
        )
        payload = f"stars_charge_{points}_{event.sender_id}"

        prices = [
            LabeledPrice(label=f"{points} نقطة", amount=stars_required)
        ]

        await client.send_file(
            event.chat_id,
            file=InputMediaInvoice(
                title=invoice_title,
                description=invoice_description,
                invoice=f"stars_invoice_{points}",
                payload=payload.encode(),
                provider="",  # فارغ لنجوم التلجرام
                currency="XTR",  # رمز نجوم التلجرام
                prices=prices,
                start_parameter="buy-points",
            ),
        )


# 3. معالجة الدفع التلقائي عند إتمام خصم النجوم وإضافة النقاط للمستخدم
@client.on(events.Raw)
async def payment_successful_handler(event):
    from telethon.tl.types import MessageActionPaymentSentMe

    if hasattr(event, "message") and hasattr(event.message, "action"):
        if isinstance(event.message.action, MessageActionPaymentSentMe):
            action = event.message.action
            payload = action.payload.decode()

            if payload.startswith("stars_charge_"):
                parts = payload.split("_")
                points_to_add = int(parts[2])
                user_id = int(parts[3])

                # ----------------------------------------------------
                # 📝 اضف كود قاعدة البيانات لتزويد نقاط المستخدم هنا
                # مثال: add_user_points(user_id, points_to_add)
                # ----------------------------------------------------

                await client.send_message(
                    user_id,
                    f"✅ **تم الشحن بنجاح عبر نجوم التلجرام!**\n\n"
                    f"🎉 تمت إضافة **{points_to_add}** نقطة إلى حسابك تلقائياً.",
                )
                

async def hndl_a_main(event):
    send_func = event.respond if isinstance(event, events.NewMessage.Event) else event.edit
    await send_func(
        '👋 **أهلاً بك في لوحة تحكم الأدمن**', parse_mode='markdown',
        buttons=[
            [
                Button.inline('📱 قسم الأرقام', 'admin_numbers_section'),
                Button.inline('👥 قسم الأدمنية', 'admin_admins_section')
            ],
            [
                Button.inline('💰 قسم البيع والشراء', 'admin_sales_section'),
                Button.inline('💳 قسم الرصيد', 'admin_balance_section')
            ],
            [
                Button.inline('📊 الإحصائيات', 'admin_stats_section'),
                Button.inline('⚙️ الإعدادات', 'admin_settings_section')
            ],
            [
                Button.inline('📢 الاشتراك الإجباري', 'admin_forced_sub_section')
            ]
        ]
    )

async def hndl_a_nums(event):
    await event.edit(
        '📱 **إدارة الأرقام**', parse_mode='markdown',
        buttons=[
            [
                Button.inline('➕ إضافة رقم جديد للبيع', 'add_new_number'),
                Button.inline('📋 عرض الأرقام المضافة', 'view_added_numbers')
            ],
            [
                Button.inline('🗑️ حذف الأرقام المعروضة', 'delete_displayed_numbers')
            ],
            [
                Button.inline('🔙 العودة', 'main_admin_menu')
            ]
        ]
    )

async def hndl_a_add(event):
    await event.edit('🔄 جارٍ بدء عملية إضافة الرقم...')
    new_acc, sale_details = await add_num(event)
    if new_acc and sale_details:
        u_sessions.update(new_acc)
        avail_nums.update(sale_details)
        save_all()
        for phone, info in new_acc.items():
            asyncio.create_task(init_acc(phone, info['api_id'], info['api_hash'], info['session_str']))
    else:
        await event.edit('❌ تم إلغاء عملية إضافة الرقم أو فشلت.', buttons=[[Button.inline("🔙 العودة", data='admin_numbers_section')]])

async def hndl_a_view_num(event, phone):
    if phone in avail_nums:
        details = avail_nums[phone]
        status = details.get('status', 'N/A')
        category = details.get('category', 'غير محدد')
        cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
        emoji = ""
        txt = ""

        if status == 'available':
            emoji = "🟢"
            txt = "متاح"
        elif status == 'booked':
            emoji = "🟡"
            booked_by = details.get('booked_by', 'N/A')
            expiry = details.get('expiry_time')
            if expiry:
                rem_sec = max(0, int(expiry - time.time()))
                mins = rem_sec // 60
                secs = rem_sec % 60
                txt = f"محجوز لـ `{booked_by}` ({mins:02d}:{secs:02d} متبقي)"
            else:
                txt = f"محجوز لـ `{booked_by}`"
        elif status == 'sold':
            emoji = "🔴"
            txt = f"مباع للمستخدم `{details.get('buyer_id', 'غير معروف')}`"

        message = (
            f"**📱 تفاصيل الرقم:**\n"
            f"📞 الرقم: `{phone}`\n"
            f"🌍 الدولة: {details.get('country', 'N/A')}\n"
            f"🏷️ التصنيف: {cat_emoji} {category}\n"
            f"💰 السعر (نقاط): {details.get('price_points', 0)}\n"
            f"⭐ السعر (نجوم): {details.get('price_stars', 0)}\n"
            f"{emoji} الحالة: {txt}\n"
            f"👤 بواسطة: `{details.get('added_by', 'غير معروف')}`\n"
        )
        buttons = []
        if status == 'booked':
            buttons.append([Button.inline("❌ إلغاء الحجز", data=f"admin_cancel_booking:{phone}")])
        buttons.append([Button.inline("🔙 العودة لقائمة الأرقام", data="view_added_numbers")])

        await event.edit(message, parse_mode='markdown', buttons=buttons)

async def hndl_a_end_book(event, phone):
    if phone in avail_nums and avail_nums[phone]['status'] == 'booked':
        await end_resv(phone)
        await event.answer("✅ تم إلغاء الحجز بنجاح.", alert=True)
        await show_a_nums(event)
    else:
        await event.answer("❌ الحجز غير موجود أو انتهى بالفعل.", alert=True)
        await show_a_nums(event)

async def hndl_a_del_conf(event, phone):
    if phone in avail_nums:
        buttons = [
            [
                Button.inline("✅ تأكيد الحذف", data=f"delete_number_execute:{phone}"),
                Button.inline("❌ إلغاء", data="delete_displayed_numbers")
            ]
        ]
        await event.edit(f"⚠️ **هل أنت متأكد من حذف الرقم `{phone}`؟**\nسيتم حذف جميع بياناته.", buttons=buttons, parse_mode='markdown')
    else:
        await event.answer("❌ الرقم غير موجود.", alert=True)
        await show_a_del(event)

async def hndl_a_del_exec(event, phone):
    if phone in avail_nums:
        if phone in u_clients:
            await u_clients[phone].disconnect()
            del u_clients[phone]
        if phone in res_timers:
            res_timers[phone].cancel()
            del res_timers[phone]

        del avail_nums[phone]
        if phone in u_sessions:
            del u_sessions[phone]
        save_all()
        await event.answer(f"✅ تم حذف الرقم `{phone}` بنجاح.", alert=True)
        await show_a_del(event)
    else:
        await event.answer("❌ الرقم غير موجود.", alert=True)
        await show_a_del(event)

async def hndl_a_adm_sec(event):
    await event.edit(
        '👥 **إدارة الأدمنية**', parse_mode='markdown',
        buttons=[
            [
                Button.inline('➕ رفع أدمن', 'admin_promote_admin'),
                Button.inline('➖ تنزيل أدمن', 'admin_demote_admin')
            ],
            [
                Button.inline('📋 عرض الأدمنية', 'admin_view_admins')
            ],
            [
                Button.inline('🔙 العودة', 'main_admin_menu')
            ]
        ]
    )

async def hndl_a_promo(event):
    async with client.conversation(event.sender_id, timeout=120) as conv:
        await conv.send_message("📤 أرسل آي دي المستخدم لترفعه كأدمن:", buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]])
        user_resp = await conv.get_response()
        if user_resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])
            return
        user_to_promo = user_resp.text.strip()
        if not user_to_promo.isdigit():
            await conv.send_message("❌ آي دي غير صالح.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])
            return
        if user_to_promo in syyad_conf['admin_ids']:
            await conv.send_message("⚠️ المستخدم هو أدمن بالفعل.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])
        else:
            syyad_conf['admin_ids'].append(user_to_promo)
            save_all()
            await conv.send_message(f"✅ تمت ترقية المستخدم `{user_to_promo}` كأدمن.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])

async def hndl_a_demote(event):
    async with client.conversation(event.sender_id, timeout=120) as conv:
        await conv.send_message("📤 أرسل آي دي المستخدم لتنزيله من الأدمنية:", buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]])
        user_resp = await conv.get_response()
        if user_resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])
            return
        user_to_demote = user_resp.text.strip()
        if not user_to_demote.isdigit():
            await conv.send_message("❌ آي دي غير صالح.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])
            return
        if user_to_demote not in syyad_conf['admin_ids']:
            await conv.send_message("⚠️ المستخدم ليس أدمن.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])
        elif user_to_demote == str(event.sender_id):
            await conv.send_message("❌ لا يمكنك تنزيل نفسك من الأدمنية.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])
        else:
            syyad_conf['admin_ids'].remove(user_to_demote)
            save_all()
            await conv.send_message(f"✅ تم تنزيل المستخدم `{user_to_demote}` من الأدمنية.", buttons=[[Button.inline("🔙 العودة لقسم الأدمنية", data='admin_admins_section')]])

async def hndl_a_sale_sec(event):
    await event.edit(
        '💰 **إدارة البيع والشراء**', parse_mode='markdown',
        buttons=[
            [
                Button.inline('📋 عرض الأرقام المباعة', 'admin_view_sold_numbers'),
                Button.inline('📋 عرض الأرقام المتاحة', 'admin_view_available_numbers')
            ],
            [
                Button.inline('🔙 العودة', 'main_admin_menu')
            ]
        ]
    )

async def hndl_a_sold(event):
    sold_nums = [num for num, details in avail_nums.items() if details.get('status') == 'sold']
    if not sold_nums:
        await event.edit("لا توجد أرقام مباعة حالياً.", buttons=[[Button.inline("🔙 العودة لقسم البيع والشراء", data="admin_sales_section")]])
        return

    lines = []
    for phone in sold_nums:
        details = avail_nums[phone]
        category = details.get('category', 'غير محدد')
        cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
        lines.append(
            f"📞 الرقم: `{phone}`\n"
            f"🏷️ التصنيف: {cat_emoji} {category}\n"
            f"💰 السعر (نقاط): {details.get('price_points', 0)}\n"
            f"⭐ السعر (نجوم): {details.get('price_stars', 0)}\n"
            f"المشتري: `{details.get('buyer_id', 'غير معروف')}`\n"
            f"--------------------"
        )
    await event.edit(
        "**📋 قائمة الأرقام المباعة:**\n\n" + "\n".join(lines),
        buttons=[[Button.inline("🔙 العودة لقسم البيع والشراء", data="admin_sales_section")]],
        parse_mode='markdown'
    )

async def hndl_a_avail(event):
    avail_filter = [num for num, details in avail_nums.items() if details.get('status') == 'available']
    if not avail_filter:
        await event.edit("لا توجد أرقام متاحة للبيع حالياً.", buttons=[[Button.inline("🔙 العودة لقسم البيع والشراء", data="admin_sales_section")]])
        return

    lines = []
    for phone in avail_filter:
        details = avail_nums[phone]
        category = details.get('category', 'غير محدد')
        cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
        lines.append(
            f"📞 الرقم: `{phone}`\n"
            f"🌍 الدولة: {details.get('country', 'N/A')}\n"
            f"🏷️ التصنيف: {cat_emoji} {category}\n"
            f"💰 السعر (نقاط): {details.get('price_points', 0)}\n"
            f"⭐ السعر (نجوم): {details.get('price_stars', 0)}\n"
            f"--------------------"
        )
    await event.edit(
        "**📋 قائمة الأرقام المتاحة للبيع:**\n\n" + "\n".join(lines),
        buttons=[[Button.inline("🔙 العودة لقسم البيع والشراء", data="admin_sales_section")]],
        parse_mode='markdown'
    )

async def hndl_a_bal_sec(event):
    await event.edit(
        '💳 **إدارة أرصدة المستخدمين**', parse_mode='markdown',
        buttons=[
            [
                Button.inline('➕ إضافة نقاط لمستخدم', 'admin_add_points'),
                Button.inline('➕ إضافة نجوم لمستخدم', 'admin_add_stars')
            ],
            [
                Button.inline('🔙 العودة', 'main_admin_menu')
            ]
        ]
    )

async def hndl_a_add_pts(event):
    async with client.conversation(event.sender_id, timeout=120) as conv:
        await conv.send_message("📤 أرسل آي دي المستخدم لإضافة النقاط له:", buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]])
        uid_resp = await conv.get_response()
        if uid_resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
            return
        target_uid = uid_resp.text.strip()
        if not target_uid.isdigit():
            await conv.send_message("❌ آي دي غير صالح.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
            return

        await conv.send_message("💰 أرسل عدد النقاط لإضافتها:", buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]])
        pts_resp = await conv.get_response()
        if pts_resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
            return
        try:
            pts_amount = int(pts_resp.text.strip())
            if pts_amount <= 0: raise ValueError
        except ValueError:
            await conv.send_message("❌ عدد نقاط غير صالح.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
            return

        user_bal = get_syyad_bal(target_uid)
        user_bal['points'] += pts_amount
        save_all()
        
        await conv.send_message(f"✅ تم استلام `{pts_amount}` نقطة من قبل المالك.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
        
        try:
            await client.send_message(
                int(target_uid),
                f"💰 **تم إضافة نقاط إلى رصيدك!**\n\n"
                f"تم استلام `{pts_amount}` نقطة من قبل المالك.\n"
                f"رصيدك الحالي: `{user_bal['points']}` نقطة.",
                parse_mode='markdown'
            )
        except:
            pass

async def hndl_a_add_star(event):
    async with client.conversation(event.sender_id, timeout=120) as conv:
        await conv.send_message("📤 أرسل آي دي المستخدم لإضافة النجوم له:", buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]])
        uid_resp = await conv.get_response()
        if uid_resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
            return
        target_uid = uid_resp.text.strip()
        if not target_uid.isdigit():
            await conv.send_message("❌ آي دي غير صالح.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
            return

        await conv.send_message("⭐ أرسل عدد النجوم لإضافتها:", buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]])
        star_resp = await conv.get_response()
        if star_resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
            return
        try:
            star_amount = int(star_resp.text.strip())
            if star_amount <= 0: raise ValueError
        except ValueError:
            await conv.send_message("❌ عدد نجوم غير صالح.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])
            return

        user_bal = get_syyad_bal(target_uid)
        user_bal['stars'] += star_amount
        save_all()
        await conv.send_message(f"✅ تم إضافة `{star_amount}` نجمة للمستخدم `{target_uid}`. رصيده الحالي: `{user_bal['stars']}` نجمة.", buttons=[[Button.inline("🔙 العودة لقسم الرصيد", data='admin_balance_section')]])

async def hndl_a_stats_sec(event):
    await event.edit(
        '📊 **الإحصائيات**', parse_mode='markdown',
        buttons=[
            [
                Button.inline('📈 عرض الإحصائيات', 'admin_view_stats'),
                Button.inline('📋 سجل المبيعات', 'admin_sales_history')
            ],
            [
                Button.inline('🔙 العودة', 'main_admin_menu')
            ]
        ]
    )

async def hndl_a_set_sec(event):
    await event.edit(
        '⚙️ **إعدادات البوت**', parse_mode='markdown',
        buttons=[
            [
                Button.inline('تحديد قناة النشر', 'admin_set_publish_channel')
            ],
            [
                Button.inline('تحديد قناة البوت', 'admin_set_bot_channel')
            ],
            [
                Button.inline('🔙 العودة', 'main_admin_menu')
            ]
        ]
    )

async def hndl_a_set_chan(event):
    async with client.conversation(event.sender_id, timeout=120) as conv:
        curr_chan = syyad_conf.get('publish_channel_id', 'لم يتم التعيين')
        await conv.send_message(
            f"📢 القناة الحالية للنشر: `{curr_chan}`\n"
            "أرسل الآن معرف القناة الجديد (مثال: `@username` أو `-100123456789`). "
            "أرسل 'حذف' لإلغاء النشر التلقائي.",
            buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]]
        )
        resp = await conv.get_response()
        if resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الإعدادات", data='admin_settings_section')]])
            return
        
        new_chan_id = resp.text.strip()
        if new_chan_id.lower() == 'حذف':
            syyad_conf['publish_channel_id'] = None
            msg = "✅ تم إلغاء قناة النشر."
        else:
            syyad_conf['publish_channel_id'] = new_chan_id
            msg = f"✅ تم تحديث قناة النشر إلى `{new_chan_id}`."
        
        save_all()
        await conv.send_message(msg, buttons=[[Button.inline("🔙 العودة لقسم الإعدادات", data='admin_settings_section')]])

async def hndl_a_set_bot_chan(event):
    async with client.conversation(event.sender_id, timeout=120) as conv:
        curr_chan = syyad_conf.get('bot_channel_url', 'https://t.me/AF_R_O_TO')
        await conv.send_message(
            f"📢 رابط قناة البوت الحالي: `{curr_chan}`\n"
            "أرسل الرابط الجديد لقناة البوت:",
            buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]]
        )
        resp = await conv.get_response()
        if resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الإعدادات", data='admin_settings_section')]])
            return
        
        new_chan_url = resp.text.strip()
        syyad_conf['bot_channel_url'] = new_chan_url
        save_all()
        await conv.send_message(f"✅ تم تحديث رابط قناة البوت إلى `{new_chan_url}`.", buttons=[[Button.inline("🔙 العودة لقسم الإعدادات", data='admin_settings_section')]])

async def hndl_a_forced_sub_section(event):
    await event.edit(
        '📢 **إدارة الاشتراك الإجباري**\n\n'
        f'عدد القنوات: {len(forced_subs)}',
        parse_mode='markdown',
        buttons=[
            [
                Button.inline('➕ إضافة قناة', 'admin_add_forced_channel'),
                Button.inline('🗑️ حذف قناة', 'admin_remove_forced_channel')
            ],
            [
                Button.inline('📋 عرض القنوات', 'admin_view_forced_channels')
            ],
            [
                Button.inline('🔙 العودة', 'main_admin_menu')
            ]
        ]
    )

async def hndl_a_add_forced_channel(event):
    async with client.conversation(event.sender_id, timeout=120) as conv:
        await conv.send_message(
            "📤 أرسل رابط القناة التي تريد إضافتها للاشتراك الإجباري:\n"
            "مثال: @username أو https://t.me/username",
            buttons=[[Button.inline("❌ إلغاء", data='cancel_op')]]
        )
        resp = await conv.get_response()
        if resp.text == 'إلغاء':
            await conv.send_message("تم الإلغاء.", buttons=[[Button.inline("🔙 العودة لقسم الاشتراك الإجباري", data='admin_forced_sub_section')]])
            return
        
        channel = resp.text.strip()
        if channel not in forced_subs:
            forced_subs.append(channel)
            save_all()
            await conv.send_message(f"✅ تم إضافة القناة `{channel}` بنجاح.", buttons=[[Button.inline("🔙 العودة لقسم الاشتراك الإجباري", data='admin_forced_sub_section')]])
        else:
            await conv.send_message("⚠️ هذه القناة موجودة بالفعل.", buttons=[[Button.inline("🔙 العودة لقسم الاشتراك الإجباري", data='admin_forced_sub_section')]])

async def hndl_a_remove_forced_channel(event):
    if not forced_subs:
        await event.edit("لا توجد قنوات مضاف حالياً.", buttons=[[Button.inline("🔙 العودة لقسم الاشتراك الإجباري", data='admin_forced_sub_section')]])
        return
    
    buttons = []
    for channel in forced_subs:
        buttons.append([Button.inline(f"🗑️ حذف {channel}", data=f"remove_forced_channel:{channel}")])
    buttons.append([Button.inline("🔙 العودة", data="admin_forced_sub_section")])
    
    await event.edit("اختر القناة التي تريد حذفها:", buttons=buttons)

async def hndl_a_remove_forced_channel_exec(event, channel):
    if channel in forced_subs:
        forced_subs.remove(channel)
        save_all()
        await event.answer(f"✅ تم حذف القناة `{channel}` بنجاح.", alert=True)
        await hndl_a_remove_forced_channel(event)
    else:
        await event.answer("❌ القناة غير موجودة.", alert=True)

async def hndl_a_view_forced_channels(event):
    if not forced_subs:
        await event.edit("لا توجد قنوات مضاف حالياً.", buttons=[[Button.inline("🔙 العودة لقسم الاشتراك الإجباري", data='admin_forced_sub_section')]])
        return
    
    channels_text = "\n".join([f"• {channel}" for channel in forced_subs])
    await event.edit(
        f"📋 **قائمة قنوات الاشتراك الإجباري:**\n\n{channels_text}",
        parse_mode='markdown',
        buttons=[[Button.inline("🔙 العودة لقسم الاشتراك الإجباري", data='admin_forced_sub_section')]]
    )

async def hndl_u_view(event, phone, uid):
    if phone in avail_nums:
        details = avail_nums[phone]
        status = details.get('status')
        category = details.get('category', 'غير محدد')
        cat_emoji = CATEGORY_EMOJIS.get(category, "📞")
        pts_price = details.get('price_points', 0)
        star_price = details.get('price_stars', 0)
        hidden_phone = hide_number(phone)

        message = (
            f"**📱 تفاصيل الرقم `{hidden_phone}`:**\n\n"
            f"🌍 الدولة: {details['country']}\n"
            f"🏷️ التصنيف: {cat_emoji} {category}\n"
        )
        if pts_price > 0:
            message += f"💰 السعر بالنقاط: `{pts_price}`\n"
        if star_price > 0:
            message += f"⭐ السعر بالنجوم: `{star_price}`\n"

        buttons = []
        action_btns = []
        if status == 'available':
            if pts_price > 0:
                action_btns.append(Button.inline(f"💳 شراء بـ {pts_price} نقطة", data=f"choose_payment_method:{phone}:points_only"))
            if star_price > 0:
                action_btns.append(Button.inline(f"⭐ شراء بـ {star_price} نجمة", data=f"choose_payment_method:{phone}:stars_only"))
            if action_btns:
                buttons.append(action_btns)
        elif status == 'booked' and str(details.get('booked_by')) == uid:
            message += f"**🔔 حالة الحجز:** محجوز لك!\n"
            if details.get('expiry_time'):
                rem_sec = max(0, int(details['expiry_time'] - time.time()))
                mins = rem_sec // 60
                secs = rem_sec % 60
                message += f"⏱️ الوقت المتبقي: `{mins:02d}:{secs:02d}` دقيقة\n\n"

            if pts_price > 0:
                action_btns.append(Button.inline(f"💳 إتمام الشراء ({pts_price} نقاط)", data=f"choose_payment_method:{phone}:points_only"))
            if star_price > 0:
                action_btns.append(Button.inline(f"⭐ إتمام الشراء ({star_price} نجوم)", data=f"choose_payment_method:{phone}:stars_only"))
            
            if action_btns:
                buttons.append(action_btns)
            
            buttons.append([Button.inline("❌ إلغاء الحجز", data=f"user_cancel_booking:{phone}")])
        elif status == 'booked' and str(details.get('booked_by')) != uid:
             await event.answer("⚠️ هذا الرقم محجوز لمستخدم آخر حالياً.", alert=True)
             await show_u_buy_menu(event)
             return
        elif status == 'sold':
            await event.answer("❌ هذا الرقم مباع بالفعل.", alert=True)
            await show_u_buy_menu(event)
            return

        buttons.append([Button.inline("🔙 العودة للتصنيفات", data="user_buy_number_menu")])
        await event.edit(message, parse_mode='markdown', buttons=buttons)
    else:
        await event.answer("❌ الرقم لم يعد متاحاً.", alert=True)
        await show_u_buy_menu(event)

async def hndl_u_endb_conf(event, phone, uid):
    if phone in avail_nums and avail_nums[phone]['status'] == 'booked' and str(avail_nums[phone]['booked_by']) == uid:
        buttons = [
            [
                Button.inline("✅ نعم، إلغاء الحجز", data=f"execute_user_cancel_booking:{phone}"),
                Button.inline("❌ لا، العودة", data=f"view_number_details:{phone}")
            ]
        ]
        await event.edit("⚠️ **هل أنت متأكد من إلغاء حجز الرقم؟**\nلن يتم استرداد مبلغ الحجز.", buttons=buttons)
    else:
        await event.answer("❌ هذا الرقم ليس محجوزاً لك.", alert=True)
        await show_u_buy_menu(event)

async def hndl_u_endb_exec(event, phone, uid):
    if phone in avail_nums and avail_nums[phone]['status'] == 'booked' and str(avail_nums[phone]['booked_by']) == uid:
        await end_resv(phone)
        await event.answer("✅ تم إلغاء الحجز بنجاح.", alert=True)
        await show_u_buy_menu(event)
    else:
        await event.answer("❌ هذا الرقم ليس محجوزاً لك أو الحجز انتهى.", alert=True)
        await show_u_buy_menu(event)

async def hndl_u_pay_meth(event, phone, pay_type, uid):
    if phone not in avail_nums:
        await event.answer("❌ الرقم لم يعد متاحاً.", alert=True)
        await show_u_buy_menu(event)
        return

    details = avail_nums[phone]
    user_bal = get_syyad_bal(uid)

    if pay_type == 'points_only':
        pts_to_pay = details.get('price_points', 0)
        if pts_to_pay > 0 and user_bal['points'] >= pts_to_pay:
            user_bal['points'] -= pts_to_pay
            user_bal['purchases'] += 1
            avail_nums[phone]['status'] = 'sold'
            avail_nums[phone]['buyer_id'] = uid
            code_reqs[phone] = event.sender_id
            
            stats['total_sales'] += 1
            stats['total_revenue_points'] += pts_to_pay
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            stats['daily_sales'][today] = stats['daily_sales'].get(today, 0) + 1
            
            buyer_username = "غير معروف"
            try:
                buyer_entity = await client.get_entity(int(uid))
                if buyer_entity.username:
                    buyer_username = f"@{buyer_entity.username}"
                else:
                    buyer_username = buyer_entity.first_name or "مستخدم"
            except:
                buyer_username = uid
            
            category = details.get('category', 'غير محدد')
            stats['sales_history'].append({
                'phone': phone,
                'country': details.get('country', 'N/A'),
                'category': category,
                'price_points': pts_to_pay,
                'price_stars': details.get('price_stars', 0),
                'buyer_id': buyer_username,
                'date': today
            })
            
            save_all()
            await edit_post(phone)
            
            await send_channel_message(phone, details.get('country', 'N/A'), pts_to_pay, details.get('price_stars', 0), category)
            
            await event.edit(
                f"✅ **تمت عملية الشراء بنجاح للرقم `{phone}`.**\n\n"
                f"🌍 الدولة: {details.get('country', 'N/A')}\n"
                f"🏷️ التصنيف: {CATEGORY_EMOJIS.get(category, '📞')} {category}\n"
                f"💰 المبلغ: {pts_to_pay} نقطة\n"
                f"⭐ النجوم: {details.get('price_stars', 0)}"
            )
            
            admin_notify_msg = f"✅ تم شراء الرقم `{phone}` بواسطة `{buyer_username}` (بالنقاط)."
            await client.send_message(
                int(syyad_conf['admin_ids'][0]),
                admin_notify_msg,
                parse_mode='markdown'
            )
        else:
            await event.answer("❌ نقاطك غير كافية لإتمام عملية الشراء.", alert=True)
            await event.edit("❌ **نقاطك غير كافية.**", buttons=[
                [Button.inline("🔙 العودة", data=f"view_number_details:{phone}")]
            ])
    elif pay_type == 'stars_only':
        await event.edit(
            "❌ **الدفع بالنجوم غير متاح حالياً**\n\n"
            "**راس المطور Jjf_y**\n\n"
            "للتواصل مع المطور لحل مشكلة الدفع.",
            parse_mode='markdown',
            buttons=[[Button.inline("🔙 العودة", data=f"view_number_details:{phone}")]]
        )
    else:
        await event.answer("❌ طريقة دفع غير صالحة.", alert=True)

async def hndl_u_get_ref(event, uid):
    bot_info = await client.get_me()
    bot_user = bot_info.username
    ref_link = f"https://t.me/{bot_user}?start=ref_{uid}"
    await event.edit(
        f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
        f"📢 شارك هذا الرابط مع أصدقائك.\n"
        f"ستحصل على `{syyad_conf.get('referralPoints', 0)}` نقطة لكل مستخدم جديد يسجل عبر رابطك.",
        parse_mode='markdown',
        buttons=[[Button.inline("🔙 العودة", data="user_main_menu")]]
    )

async def hndl_u_show_terms(event):
    await event.edit(
        TERMS_TEXT,
        parse_mode='markdown',
        buttons=[[Button.inline("🔙 العودة للقائمة الرئيسية", data="user_main_menu")]]
    )

async def hndl_get_code(event, phone):
    buyer_id = str(event.sender_id)
    if phone in avail_nums and avail_nums[phone]['status'] == 'sold' and str(avail_nums[phone]['buyer_id']) == buyer_id:
        try:
            if phone not in u_clients or not u_clients[phone].is_connected():
                acc_details = u_sessions.get(phone)
                if acc_details:
                    await init_acc(phone, acc_details['api_id'], acc_details['api_hash'], acc_details['session_str'])
                    await asyncio.sleep(2)
            
            if phone in u_clients and u_clients[phone].is_connected():
                try:
                    await u_clients[phone].send_code_request(phone)
                    await event.edit(
                        "🔄 **جارٍ طلب الكود...**\n"
                        f"📱 الرقم: `{phone}`\n\n"
                        "✅ سيصلك الكود خلال لحظات.",
                        parse_mode='markdown'
                    )
                except Exception as e:
                    await event.edit(
                        f"❌ **حدث خطأ أثناء طلب الكود:**\n`{str(e)}`\n\n"
                        "يرجى المحاولة مرة أخرى.",
                        parse_mode='markdown',
                        buttons=[[Button.inline("📲 المحاولة مرة أخرى", data=f"get_code:{phone}")]]
                    )
            else:
                await event.edit(
                    "❌ **تعذر الاتصال بالرقم.**\n\n"
                    "يرجى المحاولة مرة أخرى.",
                    parse_mode='markdown',
                    buttons=[[Button.inline("📲 المحاولة مرة أخرى", data=f"get_code:{phone}")]]
                )
        except Exception as e:
            await event.edit(
                f"❌ **حدث خطأ:**\n`{str(e)}`\n\n"
                "يرجى المحاولة مرة أخرى.",
                parse_mode='markdown',
                buttons=[[Button.inline("📲 المحاولة مرة أخرى", data=f"get_code:{phone}")]]
            )
    else:
        await event.answer("❌ هذا الرقم غير مسجل كرقم مشترى لك.", alert=True)

@client.on(events.NewMessage(pattern='/start(?: ref_(\\d+))?'))
async def hndl_start(event):
    uid = str(event.sender_id)
    ref_id = event.pattern_match.group(1)

    is_new = uid not in syyad_users
    get_syyad_bal(uid)
    
    if is_new and ref_id and ref_id != uid:
        if 'referred_by' not in syyad_users.get(uid, {}):
            get_syyad_bal(ref_id)
            syyad_users[uid]['referred_by'] = ref_id
            ref_pts = syyad_conf.get('referralPoints', 0)
            if ref_pts > 0:
                syyad_users[ref_id]['points'] += ref_pts
                save_all()
                await client.send_message(int(ref_id), f"🎉 لقد ربحت `{ref_pts}` نقطة من إحالة مستخدم جديد!")

    if is_adm(event.sender_id):
        await hndl_a_main(event)
    else:
        await show_u_main(event)

@client.on(events.CallbackQuery)
async def hndl_cb(event):
    uid = str(event.sender_id)
    data = event.data.decode()

    if data == 'dummy_sep':
        await event.answer()
        return

    if data == 'check_subscription':
        if await check_forced_subscription(event.sender_id):
            await event.edit("✅ **تم التحقق بنجاح!** يمكنك الآن استخدام البوت.")
            await show_u_main(event)
        else:
            await event.answer("❌ ما زلت غير مشترك في جميع القنوات.", alert=True)
        return

    if is_adm(uid):
        if data == 'main_admin_menu': await hndl_a_main(event)
        elif data == 'admin_numbers_section': await hndl_a_nums(event)
        elif data == 'add_new_number': await hndl_a_add(event)
        elif data == 'view_added_numbers': await show_a_nums(event)
        elif data.startswith('view_specific_number:'): await hndl_a_view_num(event, data.split(':', 1)[1])
        elif data.startswith('admin_cancel_booking:'): await hndl_a_end_book(event, data.split(':', 1)[1])
        elif data == 'delete_displayed_numbers': await show_a_del(event)
        elif data.startswith('delete_number_confirm:'): await hndl_a_del_conf(event, data.split(':', 1)[1])
        elif data.startswith('delete_number_execute:'): await hndl_a_del_exec(event, data.split(':', 1)[1])
        elif data == 'admin_admins_section': await hndl_a_adm_sec(event)
        elif data == 'admin_promote_admin': await hndl_a_promo(event)
        elif data == 'admin_demote_admin': await hndl_a_demote(event)
        elif data == 'admin_view_admins': await show_a_list(event)
        elif data == 'admin_sales_section': await hndl_a_sale_sec(event)
        elif data == 'admin_view_sold_numbers': await hndl_a_sold(event)
        elif data == 'admin_view_available_numbers': await hndl_a_avail(event)
        elif data == 'admin_balance_section': await hndl_a_bal_sec(event)
        elif data == 'admin_add_points': await hndl_a_add_pts(event)
        elif data == 'admin_add_stars': await hndl_a_add_star(event)
        elif data == 'admin_stats_section': await hndl_a_stats_sec(event)
        elif data == 'admin_view_stats': await show_stats(event)
        elif data == 'admin_sales_history': await show_a_sales_history(event)
        elif data == 'admin_stats_back': await hndl_a_stats_sec(event)
        elif data == 'admin_settings_section': await hndl_a_set_sec(event)
        elif data == 'admin_set_publish_channel': await hndl_a_set_chan(event)
        elif data == 'admin_set_bot_channel': await hndl_a_set_bot_chan(event)
        elif data == 'admin_forced_sub_section': await hndl_a_forced_sub_section(event)
        elif data == 'admin_add_forced_channel': await hndl_a_add_forced_channel(event)
        elif data == 'admin_remove_forced_channel': await hndl_a_remove_forced_channel(event)
        elif data.startswith('remove_forced_channel:'): await hndl_a_remove_forced_channel_exec(event, data.split(':', 1)[1])
        elif data == 'admin_view_forced_channels': await hndl_a_view_forced_channels(event)
        elif data == 'cancel_op': await event.edit("❌ تم الإلغاء.", buttons=[[Button.inline("🔙 العودة", data='main_admin_menu')]])
    else:
        if data == 'user_main_menu': 
            await show_u_main(event)
        elif data == 'user_show_terms':
            await hndl_u_show_terms(event)
        elif data == 'user_bot_channel':
            await show_u_bot_channel(event)
        elif data == 'user_support_menu':
            await show_u_support(event)
        elif data == 'user_help_info':
            await show_u_help_info(event)
        elif data == 'user_buy_number_menu': 
            await show_u_buy_menu(event)
        elif data.startswith('buy_category:'): 
            await show_u_nums_by_category(event, data.split(':', 1)[1])
        elif data.startswith('view_number_details:'): 
            await hndl_u_view(event, data.split(':', 1)[1], uid)
        elif data.startswith('user_cancel_booking:'): 
            await hndl_u_endb_conf(event, data.split(':', 1)[1], uid)
        elif data.startswith('execute_user_cancel_booking:'): 
            await hndl_u_endb_exec(event, data.split(':', 1)[1], uid)
        elif data.startswith('choose_payment_method:'): 
            await hndl_u_pay_meth(event, *data.split(':', 2)[1:], uid)
        elif data.startswith('get_code:'):
            await hndl_get_code(event, data.split(':', 1)[1])
        elif data == 'user_charge_points_menu': 
            await show_u_chrg(event)
        elif data == 'user_get_referral_link': 
            await hndl_u_get_ref(event, uid)

@bot.pre_checkout_query_handler(func=lambda query: True)
def hndl_pre_cq(pre_cq):
    bot.answer_pre_checkout_query(pre_cq.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def hndl_paid(paid_msg):
    bot.send_message(paid_msg.chat.id, "❌ الدفع بالنجوم غير متاح حالياً. راس المطور Jjf_y")

async def run_syyad_app():
    load_all()

    await client.start(bot_token=BOT_TOKEN)
    await run_accs()
    await init_resv()

    await client.send_message(
        int(syyad_conf['admin_ids'][0]),
        "✅ **البوت اشتغل بنجاح!**\n\n"
        "📊 الإحصائيات جاهزة للعمل 🚀",
        parse_mode='markdown'
    )

    poll_thread = threading.Thread(target=run_poll, daemon=True)
    poll_thread.start()

    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(run_syyad_app())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        save_all()