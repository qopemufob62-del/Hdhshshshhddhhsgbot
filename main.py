#!/usr/bin/env python
# -*- coding: utf-8 -*-

#====================#
#CH : @J_D_D_M
#DEV : @J_D_D_M
#====================#
#
# ملف واحد مجمّع: main.py + plugins/messages.py + plugins/get_code.py + plugins/SessionConverter.py
# المكتبات الخارجية اتسابت عادي زي ما هي (نفس requirements.txt)، ملفش داعي تغيّر فيها حاجة.
#

# ===== مكتبات بايثون القياسية =====
import os
import re
import zipfile
import sys
import json
import time
import shutil
import uuid
import struct
import base64
import secrets
import sqlite3
import asyncio
import ipaddress
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Type, Optional
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import datetime
from datetime import timedelta
import random

# ===== مكتبات خارجية (ثبّتها زي ما هي عن طريق requirements.txt) =====
import aiosqlite
from kvsqlite.sync import Client as uu

# لازم قاعدة البيانات تتفتح من هنا بدري، لأن فيه كود تحت بيستخدم db قبل مكانها القديم
os.makedirs('database', exist_ok=True)
db = uu('database/FFJFF5.ss', 'bot')

# ============================================================
#  نظام الأقسام (سليم / سبام / احتيالي / أي قسم يضيفه الأدمن)
#  كل قسم له دوله + أسعاره + مخزونه المستقل. نفس الأقسام بتظهر في «حسابات تليجرام» و«شراء سيشن».
#  القسم "spam" بيستخدم مفاتيح قاعدة البيانات القديمة، فبياناتك الحالية بتفضل سليمة وبتبقى تحت قسم «سبام».
# ============================================================
import contextvars

DEFAULT_CAT = "spam"
# القسم الحالي أثناء معالجة حدث واحد (للأدمن = القسم اللي اختاره، لباقي الأكواد القديمة)
CUR_CAT = contextvars.ContextVar("cur_cat", default=None)

def _cat_or_default(cid=None):
    return cid or CUR_CAT.get() or DEFAULT_CAT

def countries_key(cid=None):
    cid = _cat_or_default(cid)
    return "countries" if cid == DEFAULT_CAT else f"countries__{cid}"

def accounts_key(code, cid=None):
    cid = _cat_or_default(cid)
    return f"accounts_{code}" if cid == DEFAULT_CAT else f"accounts__{cid}__{code}"

def stops_key(code, cid=None):
    cid = _cat_or_default(cid)
    return f"stops_{code}" if cid == DEFAULT_CAT else f"stops__{cid}__{code}"

def _dbget(key, default=None):
    if db.exists(key):
        value = db.get(key)
        return default if value is None else value
    return default

def get_categories():
    return _dbget("categories", []) or []

def get_category(cid):
    return next((c for c in get_categories() if c.get("id") == cid), None)

def get_category_name(cid=None):
    c = get_category(_cat_or_default(cid))
    return c["name"] if c else str(_cat_or_default(cid))

def cat_label():
    return get_category_name()

def get_admin_cat(user_id):
    cid = _dbget(f"admin_cat_{user_id}", None)
    if cid and get_category(cid):
        return cid
    cats = get_categories()
    if get_category(DEFAULT_CAT):
        return DEFAULT_CAT
    return cats[0]["id"] if cats else DEFAULT_CAT

def find_country(cid, code):
    for c in _dbget(countries_key(cid), []):
        if str(c.get("calling_code")) == str(code):
            return c
    return None

def category_stock(cid):
    total = 0
    for c in _dbget(countries_key(cid), []):
        total += len(_dbget(accounts_key(c.get("calling_code"), cid), []))
    return total

def _seed_categories():
    if not get_categories():
        db.set("categories", [
            {"id": "slim", "name": "سليم"},
            {"id": "spam", "name": "سبام"},
            {"id": "fraud", "name": "احتيالي"},
        ])
    for c in get_categories():
        k = countries_key(c["id"])
        if not db.exists(k):
            db.set(k, [])

def _short_cb(text, limit=10):
    """يقصّ النص لعدد بايتات صغير عشان داتا الزرار (حد تيليجرام 64 بايت)."""
    out = ""
    for ch in str(text):
        if len((out + ch).encode("utf-8")) > limit:
            break
        out += ch
    return out.replace(":", "").replace("_", "")

_seed_categories()


# الألوان بقت أزرار ملونة حقيقية (شوف قسم "أزرار ملونة" تحت)، فـ accent بقت بتسيب النص زي ما هو.
def accent(text: str) -> str:
    return text


def get_user_record(user_id):
    """بيرجع بيانات المستخدم، ولو مش موجود (لسه عمل /start) بيعمله واحدة جديدة برصيد صفر بدل ما يوقع البوت."""
    key = f"user_{user_id}"
    user = db.get(key) if db.exists(key) else None
    if not user:
        user = {"id": int(user_id), "coins": 0.0, "joined_at": time.time()}
        db.set(key, user)
    return user


from telethon import TelegramClient, events, functions, types, Button
from telethon.sessions import StringSession, SQLiteSession
from telethon.tl.types import (
    KeyboardButton, ReplyInlineMarkup, DocumentAttributeFilename,
    InputPeerUser, InputPeerChannel,
)
from telethon.tl.custom.conversation import Conversation
from telethon.tl.functions.account import GetAuthorizationsRequest
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.errors import (
    ApiIdInvalidError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
    UserNotParticipantError,
    ChannelPrivateError,
    ChatAdminRequiredError,
)
from telethon.errors.rpcerrorlist import UserDeactivatedBanError

# ============================================================
#  أزرار ملونة حقيقية (أزرق / أخضر / أحمر) بدل دوائر الإيموجي
#  تيليجرام بقى بيدعم ستايل للزراير (Bot API 9.4 / Layer 224+) ونسخة Telethon 1.43 أو أحدث بتدعمه:
#      pip install -U telethon
#  النظام: كل زرار بياخد لونه تلقائيًا من معناه — مفيش حاجة تتغير في باقي الكود:
#      🟢 أخضر = تأكيد / شراء / إضافة / شحن / تفعيل / قبول
#      🔴 أحمر = إلغاء / حذف / تعطيل / حظر / خصم / رفض
#      🔵 أزرق = كل حاجة تانية (تنقل، رجوع، قوايم، دول، معلومات)
#  لو نسخة Telethon عندك قديمة، الأزرار بتشتغل عادي بدون ألوان ومفيش أي خطأ.
# ============================================================
import re as _re_colors

_RED_AR = ("إلغاء", "الغاء", "رفض", "حذف", "تعطيل", "حظر", "خصم", "إزالة", "ازالة",
           "إيقاف", "ايقاف", "إغلاق", "اغلاق", "تنزيل", "❌")
_GREEN_AR = ("تأكيد", "تاكيد", "موافق", "شراء", "قبول", "تفعيل", "إضافة", "اضافة", "شحن", "إنشاء",
             "انشاء", "تحقق", "ارسلت", "إرسال", "استبدال", "تسجيل الدخول", "رفع", "اشتراك",
             "الاشتراك", "نعم", "✅")
_RED_EN = _re_colors.compile(r"\b(cancel|delete|remove|reject|disable|ban|close|stop)\b", _re_colors.I)
_GREEN_EN = _re_colors.compile(r"\b(buy|confirm|accept|enable|add|recharge|redeem|verify|create|send|login|subscribe)\b", _re_colors.I)
_GREEN_DATA = ("buy", "bsbuy", "shahn")

def _button_color(text, data=None):
    text = str(text or "")
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("utf-8", "ignore")
    data = str(data or "")
    if data in _GREEN_DATA:
        return "success"
    if "الغاء حظر" in text or "إلغاء حظر" in text:
        return "success"
    if any(w in text for w in _RED_AR) or _RED_EN.search(text):
        return "danger"
    if any(w in text for w in _GREEN_AR) or _GREEN_EN.search(text):
        return "success"
    return "primary"

_STYLE_KW = {"primary": "bg_primary", "success": "bg_success", "danger": "bg_danger"}
_orig_inline = Button.inline
_orig_url = Button.url
STYLE_MODE = None   # 'obj' | 'str' | 'raw' | None

def _style_obj(color):
    cls = getattr(types, "KeyboardButtonStyle", None)
    if cls is None:
        return None
    try:
        return cls(**{_STYLE_KW[color]: True})
    except Exception:
        return None

# ---- fallback: لو نسخة Telethon معندهاش KeyboardButtonStyle نبني الأزرار الملونة بنفسنا (Layer 224) ----
import struct as _struct
_TL_OK = False
try:
    from telethon.tl.tlobject import TLObject as _TLObject
    from telethon.tl import alltlobjects as _alltl

    _STYLE_BITS = {"primary": 1, "danger": 2, "success": 4}

    def _tl_style(color):
        return _struct.pack("<II", 0x4fdd3430, _STYLE_BITS.get(color, 1))

    def _tl_skip_style(reader):
        reader.read_int(signed=False)          # constructor id بتاع KeyboardButtonStyle
        sflags = reader.read_int(signed=False)
        if sflags & 8:
            reader.read_long()                 # icon

    class _StyledCallback(types.KeyboardButtonCallback):
        CONSTRUCTOR_ID = 0xe62bc960
        def __init__(self, text, data, color="primary", requires_password=None):
            super().__init__(text, data, requires_password=requires_password)
            self._color = color
        def _bytes(self):
            flags = (1 if getattr(self, "requires_password", None) else 0) | (1 << 10)
            return b"".join((_struct.pack("<II", 0xe62bc960, flags), _tl_style(self._color),
                             _TLObject.serialize_bytes(self.text), _TLObject.serialize_bytes(self.data)))
        def __bytes__(self):
            return self._bytes()
        @classmethod
        def from_reader(cls, reader):
            flags = reader.read_int()
            if flags & 1024:
                _tl_skip_style(reader)
            text = reader.tgread_string()
            data = reader.tgread_bytes()
            return cls(text, data, requires_password=bool(flags & 1))
        @classmethod
        def _read_from(cls, reader):
            return cls.from_reader(reader)

    class _StyledUrl(types.KeyboardButtonUrl):
        CONSTRUCTOR_ID = 0xd80c25ec
        def __init__(self, text, url, color="primary"):
            super().__init__(text, url)
            self._color = color
        def _bytes(self):
            return b"".join((_struct.pack("<II", 0xd80c25ec, 1 << 10), _tl_style(self._color),
                             _TLObject.serialize_bytes(self.text), _TLObject.serialize_bytes(self.url)))
        def __bytes__(self):
            return self._bytes()
        @classmethod
        def from_reader(cls, reader):
            flags = reader.read_int()
            if flags & 1024:
                _tl_skip_style(reader)
            return cls(reader.tgread_string(), reader.tgread_string())
        @classmethod
        def _read_from(cls, reader):
            return cls.from_reader(reader)

    try:                                       # عشان لو تيليجرام رجّع الزرار بالصيغة الجديدة Telethon يقدر يقراه
        _alltl.tlobjects[0xe62bc960] = _StyledCallback
        _alltl.tlobjects[0xd80c25ec] = _StyledUrl
    except Exception:
        pass
    _TL_OK = True
except Exception as _tl_err:
    print("⚠️ custom button style unavailable:", _tl_err)

def _build_styled(kind, text, payload, color, mode):
    if mode == "tl":
        if not _TL_OK:
            raise TypeError("custom TL styles unavailable")
        if kind == "inline":
            if not payload:
                payload = str(text).encode("utf-8")
            elif not isinstance(payload, (bytes, bytearray, memoryview)):
                payload = str(payload).encode("utf-8")
            if len(payload) > 64:
                raise ValueError("Too many bytes for the data")
            return _StyledCallback(text, bytes(payload), color)
        return _StyledUrl(text, payload or text, color)
    if mode == "obj":
        style = _style_obj(color)
        if style is None:
            raise TypeError("no KeyboardButtonStyle")
        return (_orig_inline if kind == "inline" else _orig_url)(text, payload, style=style)
    if mode == "str":
        return (_orig_inline if kind == "inline" else _orig_url)(text, payload, style=color)
    # raw: بناء الزرار مباشرة من أنواع تيليجرام
    style = _style_obj(color)
    if style is None:
        raise TypeError("no KeyboardButtonStyle")
    if kind == "inline":
        if not payload:
            payload = str(text).encode("utf-8")
        elif not isinstance(payload, (bytes, bytearray, memoryview)):
            payload = str(payload).encode("utf-8")
        if len(payload) > 64:
            raise ValueError("Too many bytes for the data")
        return types.KeyboardButtonCallback(text, bytes(payload), style=style)
    return types.KeyboardButtonUrl(text, payload or text, style=style)

def _raw_bytes(b):
    return bytes(getattr(b, "button", b))

def _probe_style_mode():
    try:
        plain = _raw_bytes(_orig_inline("x", b"x"))
    except Exception as ex:
        print("style probe: plain button failed:", ex)
        return None
    for mode in ("raw", "tl", "obj", "str"):
        try:
            styled = _raw_bytes(_build_styled("inline", "x", b"x", "success", mode))
            if styled != plain:       # الستايل اتحط فعلًا في البيانات المتبعتة لتيليجرام
                return mode
        except Exception:
            continue
    return None

STYLE_MODE = _probe_style_mode()

def _styled_inline(text, data=None, *args, **kwargs):
    if STYLE_MODE and not args and not kwargs:
        try:
            return _build_styled("inline", text, data, _button_color(text, data), STYLE_MODE)
        except Exception:
            pass
    return _orig_inline(text, data, *args, **kwargs)

def _styled_url(text, url=None, *args, **kwargs):
    if STYLE_MODE and not args and not kwargs:
        try:
            return _build_styled("url", text, url, "primary", STYLE_MODE)
        except Exception:
            pass
    return _orig_url(text, url, *args, **kwargs)

Button.inline = staticmethod(_styled_inline)
Button.url = staticmethod(_styled_url)
print("🎨 الأزرار الملونة:", f"مفعّلة ({STYLE_MODE})" if STYLE_MODE else "غير مفعّلة — حدّث المكتبة: pip install -U telethon")


from pyrogram import enums
from pyrogram.client import Client as PyroClient
from pyrogram.session.internals.data_center import DataCenter

# opentele محتاجة PyQt5 (مش بتتثبت على الموبايل/Pydroid) وكنا بنستخدمها بس عشان بيانات API.TelegramDesktop،
# فاستبدلتها بكلاس بسيط بنفس القيم، ومفيش داعي لتثبيت opentele خالص.
import copy as _copy

class APIData:
    api_id = 2040
    api_hash = "b18441a1ff607e10a989891a5462e627"
    device_model = "Desktop"
    system_version = "Windows 10"
    app_version = "4.8.1 x64"
    lang_code = "en"
    system_lang_code = "en-US"

    def copy(self):
        return _copy.copy(self)

class API:
    TelegramDesktop = APIData()

# binance.client بيتسمى Client برضه، فسميتها BinanceClient عشان ميتعارضش مع Client بتاع pyrogram
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException


# ============================================================
#  محتوى plugins/messages.py (نصوص الرسائل)
# ============================================================
msgs = {

    "START_MESSAGE":
        u"**• القائمة الرئيسية 🏡**\n\n"
        u"📁 ¦ ايدي حسابك : : `{}`\n"
        u"🏛 ¦ رصيدك : {} $ \n"
        u"💱 ¦ عملة البوت : $ دولار امريكي $\n\n"
        u"                [❶ قناة البوت الرسمية ❶](https://t.me/AF_R_O_TO)\n\n"
        u"     [❷ قناة متابعه اسعار الحسابات ❷](https://t.me/TG_AFROTO_PRICE)\n\n"
        u"⚙️ تحكم بالبوت عبر الأزرار بالأسفل ⬇️",
        
    "ADMIN_MESSAGE":
        u"**• مرحبًا بك عزيزي الادمن في لوحة التحكم الخاصه بك ** \n **- يمكنك التحكم في البوت من هذه الازرار**",
    
    "NEW_MESSAGE":
        u"**• 👨‍✈️ ⁞ مرحبا بك عزيزي المستخدم**\n\n"
        u"✅ - في -  بوت حسابات AFROTO\n"
        u"📮 - لخدمات الحسابات التيلجرام الجاهزة . \n\n"
        u"1⃣ -أكثر من 200 دولة في المخزون مع إضافات جديدة .\n2⃣ -سرعة العمل ارخص بوت لبيع الأرقام الجاهزة \n3⃣-سعر الرقم يـبدا مـن 0.20 سنت يحتوي على اكثر دول العالم\n4⃣ -يحتوي على الأرقام النـادره .\n\n"
        u"- يرجى بدء الاستخدام بإنشاء حساب ، اذا لديك حساب من قبل ، قم بالضغط على زر تسجيل الدخول ✅.\n\n"
        u"الإدارة والدعم الفني: @J_D_D_M",
        
    "ADMIN_MESSAGE":
        u"**• مرحبًا بك عزيزي الادمن في لوحة التحكم الخاصه بك ** \n **- يمكنك التحكم في البوت من هذه الازرار**",
        
    "BUY_MESSAGE":
        u"**✅︙هل انت متاكد من شراء الرقم ؟**"
        u"\n ⌁︙معلومات الرقم :-"
        u"\n"
        u"🌎︙ الدولة: {}\n"
        u"💰︙ سعر الرقم : {} $\n"
        u"**🚀︙اذا تريد الشراء اضغط (تأكيد ✅) و اذا لا تريد اضغط (الغاء ❌)**",
        
    "TRANSFER_MESSAGE":
        u"**- ارسل ايدي المستخدم المراد تحويل الرصيد اليه**",
        
    "COUNTRY_LIST":
        u"**• 🏆 - أهلاً بك عزيزي في قسم حسابات تيليجرام الجاهزة 💥. **\n\n"
        u"- من خلال هذا القسم يمكنك شراء حسابات تيليجرام جاهز من أي دولة موجودة في الأسفل وبالسعر الموضح بجانبها بكل سهولة، بأسعار مغرية وضمان تفعيل الرقم ☑️ .\n\n"
        u"**♻️ - يرجى إختيار الدولة التي تريد شراء حساب منها ، جميع الدول بالأسفل متوفر لديها أرقام ☑️ . **.",

    "TRUST_MESSAGE":
        u"**- تم شراء حساب جديد من البوت 📢**\n\n"
        u"🏳 - الدولة : {}\n"
        u"❇️ - المنصة : تليجرام 🔥\n"
        u"☎️ - الرقم : {}\n"
        u"💰 - السعر : $ {}\n"
        u"🆔 - العميل : {}\n"
        u"📥 - كود التفعيل : `{}`\n"
        u"🛠 الحاله : تم التفعيل ✅",

    "TRUST_NUMBER_PURCHASE_MESSAGE":
        u"**- تم شراء رقم جديد من البوت 📢**\n\n"
        u"🏳 - الدولة : {}\n"
        u"❇️ - المنصة : تليجرام 🔥\n"
        u"☎️ - الرقم : {}\n"
        u"💰 - السعر : $ {}\n"
        u"🆔 - العميل : {}\n"
        u"🛠 الحاله : بانتظار سحب الكود ⏳",

    "TRUST_SESSION_MESSAGE":
        u"**- تم شراء جلسه جديدة من البوت 📢**\n\n"
        u"🏳 - الدولة : {}\n"
        u"❇️ - المنصة : تليجرام 🔥\n"
        u"🔢 العدد : {}\n"
        u"💰 - السعر : $ {}\n"
        u"🆔 - العميل : {}"
}

TRUST_BOT_LINK = "https://t.me/TG_AFR_TO_BOT"

def trust_mask_phone(phone):
    """إخفاء رقمين من نص الرقم بـ **."""
    d = re.sub(r"\D", "", str(phone))
    if len(d) < 4:
        return "+" + d
    m = (len(d) - 2) // 2
    return "+" + d[:m] + "*\u200b*" + d[m + 2:]

def trust_mask_id(uid):
    return f"{uid}"

def trust_short_id(uid):
    """أيدي العميل مختصر (أول 8 أرقام + ...) — بيُستخدم في رسائل شراء الأرقام فقط."""
    t = str(uid)
    return (t[:8] if len(t) > 8 else t[:-2]) + "..."

_TRUST_SENT = set()

def _trust_key(phone, uid):
    return f"trust_num_{phone}_{uid}"

def _trust_claim(key):
    """بيحجز النشر قبل الإرسال: لو اتحجز قبل كده يرجّع False (يعني متنزلش تاني)."""
    if key in _TRUST_SENT or db.exists(key):
        return False
    _TRUST_SENT.add(key)
    try:
        db.set(key, True)
    except Exception:
        pass
    return True

def _trust_release(key):
    _TRUST_SENT.discard(key)
    try:
        if db.exists(key):
            db.delete(key)
    except Exception:
        pass

async def post_trust_number(name, phone, price, uid, code):
    """نشر كود التفعيل — بينزل فقط لو الشراء ده لسه متنشرش (مرة واحدة لكل رقم/عميل)."""
    if not db.exists("trust_channel"):
        return
    _k = _trust_key(phone, uid)
    if not _trust_claim(_k):
        return
    try:
        await l_client_send_message(client, db.get("trust_channel"),
            msgs['TRUST_MESSAGE'].format(name, trust_mask_phone(phone), price, trust_short_id(uid), code),
            buttons=[[Button.url("شراء رقم", TRUST_BOT_LINK)]], parse_mode="markdown")
    except Exception as ex:
        _trust_release(_k)
        print("trust channel post error:", ex)

async def post_trust_number_purchase(name, phone, price, uid):
    """شراء الرقم لوحده مبينزلش في القناة؛ أول كود بيتسحب بينزل رسالة واحدة فيها الكود (قابل للنسخ)."""
    _trust_release(_trust_key(phone, uid))   # شراء جديد = أول كود يتسحب ينزل في القناة مرة واحدة
    return
    if not db.exists("trust_channel"):
        return
    _k = _trust_key(phone, uid)
    _trust_release(_k)
    if not _trust_claim(_k):
        return
    try:
        await l_client_send_message(client, db.get("trust_channel"),
            msgs['TRUST_MESSAGE'].replace("`{}`", "").format(name, trust_mask_phone(phone), price, trust_short_id(uid)),
            buttons=[[Button.url("شراء رقم", TRUST_BOT_LINK)]], parse_mode="markdown")
    except Exception as ex:
        _trust_release(_k)
        print("trust channel post error:", ex)

async def post_trust_session(name, price, uid, count=1, phone=None):
    if not db.exists("trust_channel"):
        return
    if phone is not None:
        # نعلّم الرقم إنه اتنشر عشان طلب الكود بعد شراء السيشن مينزلش رسالة تانية
        _k = _trust_key(phone, uid)
        _trust_release(_k)
        _trust_claim(_k)
    try:
        await l_client_send_message(client, db.get("trust_channel"),
            msgs['TRUST_SESSION_MESSAGE'].format(name, count, price, trust_mask_id(uid)),
            buttons=[[Button.url("شراء جلسه", TRUST_BOT_LINK)]], parse_mode="markdown")
    except Exception as ex:
        print("trust channel post error:", ex)


# ============================================================
#  محتوى plugins/get_code.py (جلب كود التحقق + تغيير/تفعيل الباسورد السحابي)
# ============================================================

async def get_code(session):
    X = TelegramClient(StringSession(session), api_id=API_ID, api_hash=API_HASH)
    try:
        await X.connect()
        async for x in X.iter_messages(777000, limit=1):
            code_match = re.search(r'\b(\d{5})\b', x.text)
            if code_match:
                code = code_match.group(1)
                return code
            else:
                return "لم يتم العثور"
    except Exception  as a:
        return "لم يتم العثور"
    finally:
        try:
            await X.disconnect()
        except Exception:
            pass

async def change_password(session, old_password, new_password):
    c = PyroClient('::memory::', in_memory=True, api_hash='e0e2a70a885d1497c8feb47815bb3e36', api_id=24188127,lang_code="ar", no_updates=True, session_string=session)
    try:
        await c.start()
    except:
        return False
    try:
        await c.change_cloud_password(old_password, new_password)
        await c.stop()
        return True
    except:
        return False
    
async def enable_password(session, new_password):
    c = PyroClient('::memory::', in_memory=True, api_hash='e0e2a70a885d1497c8feb47815bb3e36', api_id=24188127,lang_code="ar", no_updates=True, session_string=session)
    try:
        await c.start()
    except:
        return False
    try:
        await c.enable_cloud_password(new_password)
        await c.stop()
        return True
    except:
        return False
    


# ============================================================
#  محتوى plugins/SessionConverter.py (تحويل سيشن Telethon <-> Pyrogram)
# ============================================================

class ValidationError(Exception):
    pass


SCHEMAT = """
CREATE TABLE version (version integer primary key);

CREATE TABLE sessions (
    dc_id integer primary key,
    server_address text,
    port integer,
    auth_key blob,
    takeout_id integer
);

CREATE TABLE entities (
    id integer primary key,
    hash integer not null,
    username text,
    phone integer,
    name text,
    date integer
);

CREATE TABLE sent_files (
    md5_digest blob,
    file_size integer,
    type integer,
    id integer,
    hash integer,
    primary key(md5_digest, file_size, type)
);

CREATE TABLE update_state (
    id integer primary key,
    pts integer,
    qts integer,
    date integer,
    seq integer
);
"""


class TeleSession:
    _STRUCT_PREFORMAT = '>B{}sH256s'
    CURRENT_VERSION = '1'
    TABLES = {
        "sessions": {
            "dc_id", "server_address", "port", "auth_key", "takeout_id"
            },
        "entities": {"id", "hash", "username", "phone", "name", "date"},
        "sent_files": {"md5_digest", "file_size", "type", "id", "hash"},
        "update_state": {"id", "pts", "qts", "date", "seq"},
        "version": {"version"},
    }

    def __init__(
        self,
        *,
        dc_id: int,
        auth_key: bytes,
        server_address: Optional[str] = None,
        port: Optional[int] = None,
        takeout_id: Optional[int] = None
    ):
        self.dc_id = dc_id
        self.auth_key = auth_key
        self.server_address = server_address
        self.port = port
        self.takeout_id = takeout_id

    @classmethod
    def from_string(cls, string: str):
        string = string[1:]
        ip_len = 4 if len(string) == 352 else 16
        dc_id, ip, port, auth_key = struct.unpack(
            cls._STRUCT_PREFORMAT.format(ip_len), cls.decode(string)
        )
        server_address = ipaddress.ip_address(ip).compressed
        return cls(
            auth_key=auth_key,
            dc_id=dc_id,
            port=port,
            server_address=server_address,
        )

    @classmethod
    async def from_file(cls, path: Path):
        if not await cls.validate(path):
            raise ValidationError()

        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM sessions") as cursor:
                session = await cursor.fetchone()

        return cls(**session)

    @classmethod
    async def validate(cls, path: Path) -> bool:
        try:
            async with aiosqlite.connect(path) as db:
                db.row_factory = aiosqlite.Row
                sql = "SELECT name FROM sqlite_master WHERE type='table'"
                async with db.execute(sql) as cursor:
                    tables = {row["name"] for row in await cursor.fetchall()}

                if tables != set(cls.TABLES.keys()):
                    return False

                for table, session_columns in cls.TABLES.items():
                    sql = f'pragma table_info("{table}")'
                    async with db.execute(sql) as cur:
                        columns = {row["name"] for row in await cur.fetchall()}
                        if session_columns != columns:
                            return False

        except aiosqlite.DatabaseError:
            return False

        return True

    @staticmethod
    def encode(x: bytes) -> str:
        return base64.urlsafe_b64encode(x).decode('ascii')

    @staticmethod
    def decode(x: str) -> bytes:
        return base64.urlsafe_b64decode(x)

    def client(
        self,
        api: Type[APIData],
        proxy: Optional[dict] = None,
        no_updates: bool = True
    ):
        client = TelegramClient(
            session=StringSession(self.to_string()),
            api_id=api.api_id,
            api_hash=api.api_hash,
            proxy=proxy,
            device_model=api.device_model,
            system_version=api.system_version,
            app_version=api.app_version,
            lang_code=api.lang_code,
            system_lang_code=api.system_lang_code,
            receive_updates=not no_updates,
        )
        return client

    def to_string(self) -> str:
        if self.server_address is None:
            self.server_address, self.port = DataCenter(
                self.dc_id, False, False, False
            )
        ip = ipaddress.ip_address(self.server_address).packed
        return self.CURRENT_VERSION + self.encode(struct.pack(
            self._STRUCT_PREFORMAT.format(len(ip)),
            self.dc_id,
            ip,
            self.port,
            self.auth_key
        ))

    async def to_file(self, path: Path):
        async with aiosqlite.connect(path) as db:
            await db.executescript(SCHEMAT)
            await db.commit()
            sql = "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)"
            params = (
                self.dc_id,
                self.server_address,
                self.port,
                self.auth_key,
                self.takeout_id
            )
            await db.execute(sql, params)
            await db.commit()





SCHEMA = """
CREATE TABLE sessions (
    dc_id     INTEGER PRIMARY KEY,
    api_id    INTEGER,
    test_mode INTEGER,
    auth_key  BLOB,
    date      INTEGER NOT NULL,
    user_id   INTEGER,
    is_bot    INTEGER
);

CREATE TABLE peers (
    id             INTEGER PRIMARY KEY,
    access_hash    INTEGER,
    type           INTEGER NOT NULL,
    username       TEXT,
    phone_number   TEXT,
    last_update_on INTEGER NOT NULL DEFAULT (CAST(STRFTIME('%s', 'now') AS INTEGER))
);

CREATE TABLE version (
    number INTEGER PRIMARY KEY
);

CREATE INDEX idx_peers_id ON peers (id);
CREATE INDEX idx_peers_username ON peers (username);
CREATE INDEX idx_peers_phone_number ON peers (phone_number);

CREATE TRIGGER trg_peers_last_update_on
    AFTER UPDATE
    ON peers
BEGIN
    UPDATE peers
    SET last_update_on = CAST(STRFTIME('%s', 'now') AS INTEGER)
    WHERE id = NEW.id;
END;
"""


class PyroSession:
    OLD_STRING_FORMAT = ">B?256sI?"
    OLD_STRING_FORMAT_64 = ">B?256sQ?"
    STRING_SIZE = 351
    STRING_SIZE_64 = 356
    STRING_FORMAT = ">BI?256sQ?"
    TABLES = {
        "sessions": {"dc_id", "test_mode", "auth_key", "date", "user_id", "is_bot"},
        "peers": {"id", "access_hash", "type", "username", "phone_number", "last_update_on"},
        "version": {"number"}
    }

    def __init__(
        self,
        *,
        dc_id: int,
        auth_key: bytes,
        user_id: Optional[int] = None,
        is_bot: bool = False,
        test_mode: bool = False,
        api_id: Optional[int] = None,
        **kw
    ):
        self.dc_id = dc_id
        self.auth_key = auth_key
        self.user_id = user_id
        self.is_bot = is_bot
        self.test_mode = test_mode
        self.api_id = api_id

    @classmethod
    def from_string(cls, session_string: str):
        if len(session_string) in [cls.STRING_SIZE, cls.STRING_SIZE_64]:
            string_format = cls.OLD_STRING_FORMAT_64

            if len(session_string) == cls.STRING_SIZE:
                string_format = cls.OLD_STRING_FORMAT

            api_id = None
            dc_id, test_mode, auth_key, user_id, is_bot = struct.unpack(
                string_format,
                base64.urlsafe_b64decode(
                    session_string + "=" * (-len(session_string) % 4)
                )
            )
        else:
            dc_id, api_id, test_mode, auth_key, user_id, is_bot = struct.unpack(
                cls.STRING_FORMAT,
                base64.urlsafe_b64decode(
                    session_string + "=" * (-len(session_string) % 4)
                )
            )

        return cls(
            dc_id=dc_id,
            api_id=api_id,
            auth_key=auth_key,
            user_id=user_id,
            is_bot=is_bot,
            test_mode=test_mode,
        )

    @classmethod
    async def from_file(cls, path: Path):
        if not await cls.validate(path):
            raise ValidationError()

        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM sessions") as cursor:
                session = await cursor.fetchone()

        return cls(**session)

    @classmethod
    async def validate(cls, path: Path) -> bool:
        try:
            async with aiosqlite.connect(path) as db:
                db.row_factory = aiosqlite.Row
                sql = "SELECT name FROM sqlite_master WHERE type='table'"
                async with db.execute(sql) as cursor:
                    tables = {row["name"] for row in await cursor.fetchall()}

                if tables != set(cls.TABLES.keys()):
                    return False

                for table, session_columns in cls.TABLES.items():
                    sql = f'pragma table_info("{table}")'
                    async with db.execute(sql) as cur:
                        columns = {row["name"] for row in await cur.fetchall()}
                        if "api_id" in columns:
                            columns.remove("api_id")
                        print(columns, session_columns)
                        print(columns != session_columns)
                        if session_columns != columns:
                            return False

        except aiosqlite.DatabaseError:
            return False

        return True

    def client(
        self,
        api: Type[APIData],
        proxy: Optional[dict] = None,
        no_updates: bool = True
    ) -> PyroClient:
        client = PyroClient(
            name=secrets.token_urlsafe(8),
            api_id=api.api_id,
            api_hash=api.api_hash,
            app_version=api.app_version,
            device_model=api.device_model,
            system_version=api.system_version,
            lang_code=api.lang_code,
            proxy=proxy,
            session_string=self.to_string(),
            no_updates=no_updates,
            test_mode=self.test_mode,
        )
        return client

    def to_string(self) -> str:
        packed = struct.pack(
            self.STRING_FORMAT,
            self.dc_id,
            self.api_id or 0,
            self.test_mode,
            self.auth_key,
            self.user_id or 9999,
            self.is_bot
        )
        return base64.urlsafe_b64encode(packed).decode().rstrip("=")

    async def to_file(self, path: Path):
        async with aiosqlite.connect(path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
            sql = "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)"
            params = (
                self.dc_id,
                self.api_id,
                self.test_mode,
                self.auth_key,
                0,
                self.user_id or 9999,
                self.is_bot
            )
            await db.execute(sql, params)
            await db.commit()



class SessionManager:
    def __init__(
        self,
        dc_id: int,
        auth_key: bytes,
        user_id: Optional[int] = None,
        valid: Optional[bool] = None,
        api: Type[APIData] = API.TelegramDesktop,
    ):
        self.dc_id = dc_id
        self.auth_key = auth_key
        self.user_id = user_id
        self.valid = valid
        self.api = api.copy()
        self.user = None
        self.client = None

    async def __aenter__(self):
        self.client = self.telethon_client()
        await self.client.connect()
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.disconnect()
        self.client = None

    @property
    def auth_key_hex(self) -> str:
        return self.auth_key.hex()

    @classmethod
    async def from_telethon_file(cls, file: Path, api=API.TelegramDesktop):
        session = await TeleSession.from_file(file)
        return cls(
            dc_id=session.dc_id,
            auth_key=session.auth_key,
            api=api
        )

    @classmethod
    def from_telethon_string(cls, string: str, api=API.TelegramDesktop):
        session = TeleSession.from_string(string)
        return cls(
            dc_id=session.dc_id,
            auth_key=session.auth_key,
            api=api
        )

    @classmethod
    async def from_pyrogram_file(cls, file: Path, api=API.TelegramDesktop):
        session = await PyroSession.from_file(file)
        return cls(
            auth_key=session.auth_key,
            dc_id=session.dc_id,
            api=api,
            user_id=session.user_id,
        )

    @classmethod
    def from_pyrogram_string(cls, string: str, api=API.TelegramDesktop):
        session = PyroSession.from_string(string)
        return cls(
            auth_key=session.auth_key,
            dc_id=session.dc_id,
            api=api,
            user_id=session.user_id,
        )



    async def to_pyrogram_file(self, path: Path):
        await self.pyrogram.to_file(path)

    def to_pyrogram_string(self) -> str:
        return self.pyrogram.to_string()

    async def to_telethon_file(self, path: Path):
        await self.telethon.to_file(path)

    def to_telethon_string(self) -> str:
        return self.telethon.to_string()



    @property
    def pyrogram(self) -> PyroSession:
        return PyroSession(
            dc_id=self.dc_id,
            auth_key=self.auth_key,
            user_id=self.user_id,
        )

    @property
    def telethon(self) -> TeleSession:
        return TeleSession(
            dc_id=self.dc_id,
            auth_key=self.auth_key,
        )



    def pyrogram_client(self, proxy=None, no_updates=True):
        client = self.pyrogram.client(
            api=self.api,
            proxy=proxy,
            no_updates=no_updates,
        )
        return client

    def telethon_client(self, proxy=None, no_updates=True):
        client = self.telethon.client(
            api=self.api,
            proxy=proxy,
            no_updates=no_updates,
        )
        return client

    async def validate(self) -> bool:
        user = await self.get_user()
        self.valid = bool(user)
        return self.valid

    async def get_user_id(self):
        if self.user_id:
            return self.user_id

        user = await self.get_user()

        if user is None:
            raise ValidationError()

        return user.id

    async def get_user(self):
        async with self as client:
            self.user = await client.get_me()
            if self.user:
                self.user_id = self.user.id
        return self.user


class MangSession:


    def PYROGRAM_TO_TELETHON(session_string: str):
        Session_data = SessionManager.from_pyrogram_string(session_string)
        return Session_data.to_telethon_string()
        
    def TELETHON_TO_PYROGRAM(session_string: str):
        Session_data = SessionManager.from_telethon_string(session_string)
        return Session_data.to_pyrogram_string()


# ============================================================
#  محتوى main.py الأصلي
# ============================================================

bot_enabled = True

def is_bot_enabled():
    global bot_enabled
    return bot_enabled

import sqlite3

conn = sqlite3.connect('database.db')
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    coind REAL DEFAULT 0.0
)
''')

conn.commit()
conn.close()

#غير الحقوق واثبت انك فاشل اذا تريد تنقل اذكر اسمي او اسم قناتي #

#====================#
#CH : @J_D_D_M 
#DEV : @J_D_D_M
#====================#

def get_user_balance(user_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT coind FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0.0

def update_user_balance(user_id, amount):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT coind FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if result:
        new_balance = result[0] + amount
        cursor.execute('UPDATE users SET coind = ? WHERE user_id = ?', (new_balance, user_id))
    else:
        cursor.execute('INSERT INTO users (user_id, coind) VALUES (?, ?)', (user_id, amount))
    
    conn.commit()
    conn.close()

def get_user_coind(user_id):
    """رصيد محفظة السحب (عمود coind في database.db)."""
    return float(get_user_balance(int(user_id)) or 0.0)

def update_user_coind(user_id, value):
    """بيضبط رصيد محفظة السحب على قيمة محددة."""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT coind FROM users WHERE user_id = ?', (int(user_id),))
    if cur.fetchone():
        cur.execute('UPDATE users SET coind = ? WHERE user_id = ?', (float(value), int(user_id)))
    else:
        cur.execute('INSERT INTO users (user_id, coind) VALUES (?, ?)', (int(user_id), float(value)))
    conn.commit()
    conn.close()

def check_vip(user):
    user_id = int(user)
    users = db.get(f"vip_{user_id}")
    noww = time.time()
    if db.exists(f"vip_{user_id}"):
        last_time = users['vip']
        timeee = int(db.get(f"vip_{user_id}_time"))
        WAIT_TIMEE = int(timeee) * 24 * 60 * 60
        elapsed_time = noww - last_time
        if elapsed_time < WAIT_TIMEE:
            remaining_time = WAIT_TIMEE - elapsed_time
            return int(remaining_time)
        else:
            return None
    else:
        return None
        
def get_exchange_rate(currency):
    rates = {
        'BTC': 50000, 
        'USDT': 1,    
        'LTC': 200    
    }
    return rates.get(currency, 1)
        
if not os.path.isdir('database'):
    os.mkdir('database')

# ============================================================
#  أدوات مشتركة: حجز رقم من المخزون + تحويل السيشن لملف حقيقي
# ============================================================

def take_random_account(calling_code, cid=None):
    """بياخد حساب عشوائي من مخزون الدولة دي (في القسم المحدد) ويشيله فورًا، عشان ميتباعش مرتين."""
    key = accounts_key(calling_code, cid)
    accounts = db.get(key) if db.exists(key) else []
    if not accounts:
        return None
    idx = random.randrange(len(accounts))
    acc = accounts.pop(idx)
    db.set(key, accounts)
    return acc

def match_country_by_phone(phone_number, countries):
    """بيدور على أنسب دولة لرقم معين (أطول calling_code متطابق)."""
    best = None
    for c in (countries or []):
        cc = str(c.get('calling_code', '')).replace("+", "")
        p = phone_number.replace("+", "")
        if cc and p.startswith(cc):
            if best is None or len(cc) > len(str(best.get('calling_code', '')).replace("+", "")):
                best = c
    return best

def build_session_json(acc, session_file_name):
    """ملف JSON فيه كل المعلومات اللي أي بوت/برنامج يحتاجها يعمل جلسة جوه الحساب."""
    ss = StringSession(acc["session"])
    try:
        pyro = MangSession.TELETHON_TO_PYROGRAM(acc["session"])
    except Exception:
        pyro = ""
    two = acc.get("two-step") or acc.get("password") or ""
    return {
        "phone": str(acc.get("phone_number", "")),
        "user_id": acc.get("user_id"),
        "username": acc.get("username", ""),
        "first_name": acc.get("first_name", ""),
        "twoFA": two,
        "has_2fa": bool(two),
        "api_id": int(acc.get("api_id") or API_ID),
        "api_hash": API_HASH,
        "dc_id": ss.dc_id,
        "server_address": ss.server_address,
        "port": ss.port,
        "auth_key_hex": ss.auth_key.key.hex() if ss.auth_key else "",
        "session_string_telethon": acc["session"],
        "session_string_pyrogram": pyro,
        "session_file": session_file_name,
        "session_file_format": "telethon-sqlite",
        "note": "الجلسة مالهاش مدة انتهاء ثابتة: بتفضل شغالة لحد ما تتلغى من إعدادات الأجهزة أو الحساب يتحظر. "
                "ما تشغلهاش من مكانين في نفس الوقت.",
    }

def string_session_to_file(string_session, path_no_ext):
    """بيحول StringSession المخزن عندنا لملف .session حقيقي قابل للاستخدام في أي جهاز تليجرام."""
    ss = StringSession(string_session)
    path = path_no_ext + ".session"
    if os.path.exists(path):
        os.remove(path)
    sq = SQLiteSession(path_no_ext)
    sq.set_dc(ss.dc_id, ss.server_address, ss.port)
    sq.auth_key = ss.auth_key
    sq._update_session_table()
    sq.save()
    try:
        sq.close()
    except Exception:
        pass
    if not os.path.exists(path):
        raise RuntimeError("session file was not created")
    # تأكد إن الملف بيتقرا فعلاً وفيه auth_key سليم قبل ما يتبعت للعميل
    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT dc_id, auth_key FROM sessions").fetchone()
        if not row or not row[1] or len(row[1]) != 256:
            raise RuntimeError("session file is empty/corrupt")
    finally:
        con.close()
    for ext in ("-journal", "-wal", "-shm"):
        try:
            if os.path.exists(path + ext):
                os.remove(path + ext)
        except Exception:
            pass
    return path

async def make_fresh_session(string_session, two="", timeout=30):
    """بيعمل جلسة جديدة تماماً (auth_key جديد ومستقل) على نفس الحساب من غير أي كود.
    الطريقة: دخول QR. الجلسة القديمة بتوافق على التوكن (AcceptLoginToken) والجلسة الجديدة بتدخل لوحدها.
    لو الحساب عليه تحقق بخطوتين بيستخدم الباسورد المخزن. بيرجّع StringSession جديدة.
    الجلسة القديمة بتفضل زي ما هي (مش بتتلغي)."""
    twos = []
    for _t in (two if isinstance(two, (list, tuple)) else [two]):
        if _t and str(_t) not in twos:
            twos.append(str(_t))
    old = TelegramClient(StringSession(string_session), int(API_ID), API_HASH,
                         use_ipv6=False, connection_retries=3, retry_delay=1)
    new = TelegramClient(StringSession(), int(API_ID), API_HASH,
                         use_ipv6=False, connection_retries=3, retry_delay=1)
    try:
        await asyncio.wait_for(old.connect(), timeout)
        me_old = await asyncio.wait_for(old.get_me(), timeout)
        if me_old is None:
            raise RuntimeError("الجلسة الأصلية مش شغالة")
        await asyncio.wait_for(new.connect(), timeout)

        user, accepted, deadline = None, None, time.time() + 60
        try:
            while user is None and time.time() < deadline:
                res = await new(functions.auth.ExportLoginTokenRequest(
                    api_id=int(API_ID), api_hash=API_HASH, except_ids=[]))
                if isinstance(res, types.auth.LoginTokenMigrateTo):
                    await new._switch_dc(res.dc_id)
                    res = await new(functions.auth.ImportLoginTokenRequest(res.token))
                if isinstance(res, types.auth.LoginTokenSuccess):
                    user = res.authorization.user
                    break
                if accepted != res.token:
                    await old(functions.auth.AcceptLoginTokenRequest(token=res.token))
                    accepted = res.token
                await asyncio.sleep(1)
        except SessionPasswordNeededError:
            if not twos:
                raise RuntimeError("الحساب عليه تحقق بخطوتين ومفيش باسورد متخزن")
            _ok = False
            for _t in twos:
                try:
                    await new.sign_in(password=_t)
                    _ok = True
                    break
                except PasswordHashInvalidError:
                    continue
            if not _ok:
                raise RuntimeError("الحساب عليه تحقق بخطوتين والباسورد المتخزن غلط")
            user = await new.get_me()

        if user is None:
            raise RuntimeError("تليجرام ماكملش تسجيل الدخول الجديد (انتهت المهلة)")
        me_new = await asyncio.wait_for(new.get_me(), timeout)
        if me_new is None or me_new.id != me_old.id:
            raise RuntimeError("الجلسة الجديدة مش بتاعت نفس الحساب")
        fresh = StringSession.save(new.session)
        if fresh == string_session:
            raise RuntimeError("الجلسة الجديدة طلعت نفس القديمة")
        print("✅ fresh session created for", getattr(me_new, "phone", "?"))
        return fresh
    finally:
        for c in (old, new):
            try:
                await c.disconnect()
            except Exception:
                pass


_PW_PLACEHOLDERS = {"", "none", "null", "no", "false", "true", "-", "—", "n/a", "na", "unknown",
                    "غير معروفة", "غير معروف", "غير متاح", "لا يوجد", "لايوجد"}

def _clean_pw(v):
    """بيرجّع كلمة السر نضيفة، أو "" لو القيمة فاضية/كلمة بديلة مش باسورد حقيقي (زي «غير معروفة»)."""
    if v is None or isinstance(v, bool):
        return ""
    t = str(v).strip()
    return "" if t.lower() in _PW_PLACEHOLDERS else t

def acc_pw(acc):
    """كلمة سر التحقق بخطوتين المعروفة للحساب (two-step ثم password ثم current_2fa)."""
    for k in ("two-step", "password", "current_2fa"):
        v = _clean_pw(acc.get(k))
        if v:
            return v
    return ""

def _pw_candidates(acc, extra=None):
    """كلمات سر التحقق بخطوتين المعروفة فعلاً لهذا الحساب بس (مفيش تخمين بقيمة افتراضية عامة)."""
    out = []
    for v in [acc.get("two-step"), acc.get("password"), acc.get("current_2fa"), extra]:
        v = _clean_pw(v)
        if v and v not in out:
            out.append(v)
    return out

async def ensure_account_two_step(string_session, candidates, target=None, timeout=40):
    """بيتأكد بس من حالة التحقق بخطوتين الحالية على الحساب، وما بيفعّل ولا بيغيّر ولا بيضبط أي كلمة سر خالص.
    - لو الحساب مفيهوش تحقق بخطوتين أصلاً: بيرجّع None (من غير ما يحطله واحد).
    - لو فيه تحقق بخطوتين: بيجرب يتحقق من الكلمات المعروفة عندنا للحساب ده بس (مش بتتغير)، ولو واحدة منها
      مطابقة بيرجّعها (عشان نعرف نعمل بيها حاجات للقراءة بس زي تسجيل دخول جديد)، وإلا بيرجّع None."""
    from telethon.password import compute_check
    app = TelegramClient(StringSession(string_session), int(API_ID), API_HASH,
                         receive_updates=False, use_ipv6=False, connection_retries=3, retry_delay=1)
    try:
        await asyncio.wait_for(app.connect(), timeout)
        pwd = await asyncio.wait_for(app(functions.account.GetPasswordRequest()), timeout)
        if not getattr(pwd, "has_password", False):
            return None
        for c in candidates:
            try:
                pwd = await asyncio.wait_for(app(functions.account.GetPasswordRequest()), timeout)
                await asyncio.wait_for(app(functions.account.GetPasswordSettingsRequest(compute_check(pwd, c))), timeout)
                return c
            except PasswordHashInvalidError:
                continue
        return None
    except Exception as ex:
        print("check 2FA failed:", type(ex).__name__, ex)
        return None
    finally:
        try:
            await app.disconnect()
        except Exception:
            pass


_UNIFY_ERR_AR = {
    "AuthKeyUnregisteredError": "الجلسة اتلغت",
    "SessionRevokedError": "الجلسة اتلغت",
    "SessionExpiredError": "الجلسة منتهية",
    "UserDeactivatedBanError": "الحساب محظور",
    "UserDeactivatedError": "الحساب محذوف/معطّل",
    "PhoneNumberBannedError": "الحساب محظور",
    "FreshResetAuthorisationForbiddenError": "الجلسة جديدة (أقل من 24 ساعة) — هتتضبط تلقائي بعد شوية",
    "FreshChangePhoneForbiddenError": "الجلسة جديدة (أقل من 24 ساعة) — هتتضبط تلقائي بعد شوية",
    "PasswordTooFreshError": "تليجرام مانع تغيير الباسورد دلوقتي (الجلسة جديدة)",
    "SessionTooFreshError": "تليجرام مانع تغيير الباسورد دلوقتي (الجلسة جديدة)",
}

def _unify_explain(exc):
    return _UNIFY_ERR_AR.get(type(exc).__name__, f"{type(exc).__name__}: {str(exc)[:80]}")

async def unify_two_step(string_session, candidates, target_pw=None, timeout=40):
    """
    بيوحّد التحقق بخطوتين على حساب واحد: لو مفيهوش تحقق يفعّله، ولو فيه بكلمة مختلفة يغيّرها،
    كل ده على نفس كلمة السر العامة بتاعة البوت (target_pw). من غير ما نخمّن كلمة الحساب القديمة —
    بس بيجرب الكلمات المعروفة لنا (candidates) عشان يقدر يغيّرها.
    بيرجّع (success, final_password, note) — note بتتسجل في اللوج بس، مش شرط تفشل العملية.
    """
    from telethon.password import compute_check
    target_pw = target_pw or _two_step_password()
    app = TelegramClient(StringSession(string_session), int(API_ID), API_HASH,
                         receive_updates=False, use_ipv6=False, connection_retries=2, retry_delay=1)
    try:
        await asyncio.wait_for(app.connect(), timeout)
        pwd = await asyncio.wait_for(app(functions.account.GetPasswordRequest()), timeout)
        if not getattr(pwd, "has_password", False):
            await asyncio.wait_for(app.edit_2fa(new_password=target_pw), timeout)
            return True, target_pw, "اتفعّل تحقق بخطوتين جديد"

        current = None
        for cand in candidates:
            try:
                pwd = await asyncio.wait_for(app(functions.account.GetPasswordRequest()), timeout)
                await asyncio.wait_for(
                    app(functions.account.GetPasswordSettingsRequest(password=compute_check(pwd, cand))), timeout)
                current = cand
                break
            except PasswordHashInvalidError:
                continue
        if current == target_pw:
            return True, target_pw, "كان مضبوط بالفعل"
        if current is None:
            return False, None, "عليه تحقق بخطوتين بكلمة مختلفة مش معروفة لينا (مينفعش نغيّرها من غير الكلمة القديمة)"
        await asyncio.wait_for(app.edit_2fa(current_password=current, new_password=target_pw), timeout)
        return True, target_pw, "اتغيّر لنفس كلمة السر العامة"
    except Exception as ex:
        return False, None, _unify_explain(ex)
    finally:
        try:
            await app.disconnect()
        except Exception:
            pass


async def inspect_two_step(string_session, candidates, timeout=40):
    """(has_password, known_password) — has_password=None لو تعذر الاتصال بالحساب.
    known_password = الكلمة اللي اتأكدنا من تليجرام إنها صح (من غير ما نغيّر أي حاجة)."""
    from telethon.password import compute_check
    app = TelegramClient(StringSession(string_session), int(API_ID), API_HASH,
                         receive_updates=False, use_ipv6=False, connection_retries=3, retry_delay=1)
    try:
        await asyncio.wait_for(app.connect(), timeout)
        pwd = await asyncio.wait_for(app(functions.account.GetPasswordRequest()), timeout)
        if not getattr(pwd, "has_password", False):
            return False, None
        for c in candidates:
            try:
                pwd = await asyncio.wait_for(app(functions.account.GetPasswordRequest()), timeout)
                await asyncio.wait_for(app(functions.account.GetPasswordSettingsRequest(compute_check(pwd, c))), timeout)
                return True, c
            except PasswordHashInvalidError:
                continue
        return True, None
    except Exception as ex:
        print("inspect 2FA failed:", type(ex).__name__, ex)
        return None, None
    finally:
        try:
            await app.disconnect()
        except Exception:
            pass


async def get_real_two_step_status(string_session, timeout=15):
    """بيتحقق Live من الحساب هل عليه تحقق بخطوتين دلوقتي ولا لأ، من غير ما يغيّر أي حاجة.
    بيرجّع True (مفعّل) / False (مش مفعّل) / None (تعذر الاتصال بالحساب)."""
    app = TelegramClient(StringSession(string_session), int(API_ID), API_HASH,
                         receive_updates=False, use_ipv6=False, connection_retries=2, retry_delay=1)
    try:
        await asyncio.wait_for(app.connect(), timeout)
        pwd = await asyncio.wait_for(app(functions.account.GetPasswordRequest()), timeout)
        return bool(getattr(pwd, "has_password", False))
    except Exception as ex:
        print("get real 2FA status failed:", type(ex).__name__, ex)
        return None
    finally:
        try:
            await app.disconnect()
        except Exception:
            pass


def _two_step_display(has_pw, stored_pw=None):
    """نص موحّد لعرض حالة التحقق بخطوتين الحقيقية. stored_pw بيتعرض بس لو إحنا عارفين كلمة السر فعلاً."""
    if has_pw is None:
        return "⚠️ تعذر التحقق الآن"
    if not has_pw:
        return "❌ غير مفعّل"
    if stored_pw:
        return f"✅ مفعّل (الكلمة المعروفة: `{stored_pw}`)"
    return "✅ مفعّل (الكلمة غير معروفة لدينا)"


API_ID = "24188127"
API_HASH = "e0e2a70a885d1497c8feb47815bb3e36"

admin = 8379531283

token = "8387994594:AAE-bvFB-XXKbhpz0Rs84vbwiWHTJwCvLBI" #توكن بوتك

binance_api_key = '000000' #ايبي حسابك بينانس
binance_api_secret = '000000' #سيكرت حسابك بينانس

new_password = db.get("two_step_password") if db.exists("two_step_password") else "MT"

def _reset_bad_session(name):
    """لو ملف سيشن البوت القديم (FFJFF5.session) شكله مش متوافق مع نسخة Telethon المثبتة
    (زي خطأ too many values to unpack) بيتمسح، والبوت بيعمل واحد جديد لوحده بالتوكن."""
    try:
        SQLiteSession(name).close()
    except Exception as _e:
        print("⚠️ ملف سيشن البوت القديم غير متوافق، هتم حذفه وإنشاء واحد جديد:", type(_e).__name__)
        for _ext in (".session", ".session-journal", ".session-wal", ".session-shm"):
            try:
                os.remove(name + _ext)
            except OSError:
                pass

_reset_bad_session('FFJFF5')
try:
    import fcntl
    _LOCK_FH = open('.bot_instance.lock', 'w')
    for _t in range(40):
        try:
            fcntl.flock(_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            time.sleep(1)
    else:
        print("⚠️ نسخة تانية من البوت شغالة — قفل النسخة دي عشان الرد/النشر ميتكررش.")
        os._exit(0)
except ImportError:
    pass

client = TelegramClient('FFJFF5', api_id=API_ID, api_hash=API_HASH).start(bot_token=token)
bot = client

#غير الحقوق واثبت انك فاشل اذا تريد تنقل اذكر اسمي او اسم قناتي #

#====================#
#CH : @J_D_D_M 
#DEV : @J_D_D_M
#====================#

MIN_AMOUNT = 0.01
MAX_AMOUNT = 1000.0

CENT = Decimal("0.01")

def money(value):
    """Display balances and dollar amounts as exact cents, e.g. 0.01 / 0.10 / 1.00."""
    try:
        d = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
        return f"{d:.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return "0.00"

def money_value(value):
    """Keep the real dollar amount; formatting to 2 decimals is display-only."""
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("invalid amount")

# ================= Global bilingual UI layer =================
# Every outgoing text/button passes through this layer. Arabic remains unchanged;
# English users get an automatic Arabic -> English translation with in-memory caching.
_TRANSLATION_CACHE = {}

def _protect_translation_tokens(text):
    import re as _re
    vals=[]
    def repl(m):
        vals.append(m.group(0))
        return f"__AFROTO_TOKEN_{len(vals)-1}__"
    protected = _re.sub(r"\{[^{}]*\}|https?://\S+|@[A-Za-z0-9_]+", repl, str(text))
    return protected, vals

def _restore_translation_tokens(text, vals):
    for i,v in enumerate(vals):
        text = text.replace(f"__AFROTO_TOKEN_{i}__", v)
    return text

def _translate_ar_to_en(text):
    text = str(text)
    if not re.search(r'[\u0600-\u06ff]', text):
        return text
    key = text
    if key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[key]
    try:
        protected, vals = _protect_translation_tokens(text)
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=ar&tl=en&dt=t&q=" + urllib.parse.quote(protected)
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        translated = ''.join(part[0] for part in data[0] if part and part[0])
        translated = _restore_translation_tokens(translated, vals)
        _TRANSLATION_CACHE[key] = translated or text
        return translated or text
    except Exception:
        return text

def _localize_text(user_id, text):
    if text is None or user_id is None:
        return text
    try:
        if user_language(int(user_id)) != 'en':
            return text
    except Exception:
        return text
    return _translate_ar_to_en(text) if re.search(r'[\u0600-\u06ff]', str(text)) else text

def _localize_buttons(user_id, buttons):
    if not buttons or user_id is None or user_language(int(user_id)) != 'en':
        return buttons
    def one(btn):
        try:
            inner = getattr(btn, 'button', btn)
            txt = getattr(inner, 'text', None)
            if not txt or not re.search(r'[\u0600-\u06ff]', str(txt)):
                return btn
            newtxt = _translate_ar_to_en(txt)
            url = getattr(inner, 'url', None)
            data = getattr(inner, 'data', None)
            if url:
                return Button.url(newtxt, url)
            if data is not None:
                return Button.inline(newtxt, data=data)
        except Exception:
            pass
        return btn
    try:
        return [[one(b) for b in row] for row in buttons]
    except Exception:
        return buttons

async def l_respond(event, *args, **kwargs):
    uid = getattr(event, 'sender_id', None) or getattr(event, 'chat_id', None)
    if args:
        args = (_localize_text(uid, args[0]),) + tuple(args[1:])
    if 'buttons' in kwargs:
        kwargs['buttons'] = _localize_buttons(uid, kwargs['buttons'])
    return await event.respond(*args, **kwargs)

async def l_edit(event, *args, **kwargs):
    uid = getattr(event, 'sender_id', None) or getattr(event, 'chat_id', None)
    if args:
        args = (_localize_text(uid, args[0]),) + tuple(args[1:])
    if 'buttons' in kwargs:
        kwargs['buttons'] = _localize_buttons(uid, kwargs['buttons'])
    return await event.edit(*args, **kwargs)

async def l_client_send_message(client_obj, entity, *args, **kwargs):
    uid = entity if isinstance(entity, int) else getattr(entity, 'user_id', None) or getattr(entity, 'id', None)
    if args:
        args = (_localize_text(uid, args[0]),) + tuple(args[1:])
    if 'buttons' in kwargs:
        kwargs['buttons'] = _localize_buttons(uid, kwargs['buttons'])
    return await client_obj.send_message(entity, *args, **kwargs)

async def l_bot_send_message(bot_obj, chat_id, *args, **kwargs):
    uid = chat_id if isinstance(chat_id, int) else getattr(chat_id, 'user_id', None) or getattr(chat_id, 'id', None)
    if args:
        args = (_localize_text(uid, args[0]),) + tuple(args[1:])
    if 'buttons' in kwargs:
        kwargs['buttons'] = _localize_buttons(uid, kwargs['buttons'])
    return await bot_obj.send_message(chat_id, *args, **kwargs)

# Localize every Conversation.send_message call as well, so sub-menus and
# multi-step dialogs follow the user's saved language automatically.
_original_conversation_send_message = Conversation.send_message
async def _localized_conversation_send_message(self, message=None, *args, **kwargs):
    uid = getattr(self, "chat_id", None)
    if isinstance(message, str):
        message = _localize_text(uid, message)
    if "buttons" in kwargs:
        kwargs["buttons"] = _localize_buttons(uid, kwargs["buttons"])
    return await _original_conversation_send_message(self, message, *args, **kwargs)
Conversation.send_message = _localized_conversation_send_message

# ================= User language / currency =================
def get_user_prefs(user_id):
    user_id = int(user_id)
    key = f"prefs_{user_id}"
    prefs = db.get(key) if db.exists(key) else {}
    if not isinstance(prefs, dict):
        prefs = {}
    # Keep language/currency inside the user record as well as the prefs key.
    # This makes the choice survive /start and older database layouts.
    user = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {}
    if isinstance(user, dict):
        if not prefs.get("language") and user.get("language"):
            prefs["language"] = user.get("language")
        if not prefs.get("currency") and user.get("currency"):
            prefs["currency"] = user.get("currency")
    prefs.setdefault("language", "ar")
    prefs.setdefault("currency", "USD")
    return prefs

def set_user_pref(user_id, key, value):
    user_id = int(user_id)
    prefs = get_user_prefs(user_id)
    prefs[key] = value
    db.set(f"prefs_{user_id}", prefs)
    if db.exists(f"user_{user_id}"):
        user = db.get(f"user_{user_id}")
        if isinstance(user, dict):
            user[key] = value
            db.set(f"user_{user_id}", user)
    return prefs

def get_global_exchange_rate():
    try:
        value = db.get("usd_egp_rate") if db.exists("usd_egp_rate") else 50
        value = float(value)
        return value if value > 0 else 50.0
    except Exception:
        return 50.0

def user_currency(user_id):
    return get_user_prefs(user_id).get("currency", "USD")

def user_language(user_id):
    return get_user_prefs(user_id).get("language", "ar")

def display_money(value, user_id, with_symbol=True):
    try:
        usd = float(value)
    except Exception:
        usd = 0.0
    cur = user_currency(user_id)
    if cur == "EGP":
        amount = usd * get_global_exchange_rate()
        return f"{amount:.2f} {'ج.م' if with_symbol else ''}".strip()
    return f"{usd:.2f} {'$' if with_symbol else ''}".strip()


def display_usd_price(value, user_id):
    """Display a normal USD-denominated value in the user's selected currency.
    This helper is used by non-account UI such as balance/rewards/payment summaries.
    """
    return display_money(value, user_id, with_symbol=True)

def tr(user_id, ar, en):
    return en if user_language(user_id) == "en" else ar

def client_main_panel(user_id):
    """Build the old-style client main panel, keeping channel names as clickable links.
    Language and display currency are taken from the saved user preferences.
    """
    user_id = int(user_id)
    user = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {}
    coins = user.get("coins", 0) if isinstance(user, dict) else 0
    lang = user_language(user_id)
    cur = user_currency(user_id)
    balance = display_money(coins, user_id)

    if lang == "en":
        if cur == "EGP":
            currency_name = "Egyptian Pound"
            currency_symbol = "EGP"
        else:
            currency_name = "US Dollar"
            currency_symbol = "$"
        return (
            "**• Main Menu 🏡**\n\n"
            f"📁 ¦ Your ID : : `{user_id}`\n"
            f"🏛 ¦ Your balance : {balance}\n"
            f"💱 ¦ Bot currency : {currency_symbol} {currency_name} {currency_symbol}\n\n"
            "                [❶ Official Bot Channel ❶](https://t.me/AF_R_O_TO)\n\n"
            "     [❷ Account Prices Channel ❷](https://t.me/TG_AFROTO_PRICE)\n\n"
            "⚙️ Control the bot using the buttons below ⬇️"
        )

    if cur == "EGP":
        currency_name = "جنيه مصري"
        currency_symbol = "ج.م"
    else:
        currency_name = "دولار امريكي"
        currency_symbol = "$"
    return (
        "**• القائمة الرئيسية 🏡**\n\n"
        f"📁 ¦ ايدي حسابك : : `{user_id}`\n"
        f"🏛 ¦ رصيدك : {balance}\n"
        f"💱 ¦ عملة البوت : {currency_symbol} {currency_name} {currency_symbol}\n\n"
        "                [❶ قناة البوت الرسمية ❶](https://t.me/AF_R_O_TO)\n\n"
        "     [❷ قناة متابعه اسعار الحسابات ❷](https://t.me/TG_AFROTO_PRICE)\n\n"
        "⚙️ تحكم بالبوت عبر الأزرار بالأسفل ⬇️"
    )

def _is_admin_user(user_id):
    try:
        uid = int(user_id)
        admins = db.get("admins") if db.exists("admins") else []
        return uid == int(admin) or uid in [int(x) for x in (admins or [])]
    except Exception:
        return False

def main_menu_buttons(user_id):
    rows = _main_menu_rows(user_id)
    if _is_admin_user(user_id):
        rows.append([Button.inline(accent("🛠 لوحة الأدمن"), data="admin_panel")])
    return rows

def _main_menu_rows(user_id):
    en = user_language(user_id) == "en"
    return [
        [Button.inline(accent("📲 Ready Telegram Accounts") if en else "📲| حسابات تليجرام جاهزه", data="buy")],
        [Button.inline(accent("💾 Buy Session") if en else "💾| شراء سيشن", data="bsbuy")],
        [Button.inline(accent("🛒 Sell Accounts") if en else "🛒| بيع الحسابات", data="shp:hub")],
        [Button.url(accent("Support") if en else "فريق الدعم", url="t.me/J_D_D_M"), Button.inline(accent("Agents") if en else "الوكلاء", data="wkala")],
        [Button.inline(accent("💳 Recharge Balance") if en else "شحن رصيد", data="shahn")],
        [Button.inline(accent("🤖 AI Support") if en else "🤖 مساعد الدعم الآلي", data="ai_support")],
        [Button.url(accent("Successful Purchases") if en else "الشراء الناجح", url="t.me/A_F_R_OTO_TRA_ST"), Button.url(accent("📢 Updates") if en else "📢 التحديثات", url=updates_channel_url())],
        [Button.inline(accent("👥 My Referrals") if en else "الاحالة الخاص بك", data="referral"), Button.inline(accent("🎁 Referral Rewards") if en else "🎁 استبدال رصيد الإحالات", data="ref_rewards")],
        [Button.inline(accent("🌐 Language / اللغة"), data="change_language"), Button.inline(accent("💱 Currency / العملة"), data="change_currency")]
    ]

while True: 
    try:
        binance_client = BinanceClient(binance_api_key, binance_api_secret)
        print("✅ الاتصال بـ Binance ناجح!")
        break 
    except BinanceAPIException as e:
        if "Service unavailable from a restricted location" in str(e):
            print("⚠️ تم حظر الوصول من موقعك! يستمر البرنامج في العمل...")
            break 
        else:
            print(f"❌ خطأ غير متوقع: {e}")

pending_deposits = {}
countdown_time = 360

# Telegram Stars pricing is configurable from the admin panel.
# Example: 0.01 => 1 Star = 1 cent, 0.02 => 1 Star = 2 cents.
if not db.exists("star_usd_value"):
    db.set("star_usd_value", 0.01)

def get_star_usd_value():
    try:
        value = float(db.get("star_usd_value") or 0.01)
        return value if value > 0 else 0.01
    except Exception:
        return 0.01

def stars_for_usd(usd):
    value = get_star_usd_value()
    # Telegram Stars are whole units, so always round upward.
    return max(1, int((float(usd) / value) + 0.999999999))

wallet_addresses = {
    'BTC': {
        'address': 'bc1q9728ht5ttcctz76f62emq89n5kxg0l04ehrm84',
        'network': 'Bitcoin'
    },
    'USDT': {
        'address': 'TEYNBhRiEuYVWsc33KiTvG6qZuWRGLHRNe',
        'network': 'TRC20'
    },
    'LTC': {
        'address': '0xd9B24A99726178817B79c15c1fFA288A2dBBE9e3',
        'network': 'BNB smart chain (BEP20)'
    }
}

USERS_FILE = 'users.json'

def load_data(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as file:
            return json.load(file)
    return {}

def save_data(file_path, data):
    with open(file_path, 'w') as file:
        json.dump(data, file, indent=4)

def generate_random_key():
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(16))

import random
import string
import secrets
import requests
from telethon import Button, events
import telethon.tl.functions as functele

def generate_random_password(length=5):
    characters = string.ascii_letters + string.digits 
    return ''.join(random.choice(characters) for _ in range(length))

def generate_referral_code():
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(8))

users = load_data(USERS_FILE)

if not db.exists("accounts"):
    db.set("accounts", [])

if not db.exists("countries"):
    db.set("countries", [])

if not db.exists("bad_guys"):
    db.set("bad_guys", [])

if not db.exists("force"):
   db.set("force", [])

if not db.exists("admins"):
   db.set("admins", [admin])

if not db.exists("auto_charge_enabled"):
    db.set("auto_charge_enabled", True)

if not db.exists("manual_charge_enabled"):
    db.set("manual_charge_enabled", True)
if not db.exists("recharge_coupons"):
    db.set("recharge_coupons", {})
if not db.exists("stars_charge_enabled"):
    db.set("stars_charge_enabled", True)
if not db.exists("vodafone_cash_number"):
    db.set("vodafone_cash_number", "01061362203") 
if not db.exists("crypto_manual_enabled"):
    db.set("crypto_manual_enabled", True)
if not db.exists("crypto_manual_methods"):
    db.set("crypto_manual_methods", [])

# إعدادات الدفع اليدوي: يتم تعديلها من لوحة الأدمن.
if not db.exists("manual_payment_settings"):
    db.set("manual_payment_settings", {
        "binance_uid": "",
        "instapay_address": "",
        "vodafone_cash_number": db.get("vodafone_cash_number") if db.exists("vodafone_cash_number") else "",
        "binance_rate": 50.0,
        "instapay_rate": 50.0,
        "vodafone_rate": 50.0,
    })

def get_manual_payment_settings():
    settings = db.get("manual_payment_settings") if db.exists("manual_payment_settings") else {}
    settings = dict(settings or {})
    settings.setdefault("binance_uid", "")
    settings.setdefault("instapay_address", "")
    settings.setdefault("vodafone_cash_number", db.get("vodafone_cash_number") if db.exists("vodafone_cash_number") else "")
    settings.setdefault("binance_rate", 50.0)
    settings.setdefault("instapay_rate", 50.0)
    settings.setdefault("vodafone_rate", 50.0)
    # إخفاء/إظهار كل طريقة دفع من لوحة الأدمن (الافتراضي: ظاهرة)
    settings.setdefault("binance_enabled", True)
    settings.setdefault("instapay_enabled", True)
    settings.setdefault("vodafone_enabled", True)
    return settings

def save_manual_payment_settings(settings):
    db.set("manual_payment_settings", settings)
    db.set("vodafone_cash_number", settings.get("vodafone_cash_number", ""))

if not db.exists("groq_api_key"):
    db.set("groq_api_key", "")

async def ai_support_answer(user_id, question):
    key = str(db.get("groq_api_key") or "").strip()
    if not key:
        return "🤖 مساعد الدعم الآلي غير مُفعّل حاليًا. تواصل مع مدير البوت."

    payment = get_manual_payment_settings()
    crypto = get_crypto_methods()
    crypto_names = []
    for item in crypto:
        try:
            crypto_names.append(f"{item.get('name')} (1$ = {money(item.get('rate', 0))} {item.get('name')})")
        except Exception:
            pass

    selected_lang = user_language(user_id)
    language_instruction = (
        "IMPORTANT: Reply ONLY in English. Do not use Arabic, even if the user's question contains Arabic."
        if selected_lang == "en" else
        "IMPORTANT: Reply in clear Egyptian Arabic. Do not switch to English unless the user changes the bot language."
    )
    context = (
        "You are the support assistant for this bot only, not a general assistant. "
        "Answer questions about the bot's services, usage, charging and payment methods only. "
        "Do not take actions or claim to verify payments. You only answer after the user opens the AI Support button. "
        f"Payment methods: Binance Pay, InstaPay and Vodafone Cash. "
        f"Binance rate: {money(payment.get('binance_rate', 50))} EGP per USD, "
        f"InstaPay rate: {money(payment.get('instapay_rate', 50))} EGP per USD, "
        f"Vodafone Cash rate: {money(payment.get('vodafone_rate', 50))} EGP per USD. "
        f"Crypto methods: {', '.join(crypto_names) if crypto_names else 'none configured'}. "
        "Never invent prices or payment methods. Never request or reveal API keys, bot tokens, passwords, verification codes or session data. "
        "If the question is outside this bot's services, say briefly that you are only the bot support assistant. "
        + language_instruction
    )
    try:
        payload = {
            "model": "openai/gpt-oss-20b",
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": str(question)[:2000]}
            ],
            "temperature": 0.3,
            "max_completion_tokens": 700
        }
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=30
        )
        if r.status_code >= 400:
            print(f"Groq API error: {r.status_code} {r.text[:500]}")
            return "🤖 تعذر تشغيل مساعد الدعم الآلي حاليًا. تواصل مع مدير البوت."
        data = r.json()
        answer = str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        return answer or "🤖 لم أستطع استخراج رد الآن. تواصل مع مدير البوت."
    except Exception as e:
        print(f"Groq request error: {e}")
        return "🤖 تعذر تشغيل مساعد الدعم الآلي حاليًا. تواصل مع مدير البوت."

@client.on(events.NewMessage(func=lambda e: e.is_private and bool(e.raw_text) and not e.raw_text.startswith('/')))
async def ai_support_message(event):
    text_in=event.raw_text.strip()
    if len(text_in)<3 or text_in.startswith(('📷','💵')): return
    if not db.exists(f"ai_mode_{event.sender_id}"): return
    answer=await ai_support_answer(event.sender_id,text_in)
    await l_respond(event, answer)




def get_banned_ids():
    """Return banned user IDs as integers, tolerating old string entries."""
    raw = db.get("bad_guys") if db.exists("bad_guys") else []
    result = []
    for value in raw or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            pass
    return result


def normalize_force_channel(value):
    value = (value or "").strip()
    value = value.replace("https://t.me/", "").replace("http://t.me/", "")
    value = value.replace("t.me/", "").replace("@", "")
    return value.strip().strip("/").split("?")[0]


async def check_force_subscription(user_id):
    """Check all configured public channels. Returns (ok, missing_channels)."""
    if user_id == admin or user_id in (db.get("admins") if db.exists("admins") else []):
        return True, []

    force = db.get("force") if db.exists("force") else []
    force = [normalize_force_channel(x) for x in (force or [])]
    force = [x for x in force if x]
    if not force:
        return True, []

    missing = []
    for channel in force:
        try:
            entity = await client.get_entity(channel)
            await client.get_permissions(entity, user_id)
        except (UserNotParticipantError, ChannelPrivateError, ValueError):
            missing.append(channel)
        except Exception as exc:
            # If the bot cannot verify the channel, report it to the admin log
            # instead of silently bypassing the forced subscription.
            print(f"[force] failed to verify @{channel} for {user_id}: {exc}")
            missing.append(channel)
    return len(missing) == 0, missing


def force_buttons(channels):
    buttons = []
    for channel in channels:
        buttons.append([Button.url(accent(f"📢 الاشتراك في @{channel}"), url=f"https://t.me/{channel}")])
    buttons.append([Button.inline(accent("✅ تحقق من الاشتراك"), data="check_force")])
    return buttons

#غير الحقوق واثبت انك فاشل اذا تريد تنقل اذكر اسمي او اسم قناتي #

#====================#
#CH : @J_D_D_M 
#DEV : @J_D_D_M
#====================#

@client.on(events.NewMessage(pattern="/sell_price", func = lambda x: x.is_private))
async def start(event):
    user_id = event.chat_id
    bans = db.get('bad_guys') if db.exists('bad_guys') else []
    async with bot.conversation(event.chat_id) as x:
        countries = db.get(countries_key())
        text = ""
        for i in countries:
            text += f'{i["name"]} ({i["calling_code"]}): {i["sell_price"]}$'
        await x.send_message(text)
        
from telethon import events, Button
import random
import string
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

def generate_verification_image(code):
    width, height = 300, 150
    img = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    
    for i in range(width):
        r = random.randint(100, 200)
        g = random.randint(100, 200)
        b = random.randint(100, 200)
        draw.line((i, 0, i, height), fill=(r, g, b), width=1)
    
    try:
        font = ImageFont.truetype("arial.ttf", 150)
    except:
        font = ImageFont.load_default()
    
    for _ in range(90):  
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)), width=2)
    
    for _ in range(200): 
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
    
    bbox = draw.textbbox((0, 0), code, font=font)
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - text_width) / 2
    y = (height - text_height) / 2
    
    draw.text((x, y), code, font=font, fill=(0, 0, 0))
    
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    
    img.save("verification_code.png")

def generate_verification_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def cleanup_temp_files():
    if os.path.exists("verification_code.png"):
        os.remove("verification_code.png")

async def show_captcha_buttons(event):
    correct_code = generate_verification_code()
    
    wrong_codes = [generate_verification_code() for _ in range(4)]
    
    all_codes = wrong_codes + [correct_code]
    random.shuffle(all_codes)  
    
    buttons = [
        [Button.inline(code, data=f"captcha_{code}") for code in all_codes[:2]],
        [Button.inline(code, data=f"captcha_{code}") for code in all_codes[2:4]],
        [Button.inline(code, data=f"captcha_{code}") for code in all_codes[4:]],
    ]
    
    generate_verification_image(correct_code)
    
    await l_respond(event, "**🔐 | تحقق أنك لست روبوت:**\n\nالرجاء الضغط على الزر الذي يحتوي على الكود الصحيح.", file="verification_code.png", buttons=buttons)
    
    db.set(f"captcha_{event.chat_id}", correct_code)
    
    cleanup_temp_files()

buttonsr = main_menu_buttons

        
def get_referral_reward():
    """سعر الإحالة العام اللي بيحدده الأدمن (بالدولار داخليًا)."""
    try:
        value = float(db.get("referral_reward_amount") if db.exists("referral_reward_amount") else 0.01)
        return value if value > 0 else 0.01
    except Exception:
        return 0.01

async def process_referral_for_user(user_id, referral_id):
    """تسجيل الإحالة فقط. المكافأة بتتضاف بعد أول عملية شراء لرقم من البوت."""
    try:
        user_id = int(user_id)
        referral_id = int(referral_id)
    except (TypeError, ValueError):
        return False
    if referral_id == user_id or not db.exists(f"user_{referral_id}"):
        return False
    if db.exists(f"referral_processed_{user_id}"):
        return False
    db.set(f"referral_owner_{user_id}", referral_id)
    db.set(f"referral_processed_{user_id}", {"referrer_id": referral_id, "registered_at": time.time()})
    return True

async def reward_referral_for_purchase(buyer_id):
    """بتتصرف مرة واحدة بس، وأول ما الشخص اللي اتسمى عن طريق رابط إحالة يشتري رقم."""
    try:
        buyer_id = int(buyer_id)
    except (TypeError, ValueError):
        return False
    paid_key = f"referral_reward_paid_{buyer_id}"
    if db.exists(paid_key):
        return False
    owner_key = f"referral_owner_{buyer_id}"
    referrer_id = db.get(owner_key) if db.exists(owner_key) else None
    if not referrer_id and db.exists(f"pending_referral_{buyer_id}"):
        referrer_id = db.get(f"pending_referral_{buyer_id}")
    try:
        referrer_id = int(referrer_id)
    except (TypeError, ValueError):
        return False
    if referrer_id == buyer_id or not db.exists(f"user_{referrer_id}"):
        return False
    amount = get_referral_reward()
    referrer_data = db.get(f"user_{referrer_id}")
    if not isinstance(referrer_data, dict):
        return False
    try:
        current_ref = float(referrer_data.get("referral_balance", 0) or 0)
    except (TypeError, ValueError):
        current_ref = 0.0
    referrer_data["referral_balance"] = money_value(current_ref + amount)
    referrer_data["referrals_count"] = int(referrer_data.get("referrals_count", 0) or 0) + 1
    db.set(f"user_{referrer_id}", referrer_data)
    db.set(paid_key, {"referrer_id": referrer_id, "buyer_id": buyer_id, "amount": amount, "at": time.time()})
    try:
        await l_client_send_message(client, referrer_id,
            f"💎 • تم احتساب إحالة جديدة لك بعد أول عملية شراء لصديقك.\n\n"
            f"💵 مكافأة الإحالة: {display_money(amount, referrer_id)}\n"
            f"👥 عدد الإحالات: {referrer_data['referrals_count']}\n"
            f"🎁 رصيد الإحالات: {display_money(referrer_data.get('referral_balance', 0), referrer_id)}")
    except Exception:
        pass
    return True

@client.on(events.NewMessage(pattern=r"/admin(?:\s+.*)?$", func=lambda x: x.is_private))
async def admin_command(event):
    admins = db.get("admins") if db.exists("admins") else []
    if int(event.sender_id) != int(admin) and int(event.sender_id) not in [int(x) for x in (admins or [])]:
        await l_respond(event, "❌ الأمر مخصص للأدمن فقط.")
        return
    await l_respond(event, msgs['ADMIN_MESSAGE'], buttons=admin_main_keyboard())

@client.on(events.NewMessage(pattern=r"/pay(?:\s+.*)?$", func=lambda x: x.is_private))
async def pay_command(event):
    # Open the existing recharge menu directly.
    await deposit_menu(event)

@client.on(events.NewMessage(pattern=r"/buy(?:\s+.*)?$", func=lambda x: x.is_private))
async def buy_command(event):
    if not is_bot_enabled():
        await l_respond(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    await show_category_picker(event, "num", edit=False)

@client.on(events.NewMessage(pattern="/start", func=lambda x: x.is_private))
async def start(event):
    user_id = event.chat_id
    await _remember_username(event, force=True)
    bans = get_banned_ids()
    admins = db.get("admins") if db.exists("admins") else []
    
    if user_id != admin and user_id not in admins:
        if not is_bot_enabled():
            await l_respond(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمة")
            return
    
    referral_id = None
    if len(event.raw_text.split()) > 1:
        raw_referral_id = event.raw_text.split()[1].strip()
        if raw_referral_id.isdigit():
            candidate = int(raw_referral_id)
            if candidate != int(user_id):
                referral_id = candidate

    if len(event.raw_text.split()) > 1 and event.raw_text.split()[1].strip().startswith("pl_"):
        db.set(f"pending_plink_{user_id}", event.raw_text.split()[1].strip()[3:])

    if not is_bot_enabled():
        # وصلنا هنا يعني أدمن (المستخدم العادي اتوقف فوق برسالة الصيانة)
        await l_respond(event, MAINT_TEXT + "\n\n(البوت معطّل للمستخدمين — أنت أدمن فتقدر تستخدمه)")

    keyboard = [
        [Button.inline(accent("- نسخه احتياطيه  "), data="zip_all")],
        [Button.inline(accent("- اعدادات الارقام  "), data="ajxjao")],
        [Button.inline(accent("- الاشتراك الاجباري "), data="ajxkho"), Button.inline(accent("- الادمن "), data="aksgl")],
        [Button.inline(accent("- قناة اثباتات التسليم"), data="set_trust_channel")],
        [Button.inline(accent("- الشراء والبيع  "), data="ajkofgl")],
        [Button.inline(accent("- قسم الرصيد "), data="ajkcoingl"), Button.inline(accent("- قسم الحظر "), data="bbvjls")],
        [Button.inline(accent("- تعطيل استقبال دولة "), data="stopcountry")],
        [Button.inline(accent("- الدول المغلق استقبالها "), data="ttrt")],
        [Button.inline(accent("- اذاعة "), data="start_broadcast")],
        [Button.inline(accent("- تغيير سعر الاحالة "), data="amubnf")],
        [Button.inline(accent("🎟 إنشاء قسيمة شحن"), data="coupon_admin")],
        [Button.inline(accent("🎁 إدارة استبدالات الإحالات"), data="ref_admin")],
        [Button.inline(accent('تعطيل النجوم'), b'disablestars'), Button.inline(accent('تفعيل النجوم'), b'enablestars')],
        [Button.inline(accent('تعطيل المانيوال'), b'disablemanual'), Button.inline(accent('تفعيل المانيوال'), b'enablemanual')],
        [Button.inline(accent("💳 إدارة الدفع اليدوي"), data="manual_payment_settings")],
        [Button.inline(accent("🪙 إدارة العملات الرقمية"), data="set_crypto_wallets")],
        [Button.inline(accent("💱 تغيير سعر الصرف"), data="set_exchange_rate")],
        [Button.inline(accent("⭐ تغيير سعر النجمة"), data="set_star_price")],
        [Button.inline(accent("🤖 إعداد مساعد الدعم الآلي"), data="ai_admin")],
        [Button.inline(accent('تعطيل البوت'), b'disablebot'), Button.inline(accent('تفعيل البوت'), b'enablebot')],
        [Button.inline(accent("- حالة البوت "), data="sts")],
    ]
      
    new_user_buttons = [
        [Button.inline(accent("✅- تسجيل الدخول."), data="login"), Button.inline(accent("📌- انشاء حساب."), data="create_account")],
        [Button.inline(accent("📃- شرح البوت."), data="sharh")],
        [Button.url(accent("⚠️- الدعم الفني."), url="t.me/J_D_D_M")]
    ]
    
    buttons = [
        [Button.inline(accent("📲| حسابات تليجرام جاهزه"), data="buy")],
        [Button.inline(accent("💾| شراء سيشن"), data="bsbuy")],
        [Button.url(accent("فريق الدعم"), url="t.me/J_D_D_M"), Button.inline(accent("الوكلاء"), data="wkala")],
        [Button.inline(accent("شحن رصيد"), data="shahn")],
        [Button.inline(accent("🤖 مساعد الدعم الآلي"), data="ai_support")],
        [Button.url(accent("الشراء الناجح"), url="t.me/A_F_R_OTO_TRA_ST"), Button.inline(accent("الاعدادات"), data="SUPEGY")],
        [Button.inline(accent("الاحاله الخاص بك"), data="referral"), Button.inline(accent("🎁 استبدال رصيد الإحالات"), data="ref_rewards")],
        [Button.inline(accent("🎟 استبدال قسيمة شحن"), data="redeem_coupon")]
    ]
    
    if user_id in bans:
        await l_respond(event, "🚫 تم حظرك من استخدام البوت.")
        return

    if user_id != admin and user_id not in admins:
        if referral_id and not db.exists(f"user_{user_id}"):
            db.set(f"pending_referral_{user_id}", referral_id)
        force_ok, missing_force = await check_force_subscription(user_id)
        if not force_ok:
            await l_respond(event, 
                "🔒 **الاشتراك الإجباري**\n\n"
                "لاستخدام البوت، اشترك في القنوات المطلوبة ثم اضغط «تحقق من الاشتراك»."
                , buttons=force_buttons(missing_force)
            )
            return

        # بعد إكمال الاشتراك، لا يدخل المستخدم إلى أقسام البوت قبل اجتياز CAPTCHA.
        if not db.get(f"captcha_verified_{user_id}"):
            await show_captcha_buttons(event)
            return
    
    if not db.exists(f"user_{user_id}"):
        # مشكلة كانت هنا: الكود القديم كان بيبعت ترحيب وإشعار «شخص جديد» من غير ما يسجّل حساب المستخدم فعليًا،
        # فكل /start تاني (حتى من نفس الشخص، وده بالذات بيحصل مع الأدمن لأنه بيتخطى شاشة إنشاء الحساب)
        # كان بيدخل هنا تاني ويبعت «شخص جديد انضم» من جديد. دلوقتي بنسجّل الحساب فورًا فمتتكررش.
        db.set(f"user_{user_id}", {"coins": 0.0, "id": int(user_id), "password": "default_password", "joined_at": time.time()})
        members = 0
        if referral_id:
            await process_referral_for_user(user_id, referral_id)
            if db.exists(f"pending_referral_{user_id}"):
                db.delete(f"pending_referral_{user_id}")
        # Initialize preferences only once; never reset them on /start.
        if not db.exists(f"prefs_{user_id}"):
            db.set(f"prefs_{user_id}", {"language": "ar", "currency": "USD"})
        await l_bot_send_message(bot, user_id, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
        
        user_info = await client.get_entity(user_id)
        users = db.keys('user_%')
        for _ in users:
            members += 1
        
        username = "@" + str(user_info.username) if user_info.username else "None"
        if user_id != admin and user_id not in (admins or []):
            await l_bot_send_message(bot, admin, f'• شخص جديد انضم للبوت 👾\n\n- معلومات الشخص.\n\n- المستخدم : <a href="tg://user?id={user_id}">{user_info.first_name}</a>\n- يوزره : {username}\n- ايديه : {user_id}\n\n• اجمالي المستخدمين : {members}', parse_mode="html")
    else:
        user_record = db.get(f"user_{user_id}")
        coins = user_record.get("coins", 0) if isinstance(user_record, dict) else 0
        # /start is idempotent: preserve the user's saved language and currency.
        await l_bot_send_message(bot, user_id, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))

    pend_key = f"pending_plink_{user_id}"
    if db.exists(pend_key):
        token = db.get(pend_key)
        db.delete(pend_key)
        try:
            await l_respond(event, await _claim_points_link(user_id, token))
        except Exception as ex:
            print("points link error:", ex)

@bot.on(events.CallbackQuery(pattern=b'sts'))
async def toggle_bot(event):
    global bot_enabled
    bot_enabled = not bot_enabled
    status = "مُفعّل ✅" if bot_enabled else "مُعطّل ❌"
    await event.answer(f"- حالة البوت : {status}")

@client.on(events.CallbackQuery(data=b'disablebot'))
async def disable_bot(event):
    global bot_enabled
    if event.sender_id == admin:
        bot_enabled = False
        await event.answer("تم تعطيل البوت ❌")

#غير الحقوق واثبت انك فاشل اذا تريد تنقل اذكر اسمي او اسم قناتي #

#====================#
#CH : @J_D_D_M 
#DEV : @J_D_D_M
#====================#

@client.on(events.CallbackQuery(data=b'enablebot'))
async def enable_bot(event):
    global bot_enabled
    if event.sender_id == admin:
        bot_enabled = True
        await event.answer("تم تفعيل البوت ✅")

@bot.on(events.CallbackQuery(pattern=b'disableauto'))
async def disable_auto(event):
    db.set("auto_charge_enabled", False)
    await l_edit(event, "✅ تم تعطيل الشحن التلقائي.")

@bot.on(events.CallbackQuery(pattern=b'enableauto'))
async def enable_auto(event):
    db.set("auto_charge_enabled", True)
    await l_edit(event, "✅ تم تفعيل الشحن التلقائي.")

@bot.on(events.CallbackQuery(pattern=b'disablemanual'))
async def disable_manual(event):
    db.set("manual_charge_enabled", False)
    await l_edit(event, "✅ تم تعطيل الشحن المانيوال.", buttons=[[Button.inline(accent("🔙 رجوع"), data="adm:pay")]])

@bot.on(events.CallbackQuery(pattern=b'enablemanual'))
async def enable_manual(event):
    db.set("manual_charge_enabled", True)
    await l_edit(event, "✅ تم تفعيل الشحن المانيوال.", buttons=[[Button.inline(accent("🔙 رجوع"), data="adm:pay")]])


@bot.on(events.CallbackQuery(data=b'disablestars'))
async def disable_stars(event):
    if event.sender_id == admin:
        db.set("stars_charge_enabled", False)
        await event.answer("تم تعطيل الدفع عبر نجوم Telegram ❌")

@bot.on(events.CallbackQuery(data=b'enablestars'))
async def enable_stars(event):
    if event.sender_id == admin:
        db.set("stars_charge_enabled", True)
        await event.answer("تم تفعيل الدفع عبر نجوم Telegram ⭐")


def _bot_api_call(method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


async def send_stars_invoice(user_id, input_amount, input_currency=None):
    """Create a Stars invoice using the bot's current exchange rate.

    USD remains the accounting currency inside the bot. If the user selected
    EGP, the entered EGP amount is converted to USD using usd_egp_rate, then
    converted to Telegram Stars at 100 Stars = 1 USD.
    """
    uid = int(user_id)
    input_amount = money_value(input_amount)
    input_currency = input_currency or user_currency(uid)
    rate = get_global_exchange_rate()

    if input_currency == "EGP":
        requested_usd = input_amount / rate
    else:
        requested_usd = input_amount

    star_value = get_star_usd_value()
    stars = stars_for_usd(requested_usd)
    credited_usd = stars * star_value
    payload = (
        f"stars_deposit:{uid}:{credited_usd:.8f}:{stars}:"
        f"{input_amount:.8f}:{input_currency}:{rate:.8f}"
    )
    currency_label = "ج.م" if input_currency == "EGP" else "$"
    result = await asyncio.to_thread(_bot_api_call, "sendInvoice", {
        "chat_id": uid,
        "title": "شحن الرصيد عبر Telegram Stars",
        "description": (
            f"شحن بقيمة {input_amount:g} {currency_label}. "
            f"سعر الصرف 1$ = {rate:g} ج.م — قيمة ⭐ = {star_value:g}$"
        ),
        "payload": payload,
        "provider_token": "",
        "currency": "XTR",
        "prices": json.dumps([
            {"label": f"{credited_usd:g}$ رصيد", "amount": stars}
        ], ensure_ascii=False)
    })
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return stars, credited_usd, rate


@bot.on(events.CallbackQuery(data=b'chargestars'))
async def charge_stars(event):
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    if not db.get("stars_charge_enabled"):
        await event.answer("⚠️ الدفع عبر النجوم معطل حاليًا.", alert=True)
        return
    async with bot.conversation(event.chat_id) as x:
        cur = user_currency(event.sender_id)
        rate = get_global_exchange_rate()
        if cur == "EGP":
            await x.send_message(
                f"⭐ أرسل قيمة الرصيد بالجنيه المصري.\n\n"
                f"📊 سعر الصرف الحالي: 1$ = {rate:.2f} ج.م\n"
                f"⭐ قيمة النجمة: {get_star_usd_value():g}$"
            )
        else:
            await x.send_message(
                f"⭐ أرسل قيمة الرصيد بالدولار.\n\n"
                f"📊 سعر الصرف الحالي: 1$ = {rate:.2f} ج.م\n"
                f"⭐ قيمة النجمة: {get_star_usd_value():g}$"
            )
        amount_msg = await x.get_response()
        try:
            amount = money_value(amount_msg.text.strip().replace(',', '.'))
            max_input = 1000.0 if cur == "USD" else 1000.0 * rate
            if amount <= 0 or amount > max_input:
                raise ValueError
        except Exception:
            unit = "$" if cur == "USD" else "ج.م"
            await x.send_message(f"❌ أرسل مبلغًا صحيحًا أكبر من 0 وأقل من أو يساوي {max_input:g} {unit}.")
            return
        try:
            stars, credited_usd, used_rate = await send_stars_invoice(event.sender_id, amount, cur)
            credited_local = credited_usd if cur == "USD" else credited_usd * used_rate
            unit = "$" if cur == "USD" else "ج.م"
            await x.send_message(
                f"⭐ تم إنشاء فاتورة الدفع.\n\n"
                f"💵 المبلغ المطلوب: {amount:g} {unit}\n"
                f"📊 سعر الصرف: 1$ = {used_rate:.2f} ج.م\n"
                f"⭐ عدد النجوم: {stars} نجمة\n"
                f"💰 الرصيد الذي سيُضاف: {credited_usd:.2f}$"
                + (f"\n💱 ما يعادله: {credited_local:.2f} ج.م" if cur == "EGP" else "")
            )
        except Exception as e:
            print(f"Stars invoice error: {e}")
            await x.send_message("❌ تعذر إنشاء فاتورة النجوم حاليًا، حاول مرة أخرى لاحقًا.")


async def stars_payment_polling():
    offset = None
    while True:
        try:
            params = {"timeout": 25, "allowed_updates": json.dumps(["pre_checkout_query", "message"])}
            if offset is not None:
                params["offset"] = offset
            result = await asyncio.to_thread(_bot_api_call, "getUpdates", params)
            if not result.get("ok"):
                await asyncio.sleep(3)
                continue
            for upd in result.get("result", []):
                offset = upd["update_id"] + 1
                pre = upd.get("pre_checkout_query")
                if pre:
                    payload = pre.get("invoice_payload", "")
                    valid = payload.startswith("stars_deposit:") and pre.get("currency") == "XTR"
                    if valid:
                        try:
                            parts = payload.split(":")
                            if len(parts) == 4:
                                _, uid, dollars, stars = parts
                            elif len(parts) >= 7:
                                _, uid, dollars, stars = parts[:4]
                            else:
                                raise ValueError
                            valid = int(uid) == int(pre.get("from", {}).get("id")) and int(stars) == int(pre.get("total_amount"))
                        except Exception:
                            valid = False
                    answer = {"pre_checkout_query_id": pre["id"], "ok": valid}
                    if not valid:
                        answer["error_message"] = "تعذر التحقق من الفاتورة. حاول إنشاء فاتورة جديدة."
                    await asyncio.to_thread(_bot_api_call, "answerPreCheckoutQuery", answer)
                    continue
                msg = upd.get("message", {})
                payment = msg.get("successful_payment")
                if not payment or payment.get("currency") != "XTR":
                    continue
                payload = payment.get("invoice_payload", "")
                try:
                    parts = payload.split(":")
                    if len(parts) == 4:
                        _, uid, dollars, expected_stars = parts
                    elif len(parts) >= 7:
                        _, uid, dollars, expected_stars = parts[:4]
                    else:
                        raise ValueError
                    uid = int(uid)
                    dollars = money_value(dollars)
                    expected_stars = int(expected_stars)
                    if int(msg.get("chat", {}).get("id")) != uid:
                        continue
                    if int(payment.get("total_amount", 0)) != expected_stars:
                        continue
                except Exception:
                    continue
                charge_id = payment.get("telegram_payment_charge_id")
                if not charge_id or db.exists(f"stars_charge_{charge_id}"):
                    continue
                # Add Stars payment to the main `coins` balance used by the bot UI.
                # The previous code wrote to the separate sqlite `coind` wallet.
                if not db.exists(f"user_{uid}"):
                    db.set(f"user_{uid}", {"coins": 0.0, "id": uid, "password": "default_password", "joined_at": time.time()})
                user_data = db.get(f"user_{uid}")
                try:
                    current_coins = float(user_data.get("coins", 0))
                except (TypeError, ValueError):
                    current_coins = 0.0
                user_data["coins"] = money_value(current_coins + dollars)
                user_data.setdefault("id", uid)
                db.set(f"user_{uid}", user_data)

                db.set(f"stars_charge_{charge_id}", {"user_id": uid, "amount": dollars, "stars": expected_stars})
                balance = user_data["coins"]
                await l_client_send_message(client, uid, f"✅ تم تأكيد دفعك عبر Telegram Stars.\n\n⭐ النجوم المدفوعة: {expected_stars}\n💵 تمت إضافة: {money(dollars)}$\n💰 رصيدك الحالي: {money(balance)}$")
                await l_client_send_message(client, admin, f"⭐ شحن تلقائي عبر Telegram Stars\n\nالمستخدم: {uid}\nالمبلغ: {money(dollars)}$\nالنجوم: {expected_stars}")
        except Exception as e:
            print(f"Stars polling error: {e}")
            await asyncio.sleep(3)


def get_crypto_methods():
    methods = db.get("crypto_manual_methods") if db.exists("crypto_manual_methods") else []
    normalized = []
    changed = False
    for m in (methods or []):
        item = dict(m or {})
        # Backward compatibility with the old field name "price".
        if not item.get("rate") and item.get("price"):
            item["rate"] = item.get("price")
            changed = True
        try:
            item["rate"] = money_value(item.get("rate", 0))
        except Exception:
            item["rate"] = 0.0
        normalized.append(item)
    if changed:
        db.set("crypto_manual_methods", normalized)
    return normalized

@client.on(events.CallbackQuery(data=b'manual_crypto'))
async def manual_crypto_menu(event):
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة")
        return
    if not db.get("crypto_manual_enabled"):
        await event.answer("⚠️ الدفع بالعملات الرقمية معطل حاليًا.", alert=True)
        return
    methods = get_crypto_methods()
    # زر العملة يعرض الاسم فقط، بدون سعر الصرف.
    buttons = [[Button.inline(accent(f"🪙 {m['name']}"), data=f"mcrypto_{m['id']}")] for m in methods if m.get('name') and m.get('address') and money_value(m.get('rate', 0)) > 0]
    if not buttons:
        buttons.append([Button.inline(accent("⚠️ لا توجد عملات مضافة حاليًا"), data="noop")])
    buttons.append([Button.inline(accent("رجوع"), data="shahn")])
    await l_edit(event, "🪙 الدفع اليدوي بالعملات الرقمية\n\nاختر العملة التي تريد الدفع بها:", buttons=buttons)

@client.on(events.CallbackQuery(data=b'noop'))
async def noop_crypto(event):
    await event.answer("لم تتم إضافة عملات رقمية من لوحة الأدمن حتى الآن.", alert=True)

@client.on(events.CallbackQuery(pattern=r"mcrypto_[0-9a-f]+$"))
async def manual_crypto_coin(event):
    db.delete(f"ai_mode_{event.sender_id}")
    cid = event.data.decode().split("_", 1)[1]
    methods = get_crypto_methods()
    item = next((m for m in methods if str(m.get('id')) == cid), None)
    if not item:
        await event.answer("❌ العملة غير موجودة.", alert=True)
        return
    rate = money_value(item.get('rate', 0))
    if rate <= 0 or not item.get('address'):
        await event.answer("⚠️ العملة غير مُعدة بشكل صحيح من لوحة الأدمن.", alert=True)
        return

    async with bot.conversation(event.chat_id, timeout=120) as conv:
        await conv.send_message(
            f"🪙 الدفع عبر {item['name']}\n\n"
            "💵 اكتب المبلغ الذي تريد إضافته إلى رصيدك بالدولار.\n\n"
            "مثال: 1 أو 5 أو 10"
        )
        response = await conv.get_response()
        try:
            amount_usd = money_value(response.text.strip().replace(',', '.'))
        except Exception:
            await conv.send_message("❌ المبلغ غير صحيح. ابدأ العملية من جديد وأرسل رقمًا فقط.")
            return
        if amount_usd < MIN_AMOUNT or amount_usd > MAX_AMOUNT:
            await conv.send_message(f"❌ المبلغ يجب أن يكون بين {money(MIN_AMOUNT)}$ و {money(MAX_AMOUNT)}$.")
            return

    crypto_amount = money_value(amount_usd * rate)
    request_id = uuid.uuid4().hex[:12]
    req = {
        "id": request_id,
        "user_id": int(event.sender_id),
        "method": "crypto",
        "method_id": cid,
        "currency": item['name'],
        "amount_usd": amount_usd,
        "crypto_amount": crypto_amount,
        "rate": rate,
        "destination": item['address'],
        "status": "awaiting_payment",
        "created_at": time.time(),
    }
    db.set(f"crypto_payment_{request_id}", req)
    db.set(f"crypto_current_{event.sender_id}", request_id)

    # رسالة الدفع: تعرض سعر الصرف + المبلغ المطلوب بالعملة + ما سيضاف لرصيد المستخدم بالدولار.
    await l_respond(event, 
        f"🪙 الدفع عبر {item['name']}\n"
        "─────────────────────\n"
        f"📍 عنوان الدفع:\n`{item['address']}`\n\n"
        f"📊 سعر الصرف: 1$ = {money(rate)} {item['name']}\n"
        f"💵 رصيدك المطلوب: {display_money(amount_usd, event.sender_id)}\n"
        f"💸 المطلوب تحويله: {money(crypto_amount)} {item['name']}\n"
        "─────────────────────\n"
        "بعد التحويل اضغط الزر بالأسفل ثم أرسل صورة التحويل فقط.",
        buttons=[[Button.inline(accent("📷 إرسال صورة التحويل"), data=f"mcrypto_paid_{cid}_{request_id}")],
                 [Button.inline(accent("❌ إلغاء"), data=f"mcrypto_cancel_{request_id}")]]
    )

@client.on(events.CallbackQuery(pattern=r"mcrypto_paid_[0-9a-f]+_[0-9a-f]+$"))
async def manual_crypto_paid(event):
    parts = event.data.decode().split("_")
    cid, request_id = parts[2], parts[3]
    key = f"crypto_payment_{request_id}"
    if not db.exists(key):
        await event.answer("❌ الطلب غير موجود أو انتهت صلاحيته.", alert=True)
        return
    req = db.get(key)
    if int(req.get("user_id", 0)) != int(event.sender_id):
        return
    if req.get("status") != "awaiting_payment":
        await event.answer("⚠️ الطلب ليس في انتظار الدفع.", alert=True)
        return
    req["status"] = "awaiting_screenshot"
    req["updated_at"] = time.time()
    db.set(key, req)
    db.set(f"crypto_current_{event.sender_id}", request_id)
    await l_respond(event, "📷 أرسل الآن **صورة التحويل فقط** هنا.")

@client.on(events.CallbackQuery(pattern=r"mcrypto_cancel_[0-9a-f]+$"))
async def manual_crypto_cancel(event):
    request_id = event.data.decode().split("_", 2)[2]
    key = f"crypto_payment_{request_id}"
    if db.exists(key):
        req = db.get(key)
        if int(req.get("user_id", 0)) == int(event.sender_id):
            req["status"] = "cancelled"
            db.set(key, req)
            db.delete(f"crypto_current_{event.sender_id}")
    await l_edit(event, "❌ تم إلغاء طلب الشحن.")

@client.on(events.NewMessage(func=lambda e: e.is_private and bool(e.photo)))
async def crypto_screenshot(event):
    current_key = f"crypto_current_{event.sender_id}"
    request_id = db.get(current_key) if db.exists(current_key) else None
    if not request_id:
        return
    key = f"crypto_payment_{request_id}"
    if not db.exists(key):
        return
    req = db.get(key)
    if req.get('status') != 'awaiting_screenshot':
        return

    methods = get_crypto_methods()
    item = next((m for m in methods if str(m.get('id')) == str(req.get('method_id'))), None)
    if not item:
        return

    req['status'] = 'under_review'
    req['screenshot_message_id'] = int(event.message.id)
    req['updated_at'] = time.time()
    db.set(key, req)

    caption = (
        "⚡ • طلب شحن جديد بالعملات الرقمية\n\n"
        f"المستخدم: {event.sender_id}\n"
        f"العملة: {item['name']}\n"
        f"سعر الصرف: 1$ = {money(req['rate'])} {item['name']}\n"
        f"المبلغ المضاف للرصيد: {money(req['amount_usd'])}$\n"
        f"المطلوب تحويله: {money(req['crypto_amount'])} {item['name']}\n"
        f"رقم العملية: {request_id}\n\n"
        "• قم بالتحقق من العملية ثم اضغط قبول أو رفض."
    )
    buttons = [[
        Button.inline(accent("✅ قبول وإضافة الرصيد"), data=f"mcrypto_accept:{event.sender_id}:{item['id']}:{request_id}"),
        Button.inline(accent("❌ رفض"), data=f"mcrypto_reject:{event.sender_id}:{item['id']}:{request_id}")
    ]]

    try:
        temp_path = f"/tmp/crypto_payment_{request_id}_{event.message.id}.jpg"
        downloaded = await event.download_media(file=temp_path)
        if not downloaded:
            raise RuntimeError("تعذر تنزيل صورة التحويل")
        try:
            await client.send_file(int(admin), downloaded, caption=caption, buttons=buttons)
        finally:
            try:
                os.remove(downloaded)
            except Exception:
                pass
    except Exception as e:
        print(f"Crypto payment admin photo error [{request_id}]: {e}")
        req['status'] = 'awaiting_screenshot'
        req['admin_send_error'] = str(e)[:500]
        db.set(key, req)
        await l_respond(event, "❌ تعذر إرسال صورة التحويل للمراجعة. أرسل الصورة مرة أخرى.")
        return

    db.delete(current_key)
    await l_respond(event, "⏳ جاري مراجعة العملية .. يستغرق الامر من 5 دقائق الى 12 ساعه")

@client.on(events.CallbackQuery(pattern=r"mcrypto_accept:"))
async def manual_crypto_accept(event):
    if event.sender_id != admin:
        return
    parts = event.data.decode().split(":")
    if len(parts) < 4:
        return
    _, uid, cid, request_id = parts[:4]
    key = f"crypto_payment_{request_id}"
    if not db.exists(key):
        await event.answer("الطلب غير موجود.", alert=True)
        return
    req = db.get(key)
    if req.get('status') != 'under_review':
        await event.answer("⚠️ تم التعامل مع الطلب مسبقًا.", alert=True)
        return
    if int(req.get('user_id', 0)) != int(uid):
        return
    userkey = f"user_{uid}"
    user = db.get(userkey) if db.exists(userkey) else {"id": int(uid), "coins": 0.0, "joined_at": time.time()}
    amount = money_value(req.get("amount_usd", 0))
    user["coins"] = money_value(money_value(user.get("coins", 0)) + amount)
    db.set(userkey, user)
    req["status"] = "approved"
    req["approved_at"] = time.time()
    db.set(key, req)
    db.delete(f"crypto_current_{uid}")
    await l_edit(event, "✅ تمت الموافقة وإضافة الرصيد.")
    await l_client_send_message(client, int(uid),
        f"✅ تم قبول شحنك عبر {req.get('currency', 'العملة الرقمية')}.\n\n"
        f"💵 المضاف إلى رصيدك: {display_money(amount, uid)}\n"
        f"💰 رصيدك الحالي: {display_money(user['coins'], uid)}"
    )

@client.on(events.CallbackQuery(pattern=r"mcrypto_reject:"))
async def manual_crypto_reject(event):
    if event.sender_id != admin:
        return
    parts = event.data.decode().split(":")
    if len(parts) < 4:
        return
    _, uid, cid, request_id = parts[:4]
    key = f"crypto_payment_{request_id}"
    if not db.exists(key):
        await event.answer("الطلب غير موجود.", alert=True)
        return
    req = db.get(key)
    if req.get('status') != 'under_review':
        await event.answer("⚠️ تم التعامل مع الطلب مسبقًا.", alert=True)
        return
    req["status"] = "rejected"
    req["rejected_at"] = time.time()
    db.set(key, req)
    db.delete(f"crypto_current_{uid}")
    await l_edit(event, "❌ تم رفض طلب الشحن.")
    await l_client_send_message(client, int(uid),
        f"❌ تم رفض طلب الشحن عبر {req.get('currency', 'العملة الرقمية')}.\n"
        "إذا كنت متأكدًا من التحويل، تواصل مع الدعم."
    )

@client.on(events.CallbackQuery(data=b'set_crypto_wallets'))
async def set_crypto_wallets(event):
    if event.sender_id != admin:
        return
    methods = get_crypto_methods()
    async with bot.conversation(event.chat_id, timeout=180) as x:
        while True:
            await x.send_message("🪙 إدارة العملات الرقمية\nأرسل: إضافة / حذف / تعديل / إنهاء")
            action = (await x.get_response()).text.strip()
            if action == "إنهاء":
                break
            if action == "إضافة":
                await x.send_message("اسم العملة:")
                name = (await x.get_response()).text.strip()
                if not name:
                    await x.send_message("❌ اسم العملة غير صحيح.")
                    continue
                await x.send_message(
                    "سعر صرف العملة مقابل الدولار:\n\n"
                    "اكتب عدد وحدات العملة التي تساوي 1 دولار.\n"
                    "مثال: إذا كان 1$ = 1 USDT اكتب 1."
                )
                try:
                    rate = money_value((await x.get_response()).text.strip().replace(',', '.'))
                    if rate <= 0:
                        raise ValueError
                except Exception:
                    await x.send_message("❌ سعر الصرف غير صحيح.")
                    continue
                await x.send_message("عنوان/محفظة الدفع:")
                address = (await x.get_response()).text.strip()
                if not address:
                    await x.send_message("❌ عنوان الدفع غير صحيح.")
                    continue
                methods.append({"id": uuid.uuid4().hex[:8], "name": name, "rate": rate, "address": address})
                db.set("crypto_manual_methods", methods)
                await x.send_message(f"✅ تمت إضافة {name}.\n📊 سعر الصرف: 1$ = {money(rate)} {name}")
            elif action in ("حذف", "تعديل"):
                if not methods:
                    await x.send_message("لا توجد عملات مضافة.")
                    continue
                await x.send_message("أرسل اسم العملة التي تريد التعامل معها:")
                name = (await x.get_response()).text.strip()
                idx = next((i for i, m in enumerate(methods) if str(m.get('name')) == name), None)
                if idx is None:
                    await x.send_message("❌ العملة غير موجودة.")
                    continue
                if action == "حذف":
                    methods.pop(idx)
                    db.set("crypto_manual_methods", methods)
                    await x.send_message("✅ تم الحذف.")
                else:
                    await x.send_message("الاسم الجديد (أو نفس الاسم):")
                    new_name = (await x.get_response()).text.strip()
                    if new_name:
                        methods[idx]['name'] = new_name
                    await x.send_message(
                        "سعر الصرف الجديد:\n\n"
                        "اكتب عدد وحدات العملة التي تساوي 1 دولار.\n"
                        "مثال: 1 USDT = 1$ → اكتب 1"
                    )
                    try:
                        new_rate = money_value((await x.get_response()).text.strip().replace(',', '.'))
                        if new_rate <= 0:
                            raise ValueError
                    except Exception:
                        await x.send_message("❌ سعر الصرف غير صحيح، ولم يتم حفظ التعديل.")
                        continue
                    methods[idx]['rate'] = new_rate
                    await x.send_message("العنوان الجديد:")
                    new_address = (await x.get_response()).text.strip()
                    if new_address:
                        methods[idx]['address'] = new_address
                    methods[idx].pop('price', None)
                    db.set("crypto_manual_methods", methods)
                    await x.send_message("✅ تم التعديل.")
            else:
                await x.send_message("❌ اختر إضافة أو حذف أو تعديل أو إنهاء.")

# ==================== الدفع اليدوي: Binance Pay / InstaPay / Vodafone Cash ====================
# العميل يدخل قيمة الشحن بالدولار مرة واحدة، ثم يرسل صورة التحويل فقط.
# لا نطلب رقم مرجعي أو TxID من العميل.

MANUAL_METHODS = {
    "binance": {
        "name": "Binance Pay",
        "destination_key": "binance_uid",
        "rate_key": "binance_rate",
        "destination_label": "عنوان الدفع / UID",
        "enabled_key": "binance_enabled",
        "icon": "💰",
        "user_data": b"chargebinance",
    },
    "instapay": {
        "name": "InstaPay",
        "destination_key": "instapay_address",
        "rate_key": "instapay_rate",
        "destination_label": "رقم الهاتف / عنوان الدفع",
        "enabled_key": "instapay_enabled",
        "icon": "🏦",
        "user_data": b"chargeinstapay",
    },
    "vodafone": {
        "name": "Vodafone Cash",
        "destination_key": "vodafone_cash_number",
        "rate_key": "vodafone_rate",
        "destination_label": "رقم المحفظة",
        "enabled_key": "vodafone_enabled",
        "icon": "📱",
        "user_data": b"chargecrypto",
    },
}

def manual_method_available(settings, method):
    """الطريقة تظهر للمستخدم فقط لو مفعّلة ومعاها رقم/عنوان وسعر صرف."""
    cfg = MANUAL_METHODS[method]
    try:
        return (bool(settings.get(cfg["enabled_key"], True))
                and bool(str(settings.get(cfg["destination_key"], "")).strip())
                and money_value(settings.get(cfg["rate_key"], 0)) > 0)
    except Exception:
        return False

async def _ask_manual_amount(event, method):
    db.delete(f"ai_mode_{event.sender_id}")
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة.")
        return
    if not db.get("manual_charge_enabled"):
        await event.answer("⚠️ الشحن المانيوال معطل حاليًا.", alert=True)
        return

    settings = get_manual_payment_settings()
    cfg = MANUAL_METHODS[method]
    destination = str(settings.get(cfg["destination_key"], "")).strip()
    if not settings.get(cfg["enabled_key"], True) or not destination:
        await event.answer("⚠️ طريقة الدفع دي غير متاحة حاليًا.", alert=True)
        return

    # Use the global exchange rate configured by the admin so customer top-ups
    # do not remain stuck at the payment method's old fixed rate.
    rate = get_global_exchange_rate()
    if rate <= 0:
        await l_respond(event, "⚠️ سعر الصرف غير مضبوط من لوحة الأدمن.")
        return
    async with bot.conversation(event.chat_id, timeout=120) as conv:
        await conv.send_message(
            "💵 أرسل قيمة الرصيد التي تريد شحنها بالدولار.\n\n"
            "مثال: `0.5` أو `1` أو `10`"
        )
        response = await conv.get_response()
        try:
            amount_usd = money_value(response.text.strip().replace(',', '.'))
        except Exception:
            await conv.send_message("❌ المبلغ غير صحيح. ابدأ العملية من جديد وأرسل رقمًا فقط.")
            return
        if amount_usd < MIN_AMOUNT or amount_usd > MAX_AMOUNT:
            await conv.send_message(f"❌ المبلغ يجب أن يكون بين {money(MIN_AMOUNT)}$ و {money(MAX_AMOUNT)}$.")
            return
    local_amount = amount_usd * rate

    request_id = uuid.uuid4().hex[:12]
    request = {
        "id": request_id,
        "user_id": int(event.sender_id),
        "method": method,
        "amount_usd": amount_usd,
        "local_amount": local_amount,
        "rate": rate,
        "destination": destination,
        "status": "awaiting_payment",
        "created_at": time.time(),
    }
    db.set(f"manual_payment_{request_id}", request)
    # احفظ آخر طلب شحن للصورة بحيث نستطيع الوصول إليه مباشرة بدون البحث بالـ wildcard.
    db.set(f"manual_current_{event.sender_id}", request_id)

    # رسالة قصيرة: الرقم لوحده في سطر (اضغط عليه يتنسخ)
    text_msg = (
        f"{cfg['icon']} **{cfg['name']}**\n\n"
        f"حوّل على:\n`{destination}`\n\n"
        f"💵 {display_money(amount_usd, event.sender_id)} = **{money(local_amount)} جنيه**\n\n"
        "بعد التحويل اضغط الزر وابعت صورة التحويل 👇"
    )

    await l_respond(event, 
        text_msg,
        parse_mode='markdown',
        buttons=[[Button.inline(accent("📷 تم التحويل — إرسال الصورة"), data=f"manualpay_submit:{request_id}")],
                 [Button.inline(accent("❌ إلغاء"), data=f"manualpay_cancel:{request_id}")]]
    )

@client.on(events.CallbackQuery(data=b'ai_admin'))
async def ai_admin(event):
    if event.sender_id != admin:
        return
    db.delete(f"ai_mode_{event.sender_id}")
    db.set(f"ai_admin_pending_{event.sender_id}", True)
    await l_client_send_message(client, event.sender_id,
        "🤖 إعداد مساعد الدعم الآلي\n\nأرسل Groq API Key الآن.\nأرسل «تعطيل» لإيقاف المساعد.")

@client.on(events.NewMessage(func=lambda e: e.is_private and bool(e.raw_text) and not e.raw_text.startswith('/')))
async def ai_admin_key_receiver(event):
    uid = event.sender_id
    if not db.exists(f"ai_admin_pending_{uid}"):
        return
    value = event.raw_text.strip()
    db.delete(f"ai_admin_pending_{uid}")
    if value == "تعطيل" or value.lower() == "disable":
        db.set("groq_api_key", "")
        await l_client_send_message(client, uid, "✅ تم تعطيل مساعد الدعم الآلي.")
        return
    if len(value) < 20:
        await l_client_send_message(client, uid, "❌ المفتاح يبدو غير صحيح. أرسل Groq API Key مرة أخرى.")
        return
    # احفظ المفتاح أولًا حتى لا تتعطل إعدادات المساعد إذا كان الاستضافة تمنع
    # الاتصال الخارجي أثناء اختبار المفتاح. لا نعرض المفتاح في الرسائل أو السجلات.
    db.set("groq_api_key", value)
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {value}", "Content-Type": "application/json"},
            json={
                "model": "openai/gpt-oss-20b",
                "messages": [{"role": "user", "content": "Reply with OK only."}],
                "temperature": 0.2,
                "max_completion_tokens": 10
            },
            timeout=20
        )
        if resp.status_code == 200:
            await l_client_send_message(client, uid, "✅ تم حفظ المفتاح واختباره بنجاح. مساعد الدعم الآلي يعمل الآن.")
        elif resp.status_code == 401:
            db.set("groq_api_key", "")
            await l_client_send_message(client, uid, "❌ مفتاح Groq غير صالح أو منتهي (401). لم يتم الاحتفاظ به.")
        elif resp.status_code == 403:
            await l_client_send_message(client, uid, "⚠️ تم حفظ المفتاح، لكن Groq رفض الطلب بصلاحية 403. راجع صلاحيات المفتاح.")
        elif resp.status_code == 429:
            await l_client_send_message(client, uid, "⚠️ تم حفظ المفتاح. Groq أرجع 429 بسبب حد الاستخدام؛ جرّب المساعد لاحقًا.")
        else:
            await l_client_send_message(client, uid, f"⚠️ تم حفظ المفتاح، لكن اختبار Groq أعاد الرمز {resp.status_code}. إذا لم يعمل المساعد أرسل لوج الاستضافة.")
    except Exception as e:
        print(f"Groq key validation exception type: {type(e).__name__}")
        await l_client_send_message(client, uid, "⚠️ تم حفظ المفتاح. تعذر اختبار اتصال Groq من الاستضافة الآن. جرّب مساعد الدعم؛ وإذا لم يرد أرسل لوج الاستضافة.")

@client.on(events.CallbackQuery(data=b'ai_support'))
async def ai_support_button(event):
    key = str(db.get("groq_api_key") or "").strip()
    if not key:
        await l_edit(event, tr(event.sender_id,
            "🤖 مساعد الدعم الآلي غير مُفعّل حاليًا. تواصل مع مدير البوت.",
            "🤖 AI Support is currently disabled. Please contact the bot administrator."),
            buttons=[[Button.inline(tr(event.sender_id, "🔙 رجوع", "🔙 Back"), data="back_main")]])
        return
    db.set(f"ai_mode_{event.sender_id}", True)
    await l_edit(event, tr(event.sender_id,
        "🤖 مساعد الدعم الآلي جاهز.\n\nاكتب سؤالك عن البوت أو الشحن أو الدفع أو أي مشكلة داخل خدمات البوت.",
        "🤖 AI Support is ready.\n\nSend your question about the bot, charging, payments, or any issue with the bot's services."),
        buttons=[[Button.inline(tr(event.sender_id, "❌ إغلاق المساعد", "❌ Close AI"), data="ai_close")]])

@client.on(events.CallbackQuery(data=b'ai_close'))
async def ai_close_button(event):
    db.delete(f"ai_mode_{event.sender_id}")
    await l_edit(event, tr(event.sender_id, "تم إغلاق مساعد الدعم الآلي.", "AI Support closed."), buttons=main_menu_buttons(event.sender_id))

def _manual_admin_menu():
    s = get_manual_payment_settings()
    lines = ["💳 **إدارة الدفع اليدوي**", ""]
    rows = []
    for m, c in MANUAL_METHODS.items():
        on = bool(s.get(c["enabled_key"], True))
        has = bool(str(s.get(c["destination_key"], "")).strip())
        state = ("🟢 ظاهر" if on else "🔴 مخفي") + ("" if has else " — ⚠️ مفيش رقم")
        lines.append(f"{c['icon']} {c['name']}: {state}")
        rows.append([Button.inline(accent(f"{c['icon']} {c['name']}"), data=f"manualpay_set:{m}")])
    rows.append([Button.inline(accent("🔙 رجوع"), data="adm:pay")])
    return "\n".join(lines), rows

def _manual_method_panel(method):
    s = get_manual_payment_settings()
    c = MANUAL_METHODS[method]
    on = bool(s.get(c["enabled_key"], True))
    dest = str(s.get(c["destination_key"], "")).strip() or "—"
    text = (f"{c['icon']} **{c['name']}**\n\n"
            f"الرقم: `{dest}`\n"
            f"السعر: {money(s.get(c['rate_key'], 50))} ج = 1$\n"
            f"الحالة: {'🟢 ظاهر للمستخدمين' if on else '🔴 مخفي'}")
    rows = [
        [Button.inline(accent("✏️ تغيير الرقم"), data=f"manualpay_num:{method}"),
         Button.inline(accent("💱 تغيير السعر"), data=f"manualpay_rate:{method}")],
        [Button.inline(accent("🔴 إخفاء الزر" if on else "🟢 إظهار الزر"), data=f"manualpay_tog:{method}")],
        [Button.inline(accent("🔙 رجوع"), data="manual_payment_settings")],
    ]
    return text, rows

@client.on(events.CallbackQuery(data=b'manual_payment_settings'))
async def manual_payment_settings_menu(event):
    if not _is_admin_user(event.sender_id):
        return
    text, rows = _manual_admin_menu()
    await l_edit(event, text, parse_mode='markdown', buttons=rows)

@client.on(events.CallbackQuery(pattern=r"manualpay_set:"))
async def manual_payment_set(event):
    if not _is_admin_user(event.sender_id):
        return
    method = event.data.decode().split(":", 1)[1]
    if method not in MANUAL_METHODS:
        return
    text, rows = _manual_method_panel(method)
    await l_edit(event, text, parse_mode='markdown', buttons=rows)

@client.on(events.CallbackQuery(pattern=r"manualpay_tog:"))
async def manual_payment_toggle(event):
    if not _is_admin_user(event.sender_id):
        return
    method = event.data.decode().split(":", 1)[1]
    if method not in MANUAL_METHODS:
        return
    c = MANUAL_METHODS[method]
    s = get_manual_payment_settings()
    s[c["enabled_key"]] = not bool(s.get(c["enabled_key"], True))
    save_manual_payment_settings(s)
    await event.answer("🟢 تم إظهار الزر" if s[c["enabled_key"]] else "🔴 تم إخفاء الزر")
    text, rows = _manual_method_panel(method)
    await l_edit(event, text, parse_mode='markdown', buttons=rows)

@client.on(events.CallbackQuery(pattern=r"manualpay_num:"))
async def manual_payment_change_number(event):
    if not _is_admin_user(event.sender_id):
        return
    method = event.data.decode().split(":", 1)[1]
    if method not in MANUAL_METHODS:
        return
    c = MANUAL_METHODS[method]
    async with bot.conversation(event.chat_id, timeout=120) as conv:
        await conv.send_message(f"✏️ ابعت {c['destination_label']} الجديد لـ {c['name']}:")
        val = (await conv.get_response()).text.strip()
        if not val:
            await conv.send_message("❌ قيمة فارغة، لم يتم التغيير.")
            return
        s = get_manual_payment_settings()
        s[c["destination_key"]] = val
        save_manual_payment_settings(s)
        text, rows = _manual_method_panel(method)
        await conv.send_message("✅ تم تغيير الرقم\n\n" + text, parse_mode='markdown', buttons=rows)

@client.on(events.CallbackQuery(pattern=r"manualpay_rate:"))
async def manual_payment_change_rate(event):
    if not _is_admin_user(event.sender_id):
        return
    method = event.data.decode().split(":", 1)[1]
    if method not in MANUAL_METHODS:
        return
    c = MANUAL_METHODS[method]
    async with bot.conversation(event.chat_id, timeout=120) as conv:
        await conv.send_message(f"💱 ابعت سعر الصرف لـ {c['name']} (كم جنيه = 1$)، مثال: 50")
        try:
            rate = money_value((await conv.get_response()).text.strip().replace(',', '.'))
            if rate <= 0:
                raise ValueError
        except Exception:
            await conv.send_message("❌ سعر غير صحيح، لم يتم التغيير.")
            return
        s = get_manual_payment_settings()
        s[c["rate_key"]] = rate
        save_manual_payment_settings(s)
        text, rows = _manual_method_panel(method)
        await conv.send_message("✅ تم تغيير السعر\n\n" + text, parse_mode='markdown', buttons=rows)

@client.on(events.CallbackQuery(pattern=r"manualpay_(binance|instapay|vodafone)$"))
async def manual_payment_select(event):
    method = event.data.decode().split("_", 1)[1]
    await _ask_manual_amount(event, method)

@client.on(events.CallbackQuery(data=b'chargecrypto'))
async def handle_charge_crypto(event):
    await _ask_manual_amount(event, "vodafone")

@client.on(events.CallbackQuery(data=b'chargeinstapay'))
async def handle_charge_instapay(event):
    await _ask_manual_amount(event, "instapay")

@client.on(events.CallbackQuery(data=b'chargebinance'))
async def handle_charge_binance(event):
    await _ask_manual_amount(event, "binance")

# ============================================================
#  شحن المحفظة (Binance / InstaPay / Vodafone):
#   المبلغ ← صورة التحويل ← الرقم (رقم العملية في Binance) ← مراجعة 60 ثانية (تعديل/تأكيد)
#   ← لو العضو معملش حاجة الطلب يتبعت للأدمن تلقائي ← الأدمن: موافق / إلغاء / تعديل سعر
# ============================================================
import html as _html

PAY_CONFIRM_SECONDS = 60
PAY_TMP_DIR = "database/tmp_pay"   # اسمه يبدأ بـ tmp فمش بيدخل في النسخ الاحتياطي
_pay_timers = {}

PAY_REF_PROMPT = {
    "binance": "رقم العملية (Order ID / Transaction ID) من تطبيق Binance",
    "instapay": "رقم أو عنوان إنستا باي اللي حوّلت منه",
    "vodafone": "رقم المحفظة اللي حوّلت منها",
}
PAY_REF_LABEL = {
    "binance": "رقم العملية",
    "instapay": "حوّل من",
    "vodafone": "حوّل من",
}
PAY_WARNING = "⚠️ **تحذير:** أي طلب شحن مزيف أو بيانات غير صحيحة تعرّض حسابك للحظر من البوت."

_PAY_EDITABLE = ("awaiting_screenshot", "awaiting_number", "awaiting_screenshot_edit",
                 "awaiting_number_edit", "pending_confirm")


def _pay_get(rid):
    key = f"manual_payment_{rid}"
    try:
        req = db.get(key) if db.exists(key) else None
    except Exception:
        return None
    return req if isinstance(req, dict) else None


def _pay_save(req):
    db.set(f"manual_payment_{req['id']}", req)


def _pay_cancel_timer(rid):
    t = _pay_timers.pop(rid, None)
    if t is not None and t is not asyncio.current_task():
        t.cancel()


def _pay_remove_file(req):
    p = req.get("screenshot_path")
    if p:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        req["screenshot_path"] = None


def _pay_clear_current(uid, rid):
    k = f"manual_current_{uid}"
    try:
        if db.exists(k) and str(db.get(k)) == str(rid):
            db.delete(k)
    except Exception:
        pass


def _pay_ref_prompt(method):
    return PAY_REF_PROMPT.get(method, "الرقم اللي حوّلت منه")


def _pay_confirm_text(req):
    method = req.get("method")
    cfg = MANUAL_METHODS.get(method, {})
    return (
        "📋 **مراجعة طلب الشحن**\n\n"
        f"{cfg.get('icon', '💳')} المنصة: {cfg.get('name', 'دفع يدوي')}\n"
        f"💵 المبلغ: {money(req['amount_usd'])}$ / {money(req['local_amount'])} جنيه\n"
        "🖼 صورة التحويل: ✅ تم الاستلام\n"
        f"🔢 {PAY_REF_LABEL.get(method, 'الرقم')}: `{req.get('sender_ref', '')}`\n\n"
        f"⏳ هيتبعت طلبك للمراجعة تلقائيًا بعد {PAY_CONFIRM_SECONDS} ثانية، "
        "ولو في حاجة غلط عدّلها من الأزرار تحت.\n\n"
        + PAY_WARNING
    )


def _pay_confirm_buttons(rid):
    return [
        [Button.inline(accent("✅ تأكيد وإرسال الآن"), data=f"payconf:{rid}")],
        [Button.inline(accent("🖼 تعديل الصورة"), data=f"payedit_img:{rid}"),
         Button.inline(accent("✏️ تعديل الرقم"), data=f"payedit_num:{rid}")],
        [Button.inline(accent("❌ إلغاء"), data=f"manualpay_cancel:{rid}")],
    ]


def _pay_admin_buttons(rid):
    return [
        [Button.inline(accent("✅ موافق"), data=f"manualpay_accept:{rid}")],
        [Button.inline(accent("❌ إلغاء"), data=f"manualpay_reject:{rid}"),
         Button.inline(accent("💲 تعديل سعر"), data=f"manualpay_editprice:{rid}")],
    ]


def _pay_admin_caption(req):
    info = req.get("admin_info") or {}
    method = req.get("method")
    cfg = MANUAL_METHODS.get(method, {})
    uid = int(req["user_id"])
    name = _html.escape(str(info.get("name") or "بدون اسم"))
    uname = info.get("username")
    esc = _html.escape
    lines = [
        f"⚡ <b>طلب شحن جديد — {esc(cfg.get('name', 'دفع يدوي'))}</b>",
        "",
        "👤 <b>معلومات العضو</b>",
        f"• الاسم: <a href=\"tg://user?id={uid}\">{name}</a>",
        f"• اليوزر: {'@' + esc(str(uname)) if uname else 'بدون يوزر'}",
        f"• الأيدي: <code>{uid}</code>",
        f"• رصيده الحالي: {money(info.get('balance', 0))}$",
        "",
    ]
    if req.get("edited_price"):
        lines.append(f"💵 <b>المبلغ المطلوب (الأصلي)</b>: {money(req.get('original_amount_usd', 0))}$")
        lines.append(f"✏️ <b>المبلغ بعد التعديل</b>: {money(req['amount_usd'])}$ / {money(req['local_amount'])} جنيه")
    else:
        lines.append(f"💵 <b>المبلغ المطلوب</b>: {money(req['amount_usd'])}$ / {money(req['local_amount'])} جنيه")
    lines.append(f"🔢 <b>{esc(PAY_REF_LABEL.get(method, 'الرقم'))}</b>: <code>{esc(str(req.get('sender_ref', '')))}</code>")
    lines.append(f"🧾 رقم الطلب: <code>{esc(str(req['id']))}</code>")
    lines.append("📨 الإرسال: " + ("تلقائي بعد انتهاء المهلة" if req.get("auto_submitted") else "العضو أكّد بنفسه"))
    return "\n".join(lines)


async def _pay_show_confirm(rid):
    """يعرض (أو يعيد عرض) رسالة المراجعة ويشغّل عدّاد الـ 60 ثانية من جديد."""
    req = _pay_get(rid)
    if not req:
        return
    _pay_cancel_timer(rid)
    uid = int(req["user_id"])
    old_id = req.get("confirm_msg_id")
    version = int(req.get("confirm_version", 0)) + 1
    req["status"] = "pending_confirm"
    req["confirm_version"] = version
    req["updated_at"] = time.time()
    _pay_save(req)
    if old_id:
        try:
            await client.delete_messages(uid, int(old_id))
        except Exception:
            pass
    msg = await l_client_send_message(client, uid, _pay_confirm_text(req),
                                      parse_mode='markdown', buttons=_pay_confirm_buttons(rid))
    req = _pay_get(rid) or req
    req["confirm_msg_id"] = int(msg.id)
    _pay_save(req)
    _pay_timers[rid] = asyncio.create_task(_pay_confirm_timer(rid, version))


async def _pay_confirm_timer(rid, version):
    try:
        await asyncio.sleep(PAY_CONFIRM_SECONDS)
    except asyncio.CancelledError:
        return
    req = _pay_get(rid)
    if not req or req.get("status") != "pending_confirm" or req.get("confirm_version") != version:
        return
    await _pay_submit_to_admin(rid, auto=True)


async def _pay_submit_to_admin(rid, auto=False):
    """يبعت الطلب للأدمن (صورة + معلومات العضو + المبلغ + 3 أزرار) ويقفله فورًا عشان ميتبعتش مرتين."""
    req = _pay_get(rid)
    if not req or req.get("status") != "pending_confirm":
        return False
    req["status"] = "under_review"
    req["auto_submitted"] = bool(auto)
    req["updated_at"] = time.time()
    _pay_save(req)
    _pay_cancel_timer(rid)
    uid = int(req["user_id"])

    name, username = "", ""
    try:
        ent = await client.get_entity(uid)
        name = " ".join(x for x in [getattr(ent, "first_name", None), getattr(ent, "last_name", None)] if x)
        username = getattr(ent, "username", None) or ""
    except Exception:
        pass
    try:
        balance = float((db.get(f"user_{uid}") or {}).get("coins", 0) or 0) if db.exists(f"user_{uid}") else 0.0
    except Exception:
        balance = 0.0
    req["admin_info"] = {"name": name, "username": username, "balance": balance}
    caption = _pay_admin_caption(req)
    buttons = _pay_admin_buttons(rid)
    path = req.get("screenshot_path")
    try:
        if path and os.path.exists(path):
            await client.send_file(int(admin), path, caption=caption, buttons=buttons, parse_mode="html")
        else:
            await client.send_message(int(admin), caption + "\n\n⚠️ الصورة غير متاحة (اطلبها من العضو).",
                                      buttons=buttons, parse_mode="html")
    except Exception as ex:
        print(f"Manual payment admin send error [{rid}]: {ex}")
        req["status"] = "pending_confirm"
        req["submit_failures"] = int(req.get("submit_failures", 0)) + 1
        _pay_save(req)
        if req["submit_failures"] < 3:      # نعيد المحاولة بعد مهلة جديدة
            _pay_timers[rid] = asyncio.create_task(_pay_confirm_timer(rid, req.get("confirm_version")))
        return False

    _pay_remove_file(req)
    _pay_save(req)
    final = ("⏳ جاري مراجعة العملية .. يستغرق الأمر من 5 دقائق إلى 12 ساعة\n\n" + PAY_WARNING)
    try:
        await client.edit_message(uid, int(req["confirm_msg_id"]), _localize_text(uid, final))
    except Exception:
        try:
            await l_client_send_message(client, uid, final)
        except Exception:
            pass
    return True


@client.on(events.CallbackQuery(pattern=r"manualpay_submit:"))
async def manual_payment_submit(event):
    request_id = event.data.decode().split(":", 1)[1]
    req = _pay_get(request_id)
    if not req:
        await event.answer("❌ الطلب غير موجود أو انتهت صلاحيته.", alert=True)
        return
    if int(req.get("user_id", 0)) != int(event.sender_id):
        return
    if req.get("status") != "awaiting_payment":
        await event.answer("⚠️ الطلب ليس في حالة انتظار صورة التحويل.", alert=True)
        return
    # احفظ الحالة قبل إرسال الرسالة حتى لا تضيع العملية لو حصل تأخير في تيليجرام.
    req["status"] = "awaiting_screenshot"
    req["updated_at"] = time.time()
    _pay_save(req)
    db.set(f"manual_current_{event.sender_id}", request_id)
    try:
        await l_respond(event, "📷 **الخطوة 1/2:** أرسل الآن **صورة التحويل فقط** هنا.")
    except Exception as e:
        print(f"Manual payment prompt error [{request_id}]: {e}")


@client.on(events.CallbackQuery(pattern=r"manualpay_cancel:"))
async def manual_payment_cancel(event):
    request_id = event.data.decode().split(":", 1)[1]
    req = _pay_get(request_id)
    if req:
        if int(req.get("user_id", 0)) != int(event.sender_id):
            return
        if req.get("status") in ("under_review", "approved", "rejected"):
            await event.answer("⚠️ الطلب اتبعت للمراجعة بالفعل ومينفعش يتلغى.", alert=True)
            return
        _pay_cancel_timer(request_id)
        _pay_remove_file(req)
        req["status"] = "cancelled"
        _pay_save(req)
        _pay_clear_current(event.sender_id, request_id)
    await l_edit(event, "❌ تم إلغاء طلب الشحن.")


@client.on(events.CallbackQuery(pattern=r"payconf:"))
async def manual_payment_confirm_now(event):
    rid = event.data.decode().split(":", 1)[1]
    req = _pay_get(rid)
    if not req or int(req.get("user_id", 0)) != int(event.sender_id):
        return
    if req.get("status") != "pending_confirm":
        await event.answer("⚠️ الطلب اتبعت بالفعل أو اتغير.", alert=True)
        return
    ok = await _pay_submit_to_admin(rid, auto=False)
    if ok:
        await event.answer("✅ تم إرسال طلبك للمراجعة")
    else:
        await event.answer("❌ تعذر الإرسال، هنحاول تاني تلقائي.", alert=True)


@client.on(events.CallbackQuery(pattern=r"payedit_img:"))
async def manual_payment_edit_image(event):
    rid = event.data.decode().split(":", 1)[1]
    req = _pay_get(rid)
    if not req or int(req.get("user_id", 0)) != int(event.sender_id):
        return
    if req.get("status") != "pending_confirm":
        await event.answer("⚠️ الطلب اتبعت بالفعل أو اتغير.", alert=True)
        return
    _pay_cancel_timer(rid)
    req["status"] = "awaiting_screenshot_edit"
    _pay_save(req)
    db.set(f"manual_current_{event.sender_id}", rid)
    await l_edit(event, "🖼 أرسل الآن **صورة التحويل الجديدة** هنا.",
                 buttons=[[Button.inline(accent("❌ إلغاء الطلب"), data=f"manualpay_cancel:{rid}")]])


@client.on(events.CallbackQuery(pattern=r"payedit_num:"))
async def manual_payment_edit_number(event):
    rid = event.data.decode().split(":", 1)[1]
    req = _pay_get(rid)
    if not req or int(req.get("user_id", 0)) != int(event.sender_id):
        return
    if req.get("status") != "pending_confirm":
        await event.answer("⚠️ الطلب اتبعت بالفعل أو اتغير.", alert=True)
        return
    _pay_cancel_timer(rid)
    req["status"] = "awaiting_number_edit"
    _pay_save(req)
    db.set(f"manual_current_{event.sender_id}", rid)
    await l_edit(event, f"✏️ أرسل الآن **{_pay_ref_prompt(req.get('method'))}** الصحيح.",
                 buttons=[[Button.inline(accent("❌ إلغاء الطلب"), data=f"manualpay_cancel:{rid}")]])


def _pay_is_image(e):
    try:
        if e.photo:
            return True
        d = e.document
        return bool(d and (getattr(d, "mime_type", "") or "").startswith("image/"))
    except Exception:
        return False


@client.on(events.NewMessage(func=lambda e: e.is_private and _pay_is_image(e)))
async def manual_payment_screenshot(event):
    user_id = int(event.sender_id)
    current_key = f"manual_current_{user_id}"
    request_id = db.get(current_key) if db.exists(current_key) else None
    if not request_id:
        return
    request_id = str(request_id)
    req = _pay_get(request_id)
    if (not req or int(req.get("user_id", 0)) != user_id
            or req.get("status") not in ("awaiting_screenshot", "awaiting_screenshot_edit")):
        return
    editing = req["status"] == "awaiting_screenshot_edit"

    os.makedirs(PAY_TMP_DIR, exist_ok=True)
    temp_path = os.path.join(PAY_TMP_DIR, f"{request_id}_{event.message.id}.jpg")
    try:
        downloaded = await event.download_media(file=temp_path)
        if not downloaded:
            raise RuntimeError("تعذر تنزيل صورة التحويل")
    except Exception as e:
        print(f"Manual payment photo download error [{request_id}]: {e}")
        try:
            await l_respond(event, "❌ تعذر استلام الصورة. أرسلها مرة أخرى.")
        except Exception:
            pass
        return

    req = _pay_get(request_id)      # ممكن تتغير الحالة أثناء التنزيل
    if not req or req.get("status") not in ("awaiting_screenshot", "awaiting_screenshot_edit"):
        try:
            os.remove(downloaded)
        except Exception:
            pass
        return
    old = req.get("screenshot_path")
    if old and old != downloaded:
        _pay_remove_file(req)
    req["screenshot_path"] = downloaded
    req["screenshot_message_id"] = int(event.message.id)
    req["updated_at"] = time.time()
    if editing:
        _pay_save(req)
        await _pay_show_confirm(request_id)
        return
    req["status"] = "awaiting_number"
    _pay_save(req)
    try:
        await l_respond(event, f"✅ تم استلام الصورة.\n\n🔢 **الخطوة 2/2:** أرسل الآن **{_pay_ref_prompt(req.get('method'))}**.")
    except Exception as e:
        print(f"Manual payment number prompt error [{request_id}]: {e}")


@client.on(events.NewMessage(func=lambda e: e.is_private and bool(e.raw_text) and not e.raw_text.startswith('/')))
async def manual_payment_number_receiver(event):
    user_id = int(event.sender_id)
    current_key = f"manual_current_{user_id}"
    if not db.exists(current_key):
        return
    request_id = str(db.get(current_key))
    req = _pay_get(request_id)
    if (not req or int(req.get("user_id", 0)) != user_id
            or req.get("status") not in ("awaiting_number", "awaiting_number_edit")):
        return
    value = event.raw_text.strip().replace("`", "'")
    if len(value) < 3 or len(value) > 64 or "\n" in value:
        try:
            await l_respond(event, f"❌ القيمة غير صحيحة. أرسل **{_pay_ref_prompt(req.get('method'))}** مرة أخرى.")
        except Exception:
            pass
        return
    req["sender_ref"] = value
    req["updated_at"] = time.time()
    _pay_save(req)
    await _pay_show_confirm(request_id)


async def _pay_finish_admin_message(event, req, status_line):
    """يحدّث رسالة الأدمن (الكابشن) بنتيجة القرار ويشيل الأزرار."""
    try:
        await client.edit_message(event.chat_id, event.message_id,
                                  _pay_admin_caption(req) + "\n\n" + status_line, parse_mode="html")
    except Exception as ex:
        print("admin message edit error:", ex)
        try:
            await event.answer(_html.unescape(status_line.replace("<b>", "").replace("</b>", "")), alert=True)
        except Exception:
            pass


@client.on(events.CallbackQuery(pattern=r"manualpay_accept:"))
async def manual_payment_accept(event):
    if event.sender_id != admin:
        return
    request_id = event.data.decode().split(":", 1)[1]
    req = _pay_get(request_id)
    if not req:
        await event.answer("الطلب غير موجود.", alert=True); return
    if req.get("status") != "under_review":
        await event.answer("⚠️ تم التعامل مع الطلب مسبقًا.", alert=True); return
    uid = int(req["user_id"])
    amount = money_value(req["amount_usd"])
    user_key = f"user_{uid}"
    user = db.get(user_key) if db.exists(user_key) else {"id": uid, "coins": 0.0, "joined_at": time.time()}
    user["coins"] = money_value(user.get("coins", 0) + amount)
    db.set(user_key, user)
    req["status"] = "approved"
    req["approved_at"] = time.time()
    _pay_save(req)
    _pay_clear_current(uid, request_id)
    await _pay_finish_admin_message(event, req, "✅ <b>تمت الموافقة وإضافة الرصيد</b>")
    note = ""
    if req.get("edited_price"):
        note = (f"\nℹ️ تم تعديل المبلغ من الإدارة: {display_money(req.get('original_amount_usd', 0), uid)}"
                f" ← {display_money(amount, uid)}")
    await l_client_send_message(client, uid,
        f"✅ تم قبول شحنك عبر {MANUAL_METHODS[req['method']]['name']}.\n\n"
        f"💵 المضاف: {display_money(amount, uid)}\n"
        f"💰 رصيدك الحالي: {display_money(user['coins'], uid)}" + note
    )


@client.on(events.CallbackQuery(pattern=r"manualpay_reject:"))
async def manual_payment_reject(event):
    if event.sender_id != admin:
        return
    request_id = event.data.decode().split(":", 1)[1]
    req = _pay_get(request_id)
    if not req:
        await event.answer("الطلب غير موجود.", alert=True); return
    if req.get("status") != "under_review":
        await event.answer("⚠️ تم التعامل مع الطلب مسبقًا.", alert=True); return
    req["status"] = "rejected"
    _pay_save(req)
    _pay_clear_current(req["user_id"], request_id)
    await _pay_finish_admin_message(event, req, "❌ <b>تم إلغاء الطلب</b>")
    await l_client_send_message(client, int(req["user_id"]),
        f"❌ تم إلغاء طلب الشحن عبر {MANUAL_METHODS[req['method']]['name']}.\n"
        "إذا كنت متأكدًا من التحويل، تواصل مع الدعم وأرسل التفاصيل المطلوبة."
    )


@client.on(events.CallbackQuery(pattern=r"manualpay_editprice:"))
async def manual_payment_edit_price(event):
    if event.sender_id != admin:
        return
    request_id = event.data.decode().split(":", 1)[1]
    req = _pay_get(request_id)
    if not req:
        await event.answer("الطلب غير موجود.", alert=True); return
    if req.get("status") != "under_review":
        await event.answer("⚠️ تم التعامل مع الطلب مسبقًا.", alert=True); return
    await event.answer()
    async with bot.conversation(event.chat_id, timeout=120) as conv:
        await conv.send_message(
            f"💵 المبلغ الحالي: {money(req['amount_usd'])}$\n"
            f"أرسل المبلغ الجديد بالدولار (من {money(MIN_AMOUNT)} إلى {money(MAX_AMOUNT)}):")
        try:
            resp = await conv.get_response()
        except asyncio.TimeoutError:
            return
        try:
            new_amount = money_value(resp.text.strip().replace(",", "."))
            if new_amount < MIN_AMOUNT or new_amount > MAX_AMOUNT:
                raise ValueError
        except Exception:
            await conv.send_message("❌ مبلغ غير صحيح، لم يتم التعديل.")
            return
        req = _pay_get(request_id)
        if not req or req.get("status") != "under_review":
            await conv.send_message("⚠️ الطلب اتعامل معاه بالفعل.")
            return
        req.setdefault("original_amount_usd", req["amount_usd"])
        req["amount_usd"] = new_amount
        req["local_amount"] = new_amount * float(req.get("rate", 1) or 1)
        req["edited_price"] = True
        _pay_save(req)
        try:
            await client.edit_message(event.chat_id, event.message_id, _pay_admin_caption(req),
                                      parse_mode="html", buttons=_pay_admin_buttons(request_id))
        except Exception as ex:
            print("admin caption edit error:", ex)
        await conv.send_message(f"✅ تم تعديل المبلغ إلى {money(new_amount)}$. اضغط «موافق» عشان يتضاف للعضو.")


async def _pay_recover_pending():
    """لو البوت اتقفل أثناء مهلة الـ 60 ثانية، نبعت الطلبات المعلقة للأدمن عند التشغيل."""
    await asyncio.sleep(8)
    try:
        for k in db.keys('manual_payment_%'):
            k = k[0] if isinstance(k, (tuple, list)) else k
            req = db.get(k) if db.exists(k) else None
            if isinstance(req, dict) and req.get("status") == "pending_confirm" and req.get("id"):
                await _pay_submit_to_admin(str(req["id"]), auto=True)
    except Exception as ex:
        print("pay recover error:", ex)

@client.on(events.CallbackQuery(data="start_broadcast"))
async def ask_for_message(event):
    if event.sender_id == admin:
        async with client.conversation(admin) as conv:
            await conv.send_message("📃- أرسل الرسالة التي تريد اذاعتها لجميع المستخدمين")
            response = await conv.get_response()
            message = response.text

            db.set(f"pending_broadcast_{admin}", {"message": message, "created": time.time()})
            await conv.send_message(
                f"⚠️ | هل تريد اذاعة هذه الرسالة لجميع المستخدمين؟\n\n"
                f"**الرسالة:**\n{message}",
                buttons=[
                    [Button.inline(accent("✅ نعم، اذاعة"), data="confirms_broadcast")],
                    [Button.inline(accent("❌ إلغاء"), data="cancel_broadcast")]
                ]
            )

from telethon import events


@client.on(events.CallbackQuery(data=b'coupon_admin'))
async def coupon_admin(event):
    if event.sender_id != admin:
        return
    coupons = db.get("recharge_coupons") if db.exists("recharge_coupons") else {}
    active = sum(1 for v in coupons.values() if int(v.get("remaining", 0)) > 0)
    await l_edit(event, 
        f"🎟 **إدارة قسائم الشحن**\n\n"
        f"📦 القسائم الموجودة: {len(coupons)}\n"
        f"🟢 القسائم المتاحة: {active}",
        buttons=[
            [Button.inline(accent("➕ إنشاء قسيمة شحن"), data="coupon_create")],
            [Button.inline(accent("📋 القسائم"), data="coupon_list")],
            [Button.inline(accent("🔙 رجوع"), data="adm:free")]
        ]
    )

@client.on(events.CallbackQuery(data=b'coupon_create'))
async def coupon_create(event):
    if event.sender_id != admin:
        return
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("💵 أرسل قيمة القسيمة بالدولار، مثال: 1.50")
        amount_msg = await x.get_response()
        try:
            amount = float(amount_msg.text.strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            await x.send_message("❌ المبلغ غير صحيح.")
            return
        await x.send_message("👥 أرسل عدد مرات الاستخدام المسموح بها للقسيمة، مثال: 5")
        uses_msg = await x.get_response()
        try:
            uses = int(uses_msg.text.strip())
            if uses <= 0:
                raise ValueError
        except ValueError:
            await x.send_message("❌ عدد الاستخدامات غير صحيح.")
            return

        coupons = db.get("recharge_coupons") if db.exists("recharge_coupons") else {}
        for _ in range(20):
            code = "CHARGE-" + secrets.token_hex(5).upper()
            if code not in coupons:
                break
        coupons[code] = {"amount": round(amount, 8), "remaining": uses, "max_uses": uses, "used_by": []}
        db.set("recharge_coupons", coupons)
        await x.send_message(
            f"✅ **تم إنشاء قسيمة الشحن**\n\n"
            f"🎟 الكود: `{code}`\n"
            f"💵 القيمة: {display_money(amount, event.sender_id)}\n"
            f"👥 عدد الاستخدامات: {uses}\n\n"
            f"يمكن للمستخدمين استبدال الكود من زر «🎟 استبدال قسيمة شحن»."
        )

@client.on(events.CallbackQuery(data=b'coupon_list'))
async def coupon_list(event):
    if event.sender_id != admin:
        return
    coupons = db.get("recharge_coupons") if db.exists("recharge_coupons") else {}
    if not coupons:
        text = "🎟 لا توجد قسائم شحن حاليًا."
    else:
        lines = ["🎟 **قسائم الشحن**", ""]
        for code, info in list(coupons.items())[-30:]:
            lines.append(f"`{code}` — {display_money(info.get('amount', 0), event.sender_id)} — متبقي: {int(info.get('remaining', 0))}/{int(info.get('max_uses', info.get('remaining', 0)))}")
        text = "\n".join(lines)
    await l_edit(event, text, buttons=[[Button.inline(accent("🔙 رجوع"), data="coupon_admin")]])

@client.on(events.CallbackQuery(data=b'redeem_coupon'))
async def redeem_coupon(event):
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("🎟 أرسل كود قسيمة الشحن:")
        msg = await x.get_response()
        code = msg.text.strip().upper()
        coupons = db.get("recharge_coupons") if db.exists("recharge_coupons") else {}
        info = coupons.get(code)
        if not info:
            await x.send_message("❌ القسيمة غير موجودة أو الكود غير صحيح.")
            return
        if int(info.get("remaining", 0)) <= 0:
            await x.send_message("❌ هذه القسيمة استُنفدت بالكامل.")
            return
        used_by = list(info.get("used_by", []))
        if event.sender_id in used_by:
            await x.send_message("❌ لا يمكنك استخدام نفس القسيمة أكثر من مرة.")
            return
        user_key = f"user_{event.sender_id}"
        user = db.get(user_key) if db.exists(user_key) else {"id": event.sender_id, "coins": 0.0, "joined_at": time.time()}
        amount = money_value(info.get("amount", 0))
        user["coins"] = money_value(user.get("coins", 0) + amount)
        info["remaining"] = int(info.get("remaining", 0)) - 1
        used_by.append(event.sender_id)
        info["used_by"] = used_by
        coupons[code] = info
        db.set(user_key, user)
        db.set("recharge_coupons", coupons)
        await x.send_message(
            f"✅ **تم استبدال قسيمة الشحن بنجاح**\n\n"
            f"🎟 الكود: `{code}`\n"
            f"💵 تمت إضافة: {display_usd_price(amount, event.sender_id)}\n"
            f"💰 **الرصيد الأساسي الآن: {display_usd_price(user['coins'], event.sender_id)}**\n\n"
            f"📌 رصيد القسيمة يُضاف إلى الرصيد الأساسي وليس رصيد الإحالات."
        )


# =========================
# استبدال رصيد الإحالات - أقسام وجوائز قابلة للإدارة
# =========================
@client.on(events.CallbackQuery(data=b'ref_rewards'))
async def ref_rewards(event):
    user_id = event.sender_id
    user = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {"referral_balance": 0}
    balance = float(user.get("referral_balance", 0) or 0)
    sections = db.get("referral_sections") if db.exists("referral_sections") else []
    buttons = []
    for idx, sec in enumerate(sections):
        buttons.append([Button.inline(str(sec.get("name", "قسم")), data=f"ref_sec:{idx}")])
    buttons.append([Button.inline(accent("🔙 رجوع"), data="main")])
    await l_edit(event, 
        f"🎁 **استبدال رصيد الإحالات**\n\n💰 رصيد الإحالات: {display_money(balance, user_id)}\n\nاختر القسم الذي تريد الاستبدال منه:",
        buttons=buttons
    )

@client.on(events.CallbackQuery(pattern=r"ref_sec:(\d+)"))
async def ref_section(event):
    idx = int(event.pattern_match.group(1))
    sections = db.get("referral_sections") if db.exists("referral_sections") else []
    if idx >= len(sections):
        await event.answer("القسم غير موجود.", alert=True); return
    sec = sections[idx]
    items = sec.get("items", [])
    buttons = []
    for j, item in enumerate(items):
        buttons.append([Button.inline(accent(f"{item.get('name','جائزة')} — {display_money(item.get('price',0), event.sender_id)}"), data=f"ref_buy:{idx}:{j}")])
    buttons.append([Button.inline(accent("🔙 رجوع"), data="ref_rewards")])
    await l_edit(event, f"🎁 **{sec.get('name','قسم')}**\n\nاختر الجائزة التي تريد استبدالها:", buttons=buttons)

@client.on(events.CallbackQuery(pattern=r"ref_buy:(\d+):(\d+)"))
async def ref_buy(event):
    sid, iid = map(int, event.pattern_match.groups())
    sections = db.get("referral_sections") if db.exists("referral_sections") else []
    if sid >= len(sections) or iid >= len(sections[sid].get("items", [])):
        await event.answer("الجائزة غير موجودة.", alert=True); return
    item = sections[sid]["items"][iid]
    user_key = f"user_{event.sender_id}"
    user = db.get(user_key) if db.exists(user_key) else {"id": event.sender_id, "coins": 0, "referral_balance": 0, "joined_at": time.time()}
    balance = float(user.get("referral_balance", 0) or 0)
    price = float(item.get("price", 0) or 0)
    if balance < price:
        await event.answer("❌ رصيد الإحالات غير كافٍ.", alert=True); return
    user["referral_balance"] = round(balance - price, 8)
    db.set(user_key, user)
    await l_edit(event, 
        f"✅ **تم استبدال الجائزة بنجاح**\n\n🎁 الجائزة: {item.get('name','جائزة')}\n💵 التكلفة: {money(price)}$\n🎁 رصيد الإحالات المتبقي: {money(user['referral_balance'])}$\n\n📩 تواصل مع المطور لاستلام جائزتك: @J_D_D_M",
        buttons=[[Button.inline(accent("🔙 رجوع"), data="ref_rewards")]]
    )

@client.on(events.CallbackQuery(data=b'ref_admin'))
async def ref_admin(event):
    if event.sender_id != admin:
        return
    sections = db.get("referral_sections") if db.exists("referral_sections") else []
    buttons = [[Button.inline(accent(f"⚙️ {s.get('name','قسم')} ({len(s.get('items',[]))})"), data=f"ref_adsec:{i}")] for i,s in enumerate(sections)]
    buttons += [[Button.inline(accent("➕ إضافة قسم"), data="ref_addsec")], [Button.inline(accent("🗑 حذف قسم"), data="ref_delsec")], [Button.inline(accent("🔙 رجوع"), data="adm:free")]]
    await l_edit(event, "🎁 **إدارة أقسام استبدال رصيد الإحالات**\n\nأضف أي قسم بالاسم الذي تريده، ويمكنك إضافة جوائز وأسعار داخله.", buttons=buttons)

@client.on(events.CallbackQuery(data=b'ref_addsec'))
async def ref_addsec(event):
    if event.sender_id != admin: return
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("✏️ أرسل اسم القسم الذي تريد إضافته، مثال: تبديل الرصيد بالنجوم")
        name = (await x.get_response()).text.strip()
        if not name: await x.send_message("❌ اسم القسم فارغ."); return
        sections = db.get("referral_sections") if db.exists("referral_sections") else []
        sections.append({"name": name, "items": []})
        db.set("referral_sections", sections)
        await x.send_message(f"✅ تم إضافة القسم: {name}")

@client.on(events.CallbackQuery(data=b'ref_delsec'))
async def ref_delsec(event):
    if event.sender_id != admin: return
    sections = db.get("referral_sections") if db.exists("referral_sections") else []
    if not sections:
        await event.answer("لا توجد أقسام.", alert=True); return
    buttons=[[Button.inline(s.get('name','قسم'), data=f"ref_delone:{i}")] for i,s in enumerate(sections)]
    buttons.append([Button.inline(accent("🔙 رجوع"), data="ref_admin")])
    await l_edit(event, "🗑 اختر القسم الذي تريد حذفه:", buttons=buttons)

@client.on(events.CallbackQuery(pattern=r"ref_delone:(\d+)"))
async def ref_delone(event):
    if event.sender_id != admin: return
    idx=int(event.pattern_match.group(1)); sections=db.get("referral_sections") if db.exists("referral_sections") else []
    if idx < len(sections):
        name=sections[idx].get("name","قسم"); sections.pop(idx); db.set("referral_sections", sections); await event.answer(f"تم حذف {name}")
    await ref_admin(event)

@client.on(events.CallbackQuery(pattern=r"ref_adsec:(\d+)"))
async def ref_adsec(event):
    if event.sender_id != admin: return
    idx=int(event.pattern_match.group(1)); sections=db.get("referral_sections") if db.exists("referral_sections") else []
    if idx>=len(sections): return
    s=sections[idx]; buttons=[[Button.inline(accent(f"🎁 {it.get('name','جائزة')} — {money(it.get('price',0))}$"), data=f"ref_delitem:{idx}:{j}")] for j,it in enumerate(s.get('items',[]))]
    buttons += [[Button.inline(accent("➕ إضافة جائزة"), data=f"ref_additem:{idx}")],[Button.inline(accent("🔙 رجوع"), data="ref_admin")]]
    await l_edit(event, f"⚙️ **{s.get('name','قسم')}**\n\nاضغط على جائزة لحذفها، أو أضف جائزة جديدة.", buttons=buttons)

@client.on(events.CallbackQuery(pattern=r"ref_additem:(\d+)"))
async def ref_additem(event):
    if event.sender_id != admin: return
    idx=int(event.pattern_match.group(1))
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("🎁 أرسل اسم الجائزة:")
        name=(await x.get_response()).text.strip()
        await x.send_message("💵 أرسل سعر الجائزة من رصيد الإحالات بالدولار، مثال: 1.50")
        try: price=float((await x.get_response()).text.strip()); assert price>0
        except: await x.send_message("❌ السعر غير صحيح."); return
        sections=db.get("referral_sections") if db.exists("referral_sections") else []
        if idx>=len(sections): await x.send_message("❌ القسم غير موجود."); return
        sections[idx].setdefault("items",[]).append({"name":name,"price":round(price,8)})
        db.set("referral_sections", sections)
        await x.send_message(f"✅ تمت إضافة {name} بسعر {money(price)}$")

@client.on(events.CallbackQuery(pattern=r"ref_delitem:(\d+):(\d+)"))
async def ref_delitem(event):
    if event.sender_id != admin: return
    sid,iid=map(int,event.pattern_match.groups()); sections=db.get("referral_sections") if db.exists("referral_sections") else []
    if sid<len(sections) and iid<len(sections[sid].get("items",[])):
        sections[sid]["items"].pop(iid); db.set("referral_sections",sections)
    await ref_adsec(event)

@client.on(events.CallbackQuery(pattern="amubnf"))
async def amubnf(event):
    user_id = event.sender_id
    if not _is_admin_user(user_id):
        await event.answer("❌ للأدمن فقط.", alert=True)
        return
    current = get_referral_reward()
    async with bot.conversation(event.chat_id) as x:
        await x.send_message(
            f"💎 سعر الإحالة الحالي: {money(current)}$\n\n"
            "أرسل المبلغ الجديد الذي يحصل عليه صاحب الدعوة بعد أول عملية شراء لصديقه.\n"
            "مثال: 0.05 أو 0.5\n"
            "لإلغاء العملية اكتب: إلغاء"
        )
        resp = await x.get_response()
        text = (getattr(resp, "text", "") or "").strip().replace(",", ".")
        if text in ("الغاء", "إلغاء", "cancel", "Cancel"):
            await x.send_message("❌ تم إلغاء العملية.")
            return
        try:
            new_amount = float(text)
            if not (0.0001 <= new_amount <= MAX_AMOUNT):
                raise ValueError
        except Exception:
            await x.send_message(f"❌ المبلغ غير صحيح. لازم يكون بين 0.0001 و {MAX_AMOUNT}.")
            return
        db.set("referral_reward_amount", round(new_amount, 8))
        await x.send_message(
            f"✅ تم تحديث سعر الإحالة إلى: {money(new_amount)}$\n\n"
            "📌 المكافأة بتتضاف لصاحب الدعوة مرة واحدة، وأول ما الشخص اللي دعاه يشتري رقم من البوت.",
            buttons=[[Button.inline(accent("🔙 رجوع"), data="adm:free")]]
        )

@bot.on(events.CallbackQuery(pattern=b'withdraw'))
async def withdraw_start(event):
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    buttons = [
        [Button.inline(accent("USDT"), data="network_USDT")],
        [Button.inline(accent("TRX"), data="network_TRX")]
    ]
    await l_edit(event, "**اختر العملة المراد السحب بها**", buttons=buttons)

#غير الحقوق واثبت انك فاشل اذا تريد تنقل اذكر اسمي او اسم قناتي #

#====================#
#CH : @J_D_D_M 
#DEV : @J_D_D_M
#====================#

@bot.on(events.CallbackQuery(pattern=b'network_'))
async def choose_network(event):
    data = event.data.decode('utf-8')
    network_type = data.split('_')[1] 
    user_id = event.chat_id

    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    user_data = db.get(f"user_{user_id}")
    if user_data["coins"] < 5:
        await event.answer(" - الحد الادني للسحب 5$.")
        return

    async with bot.conversation(user_id) as conv:
        await conv.send_message(f"- أرسل عنوان  {network_type} الخاص بك بينانس")
        address = await conv.get_response()

        if network_type == "USDT":
            buttons = [
                [Button.inline(accent("USDT-TRC20"), data="network_USDT-TRC20")],
                [Button.inline(accent("USDT-ERC20"), data="network_USDT-ERC20")]
            ]
            await conv.send_message("**اختر نوع شبكة USDT**", buttons=buttons)
            network_event = await conv.wait_event(events.CallbackQuery)
            network = network_event.data.decode('utf-8').split('_')[1]
        elif network_type == "TRX":
            buttons = [
                [Button.inline(accent("TRC20"), data="network_TRC20")],
                [Button.inline(accent("BEP20"), data="network_BEP20")]
            ]
            await conv.send_message("**اختر نوع شبكة TRX**", buttons=buttons)
            network_event = await conv.wait_event(events.CallbackQuery)
            network = network_event.data.decode('utf-8').split('_')[1]
        else:
            network = network_type 

        await conv.send_message("- ارسل المبلغ الذي تريد سحبه")
        amount_info = await conv.get_response()

        try:
            amount = float(amount_info.text)
            if amount > user_data["coins"]:
                await conv.send_message("- لا يمكنك سحب مبلغ أكبر من رصيدك.")
                return

            user_data["coins"] -= amount
            db.set(f"user_{user_id}", user_data)

            withdraw_message = await l_bot_send_message(bot, 
                admin, 
                f"• طلب سحب جديد من البوت\n\n"
                f"- ايديه : {user_id}\n"
                f"- يوزره : @{event.sender.username}\n\n"
                f"- الشبكة : {network}\n"
                f"- عنوان المحفظه : {address.text}\n\n"
                f"- المبلغ : {display_money(amount, user_id)}",
                buttons=[
                    [Button.inline(accent("تأكيد السحب"), data=f"confirm_withdraw_{user_id}_{amount}")]
                ]
            )

            await conv.send_message(f"- جاري معالجة طلب السحب الخاص بك بمبلغ {display_money(amount, user_id)}\n\n- يستغرق الامر من 6 دقائق الى 8 ساعات")
        except ValueError:
            await conv.send_message("- المبلغ يكون ارقام فقط")


@bot.on(events.CallbackQuery(pattern=b'confirm_withdraw_'))
async def confirm_withdraw(event):
    data = event.data.decode('utf-8')
    parts = data.split('_')
    user_id = int(parts[2]) 
    amount = float(parts[3]) 

    await l_bot_send_message(bot, user_id, f"- تم تأكيد طلب السحب الخاص بك ✅\n\n- المبلغ : {display_money(amount, user_id)} \n\n- تحقق من المبلغ في محفظتك")

    await l_edit(event, "تم تأكيد الدفع واشعار المستخدم")

@bot.on(events.CallbackQuery(pattern=b'cancel'))
async def cancel_button(event):
    user_id = event.sender_id  
    coins = get_user_record(user_id)["coins"]
    buttons = [
        [
            Button.inline(accent("📲| حسابات تليجرام جاهزه"), data="buy"),
        ],
        [
            Button.inline(accent("💾| شراء سيشن"), data="bsbuy"),
        ],
        [
        ],
        [
            Button.url(accent("فريق الدعم"), url="t.me/J_D_D_M"),
            Button.inline(accent("الوكلاء"), data="wkala"),
        ],
        [
            Button.inline(accent("شحن رصيد"), data="shahn"),
        ],
        [
            Button.url(accent("الشراء الناجح"), url="t.me/taf3ilatbot"),
            Button.inline(accent("الاعدادات"), data="SUPEGY"),
        ],
        [
            Button.inline(accent("الاحاله الخاص بك"), data="referral"),
        ]
    ]
    await l_edit(event, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
    return
        
async def render_numbers_panel(event, header=""):
    text = '** - مرحبًا يا عزيزي في قسم الأرقام ** \n- يمكنك التحكم في أرقامك بسهولة. \n - يتم تحديث الأرقام تلقائيًا'
    text += f"\n\n🗂 القسم الحالي : **{get_category_name(get_admin_cat(event.sender_id))}**\n(اضافة/حذف الدول والأرقام والسيشنات بتتطبق على القسم ده)"
    if header:
        text = header + "\n\n" + text
    await l_edit(event, text,
                    buttons=[
                        [
                            Button.inline(accent("- عدد ارقام البوت "), data="all_of_number")
                        ],
                        [
                            Button.inline(accent("- اضافة دولة "), data="add_country"),
                            Button.inline(accent("- حذف دولة"), data="del_country")
                        ],
                        [
                            Button.inline(accent("- اضافة رقم"), data="add"),
                            Button.inline(accent("- حذف رقم"), data="delaccount")
                        ],
                        [
                            Button.inline(accent("📥 رفع سيشنات (دفعة واحدة)"), data="bulk_sessions")
                        ],
                        [
                            Button.inline(accent("🔀 تغيير القسم"), data="ca:pick:num")
                        ],
                        [
                            Button.inline(accent(" رجوع ⬅️ "), data="adm:num")
                        ]
                    ])

@bot.on(events.CallbackQuery(pattern=b'ajxjao'))
async def numgpv_button(event):
    await event.answer(' - مرحبا بك في قسم التحكم في الحسابات ☎️ .')
    await show_admin_category_pick(event, "num")

# ============================================================
#  جدول أكواد الدول (للتعرّف على دولة الرقم لو الدولة لسه متضافتش)
# ============================================================
CALLING_CODES = {
    "1": "🇺🇸 أمريكا/كندا", "7": "🇷🇺 روسيا/كازاخستان", "20": "🇪🇬 مصر", "27": "🇿🇦 جنوب أفريقيا",
    "30": "🇬🇷 اليونان", "31": "🇳🇱 هولندا", "32": "🇧🇪 بلجيكا", "33": "🇫🇷 فرنسا", "34": "🇪🇸 إسبانيا",
    "36": "🇭🇺 المجر", "39": "🇮🇹 إيطاليا", "40": "🇷🇴 رومانيا", "41": "🇨🇭 سويسرا", "43": "🇦🇹 النمسا",
    "44": "🇬🇧 بريطانيا", "45": "🇩🇰 الدنمارك", "46": "🇸🇪 السويد", "47": "🇳🇴 النرويج", "48": "🇵🇱 بولندا",
    "49": "🇩🇪 ألمانيا", "51": "🇵🇪 بيرو", "52": "🇲🇽 المكسيك", "53": "🇨🇺 كوبا", "54": "🇦🇷 الأرجنتين",
    "55": "🇧🇷 البرازيل", "56": "🇨🇱 تشيلي", "57": "🇨🇴 كولومبيا", "58": "🇻🇪 فنزويلا", "60": "🇲🇾 ماليزيا",
    "61": "🇦🇺 أستراليا", "62": "🇮🇩 إندونيسيا", "63": "🇵🇭 الفلبين", "64": "🇳🇿 نيوزيلندا", "65": "🇸🇬 سنغافورة",
    "66": "🇹🇭 تايلاند", "81": "🇯🇵 اليابان", "82": "🇰🇷 كوريا الجنوبية", "84": "🇻🇳 فيتنام", "86": "🇨🇳 الصين",
    "90": "🇹🇷 تركيا", "91": "🇮🇳 الهند", "92": "🇵🇰 باكستان", "93": "🇦🇫 أفغانستان", "94": "🇱🇰 سريلانكا",
    "95": "🇲🇲 ميانمار", "98": "🇮🇷 إيران", "211": "🇸🇸 جنوب السودان", "212": "🇲🇦 المغرب", "213": "🇩🇿 الجزائر",
    "216": "🇹🇳 تونس", "218": "🇱🇾 ليبيا", "220": "🇬🇲 غامبيا", "221": "🇸🇳 السنغال", "222": "🇲🇷 موريتانيا",
    "223": "🇲🇱 مالي", "224": "🇬🇳 غينيا", "225": "🇨🇮 ساحل العاج", "226": "🇧🇫 بوركينا فاسو", "227": "🇳🇪 النيجر",
    "228": "🇹🇬 توغو", "229": "🇧🇯 بنين", "230": "🇲🇺 موريشيوس", "231": "🇱🇷 ليبيريا", "232": "🇸🇱 سيراليون",
    "233": "🇬🇭 غانا", "234": "🇳🇬 نيجيريا", "235": "🇹🇩 تشاد", "237": "🇨🇲 الكاميرون", "238": "🇨🇻 الرأس الأخضر",
    "241": "🇬🇦 الغابون", "242": "🇨🇬 الكونغو", "243": "🇨🇩 الكونغو الديمقراطية", "244": "🇦🇴 أنغولا",
    "249": "🇸🇩 السودان", "250": "🇷🇼 رواندا", "251": "🇪🇹 إثيوبيا", "252": "🇸🇴 الصومال", "253": "🇩🇯 جيبوتي",
    "254": "🇰🇪 كينيا", "255": "🇹🇿 تنزانيا", "256": "🇺🇬 أوغندا", "257": "🇧🇮 بوروندي", "258": "🇲🇿 موزمبيق",
    "260": "🇿🇲 زامبيا", "261": "🇲🇬 مدغشقر", "263": "🇿🇼 زيمبابوي", "264": "🇳🇦 ناميبيا", "265": "🇲🇼 ملاوي",
    "351": "🇵🇹 البرتغال", "352": "🇱🇺 لوكسمبورغ", "353": "🇮🇪 أيرلندا", "354": "🇮🇸 أيسلندا", "355": "🇦🇱 ألبانيا",
    "356": "🇲🇹 مالطا", "357": "🇨🇾 قبرص", "358": "🇫🇮 فنلندا", "359": "🇧🇬 بلغاريا", "370": "🇱🇹 ليتوانيا",
    "371": "🇱🇻 لاتفيا", "372": "🇪🇪 إستونيا", "373": "🇲🇩 مولدوفا", "374": "🇦🇲 أرمينيا", "375": "🇧🇾 بيلاروسيا",
    "380": "🇺🇦 أوكرانيا", "381": "🇷🇸 صربيا", "385": "🇭🇷 كرواتيا", "386": "🇸🇮 سلوفينيا", "387": "🇧🇦 البوسنة",
    "420": "🇨🇿 التشيك", "421": "🇸🇰 سلوفاكيا", "502": "🇬🇹 غواتيمالا", "503": "🇸🇻 السلفادور", "504": "🇭🇳 هندوراس",
    "505": "🇳🇮 نيكاراغوا", "506": "🇨🇷 كوستاريكا", "507": "🇵🇦 بنما", "591": "🇧🇴 بوليفيا", "593": "🇪🇨 الإكوادور",
    "595": "🇵🇾 باراغواي", "598": "🇺🇾 أوروغواي", "852": "🇭🇰 هونغ كونغ", "853": "🇲🇴 ماكاو", "855": "🇰🇭 كمبوديا",
    "856": "🇱🇦 لاوس", "880": "🇧🇩 بنغلاديش", "886": "🇹🇼 تايوان", "960": "🇲🇻 المالديف", "961": "🇱🇧 لبنان",
    "962": "🇯🇴 الأردن", "963": "🇸🇾 سوريا", "964": "🇮🇶 العراق", "965": "🇰🇼 الكويت", "966": "🇸🇦 السعودية",
    "967": "🇾🇪 اليمن", "968": "🇴🇲 عُمان", "970": "🇵🇸 فلسطين", "971": "🇦🇪 الإمارات", "972": "🇮🇱 إسرائيل",
    "973": "🇧🇭 البحرين", "974": "🇶🇦 قطر", "975": "🇧🇹 بوتان", "976": "🇲🇳 منغوليا", "977": "🇳🇵 نيبال",
    "992": "🇹🇯 طاجيكستان", "993": "🇹🇲 تركمانستان", "994": "🇦🇿 أذربيجان", "995": "🇬🇪 جورجيا",
    "996": "🇰🇬 قرغيزستان", "998": "🇺🇿 أوزبكستان",
}

def detect_calling_code(phone_number):
    """بيرجّع (الكود, اسم الدولة) من أطول كود متطابق. لو مش معروف بيرجّع (أول 3 أرقام, None)."""
    p = str(phone_number).replace("+", "").replace(" ", "")
    for n in (3, 2, 1):
        if p[:n] in CALLING_CODES:
            return p[:n], CALLING_CODES[p[:n]]
    return p[:3], None

async def start_add_country(event, cid):
    """بيضيف دولة لقسم محدد صراحة (cid)، مش من سياق ضمني، عشان الدولة تتضاف فعلاً للقسم الصح."""
    async with bot.conversation(event.chat_id) as x:
        await x.send_message(f"🗂 القسم : {get_category_name(cid)}\n- ارسل الرمز الدولي مع + \nمثال\n- +20")
        cc_resp = None
        cc_digits = ""
        cc_text = ""
        for _ in range(3):
            cc_resp = await x.get_response()
            cc_text = cc_resp.text.strip().replace(" ", "")
            cc_digits = re.sub(r"\D", "", cc_text)
            if cc_text.startswith("+") and cc_digits and 1 <= len(cc_digits) <= 4:
                break
            await x.send_message("❌ رمز غير صحيح. لازم يبدأ بـ + وبعده أرقام بس، مثال: +20")
            cc_digits = ""
        if not cc_digits:
            await x.send_message("❌ تم الإلغاء، حاول تاني.")
            return
        calling_code_text = "+" + cc_digits

        # بنتأكد من رمز الدولة ونحاول نجيب اسمها بالعلم تلقائي من الرمز
        suggested = None
        for n in (3, 2, 1):
            if cc_digits[:n] in CALLING_CODES:
                suggested = CALLING_CODES[cc_digits[:n]]
                break

        existing = db.get(countries_key(cid)) or []
        if any(str(c.get("calling_code", "")).replace(" ", "") == calling_code_text for c in existing):
            await x.send_message(f"⚠️ الرمز {calling_code_text} مضاف بالفعل في القسم ده.")
            return

        if suggested:
            await x.send_message(f"✅ الرمز اتعرف تلقائي: {suggested}\n\nلو الاسم ده صح ابعت «تم»، ولو عايز اسم تاني ابعتو دلوقتي.")
            name_resp = await x.get_response()
            name_text = suggested if name_resp.text.strip() in ("تم", "ok", "Ok", "OK") else name_resp.text.strip()
        else:
            await x.send_message(f"⚠️ الرمز {calling_code_text} مش في جدول الدول المعروف عندي.\n- ارسل اسم الدولة بالعلم الخاص بها\nمثال :- مصر 🇪🇬")
            name_resp = await x.get_response()
            name_text = name_resp.text.strip()
        if not name_text:
            await x.send_message("❌ تم الإلغاء، حاول تاني.")
            return

        await x.send_message(f"- ارسل سعر شراء المستخدمين من البوت")
        price = await x.get_response()
        try:
            am = float(price.text)
        except:
            await x.send_message(f"- ارسل ارقام فقط")
            return
        sell_price = price

        countries = db.get(countries_key(cid)) or []
        countries.append({
            "name": name_text,
            "calling_code": calling_code_text,
            "price": price.text,
            "sell_price": sell_price.text,
        })
        db.set(countries_key(cid), countries)

        await x.send_message(f"- تم اضافة الدولة بنجاح ✅ (قسم {get_category_name(cid)})")

        bot_info = await bot.get_me()
        bot_username = bot_info.username

        channel_username = "@TG_AFROTO_PRICE"
        message_text = (
            f"- {name_text} : {calling_code_text} - ${price.text} < @{bot_username}\n"
            f"------------------------------------------------------\n"
            f"الدولة - {name_text.split(' ')[0]}"
        )

        buttons = [[Button.url(accent("شراء رقم"), f"https://t.me/{bot_username}")]]

        await l_bot_send_message(bot,
            entity=channel_username,
            message=message_text,
            buttons=buttons
        )

async def show_add_number_countries(event, cid=None):
    """بعد ما الأدمن يختار القسم: يختار الدولة ثم يضيف الرقم. cid محدد صراحة، مش من سياق ضمني."""
    cid = cid or get_admin_cat(event.sender_id)
    countries = db.get(countries_key(cid)) or []
    back = [Button.inline(accent("🔀 تغيير القسم"), data="ca:pick:add")]
    if not countries:
        await l_edit(event, f"⚠️ مفيش دول في قسم «{get_category_name(cid)}». أضف دولة الأول من «➕ إضافة دولة».",
                     buttons=[[Button.inline(accent("➕ إضافة دولة"), data=f"addctry:{cid}")], back,
                              [Button.inline(accent("رجوع ↪️"), data="adm:num")]])
        return
    buttons, row = [], []
    for c in countries:
        row.append(Button.inline(accent(f"{c['name']} : {c['price']} $"), data=f"rig_{c['calling_code']}_{c['name']}_{c['price']}_{cid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(back)
    buttons.append([Button.inline(accent("رجوع ↪️"), data="adm:num")])
    await l_edit(event, f"- اختر دولة الرقم الذي تريد اضافته\n🗂 القسم : {get_category_name(cid)}", parse_mode='markdown', buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b'bulk_sessions'))
async def bulk_sessions_start(event):
    user_id = event.sender_id
    admins_now = db.get("admins") if db.exists("admins") else []
    if user_id != admin and user_id not in (admins_now or []):
        await event.answer("❌ للأدمن فقط.", alert=True)
        return
    db.set(f"bulk_ses_{user_id}", True)
    db.set(f"bulk_ses_report_{user_id}", {"added": 0, "dup": 0, "invalid": 0, "unmatched": []})
    await l_edit(event,
        "📥 **رفع سيشنات بالدفعة**\n\n"
        f"🗂 القسم : **{get_category_name(get_admin_cat(user_id))}**\n\n"
        "- ابعتلي ملفات السيشن (.session) أو ملف zip فيه سيشنات، وهعرّف دولة كل حساب تلقائي وأضيفه وأقولك المحتويات.\n"
        "- لو فيه دولة مش متضافة، لما تدوس «✅ إنهاء» هقولك الدول اللي لقيتها وأسألك عن سعر كل واحدة وتتضاف هي وأرقامها.\n"
        "- لما تخلص، اضغط «✅ إنهاء».",
        buttons=[
            [Button.inline(accent("✅ إنهاء"), data="bulk_ses_done")],
            [Button.inline(accent("❌ إلغاء"), data="bulk_ses_cancel")]
        ])

@bot.on(events.CallbackQuery(pattern=b'bulk_ses_cancel'))
async def bulk_sessions_cancel(event):
    user_id = event.sender_id
    db.delete(f"bulk_ses_{user_id}")
    db.delete(f"bulk_ses_report_{user_id}")
    await event.answer("تم الإلغاء.")
    await render_numbers_panel(event)

SKIP_WORDS = ("تخطي", "skip", "Skip", "SKIP")

def _add_accounts(cid, code_str, items):
    """بيضيف حسابات (phone/session) لدولة في قسم، ويرجّع (المضاف, المكرر)."""
    key = accounts_key(code_str, cid)
    accounts = db.get(key) if db.exists(key) else []
    added = dup = 0
    for it in items:
        if any(a.get("phone_number") == it["phone"] for a in accounts):
            dup += 1
            continue
        rec = {"phone_number": it["phone"], "two-step": it.get("two", ""), "session": it["session"]}
        for k in ("user_id", "username", "first_name"):
            if it.get(k):
                rec[k] = it[k]
        rec["api_id"] = int(API_ID)
        rec["added_at"] = int(time.time())
        if it.get("has_2fa") is not None:
            rec["has_2fa"] = bool(it["has_2fa"])
        accounts.append(rec)
        added += 1
    db.set(key, accounts)
    return added, dup

async def _resolve_pending_countries(chat_id, cid, pending):
    """بيجمّع الأرقام اللي دولها مش مضافة، ويسأل الأدمن عن سعر كل دولة، ويضيف الدولة وأرقامها.
    بيرجّع (سطور النتيجة, عدد المتخطي)."""
    _lk = f"ses_unpriced_{cid}"
    _left = db.get(_lk) or []
    if _left:
        _have = {x.get("phone") for x in pending}
        pending = list(pending) + [x for x in _left if x.get("phone") not in _have]
        db.delete(_lk)
    groups = {}
    for it in pending:
        code, name = detect_calling_code(it["phone"])
        groups.setdefault(code, {"name": name, "items": []})["items"].append(it)
    cat_name = get_category_name(cid)
    done_lines, skipped, handled = [], 0, set()
    try:
        async with bot.conversation(chat_id, timeout=180) as x:
            for code, g in groups.items():
                label = g["name"] or f"دولة غير معروفة (+{code})"
                n = len(g["items"])
                await x.send_message(
                    f"🌍 دولة {label} (+{code}) مش مضافة في قسم «{cat_name}» — {n} حساب.\n\n"
                    f"💵 ابعت سعر الرقم الواحد بالدولار عشان تتضاف الدولة وحساباتها، أو ابعت «تخطي».")
                resp = (await x.get_response()).text.strip()
                price = None
                for _ in range(3):
                    if resp in SKIP_WORDS:
                        break
                    try:
                        v = float(resp.replace(",", "."))
                        if v > 0:
                            price = v
                            break
                    except ValueError:
                        pass
                    await x.send_message("❌ ابعت رقم صحيح أو «تخطي».")
                    resp = (await x.get_response()).text.strip()
                handled.add(code)
                if price is None:
                    skipped += n
                    continue
                code_str = f"+{code}"
                countries = db.get(countries_key(cid)) or []
                if not any(str(c.get("calling_code")) == code_str for c in countries):
                    countries.append({"name": label if g["name"] else f"دولة {code_str}", "calling_code": code_str,
                                      "price": f"{price:g}", "sell_price": f"{price:g}"})
                    db.set(countries_key(cid), countries)
                added, _dup = _add_accounts(cid, code_str, g["items"])
                done_lines.append(f"✅ {label}: تمت إضافة الدولة بسعر {price:g}$ و{added} حساب")
    except asyncio.TimeoutError:
        pass
    _saved = []
    for code, g in groups.items():
        if code not in handled:
            skipped += len(g["items"])
            _saved += g["items"]
    if _saved:
        db.set(_lk, _saved)
        done_lines.append(f"⏳ {len(_saved)} حساب اتحفظوا (الوقت خلص قبل ما تحدد سعر دولهم) — هسألك عليهم في أول إنهاء/رفع جاي ومش هيضيعوا.")
    return done_lines, skipped

def _pending_result_text(done_lines, skipped):
    text = "📥 **نتيجة الدول الجديدة**\n\n" + ("\n".join(done_lines) if done_lines else "لم تتم إضافة أي دولة.")
    if skipped:
        text += f"\n\n⏭ {skipped} حساب اتخطوا (لم تتحدد لهم أسعار) — أعد رفع سيشناتهم بعد إضافة دولتهم."
    return text

@bot.on(events.CallbackQuery(pattern=b'bulk_ses_done'))
async def bulk_sessions_done(event):
    user_id = event.sender_id
    if not db.get(f"bulk_ses_{user_id}"):
        await event.answer("مفيش عملية رفع شغالة دلوقتي.", alert=True)
        return
    rep = db.get(f"bulk_ses_report_{user_id}") or {"added": 0, "dup": 0, "invalid": 0, "unmatched": []}
    db.delete(f"bulk_ses_{user_id}")
    db.delete(f"bulk_ses_report_{user_id}")
    cid = get_admin_cat(user_id)
    pending = rep.get("pending", [])
    header = (
        "📊 **تقرير رفع السيشنات**\n"
        f"🗂 القسم: {get_category_name(cid)}\n"
        f"✅ تمت إضافتها: {rep['added']}\n"
        f"♻️ مكررة (موجودة بالفعل): {rep['dup']}\n"
        f"❌ غير صالحة: {rep['invalid']}\n"
    )
    await event.answer("تم إنهاء الرفع ✅")
    if not pending:
        await render_numbers_panel(event, header=header)
        return
    await l_edit(event, header + f"\n🌍 فيه {len(pending)} حساب دولهم مش متضافة في القسم، هسألك عن سعر كل دولة...", parse_mode='markdown')
    done_lines, skipped = await _resolve_pending_countries(event.chat_id, cid, pending)
    await event.respond(_pending_result_text(done_lines, skipped), parse_mode='markdown',
                        buttons=[[Button.inline(accent("رجوع ↪️"), data="adm:num")]])

# ============================================================
#  قارئ سيشنات شامل: بيفهم أي صيغة ويحولها لـ Telethon StringSession
#   - ملف .session (Telethon أو Pyrogram - SQLite)
#   - String Session (Telethon أو Pyrogram) في .txt / .string / .session نصي
#   - ملفات JSON (session_string / auth_key+dc_id) وفيها الرقم والـ 2FA لو موجودين
#   - ملف .session + .json بنفس الاسم (الرقم والـ 2FA بيتاخدوا من الـ json)
#   - zip فيه أي خليط من اللي فوق (وzip جوا zip)
# ============================================================
from telethon.crypto import AuthKey as _AuthKey
from telethon import errors as _tl_errors

_DC_IPS = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100",
           4: "149.154.167.91", 5: "149.154.171.5"}
_SESSION_EXTS = (".session", ".txt", ".json", ".string", ".dat", ".sess", ".sqlite", ".db")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=]{200,}")
_SQLITE_MAGIC = b"SQLite format 3\x00"
_MAX_MEMBER = 5 * 1024 * 1024
_MAX_ENTRIES = 1000


def _to_telethon_string(dc_id, auth_key, ip=None, port=443):
    if dc_id not in _DC_IPS or not auth_key or len(auth_key) != 256:
        return None
    # دايماً IPv4 الرسمي للـ DC: عناوين IPv6 أو IPs قديمة جوه ملفات السيشن
    # بتفشل الاتصال على أغلب السيرفرات (وده سبب "فشل" من غير سبب واضح)
    ip, port = _DC_IPS[dc_id], 443
    s = StringSession()
    s.set_dc(dc_id, ip, port)
    s.auth_key = _AuthKey(data=bytes(auth_key))
    return s.save()


def _try_string(tok):
    """يحاول يفهم نص كـ String Session (Telethon أو Pyrogram). يرجّع Telethon string أو None."""
    tok = (tok or "").strip().strip("\"'`,;")
    if len(tok) < 200:
        return None
    order = ("tele", "pyro") if tok[:1] == "1" else ("pyro", "tele")
    for kind in order:
        try:
            if kind == "tele":
                if len(tok) not in (353, 369):
                    continue
                s = StringSession(tok)
                if s.auth_key and s.dc_id in _DC_IPS:
                    return _to_telethon_string(s.dc_id, s.auth_key.key, s.server_address, s.port)
            else:
                p = PyroSession.from_string(tok)
                if p.dc_id in _DC_IPS and len(p.auth_key) == 256 and not p.is_bot and not p.test_mode:
                    return _to_telethon_string(p.dc_id, p.auth_key)
        except Exception:
            continue
    return None


def _norm_key(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


_PHONE_KEYS = {"phone", "phonenumber", "number", "phonenum", "tel"}
_TWOFA_KEYS = {"twofa", "2fa", "password", "twostep", "cloudpassword", "twofapassword", "twostepverification",
               "twosteppassword", "twostepverificationpassword", "2fapassword", "2fapass", "twofapass",
               "twofactor", "twofactorpassword", "twofactorauth", "tfa", "cloudpass", "passwordtwofa",
               "telegram2fa", "tgpassword"}


def _clean_phone(v):
    d = re.sub(r"\D", "", str(v or ""))
    return d if 7 <= len(d) <= 15 else ""


def _json_meta(d, _depth=0):
    """بيستخرج الرقم وكلمة التحقق بخطوتين من json (حتى لو جوه كائن داخلي)."""
    phone, two = "", ""
    if not isinstance(d, dict):
        return {"phone": "", "two": ""}
    for k, v in d.items():
        nk = _norm_key(k)
        if nk in _PHONE_KEYS and not phone:
            phone = _clean_phone(v)
        elif nk in _TWOFA_KEYS and not two and isinstance(v, (str, int)) and not isinstance(v, bool):
            two = _clean_pw(v)
    if _depth < 2 and (not phone or not two):
        for v in d.values():
            if isinstance(v, dict):
                sub = _json_meta(v, _depth + 1)
                phone = phone or sub["phone"]
                two = two or sub["two"]
    return {"phone": phone, "two": two}


def _walk_json(o, out):
    if isinstance(o, dict):
        strings = []
        for v in o.values():
            if isinstance(v, str) and len(v) >= 200:
                s = _try_string(v)
                if s:
                    strings.append(s)
        # auth_key (hex) + dc_id
        ak, dc = None, None
        for k, v in o.items():
            nk = _norm_key(k)
            if nk == "authkey" and isinstance(v, str):
                try:
                    ak = bytes.fromhex(v.strip())
                except Exception:
                    ak = None
            elif nk in ("dcid", "dc") and str(v).isdigit():
                dc = int(v)
        if ak and dc:
            s = _to_telethon_string(dc, ak)
            if s:
                strings.append(s)
        if strings:
            meta = _json_meta(o)
            for s in strings:
                out.append({"string": s, **meta})
        for v in o.values():
            if isinstance(v, (dict, list)):
                _walk_json(v, out)
    elif isinstance(o, list):
        for x in o:
            _walk_json(x, out)


def _parse_sqlite_bytes(data, tmp_dir):
    """يقرأ ملف .session (Telethon أو Pyrogram). يرجّع (string, سبب_الفشل)."""
    p = os.path.join(tmp_dir, uuid.uuid4().hex + ".sqlite")
    con = None
    try:
        with open(p, "wb") as f:
            f.write(data)
        con = sqlite3.connect(p)
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "sessions" not in tables:
            return None, "ملف SQLite مش سيشن"
        row = con.execute("SELECT * FROM sessions").fetchone()
        if row is None:
            return None, "السيشن فاضية (مفيش auth_key)"
        r = dict(row)
        key, dc = r.get("auth_key"), r.get("dc_id")
        if not key or not dc:
            return None, "السيشن فاضية (مفيش auth_key)"
        if r.get("is_bot"):
            return None, "سيشن بوت وليس حساب"
        s = _to_telethon_string(int(dc), key, r.get("server_address"), r.get("port"))
        if not s:
            return None, "auth_key أو DC غير صالح"
        return s, None
    except Exception as ex:
        return None, f"ملف تالف ({type(ex).__name__})"
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        for ext in ("", "-journal", "-wal", "-shm"):
            try:
                if os.path.exists(p + ext):
                    os.remove(p + ext)
            except Exception:
                pass


def _parse_blob(name, data, tmp_dir):
    """بيحلل محتوى ملف واحد. يرجّع (candidates, reason, meta_only)
    candidates: [{"string","phone","two"}]  — reason: سبب لو مفيش حاجة  — meta_only: json فيه بيانات بس."""
    base = os.path.basename(name)
    stem = os.path.splitext(base)[0]
    if data[:16] == _SQLITE_MAGIC:
        s, why = _parse_sqlite_bytes(data, tmp_dir)
        if not s:
            return [], why, False
        return [{"string": s, "phone": _clean_phone(stem), "two": ""}], None, False

    text = data.decode("utf-8-sig", "ignore")
    stripped = text.strip()
    if not stripped:
        return [], "ملف فاضي", False

    if stripped[:1] in "{[":
        try:
            obj = json.loads(stripped)
        except Exception:
            obj = None
        if obj is not None:
            out = []
            _walk_json(obj, out)
            if out:
                for c in out:
                    if not c["phone"]:
                        c["phone"] = _clean_phone(stem)
                return out, None, False
            meta = _json_meta(obj if isinstance(obj, dict) else {})
            if meta["phone"] or meta["two"]:
                return [], None, True       # json بيانات (رقم/2FA) بس، هتتربط بالسيشن بنفس الاسم
            return [], None, True

    out = []
    for line in text.splitlines():
        for tok in _TOKEN_RE.findall(line):
            s = _try_string(tok)
            if s:
                rest = line.replace(tok, " ")
                m = re.search(r"\+?(\d{7,15})", rest)
                out.append({"string": s, "phone": (m.group(1) if m else _clean_phone(stem)), "two": ""})
    if not out:
        for tok in _TOKEN_RE.findall(text):
            s = _try_string(tok)
            if s:
                out.append({"string": s, "phone": _clean_phone(stem), "two": ""})
    if out:
        return out, None, False
    return [], "صيغة غير معروفة (مش سيشن)", False


def _collect_candidates(path, orig_name, tmp_dir, depth=0):
    """بيفرد أي ملف/zip لقايمة مرشحين. يرجّع (candidates, reasons[list of str])."""
    cands, reasons, metas = [], [], {}
    ext = os.path.splitext(orig_name or "")[1].lower()

    if zipfile.is_zipfile(path):
        try:
            z = zipfile.ZipFile(path)
        except Exception:
            return [], ["zip تالف"]
        with z:
            infos = [i for i in z.infolist()
                     if not i.is_dir() and "__MACOSX" not in i.filename
                     and not os.path.basename(i.filename).startswith(".")][:_MAX_ENTRIES]
            if any("tdata" in i.filename.lower() for i in infos) and not any(
                    os.path.splitext(i.filename)[1].lower() in _SESSION_EXTS for i in infos):
                return [], ["مجلد tdata غير مدعوم — حوّله لسيشن الأول"]
            for i in infos:
                inner_ext = os.path.splitext(i.filename)[1].lower()
                nm = os.path.basename(i.filename)
                if i.file_size > _MAX_MEMBER * (10 if inner_ext == ".zip" else 1):
                    reasons.append(f"{nm}: حجمه كبير")
                    continue
                try:
                    data = z.read(i)
                except Exception:
                    reasons.append(f"{nm}: مش قادر يفتحه")
                    continue
                if data[:4] == b"PK\x03\x04" and depth < 2:
                    tmp = os.path.join(tmp_dir, uuid.uuid4().hex + ".zip")
                    try:
                        with open(tmp, "wb") as f:
                            f.write(data)
                        c2, r2 = _collect_candidates(tmp, nm, tmp_dir, depth + 1)
                        cands += c2
                        reasons += r2
                    finally:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    continue
                if inner_ext not in _SESSION_EXTS and data[:16] != _SQLITE_MAGIC:
                    continue            # صور وملفات تانية بنتجاهلها
                c, why, meta_only = _parse_blob(nm, data, tmp_dir)
                stem = os.path.splitext(nm)[0]
                if meta_only:
                    try:
                        obj = json.loads(data.decode("utf-8-sig", "ignore"))
                        metas[stem] = _json_meta(obj if isinstance(obj, dict) else {})
                    except Exception:
                        pass
                    continue
                for x in c:
                    x["stem"] = stem
                cands += c
                if why:
                    reasons.append(f"{nm}: {why}")
        # ربط الرقم/2FA من json بنفس اسم السيشن
        for x in cands:
            m = metas.get(x.get("stem", ""))
            if m:
                x["phone"] = x.get("phone") or m["phone"]
                x["two"] = x.get("two") or m["two"]
        return cands, reasons

    if ext in (".rar", ".7z", ".tar", ".gz"):
        return [], ["الأرشيف ده غير مدعوم — استخدم zip"]
    try:
        with open(path, "rb") as f:
            data = f.read(_MAX_MEMBER + 1)
    except Exception:
        return [], ["مش قادر يقرا الملف"]
    c, why, meta_only = _parse_blob(orig_name or "file", data, tmp_dir)
    if meta_only:
        return [], ["الملف بيانات بس (json) ومفيهوش سيشن"]
    return c, ([why] if why else [])


_FATAL_AUTH = tuple(getattr(_tl_errors, n) for n in (
    "AuthKeyUnregisteredError", "AuthKeyInvalidError", "AuthKeyDuplicatedError",
    "SessionRevokedError", "SessionExpiredError", "UserDeactivatedError",
    "UserDeactivatedBanError") if hasattr(_tl_errors, n))


def _explain_tg_error(ex):
    """بيحوّل أي خطأ من تليجرام لسبب عربي واضح."""
    n = type(ex).__name__
    if "Duplicated" in n:
        return ("السيشن شغالة في مكانين في نفس الوقت (جهاز/بوت/برنامج تاني بيستخدمها) "
                "— اقفل التاني الأول، وتليجرام غالباً بيلغيها في الحالة دي")
    if "Deactivated" in n or n in ("PhoneNumberBannedError",):
        return "حساب محظور / محذوف"
    if n in ("AuthKeyUnregisteredError", "AuthKeyInvalidError"):
        return "السيشن اتلغت من تليجرام (اتعمل تسجيل خروج للجهاز ده أو اتنهت من جهاز تاني)"
    if n in ("SessionRevokedError", "SessionExpiredError"):
        return "السيشن اتلغت / منتهية (Terminate من إعدادات الأجهزة)"
    if n == "AuthKeyPermEmptyError":
        return "الجلسة مش مسجلة دخول (auth_key مؤقت)"
    return f"تليجرام رفض السيشن ({n})"


async def _validate_string(cand):
    """بيتأكد إن السيشن شغالة ويجيب رقمها الحقيقي. يرجّع (item, None) أو (None, سبب واضح)."""
    last = "تعذر الاتصال بتليجرام من السيرفر"
    for attempt in range(3):
        app = None
        try:
            app = TelegramClient(StringSession(cand["string"]), int(API_ID), API_HASH,
                                 receive_updates=False, connection_retries=3, retry_delay=1,
                                 use_ipv6=False)
            await asyncio.wait_for(app.connect(), 30)
            # get_me مباشرة (is_user_authorized بتبلع الخطأ الحقيقي وبترجع False بس)
            # طلب مباشر عشان الخطأ الحقيقي يظهر (get_me بترجع None لو السيشن اتلغت وبتخبي السبب)
            me = (await asyncio.wait_for(app(functions.users.GetUsersRequest([types.InputUserSelf()])), 30))[0]
            if me is None:
                return None, "السيشن مش مسجلة دخول"
            if getattr(me, "bot", False):
                return None, "سيشن بوت وليس حساب"
            if getattr(me, "deleted", False):
                return None, "حساب محذوف"
            phone = _clean_phone(getattr(me, "phone", "")) or cand.get("phone") or ""
            if not phone:
                return None, "الحساب مفيهوش رقم ظاهر — ابعت معاه ملف json فيه الرقم أو سمّي الملف بالرقم"
            _sess = StringSession.save(app.session)
            # بنتحقق بس هل الحساب عليه تحقق بخطوتين من عدمه (من غير ما نفعّل أو نغيّر أي حاجة)
            _two = _clean_pw(cand.get("two", ""))
            _ok, _final, _note = await unify_two_step(_sess, _pw_candidates({"two-step": _two}))
            if _ok:
                _two = _final
            else:
                print(f"2FA unify failed for +{cand.get('phone')}: {_note}")
            _needs_pw = (not _ok) and ("مش معروفة لينا" in (_note or ""))
            try:
                # جلسة خاصة بالبوت (auth_key مستقل) عشان أي حاجة تحصل في ملف الأدمن الأصلي متأثرش على المخزون
                _sess = await asyncio.wait_for(
                    make_fresh_session(_sess, _pw_candidates({"two-step": _two})), 120)
            except Exception as _fe:
                print("fresh session on upload failed, keeping original:", type(_fe).__name__, _fe)
            return {"phone": phone, "session": _sess, "two": _two,
                    "has_2fa": True, "two_verified": _ok, "needs_pw": _needs_pw, "unify_note": _note,
                    "user_id": getattr(me, "id", None), "username": getattr(me, "username", None) or "",
                    "first_name": (getattr(me, "first_name", None) or "")[:40]}, None
        except _tl_errors.FloodWaitError as ex:
            wait = int(getattr(ex, "seconds", 0) or 0)
            last = f"تليجرام عامل حد مؤقت {wait} ثانية — جرب بعد شوية"
            if wait <= 20:
                await asyncio.sleep(wait + 1)
                continue
            return None, last
        except _tl_errors.RPCError as ex:
            return None, _explain_tg_error(ex)
        except (asyncio.TimeoutError, ConnectionError, OSError) as ex:
            last = f"مفيش اتصال بتليجرام من السيرفر ({type(ex).__name__}) — اتأكد إن السيرفر عنده إنترنت"
            await asyncio.sleep(2 * (attempt + 1))
        except Exception as ex:
            last = f"خطأ غير متوقع ({type(ex).__name__}: {str(ex)[:60]})"
            await asyncio.sleep(1)
        finally:
            if app is not None:
                try:
                    await app.disconnect()
                except Exception:
                    pass
    return None, last


def _set_stock_pw(phone, pw):
    """بيسجّل كلمة التحقق بخطوتين لحساب (أو أكتر بنفس الرقم) في المخزون. بيرجّع عدد الحسابات المتحدّثة."""
    n = 0
    for key in _iter_stock_keys():
        accs = (db.get(key) if db.exists(key) else None) or []
        changed = False
        for a in accs:
            if str(a.get("phone_number")) == str(phone):
                a["two-step"] = pw
                a["has_2fa"] = True
                changed = True
                n += 1
        if changed:
            db.set(key, accs)
    return n

async def _handle_meta_only_json(event, path, orig_name, tmp_dir, user_id):
    """ملف json فيه بيانات (رقم + twoFA) من غير سيشن: بنربطه بالحسابات الموجودة، أو بنحفظه لحد ما السيشن توصل."""
    try:
        with open(path, "rb") as f:
            data = f.read(2_000_000)
    except Exception:
        return False
    head = data[:64].lstrip(b"\xef\xbb\xbf \r\n\t")
    if head[:1] not in (b"{", b"["):
        return False
    _c, _why, meta_only = _parse_blob(orig_name or "file", data, tmp_dir)
    if not meta_only:
        return False
    try:
        obj = json.loads(data.decode("utf-8-sig", "ignore"))
    except Exception:
        return False
    stem_phone = _clean_phone(os.path.splitext(os.path.basename(orig_name or ""))[0])
    pairs = []
    if isinstance(obj, dict):
        m = _json_meta(obj)
        pairs.append((m["phone"] or stem_phone, m["two"]))
    elif isinstance(obj, list):
        for o in obj:
            if isinstance(o, dict):
                m = _json_meta(o)
                pairs.append((m["phone"], m["two"]))
    pairs = [(p, w) for p, w in pairs if p and w]
    if not pairs:
        return False
    bm = db.get(f"bulk_meta_{user_id}") or {}
    updated = 0
    for ph, pw in pairs:
        bm[ph] = pw
        updated += _set_stock_pw(ph, pw)
    db.set(f"bulk_meta_{user_id}", dict(list(bm.items())[-2000:]))
    await event.respond(
        f"✅ استلمت بيانات التحقق بخطوتين لـ {len(pairs)} حساب.\n"
        f"• اتحدّث {updated} حساب موجود في المخزون.\n"
        "• الباقي هيتربط تلقائي لما تبعت ملف السيشن بتاعه.")
    return True

@client.on(events.NewMessage(func=lambda e: e.is_private and bool(e.document)))
async def bulk_sessions_receive(event):
    user_id = event.sender_id
    if not db.get(f"bulk_ses_{user_id}"):
        return
    admins_now = db.get("admins") if db.exists("admins") else []
    if user_id != admin and user_id not in (admins_now or []):
        return

    rep = db.get(f"bulk_ses_report_{user_id}") or {"added": 0, "dup": 0, "invalid": 0, "unmatched": []}
    cid = get_admin_cat(user_id)
    CUR_CAT.set(cid)
    tmp_dir = "database/tmp_sessions"
    os.makedirs(tmp_dir, exist_ok=True)
    dl_path = os.path.join(tmp_dir, uuid.uuid4().hex + ".dl")
    orig_name = ""
    try:
        orig_name = (event.file.name or "") if event.file else ""
    except Exception:
        pass
    progress = None
    results, fail_reasons, is_zip = [], [], False
    try:
        await event.download_media(file=dl_path)
        is_zip = zipfile.is_zipfile(dl_path)
        if not is_zip and await _handle_meta_only_json(event, dl_path, orig_name, tmp_dir, user_id):
            return
        cands, parse_reasons = await asyncio.get_event_loop().run_in_executor(
            None, _collect_candidates, dl_path, orig_name, tmp_dir)
        # شيل المكرر داخل نفس الرفعة
        seen, uniq = set(), []
        for c in cands:
            if c["string"] in seen:
                continue
            seen.add(c["string"])
            uniq.append(c)
        cands = uniq[:500]
        _bm = db.get(f"bulk_meta_{user_id}") or {}
        for c in cands:
            if not c.get("two"):
                c["two"] = _clean_pw(_bm.get(c.get("phone") or "", ""))
        fail_reasons += parse_reasons
        if not cands and not parse_reasons:
            parse_reasons = ["مفيش سيشنات جوا الملف"]
            fail_reasons += parse_reasons
        if cands:
            progress = await event.respond(f"⏳ جاري فحص {len(cands)} سيشن...")
            sem = asyncio.Semaphore(5)

            async def _one(c):
                async with sem:
                    return await _validate_string(c)
            outs = await asyncio.gather(*[_one(c) for c in cands])
            for item, why in outs:
                if item:
                    results.append(item)
                else:
                    fail_reasons.append(why)
    except Exception as ex:
        print("bulk session error:", ex)
        rep["invalid"] += 1
        db.set(f"bulk_ses_report_{user_id}", rep)
        await event.respond("❌ حصل خطأ أثناء قراءة الملف.")
        return
    finally:
        if progress is not None:
            try:
                await progress.delete()
            except Exception:
                pass
        try:
            if os.path.exists(dl_path):
                os.remove(dl_path)
        except Exception:
            pass

    need_pw = [it for it in results if it.get("needs_pw")]
    other_fail = [it for it in results if (not it.get("two_verified")) and not it.get("needs_pw")]
    # الحسابات الجاهزة (اتوحّدت) + حسابات فشلت لسبب تاني غير الكلمة (بتتضاف برضه، هتتوحّد لوحدها بعدين)؛
    # أما حسابات الكلمة المجهولة (need_pw) فبتترجّع لـ results بس لو اتوحّدت يدويًا تحت.
    results = [it for it in results if it.get("two_verified")] + other_fail

    skipped = []
    if need_pw:
        for it in need_pw:
            phone = it["phone"]
            resolved = False
            try:
                async with bot.conversation(event.chat_id, timeout=180) as x:
                    await x.send_message(
                        f"🔒 الرقم +{phone} عليه تحقق بخطوتين بكلمة إحنا مش عارفينها.\n\n"
                        "ابعتلي الكلمة القديمة بتاعته عشان أتأكد منها وأوحّدها تلقائي، أو ابعت «تخطي» عشان ميتضافش للمخزون.")
                    for _try in range(3):
                        txt = (await x.get_response()).text.strip()
                        if txt in ("تخطي", "skip", "Skip", "SKIP"):
                            break
                        ok2, final2, note2 = await unify_two_step(it["session"], [txt])
                        if ok2:
                            it["two"] = final2
                            it["two_verified"] = True
                            try:
                                it["session"] = await asyncio.wait_for(make_fresh_session(it["session"], [txt]), 120)
                            except Exception:
                                pass
                            await x.send_message(f"✅ تمام، اتوحّد الرقم +{phone}.")
                            results.append(it)
                            resolved = True
                            break
                        else:
                            left = 2 - _try
                            if left > 0:
                                await x.send_message(f"❌ الكلمة مش صح ({note2}). جرّب تاني ({left} محاولة متبقية)، أو ابعت «تخطي».")
                            else:
                                await x.send_message(f"❌ الكلمة مش صح ({note2}). هيتم تخطي الرقم ده.")
            except asyncio.TimeoutError:
                try:
                    await event.respond(f"⌛ انتهى وقت الانتظار على الرقم +{phone}، هيتم تخطيه. ممكن تبعته تاني بعدين.")
                except Exception:
                    pass
            if not resolved:
                skipped.append(phone)

    if skipped:
        try:
            _lst = "\n".join(f"• +{p}" for p in skipped[:20]) + (f"\n… و{len(skipped) - 20} كمان" if len(skipped) > 20 else "")
            await event.respond(
                f"🚫 {len(skipped)} رقم **متضافش** للمخزون (تحقق بخطوتين غير معروف وماتأكدناش منه):\n{_lst}\n\n"
                "لو عرفت الكلمة بعدين، ابعت السيشن تاني.", parse_mode='markdown')
        except Exception:
            pass

    _other_unk = [it["phone"] for it in other_fail]
    if _other_unk:
        try:
            _lst = "\n".join(f"• +{p}" for p in _other_unk[:15]) + (f"\n… و{len(_other_unk) - 15} كمان" if len(_other_unk) > 15 else "")
            await event.respond(
                f"⚠️ {len(_other_unk)} حساب فشل توحيد التحقق بخطوتين عليه لسبب تاني (اتضاف للمخزون برضه، هيتوحّد لوحده بعدين):\n{_lst}")
        except Exception:
            pass
    countries = db.get(countries_key(cid)) or []
    matched = {}            # (code, اسم الدولة) -> [items]
    new_items = []          # دولهم مش متضافة
    for item in results:
        country = match_country_by_phone(item["phone"], countries)
        if country:
            matched.setdefault((country["calling_code"], country["name"]), []).append(item)
        else:
            new_items.append(item)
    invalid = len(fail_reasons)
    rep["invalid"] += invalid

    lines = []
    for (code_str, cname), items in matched.items():
        added, dup = _add_accounts(cid, code_str, items)
        rep["added"] += added
        rep["dup"] += dup
        extra = f" (+{dup} مكرر)" if dup else ""
        lines.append(f"• {added} حساب {cname}{extra}")

    pend = rep.setdefault("pending", [])
    unknown_groups = {}
    for it in new_items:
        code, name = detect_calling_code(it["phone"])
        unknown_groups.setdefault(code, {"name": name, "n": 0})["n"] += 1
    for code, g in unknown_groups.items():
        lines.append(f"• {g['n']} حساب {g['name'] or 'دولة +' + code} ⚠️ دولة غير مضافة في القسم")

    # ملخص أسباب الفشل (بدل ما يقول "غير صالحة" من غير سبب)
    reason_text = ""
    if fail_reasons:
        from collections import Counter
        cnt = Counter(re.sub(r"^[^:]+: ", "", r) for r in fail_reasons)
        reason_text = "\n".join(f"   ◦ {r} ({n})" for r, n in cnt.most_common(6))

    if is_zip:
        text = (f"📦 **محتويات الملف** ({len(results) + invalid} سيشن) — قسم «{get_category_name(cid)}»\n\n"
                + ("\n".join(lines) if lines else "لا يوجد"))
        if invalid:
            text += f"\n\n❌ غير صالحة: {invalid}\n{reason_text}"
        db.set(f"bulk_ses_report_{user_id}", rep)
        await event.respond(text, parse_mode='markdown')
        if new_items:
            done_lines, skipped = await _resolve_pending_countries(event.chat_id, cid, new_items)
            await event.respond(_pending_result_text(done_lines, skipped), parse_mode='markdown')
        await event.respond("ابعت باقي الملفات أو اضغط «✅ إنهاء» فوق.")
        return

    # ملف واحد: الدولة الجديدة تتأجل لحد «إنهاء»
    for it in new_items:
        if any(p["phone"] == it["phone"] for p in pend):
            rep["dup"] += 1
        else:
            pend.append(it)
            rep["unmatched"].append(it["phone"])
    db.set(f"bulk_ses_report_{user_id}", rep)
    try:
        msg = (f"📥 (✅ {rep['added']} | ♻️ {rep['dup']} | ❌ {rep['invalid']} | 🌍 دولة غير مضافة: {len(rep['unmatched'])})\n")
        if invalid:
            msg += f"⚠️ الملف ده فشل:\n{reason_text}\n"
        msg += "ابعت باقي الملفات أو اضغط «✅ إنهاء» فوق."
        await event.respond(msg)
    except Exception:
        pass


#غير الحقوق واثبت انك فاشل اذا تريد تنقل اذكر اسمي او اسم قناتي #

#====================#
#CH : @J_D_D_M 
#DEV : @J_D_D_M
#====================#

@bot.on(events.CallbackQuery(pattern=b'seller'))
async def numgpv_button(event):
    user_id = event.sender_id
    user_info = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {"coind": 0}
    
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    if "coind" not in user_info:
        user_info["coind"] = 0
    
    await l_edit(event, 
        f'**- ✅ هنا اللوحة المتطورة الخاصة في استلام حسابات تلجرام الجاهزة •\n\n'
        f'🪐 يمكنك بيع حسابات تلجرام لجميع الدول وسحب اموالك عبر عدة طرق دفع متوفرة •\n'
        f'🪐 يمكنك تحويل اموال محفضتك الى رصيد في البوت وشراء حسابات تلجرام جاهزة ايضا •\n'
        f'🪐 يمكنك التواصل مع الدعم في حال كان لديك استفسار حول موضوع ما •\n\n'
        f'🚀 رصيد حسابك : {user_info["coind"]}$**',
        buttons=[
            [
                Button.inline(accent("بيع حساب"), data="sell"),
                Button.inline(accent("اسعار الدول"), data="buysr")
            ],
            [
                Button.inline(accent("سحب رصيدك "), data="withdraw"),
                Button.inline(accent("تحويل رصيد"), data="transfer")
            ],
            [
                Button.url(accent("قناة التحديثات"), url="https://t.me/AF_R_O_TO")
            ],
            [
                Button.inline(accent(" رجوع ⬅️ "), data="main")
            ]
        ]
    )
                             
@bot.on(events.CallbackQuery(pattern=b'ajxkho'))
async def nuupv_button(event):
    await event.answer(' - مرحبا بك في قسم الاشتراك الاجباري')
    await l_edit(event, '** يمكنك اضافة وحذف قناة من الاشتراك الاجباري من هذا القسم**',
                    buttons=[
                        [
                            Button.inline(accent("- اضافة قناة "), data="add_force"),
                            Button.inline(accent("- حذف قناة "), data="del_force")
                        ],
                        [
                            Button.inline(accent("رجوع ↪️"), data="adm:set")
                        ]
                    ])
@bot.on(events.CallbackQuery(pattern=b'aksgl'))
async def nuupv_button(event):
    await event.answer(' - مرحبا بك في قسم الادمن‍')
    await l_edit(event, '** يمكنك رفع ادمن وتنزيل ادمن في البوت من هذا القسم **',
                    buttons=[
                        [
                            Button.inline(accent("- رفع ادمن "), data="add_admin"),
                            Button.inline(accent("- تنزيل ادمن "), data="del_admin")
                        ],
                        [
                            Button.inline(accent("رجوع ↪️"), data="adm:usr")
                        ]
                    ])
@bot.on(events.CallbackQuery(pattern=b'ajkofgl'))
async def nuupv_button(event):
    await event.answer(' - مرحبا بك في قسم البيع والشراء .')
    await render_prices_panel(event)
@bot.on(events.CallbackQuery(pattern=b'ajkcoingl'))
async def nuupv_button(event):
    await event.answer(' -  مرحبا بك في قسم الرصيد.')
    await l_edit(event, '**- يمكنك اضافة وخصم رصيد من المستخدمين من هذا القسم  **',
                    buttons=[
                        [
                            Button.inline(accent("- اضافة نقاط "), data="add_coins"),
                            Button.inline(accent("- خصم نقاط"), data="del_coins")
                        ],
                        [
                            Button.inline(accent("رجوع ↪️"), data="adm:usr")
                        ]
                    ])            

@bot.on(events.CallbackQuery(pattern=b'bbvjls'))
async def nuupv_button(event):
    await event.answer(' - مرحبا بك في قسم الحظر.')
    await l_edit(event, '**يمكنك حظر والغاء حظر مستخدمين من البوت من هذا القسم**',
                    buttons=[
                        [
                            Button.inline(accent("- حظر مستخدم"), data="ban"),
                            Button.inline(accent("- الغاء حظر مستخدم"), data="unban")
                        ],
                        [
                            Button.inline(accent("رجوع ↪️"), data="adm:usr")
                        ]
                    ])       

@bot.on(events.CallbackQuery(pattern=b'shahn'))
async def deposit_menu(event):
    print("Deposit menu called")
    
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    _ms = get_manual_payment_settings()
    buttons = []
    if db.get("manual_charge_enabled"):
        for _m, _c in MANUAL_METHODS.items():
            if manual_method_available(_ms, _m):
                buttons.append([Button.inline(accent(f"{_c['icon']} {_c['name']}"), _c['user_data'])])
    buttons += [
        [Button.url(accent('💬 الشحن عبر مدير البوت'), 'https://t.me/J_D_D_M')],
        [Button.inline(accent('⭐ الدفع عبر النجوم'), b'chargestars')],
        [Button.inline(accent('🪙 الدفع اليدوي بالعملات الرقمية'), b'manual_crypto')],
        [Button.inline(accent('🎟 استبدال قسيمة شحن'), b'redeem_coupon')],
    ]
    await l_respond(event, 'اختر طريقة شحن الرصيد 👇', buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b'chargauto'))
async def deposit_menu(event):
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    if not db.get("auto_charge_enabled"):
        await l_respond(event, "⚠️ الشحن التلقائي معطل حاليًا.")
        return

    buttons = [
        [Button.inline(accent('BTC'), b'deposit_BTC')],
        [Button.inline(accent('USDT'), b'deposit_USDT')],
        [Button.inline(accent('LTC'), b'deposit_LTC')]
    ]
    await l_edit(event, 'اختر العملة المراد الإيداع عن طريقها 🪙', buttons=buttons)

@client.on(events.CallbackQuery(data=b'deposit_LTC'))
async def handle_currency_selection(event):
    print("Currency selection handler called")
    currency = event.data.decode().split('_')[1]
    print(f"Selected currency: {currency}")

    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    if currency not in wallet_addresses:
        await l_respond(event, 'Currency not supported.')
        return

    try:
        address_info = wallet_addresses[currency]
        address = address_info['address']
        network = address_info['network']

        buttons = [[Button.inline(accent('ارسلت المبلغ ✅'), f'enter_txid_{currency}')]]
        message = await l_respond(event, 
            f'ارسل المبلغ الذي تريد ايداعه في حسابك الى العنوان التالي ثم اضغط على زر ارسلت المبلغ 💵\n\n'
            f'🖇 العنوان :\n `{address}`\n\n🪩 الشبكة : `{network}`\n\n'
            f'⏳ وقت العملية : 60:00',
            buttons=buttons
        )

        pending_deposits[event.sender_id] = {
            'currency': currency,
            'address': address,
            'network': network,
            'message_id': message.id
        }

        await start_countdown(event.sender_id, message.id, currency, countdown_time)

    except Exception as e:
        print(f'Error fetching deposit address: {str(e)}')

@client.on(events.CallbackQuery(data=b'deposit_USDT'))
async def handle_currency_selection(event):
    print("Currency selection handler called")
    currency = event.data.decode().split('_')[1]
    print(f"Selected currency: {currency}")

    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    if currency not in wallet_addresses:
        await l_respond(event, 'Currency not supported.')
        return

    try:
        address_info = wallet_addresses[currency]
        address = address_info['address']
        network = address_info['network']

        buttons = [[Button.inline(accent('ارسلت المبلغ ✅'), f'enter_txid_{currency}')]]
        message = await l_respond(event, 
            f'ارسل المبلغ الذي تريد ايداعه في حسابك الى العنوان التالي ثم اضغط على زر ارسلت المبلغ 💵\n\n'
            f'🖇 العنوان :\n `{address}`\n\n🪩 الشبكة : `{network}`\n\n'
            f'⏳ وقت العملية : 60:00',
            buttons=buttons
        )

        pending_deposits[event.sender_id] = {
            'currency': currency,
            'address': address,
            'network': network,
            'message_id': message.id
        }

        await start_countdown(event.sender_id, message.id, currency, countdown_time)

    except Exception as e:
        print(f'Error fetching deposit address: {str(e)}')
        
@client.on(events.CallbackQuery(data=b'deposit_BTC'))
async def handle_currency_selection(event):
    print("Currency selection handler called")
    currency = event.data.decode().split('_')[1]
    print(f"Selected currency: {currency}")

    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    if currency not in wallet_addresses:
        await l_respond(event, 'Currency not supported.')
        return

    try:
        address_info = wallet_addresses[currency]
        address = address_info['address']
        network = address_info['network']

        buttons = [[Button.inline(accent('ارسلت المبلغ ✅'), f'enter_txid_{currency}')]]
        message = await l_respond(event, 
            f'ارسل المبلغ الذي تريد ايداعه في حسابك الى العنوان التالي ثم اضغط على زر ارسلت المبلغ 💵\n\n'
            f'🖇 العنوان :\n `{address}`\n\n🪩 الشبكة : `{network}`\n\n'
            f'⏳ وقت العملية : 60:00',
            buttons=buttons
        )

        pending_deposits[event.sender_id] = {
            'currency': currency,
            'address': address,
            'network': network,
            'message_id': message.id
        }

        await start_countdown(event.sender_id, message.id, currency, countdown_time)

    except Exception as e:
        print(f'Error fetching deposit address: {str(e)}')

@bot.on(events.CallbackQuery(pattern=b'enter_txid_'))
async def handle_payment_made(event):
    currency = event.data.decode().split('_')[2] 
    
    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    await l_respond(event, f'ارسل (TxID) العملية الخاصه بـ {currency}.')
    pending_deposits[event.sender_id]['awaiting_txid'] = True

@bot.on(events.NewMessage())
async def handle_txid_input(event):
    user_id = event.sender_id

    if not is_bot_enabled():
        await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
        return
    
    if user_id in pending_deposits and pending_deposits[user_id].get('awaiting_txid'):
        txid = event.text.strip() 
        currency = pending_deposits[user_id]['currency']

        try:
            transactions_history = binance_client.get_deposit_history(asset=currency)
            found_transaction = False

            for transaction in transactions_history:
                txid_from_api = transaction['txId']
                if txid == txid_from_api:
                    found_transaction = True
                    amount = float(transaction['amount'])
                    status = transaction['status']

                    if status == 1: 
                        await l_respond(event, 
                            f'تم قبول العملية ✅\n\n'
                            f'💸 المبلغ : {amount} {currency}\n'
                            f'⚡ تم اضافة المبلغ الى حسابك ✅'
                        )
                        del pending_deposits[user_id] 
                        return

            if not found_transaction:
                await l_respond(event, 'المعاملة غير ة أو لم تكتمل بعد. يرجى التحقق من txid أو المحاولة مرة أخرى بعد 15 دقيقة.')
                del pending_deposits[user_id]
                return

        except Exception as e:
            print(f'Error verifying the transaction: {str(e)}')

#غير الحقوق واثبت انك فاشل اذا تريد تنقل اذكر اسمي او اسم قناتي #

#====================#
#CH : @J_D_D_M 
#DEV : @J_D_D_M
#====================#

@client.on(events.NewMessage(func=lambda e: e.is_private and bool(e.raw_text)))
async def star_price_receiver(event):
    key = f"star_price_pending_{event.sender_id}"
    if not db.exists(key) or not db.get(key):
        return
    admins = db.get("admins") if db.exists("admins") else []
    if int(event.sender_id) != int(admin) and int(event.sender_id) not in [int(x) for x in (admins or [])]:
        db.delete(key)
        return
    text = (event.raw_text or "").strip().replace(",", ".")
    if text.lower() in ("إلغاء", "الغاء", "cancel"):
        db.delete(key)
        await l_respond(event, "❌ تم إلغاء تغيير سعر النجمة.")
        return
    try:
        value = float(text)
        if value <= 0 or value > 10:
            raise ValueError
    except Exception:
        await l_respond(event, "❌ قيمة غير صحيحة. أرسل قيمة بالدولار مثل 0.01 أو 0.02")
        return
    db.set("star_usd_value", value)
    db.delete(key)
    per_dollar = 1 / value
    await l_respond(event, f"✅ تم تغيير سعر النجمة بنجاح.\n\n⭐ 1 نجمة = {value:g}$\n💵 1$ = {per_dollar:g} نجمة")

@client.on(events.NewMessage(func=lambda e: e.is_private and bool(e.raw_text)))
async def exchange_rate_receiver(event):
    key = f"exchange_rate_pending_{event.sender_id}"
    if not db.exists(key) or not db.get(key):
        return
    admins = db.get("admins") if db.exists("admins") else []
    if int(event.sender_id) != int(admin) and int(event.sender_id) not in [int(x) for x in (admins or [])]:
        db.delete(key)
        return
    text = (event.raw_text or "").strip().replace(",", ".")
    if text.lower() in ("إلغاء", "الغاء", "cancel"):
        db.delete(key)
        await l_respond(event, "❌ تم إلغاء تغيير سعر الصرف.")
        return
    try:
        rate = float(text)
        if rate <= 0 or rate > 1000000:
            raise ValueError
    except Exception:
        await l_respond(event, "❌ سعر غير صحيح. أرسل رقمًا موجبًا، مثال: 50 أو 52.50")
        return
    db.set("usd_egp_rate", rate)
    db.delete(key)
    await l_respond(event, f"✅ تم تغيير سعر الصرف بنجاح.\n\n💱 1$ = {rate:.2f} ج.م")

# ============================================================
#  واجهة الأقسام (مستخدم + أدمن)
# ============================================================
# ============================================================
#  لوحة الأدمن: 4 أقسام رئيسية، وجوا كل قسم الأزرار القديمة بالكامل
# ============================================================
# ============================================================
#  التحقق بخطوتين الموحّد — كلمة سر واحدة تتطبق على كل الأرقام
# ============================================================
import string as _string_mod

def _two_step_password():
    try:
        return db.get("two_step_password") if db.exists("two_step_password") else "MT"
    except Exception:
        return "MT"

async def export_numbers_2fa_file(event):
    """بيبعت للأدمن ملف فيه كل أرقام البوت (كل الأقسام والدول) + التحقق بخطوتين الخاص بكل رقم."""
    uid = event.sender_id
    await event.answer("⏳ جاري إنشاء الملف...")
    lines, total, no_pw = [], 0, 0
    for cat in get_categories():
        cid = cat["id"]
        cat_lines, cat_total = [], 0
        for c in _dbget(countries_key(cid), []):
            accounts = _dbget(accounts_key(c["calling_code"], cid), [])
            if not accounts:
                continue
            cat_lines.append(f"  🌍 الدولة: {c['name']} ({c['calling_code']}) — {len(accounts)} رقم")
            for acc in accounts:
                pw = acc.get("two-step") or acc.get("password") or ""
                if not pw:
                    no_pw += 1
                cat_lines.append(f"      📱 الرقم: +{str(acc.get('phone_number', '')).lstrip('+')}")
                cat_lines.append(f"      🔒 التحقق: {pw if pw else 'لا يوجد'}")
                cat_lines.append("")
                cat_total += 1
            cat_lines.append("")
        if cat_lines:
            lines.append("=" * 50)
            lines.append(f"🗂 القسم: {cat['name']} — {cat_total} رقم")
            lines.append("=" * 50)
            lines += cat_lines
            total += cat_total
    header = [
        f"ملف أرقام البوت (المخزون الحالي) — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"إجمالي الأرقام: {total} | بدون تحقق بخطوتين: {no_pw}",
        "-" * 60, "",
    ]
    out_dir = "database/tmp_exports"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"numbers_2fa_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(header + (lines if lines else ["لا توجد أرقام في المخزون."])) + "\n")
        await client.send_file(uid, path, caption=f"🔢 أرقام البوت وتحققاتها — {total} رقم",
                               attributes=[DocumentAttributeFilename(file_name=os.path.basename(path))])
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

_TFA_RUNNING = False

def _iter_stock_keys():
    for cat in get_categories():
        cid = cat["id"]
        for c in (_dbget(countries_key(cid), []) or []):
            yield accounts_key(c["calling_code"], cid)

def _find_stock_account(phone):
    for key in _iter_stock_keys():
        for a in ((db.get(key) if db.exists(key) else None) or []):
            if str(a.get("phone_number")).lstrip("+") == str(phone).lstrip("+"):
                return key, a
    return None, None

def _tfa_menu_view():
    pw = _two_step_password()
    total = sum(len((db.get(k) if db.exists(k) else None) or []) for k in _iter_stock_keys())
    text = (
        "🔒 **التحقق بخطوتين الموحّد**\n\n"
        f"• كلمة السر العامة الحالية: `{pw}`\n"
        f"• إجمالي أرقام المخزون: {total}\n\n"
        "أي رقم بتضيفه (رقم جديد أو ملف سيشن) بياخد الكلمة دي تلقائيًا:\n"
        "لو مفيهوش تحقق بيتفعّل، ولو فيه كلمة تانية بتتغيّر ليها، كلها من غير أي تدخل منك.\n\n"
        "لو عايز تغيّرها لكل الأرقان اللي عندك فعلاً دلوقتي، دوس «توحيد كل الأرقام»."
    )
    rows = [
        [Button.inline(accent("✏️ تغيير كلمة السر العامة"), data="tfa:setpw")],
        [Button.inline(accent("🔁 توحيد كل الأرقام الحالية"), data="tfa:syncall")],
        [Button.inline(accent("🔑 ضبط رقم بكلمة قديمة يدويًا"), data="tfa:manual")],
        [Button.inline(accent("🔙 رجوع"), data="adm:num")],
    ]
    return text, rows

@client.on(events.CallbackQuery(data=b'tfa:menu'))
async def tfa_menu(event):
    if not _is_admin_user(event.sender_id):
        return await event.answer("❌ للأدمن فقط.", alert=True)
    text, rows = _tfa_menu_view()
    await l_edit(event, text, buttons=rows, parse_mode='markdown')

@client.on(events.CallbackQuery(data=b'tfa:setpw'))
async def tfa_set_password(event):
    if not _is_admin_user(event.sender_id):
        return await event.answer("❌ للأدمن فقط.", alert=True)
    await event.answer()
    try:
        async with bot.conversation(event.chat_id, timeout=180) as x:
            await x.send_message(
                f"✏️ الكلمة الحالية: `{_two_step_password()}`\n\n"
                "أرسل كلمة السر الجديدة اللي هتبقى موحّدة على كل الأرقام الجديدة.\n"
                "(هتتطبق على أي رقم بتضيفه بعد كده تلقائيًا. عشان تطبقها على الأرقام الموجودة فعلاً، "
                "دوس «توحيد كل الأرقام» بعدين.)\nأو أرسل «إلغاء».", parse_mode='markdown')
            txt = (await x.get_response()).text.strip()
            if txt in ("إلغاء", "الغاء", "cancel"):
                return await x.send_message("تم الإلغاء.")
            if len(txt) < 4:
                return await x.send_message("❌ كلمة السر قصيرة جدًا (4 أحرف على الأقل).")
            db.set("two_step_password", txt)
            global new_password
            new_password = txt
            await x.send_message(f"✅ اتسجّلت كلمة السر الجديدة: `{txt}`\n\nكل رقم هيتضاف بعد كده هياخدها تلقائي.", parse_mode='markdown')
    except asyncio.TimeoutError:
        return

@client.on(events.CallbackQuery(data=b'tfa:manual'))
async def tfa_manual_set(event):
    if not _is_admin_user(event.sender_id):
        return await event.answer("❌ للأدمن فقط.", alert=True)
    await event.answer()
    await client.send_message(event.chat_id,
        "🔑 لو رقم عليه كلمة سر قديمة إحنا مش عارفينها، اكتب:\n\n"
        "`/set2fa الرقم الكلمة_القديمة`\n"
        "مثال: `/set2fa +201000000000 OldPass123`\n\n"
        "البوت هيتأكد من الكلمة ويغيّرها فورًا لنفس كلمة السر العامة الموحّدة.", parse_mode='markdown')

@client.on(events.NewMessage(pattern=r"^/set2fa\s+\+?(\d{7,15})\s+(.+)$", func=lambda e: e.is_private))
async def set2fa_command(event):
    if not _is_admin_user(event.sender_id):
        return
    phone = event.pattern_match.group(1)
    old_pw = event.pattern_match.group(2).strip()
    try:
        await event.delete()        # عشان الباسورد ميفضلش ظاهر في الشات
    except Exception:
        pass
    key, acc = _find_stock_account(phone)
    if not acc:
        return await client.send_message(event.chat_id, f"❌ الرقم +{phone} مش موجود في مخزون البوت (الأرقام المباعة أو المحجوزة مش بتتعدّل من هنا).")
    msg = await client.send_message(event.chat_id, f"⏳ جاري ضبط التحقق بخطوتين للرقم +{phone}...")
    ok, final_pw, note = await unify_two_step(acc["session"], _pw_candidates(acc, old_pw))
    if ok:
        accs = db.get(key) if db.exists(key) else []
        for a in accs:
            if str(a.get("phone_number")).lstrip("+") == phone:
                a["two-step"] = final_pw
        db.set(key, accs)
        await msg.edit(f"✅ تم ضبط +{phone} على كلمة السر الموحّدة.")
    else:
        await msg.edit(f"❌ فشل ({note}).\nلو الكلمة اللي بعتها غلط، اتأكد منها وجرّب تاني.")

async def _tfa_sync_all(chat_id, target_pw):
    global _TFA_RUNNING
    if _TFA_RUNNING:
        return await client.send_message(chat_id, "⏳ فيه عملية توحيد شغالة حاليًا بالفعل، استنى تخلص.")
    _TFA_RUNNING = True
    try:
        total = ok = failed = 0
        fails = []
        sem = asyncio.Semaphore(5)
        for key in _iter_stock_keys():
            accounts = list((db.get(key) if db.exists(key) else None) or [])
            if not accounts:
                continue
            results = {}

            async def _one(acc):
                nonlocal ok, failed
                phone = acc.get("phone_number")
                cands = _pw_candidates(acc, target_pw)
                async with sem:
                    success, final_pw, note = await unify_two_step(acc.get("session", ""), cands, target_pw)
                if success:
                    results[phone] = final_pw
                    ok += 1
                else:
                    failed += 1
                    fails.append((phone, note))

            await asyncio.gather(*[_one(a) for a in accounts])
            if results:
                accs = db.get(key) if db.exists(key) else []
                changed = False
                for a in accs:
                    if a.get("phone_number") in results:
                        a["two-step"] = results[a["phone_number"]]
                        changed = True
                if changed:
                    db.set(key, accs)
            total += len(accounts)

        lines = [
            "✅ **انتهى توحيد التحقق بخطوتين**", "",
            f"📦 الإجمالي: {total}", f"🟢 اتوحّد: {ok}", f"🔴 فشل: {failed}",
        ]
        if fails:
            lines.append("")
            lines.append("الفاشلين (أول 15):")
            for ph, note in fails[:15]:
                lines.append(f"• +{ph} — {note}")
            lines.append("")
            lines.append("لو الكلمة القديمة معروفة، استخدم /set2fa الرقم الكلمة_القديمة.")
        await client.send_message(chat_id, "\n".join(lines), parse_mode='markdown')
    except Exception as ex:
        print("tfa sync all error:", ex)
        await client.send_message(chat_id, f"❌ حصل خطأ أثناء التوحيد: {ex}")
    finally:
        _TFA_RUNNING = False

@client.on(events.CallbackQuery(data=b'tfa:syncall'))
async def tfa_sync_all_handler(event):
    if not _is_admin_user(event.sender_id):
        return await event.answer("❌ للأدمن فقط.", alert=True)
    await event.answer("⏳ بدأ التوحيد في الخلفية، هتوصلك رسالة لما يخلص.")
    target = _two_step_password()
    asyncio.create_task(_tfa_sync_all(event.chat_id, target))

@client.on(events.CallbackQuery(data=b'export_numbers_2fa'))
async def export_numbers_2fa_handler(event):
    if not _is_admin_user(event.sender_id):
        return await event.answer("❌ للأدمن فقط.", alert=True)
    await export_numbers_2fa_file(event)

DEFAULT_UPDATES_URL = "https://t.me/AF_R_O_TO"

def updates_channel_url():
    """رابط قناة التحديثات اللي بيفتحه زر «التحديثات» (قابل للتغيير من لوحة الأدمن)."""
    try:
        v = db.get("updates_channel_url") if db.exists("updates_channel_url") else ""
    except Exception:
        v = ""
    return v or DEFAULT_UPDATES_URL

def normalize_updates_channel(text):
    """بيحوّل يوزر/رابط القناة لرابط t.me صالح. بيرجّع None لو الصيغة غلط."""
    t = (text or "").strip()
    t = re.sub(r"^https?://", "", t, flags=re.I)
    t = re.sub(r"^(www\.)?t\.me/", "", t, flags=re.I)
    t = t.split("?")[0].strip().strip("/").lstrip("@")
    if t.startswith("+") or t.lower().startswith("joinchat/"):
        return "https://t.me/" + t
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", t):
        return "https://t.me/" + t
    return None

@client.on(events.CallbackQuery(data=b'set_updates_channel'))
async def set_updates_channel(event):
    if not _is_admin_user(event.sender_id):
        return await event.answer("❌ للأدمن فقط.", alert=True)
    async with bot.conversation(event.chat_id) as x:
        await x.send_message(
            f"📢 قناة التحديثات الحالية:\n{updates_channel_url()}\n\n"
            "- ارسل يوزر او رابط القناة الجديدة.\n- او ارسل «افتراضي» للرجوع للقناة الأصلية.")
        txt = (await x.get_response()).text.strip()
        if txt in ("افتراضي", "الافتراضي", "default"):
            db.set("updates_channel_url", "")
            return await x.send_message("✅ تم الرجوع لقناة التحديثات الافتراضية.")
        url = normalize_updates_channel(txt)
        if not url:
            return await x.send_message("❌ الرابط/اليوزر غير صحيح. مثال: @channel أو https://t.me/channel")
        db.set("updates_channel_url", url)
        await x.send_message(f"✅ تم تغيير قناة التحديثات.\n{url}\n\nزر «التحديثات» في القائمة الرئيسية بقى بيفتحها.")

def _flag(key):
    try:
        return bool(db.get(key)) if db.exists(key) else False
    except Exception:
        return False

def _toggle_label(title, on):
    """زر واحد بحالتين: بيبيّن الحالة الحالية وبيقلبها بالضغط."""
    return f"{title}: 🟢 مفعّل (اضغط للتعطيل)" if on else f"{title}: 🔴 معطّل (اضغط للتفعيل)"

def _toggle_bot_state():
    global bot_enabled
    bot_enabled = not bot_enabled
    return bot_enabled

MAINT_TEXT = "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه"

def admin_main_keyboard():
    return [
        [Button.inline(accent("🔢 الأرقام والجلسات"), data="adm:num")],
        [Button.inline(accent("💰 إدارة الكاش"), data="adm:pay")],
        [Button.inline(accent("🎁 إدارة الفلوس المجانية"), data="adm:free")],
        [Button.inline(accent("👥 المستخدمين والإدارة"), data="adm:usr")],
        [Button.inline(accent("⚙️ إعدادات البوت"), data="adm:set")],
        [Button.inline(accent("💵 أسعار الحسابات"), data="selladm")],
    ]

def admin_section(section):
    """بيرجّع (نص, أزرار) لكل قسم فرعي في لوحة الأدمن."""
    back = [Button.inline(accent("🔙 رجوع"), data="admin_panel")]
    if section == "num":
        return (f"**🔢 الأرقام والجلسات**\n\n🗂 القسم الحالي: **{get_category_name()}**\n(الإضافة والحذف بتتطبق على القسم ده، غيّره من «تغيير القسم»)", [
            [Button.inline(accent("➕ إضافة رقم"), data="ca:pick:add"), Button.inline(accent("🗑 حذف رقم"), data="delaccount")],
            [Button.inline(accent("➕ إضافة دولة"), data="ca:pick:country"), Button.inline(accent("🗑 حذف دولة"), data="del_country")],
            [Button.inline(accent("📥 رفع سيشنات (دفعة واحدة)"), data="ca:pick:bulk")],
            [Button.inline(accent("🔀 تغيير القسم"), data="ca:pick:num"), Button.inline(accent("📂 إدارة الأقسام"), data="ca:mgr")],
            [Button.inline(accent("📄 ملف الأرقام والتحقق"), data="export_numbers_2fa")],
            [Button.inline(accent("🔒 التحقق بخطوتين الموحّد"), data="tfa:menu")],
            [Button.inline(accent("🔢 عدد أرقام البوت"), data="all_of_number")],
            [Button.inline(accent("💲 الأسعار"), data="ajkofgl")],
            [Button.inline(accent("🚫 تعطيل استقبال دولة"), data="stopcountry"), Button.inline(accent("📋 الدول المغلقة"), data="ttrt")],
            [Button.inline(accent("📣 قناة اثباتات التسليم"), data="set_trust_channel")],
            [Button.inline(accent("📢 قناة التحديثات"), data="set_updates_channel")],
            back])
    if section == "pay":
        return ("**💰 إدارة الكاش**\n\nطرق الشحن، العملات، أسعار الصرف، وأرصدة المستخدمين.", [
            [Button.inline(accent("💳 إدارة الدفع اليدوي"), data="manual_payment_settings")],
            [Button.inline(accent("🪙 إدارة العملات الرقمية"), data="set_crypto_wallets")],
            [Button.inline(accent("💱 سعر صرف الدولار"), data="set_exchange_rate"), Button.inline(accent("⭐ سعر النجمة"), data="set_star_price")],
            [Button.inline(accent(_toggle_label("⭐ الدفع بالنجوم", _flag("stars_charge_enabled"))), data="tg:stars")],
            [Button.inline(accent(_toggle_label("✍️ الشحن المانيوال", _flag("manual_charge_enabled"))), data="tg:manual")],
            [Button.inline(accent("💵 رصيد المستخدمين"), data="ajkcoingl")],
            back])
    if section == "free":
        return ("**🎁 إدارة الفلوس المجانية**\n\nالإحالات، أكواد النقاط وروابط النقاط.", [
            [Button.inline(accent("👥 سعر الإحالة"), data="amubnf")],
            [Button.inline(accent("🎁 إدارة استبدالات الإحالات"), data="ref_admin")],
            [Button.inline(accent("🎟 صنع كود نقاط"), data="coupon_admin")],
            [Button.inline(accent("🔗 صنع رابط نقاط"), data="pl:new"), Button.inline(accent("📋 الروابط"), data="pl:list")],
            back])
    if section == "usr":
        return ("**👥 المستخدمين والإدارة**\n\nالأعضاء، الحظر، الأدمنية والإذاعة.", [
            [Button.inline(accent("📄 إنشاء ملف الأعضاء"), data="export_users")],
            [Button.inline(accent("🚫 الحظر"), data="bbvjls"), Button.inline(accent("👮 الأدمن"), data="aksgl")],
            [Button.inline(accent("📢 اذاعة"), data="start_broadcast")],
            back])
    return (f"**⚙️ إعدادات البوت**\n\nتشغيل/إيقاف البوت، الاشتراك الاجباري، المساعد الآلي والنسخ الاحتياطي.\n\n🗄 آخر نسخة احتياطية: {_last_backup_text()}\n⏱ نسخة تلقائية كل 6 ساعات.", [
        [Button.inline(accent(_toggle_label("🤖 البوت", is_bot_enabled())), data="tg:bot")],
        [Button.inline(accent("🔒 الاشتراك الاجباري"), data="ajxkho")],
        [Button.inline(accent("🤖 إعداد مساعد الدعم الآلي"), data="ai_admin")],
        [Button.inline(accent("💾 نسخة احتياطية الآن"), data="bk:now")],
        [Button.inline(accent("📥 استعمال قاعدة بيانات (ارفع ملف)"), data="bk:upload")],
        back])


# ============================================================
#  ملف الأعضاء (زر «إنشاء ملف» في لوحة المستخدمين)
# ============================================================
async def _remember_username(event, force=False):
    """بيحفظ يوزر العضو في سجله عشان يظهر في ملف الأعضاء (بدون طلبات زيادة لو متسجل قبل كده)."""
    try:
        uid = int(event.sender_id)
        key = f"user_{uid}"
        if not db.exists(key):
            return
        rec = db.get(key)
        if not isinstance(rec, dict) or (not force and "username" in rec):
            return
        sender = await event.get_sender()
        rec["username"] = (getattr(sender, "username", None) or "")
        db.set(key, rec)
    except Exception:
        pass

async def export_users_file(event):
    uid = event.sender_id
    await event.answer("⏳ جاري إنشاء الملف...")
    keys = []
    for k in db.keys('user_%'):
        k = k[0] if isinstance(k, (tuple, list)) else k
        if re.fullmatch(r"user_\d+", str(k)):
            keys.append(str(k))
    keys.sort(key=lambda k: int(k[5:]))
    now = time.time()
    started = time.time()
    lines = []
    unknown_join = 0
    for k in keys:
        rec = db.get(k)
        if not isinstance(rec, dict):
            continue
        user_id = int(k[5:])
        username = rec.get("username")
        # لو اليوزر لسه متسجلش: نحاول نجيبه من تيليجرام (بحد أقصى 60 ثانية للملف كله)
        if username is None and time.time() - started < 60:
            try:
                ent = await asyncio.wait_for(client.get_entity(user_id), timeout=4)
                username = getattr(ent, "username", None) or ""
                rec["username"] = username
                db.set(k, rec)
            except Exception:
                username = None
        user_txt = f"@{username}" if username else ("بدون يوزر" if username == "" else "غير معروف")
        try:
            balance = money(rec.get("coins", 0))
        except Exception:
            balance = "0.00"
        refs = int(rec.get("referrals_count", 0) or 0)
        joined = rec.get("joined_at")
        if joined:
            days = max(0, int((now - float(joined)) // 86400))
            days_txt = str(days)
        else:
            days_txt = "غير مسجل"
            unknown_join += 1
        lines.append(f"ID: {user_id} | اليوزر: {user_txt} | الرصيد: {balance}$ | الإحالات: {refs} | الأيام في البوت: {days_txt}")

    header = [
        f"ملف أعضاء البوت — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"إجمالي الأعضاء: {len(lines)}",
        "-" * 60,
    ]
    footer = []
    if unknown_join:
        footer = ["-" * 60, f"ملاحظة: {unknown_join} عضو انضموا قبل تفعيل تسجيل تاريخ الانضمام، فمدة بقائهم «غير مسجل»."]
    out_dir = "database/tmp_exports"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"users_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(header + lines + footer) + "\n")
        await client.send_file(uid, path, caption=f"👥 ملف الأعضاء — {len(lines)} عضو",
                               attributes=[DocumentAttributeFilename(file_name=os.path.basename(path))])
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

async def show_category_picker(event, mode, edit=True):
    prefix = "cn" if mode == "num" else "cs"
    buttons = []
    for c in get_categories():
        buttons.append([Button.inline(accent(f"🗂 {c['name']}") + f" ({category_stock(c['id'])})", data=f"{prefix}:{c['id']}:0")])
    buttons.append([Button.inline(accent("🔙 رجوع"), data="main")])
    title = "**📲 حسابات تليجرام جاهزة**" if mode == "num" else "**💾 شراء سيشن جاهز**"
    text = f"{title}\n\n🗂 اختر القسم (الرقم بين القوسين = عدد الحسابات المتاحة):"
    if edit:
        await l_edit(event, text, parse_mode='markdown', buttons=buttons)
    else:
        await l_respond(event, text, parse_mode='markdown', buttons=buttons)

async def show_countries_page(event, mode, cid, page):
    uid = event.sender_id
    cat = get_category(cid)
    if not cat:
        await event.answer("❌ القسم غير موجود.", alert=True)
        return
    countries = _dbget(countries_key(cid), [])
    if not countries:
        await event.answer("⚠️ لا توجد دول في هذا القسم حاليًا.", alert=True)
        return
    per = 20
    total_pages = (len(countries) + per - 1) // per
    page = max(0, min(page, total_pages - 1))
    pick = "bn" if mode == "num" else "bs"
    nav = "cn" if mode == "num" else "cs"
    buttons, row = [], []
    for c in countries[page * per:(page + 1) * per]:
        if mode == "num":
            label = f"{c['name']} : {display_money(c.get('price', 0), uid)}"
        else:
            available = len(_dbget(accounts_key(c['calling_code'], cid), []))
            if available:
                label = f"{c['name']} : {display_money(c.get('price', 0), uid)} ({available})"
            else:
                label = f"{c['name']} : {tr(uid, 'غير متوفر', 'unavailable')}"
        row.append(Button.inline(accent(label), data=f"{pick}:{cid}:{c['calling_code']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    navrow = []
    if page > 0:
        navrow.append(Button.inline(accent("⏪"), data=f"{nav}:{cid}:{page - 1}"))
    if page < total_pages - 1:
        navrow.append(Button.inline(accent("⏩"), data=f"{nav}:{cid}:{page + 1}"))
    if navrow:
        buttons.append(navrow)
    buttons.append([Button.inline(accent("AUTO 🔍"), data=f"cf:{'n' if mode == 'num' else 's'}:{cid}")])
    buttons.append([Button.inline(accent("رجوع ↪️"), data="buy" if mode == "num" else "bsbuy")])
    if mode == "num":
        text = f"🗂 **القسم : {cat['name']}**\n\n" + msgs['COUNTRY_LIST']
    else:
        text = (f"**💾 شراء سيشن جاهز**\n🗂 **القسم : {cat['name']}**\n\n"
                "- اختر الدولة اللي عايز تشتري سيشن منها، هتستلم ملف السيشن (.session) فورًا بعد الدفع.")
    await l_edit(event, text, parse_mode='markdown', buttons=buttons)

async def _confirm_purchase(event, mode, cid, code):
    uid = event.sender_id
    c, cat = find_country(cid, code), get_category(cid)
    if not c or not cat:
        await event.answer("❌ الدولة غير موجودة.", alert=True)
        return
    price = money_value(c.get('price', 0))
    back = f"{'cn' if mode == 'num' else 'cs'}:{cid}:0"

    if mode == "ses":
        accounts = _dbget(accounts_key(code, cid), [])
        available = len(accounts)
        if available <= 0:
            await event.answer("- لا توجد أي حسابات في هذه الدولة.", alert=True)
            return
        # المستخدم بيختار كمية السيشنات اللي عايز يشتريها من المتاح
        qty = 1
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(
                f"💾 عدد السيشنات المتاحة في {c['name']}: **{available}**\n\n"
                "ابعت عدد السيشنات اللي عايز تشتريها.\n"
                "لإلغاء العملية اكتب: إلغاء"
            )
            for _ in range(10):
                resp = await x.get_response()
                txt = (getattr(resp, "text", "") or "").strip()
                if txt in ("الغاء", "إلغاء", "cancel", "Cancel"):
                    await x.send_message("❌ تم إلغاء عملية الشراء.")
                    return
                try:
                    qty = int(txt)
                except Exception:
                    await x.send_message("❌ ابعت عدد صحيح. مثال: 1 أو 3")
                    continue
                if qty < 1:
                    await x.send_message("❌ العدد لازم يكون 1 على الأقل.")
                    continue
                if qty > available:
                    await x.send_message(
                        f"❌ الكمية دي مش متوفرة. المتاح حاليًا: **{available}**\n"
                        "ابعت كمية تساوي المتاح أو أقل."
                    )
                    continue
                break
            else:
                await x.send_message("❌ عدد المحاولات انتهى، ابدأ العملية من جديد.")
                return
        total = money_value(price * qty)
        if float(get_user_record(uid)['coins']) < total:
            await event.answer("- رصيدك لا يكفي لشراء العدد المطلوب من السيشنات.", alert=True)
            return
        go = f"bsc:{cid}:{code}:{qty}"
        await l_edit(event,
            f"**✅︙هل انت متاكد من شراء {qty} سيشن ؟**\n⌁︙معلومات الحساب :-\n\n"
            f"🗂︙ القسم: {cat['name']}\n🌎︙ الدولة: {c['name']}\n"
            f"🔢︙ العدد: {qty}\n💰︙ سعر السيشن: {display_money(price, uid)}\n"
            f"💵︙ الإجمالي: {display_money(total, uid)}\n\n"
            "**🚀︙اذا تريد الشراء اضغط (تأكيد ✅) و اذا لا تريد اضغط (الغاء ❌)**",
            parse_mode='markdown',
            buttons=[[Button.inline(accent("الغاء ❌"), data=back), Button.inline(accent("تأكيد ✅"), data=go)]])
        return

    if float(get_user_record(uid)['coins']) < price:
        await event.answer("- رصيدك لا يكفي لشراء حساب من هذه الدولة.", alert=True)
        return
    if not _dbget(accounts_key(code, cid), []):
        await event.answer("- لا توجد أي حسابات في هذه الدولة.", alert=True)
        return
    go = f"bnc:{cid}:{code}"
    await l_edit(event,
        f"**✅︙هل انت متاكد من شراء الرقم ؟**\n⌁︙معلومات الحساب :-\n\n"
        f"🗂︙ القسم: {cat['name']}\n🌎︙ الدولة: {c['name']}\n💰︙ السعر : {display_money(price, uid)}\n\n"
        "**🚀︙اذا تريد الشراء اضغط (تأكيد ✅) و اذا لا تريد اضغط (الغاء ❌)**",
        parse_mode='markdown',
        buttons=[[Button.inline(accent("الغاء ❌"), data=back), Button.inline(accent("تأكيد ✅"), data=go)]])

async def _purchase_number(event, uid, cid, code):
    c, cat = find_country(cid, code), get_category(cid)
    if not c or not cat:
        return await event.answer("❌ الدولة غير موجودة.", alert=True)
    price = money_value(c.get('price', 0))
    acc = get_user_record(uid)
    if float(acc['coins']) < price:
        return await event.answer("- رصيدك لا يكفي لشراء حساب من هذه الدولة.", alert=True)
    i = take_random_account(code, cid)   # بيتشال من المخزون فورًا عشان ميتباعش مرتين
    if i is None:
        return await event.answer("- لا توجد حسابات في هذه الدولة", alert=True)
    acc['coins'] = money_value(float(acc['coins']) - price)
    db.set(f"user_{uid}", acc)
    i = dict(i)
    i.update({"type": cat['name'], "country_name": c['name'], "cid": cid})
    db.set(f"reserved_account_{i['phone_number']}", i)
    await post_trust_number_purchase(c['name'], i['phone_number'], f"{price:g}", uid)
    await reward_referral_for_purchase(uid)
    text = (f"- الرقم : `{i['phone_number']}`\n🗂 القسم : {cat['name']}\n\n"
            "**• حاول تسجيل الدخول بالرقم في تطبيق تليجرام ثم اضغط على زر جلب الكود**")
    keyboard = [[Button.inline(accent("جلب الكود"),
                 data=f"get:{i['phone_number']}:{code}:{_short_cb(c['name'])}:{price:g}")]]
    await l_edit(event, text, buttons=keyboard)

async def _purchase_session(event, uid, cid, code, qty=1):
    try:
        qty = max(1, int(qty))
    except Exception:
        qty = 1
    c, cat = find_country(cid, code), get_category(cid)
    if not c or not cat:
        return await event.answer("❌ الدولة غير موجودة.", alert=True)
    price_each = money_value(c.get('price', 0))
    total = money_value(price_each * qty)
    name = c['name']
    available = len(_dbget(accounts_key(code, cid), []))
    if available < qty:
        return await event.answer(f"- الكمية دي مش متوفرة. المتاح حاليًا: {available}", alert=True)
    acc = get_user_record(uid)
    if float(acc['coins']) < total:
        return await event.answer("- رصيدك لا يكفي لشراء العدد المطلوب من السيشنات.", alert=True)

    # بنحجز كل الأرقام المطلوبة مرة واحدة الأول، عشان لو الكمية اتغيّرت منتصف الطلب منسيبش حاجة معلّقة
    taken = []
    for _ in range(qty):
        i = take_random_account(code, cid)
        if i is None:
            break
        taken.append(i)
    if len(taken) < qty:
        for t in taken:
            rest = _dbget(accounts_key(code, cid), [])
            rest.append(t)
            db.set(accounts_key(code, cid), rest)
        return await event.answer("- الكمية دي لم تعد متوفرة، حاول تاني.", alert=True)

    acc['coins'] = money_value(float(acc['coins']) - total)
    db.set(f"user_{uid}", acc)
    await event.answer("⏳ جاري تجهيز ملفات السيشن...")

    tmp_dir = "database/tmp_sessions"
    os.makedirs(tmp_dir, exist_ok=True)
    delivered = []
    failed = 0
    last_info_text = ""

    async def _deliver_one(i):
        tmp_base = os.path.join(tmp_dir, f"deliver_{uuid.uuid4().hex}")
        try:
            # بنتحقق من حالة التحقق بخطوتين الحقيقية على الحساب من غير ما نغيّرها أو نفعّلها خالص.
            _cands = _pw_candidates(i)
            _known = await ensure_account_two_step(i['session'], _cands)
            if _known:
                i = dict(i)
                i['two-step'] = _known
            try:
                fresh = await asyncio.wait_for(make_fresh_session(i['session'], _cands), 120)
            except Exception as _fe:
                print("fresh session on purchase failed, delivering original session instead:", type(_fe).__name__, _fe)
                fresh = i['session']
            file_path = string_session_to_file(fresh, tmp_base)
            has_pw = await get_real_two_step_status(fresh)
            two_step_for_caption = _two_step_display(has_pw, acc_pw(i))
            zip_path = tmp_base + ".zip"
            try:
                meta = build_session_json(dict(i, session=fresh), f"{i['phone_number']}.session")
                meta["has_2fa"] = bool(has_pw)
                meta["twoFA"] = acc_pw(i) or ("غير معروفة" if has_pw else "")
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(file_path, f"{i['phone_number']}.session")
                    zf.writestr(f"{i['phone_number']}.json", json.dumps(meta, ensure_ascii=False, indent=2))
                caption = (f"**💾 تم شراء السيشن بنجاح ✅**\nالرقم : `{i['phone_number']}`\n"
                           f"القسم : {cat['name']}\nالدولة : {name}\nالسعر : {price_each:g} $\n"
                           f"🔒 التحقق بخطوتين : {two_step_for_caption}\n\n"
                           "📦 الملف المرفق zip فيه ملف السيشن (.session) + ملف المعلومات الكامل (.json).")
                await client.send_file(uid, zip_path, caption=caption, parse_mode='markdown',
                                       attributes=[DocumentAttributeFilename(file_name=f"{i['phone_number']}.zip")])
            except Exception as _ze:
                print("session zip delivery failed, sending session file alone:", type(_ze).__name__, _ze)
                caption = (f"**💾 تم شراء السيشن بنجاح ✅**\nالرقم : `{i['phone_number']}`\n"
                           f"القسم : {cat['name']}\nالدولة : {name}\nالسعر : {price_each:g} $\n"
                           f"🔒 التحقق بخطوتين : {two_step_for_caption}")
                await client.send_file(uid, file_path, caption=caption, parse_mode='markdown',
                                       attributes=[DocumentAttributeFilename(file_name=f"{i['phone_number']}.session")])
            i2 = dict(i)
            i2.update({"type": cat['name'], "country_name": name, "cid": cid, "has_2fa": bool(has_pw)})
            db.set(f"reserved_account_{i2['phone_number']}", i2)
            return (i2, two_step_for_caption)
        finally:
            for ext in (".session", ".session-journal", ".json", ".zip"):
                try:
                    if os.path.exists(tmp_base + ext):
                        os.remove(tmp_base + ext)
                except Exception:
                    pass

    for i in taken:
        try:
            i2, two_step_for_caption = await _deliver_one(i)
            delivered.append(i2)
            if qty == 1:
                last_info_text = (
                    f"🌍 الدولة : {name}\n"
                    f"🔥 النوع : {cat['name']}\n"
                    f"- الرقم : `{i2['phone_number']}`\n"
                    f"🔒 التحقق بخطوتين : {two_step_for_caption}\n\n"
                    "**• ملف السيشن اتبعتلك في الخاص. تقدر تطلب كود تسجيل الدخول، أو تاخد الحساب بالكامل لنفسك بـ«إزالة جلسة البوت من الرقم».**"
                )
        except Exception as ex:
            print("session delivery error:", ex)
            failed += 1
            # فشل تجهيز الرقم ده: نرجّعه للمخزون
            rest = _dbget(accounts_key(code, cid), [])
            rest.append(i)
            db.set(accounts_key(code, cid), rest)

    if failed:
        refund = money_value(price_each * failed)
        acc = get_user_record(uid)
        acc['coins'] = money_value(float(acc['coins']) + refund)
        db.set(f"user_{uid}", acc)

    if not delivered:
        return await event.answer("❌ حصل خطأ أثناء تجهيز السيشنات، تم استرجاع رصيدك.", alert=True)

    if qty == 1:
        i2 = delivered[0]
        keyboard = [
            [Button.inline(accent("طلب كود تسجيل 📩"),
             data=f"get:{i2['phone_number']}:{code}:{_short_cb(name)}:{price_each:g}")],
            [Button.inline(accent("إزالة جلسة البوت من الرقم ❌"), data=f"delsess:{i2['phone_number']}:{code}")],
            [Button.inline(accent("رجوع ↪️"), data="main")],
        ]
        await l_edit(event, last_info_text, parse_mode='markdown', buttons=keyboard)
    else:
        phones = "\n".join(f"• `{x['phone_number']}`" for x in delivered)
        extra = f"\n\n⚠️ فشل تجهيز {failed} سيشن وتم استرجاع قيمتهم." if failed else ""
        await l_edit(event,
            f"**✅ تم شراء {len(delivered)} سيشن بنجاح**\n\n"
            f"🗂 القسم : {cat['name']}\n🌍 الدولة : {name}\n"
            f"💵 سعر السيشن : {display_money(price_each, uid)}\n"
            f"💰 الإجمالي : {display_money(money_value(price_each * len(delivered)), uid)}\n\n"
            f"📱 السيشنات:\n{phones}"
            f"{extra}\n\n"
            "📦 كل سيشن اتبعت في رسالة منفصلة بملف zip فيه السيشن + المعلومات الكاملة.",
            parse_mode='markdown',
            buttons=[[Button.inline(accent("رجوع ↪️"), data="main")]])
    await post_trust_session(name, f"{price_each * len(delivered):g}", uid, count=len(delivered),
                             phone=delivered[0]['phone_number'] if len(delivered) == 1 else None)

# ---------- أدمن ----------
async def show_admin_category_pick(event, ret="num"):
    cur = get_admin_cat(event.sender_id)
    buttons = []
    for c in get_categories():
        mark = " ✅" if c['id'] == cur else ""
        buttons.append([Button.inline(accent(f"🗂 {c['name']}") + mark, data=f"ca:set:{c['id']}:{ret}")])
    buttons.append([Button.inline(accent("🔙 رجوع"), data="adm:num")])
    titles = {"add": "🗂 **اختر القسم اللي هتضيف فيه الرقم**\n\nبعد كده هتختار الدولة وتضيف الرقم.",
              "bulk": "🗂 **اختر القسم اللي هترفع فيه السيشنات**\n\nبعد كده ابعت ملفات السيشن وهتتعرّف الدول تلقائي.",
              "country": "🗂 **اختر القسم اللي هتضيف فيه الدولة**\n\nبعد كده ابعت رمز الدولة."}
    await l_edit(event, titles.get(ret, "🗂 **اختر القسم اللي هتتحكم فيه**\n\nكل تعديلات الدول والأرقام والأسعار هتتطبق على القسم المختار."),
                 parse_mode='markdown', buttons=buttons)

async def render_prices_panel(event):
    cid = get_admin_cat(event.sender_id)
    await l_edit(event,
        f"**- يمكنك التحكم في اسعار البيع والشراء من هذا القسم**\n\n🗂 القسم الحالي : **{get_category_name(cid)}**",
        parse_mode='markdown',
        buttons=[
            [Button.inline(accent("- سعر الشراء"), data="change_price"), Button.inline(accent("- سعر البيع"), data="change_sell_price")],
            [Button.inline(accent("🔀 تغيير القسم"), data="ca:pick:prc")],
            [Button.inline(accent("رجوع ↪️"), data="adm:num")],
        ])

async def _show_cat_mgr(event):
    lines = ["📂 **إدارة الأقسام**", ""]
    for c in get_categories():
        lines.append(f"🗂 {c['name']} — {len(_dbget(countries_key(c['id']), []))} دولة / {category_stock(c['id'])} حساب")
    await l_edit(event, "\n".join(lines), parse_mode='markdown', buttons=[
        [Button.inline(accent("➕ إضافة قسم"), data="ca:add")],
        [Button.inline(accent("✏️ تعديل اسم قسم"), data="ca:ren")],
        [Button.inline(accent("🗑 حذف قسم"), data="ca:del")],
        [Button.inline(accent("🔙 رجوع"), data="adm:num")],
    ])

def _cat_list_buttons(prefix):
    rows = [[Button.inline(accent(f"🗂 {c['name']}"), data=f"{prefix}:{c['id']}")] for c in get_categories()]
    rows.append([Button.inline(accent("🔙 رجوع"), data="ca:mgr")])
    return rows

async def category_router(event, data, user_id):
    """بيعالج أزرار الأقسام. بيرجّع True لو الزرار بتاعه."""
    # ----- المستخدم -----
    user_prefixes = ("cn:", "cs:", "bn:", "bnc:", "bs:", "bsc:", "cf:")
    if data in ("buy", "back", "bsbuy") or data.startswith(user_prefixes):
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return True
        if data in ("buy", "back"):
            await show_category_picker(event, "num")
        elif data == "bsbuy":
            await show_category_picker(event, "ses")
        elif data.startswith(("cn:", "cs:")):
            kind, cid, page = data.split(":", 2)
            await show_countries_page(event, "num" if kind == "cn" else "ses", cid, int(page))
        elif data.startswith(("bn:", "bs:")):
            kind, cid, code = data.split(":", 2)
            await _confirm_purchase(event, "num" if kind == "bn" else "ses", cid, code)
        elif data.startswith("bnc:"):
            _, cid, code = data.split(":", 2)
            await _purchase_number(event, user_id, cid, code)
        elif data.startswith("bsc:"):
            parts = data.split(":")
            cid, code = parts[1], parts[2]
            qty = int(parts[3]) if len(parts) > 3 else 1
            await _purchase_session(event, user_id, cid, code, qty)
        elif data.startswith("cf:"):
            _, m, cid = data.split(":", 2)
            async with bot.conversation(event.chat_id) as x:
                await x.send_message("🔎 | ابعت رمز الدولة ... مثال +20.")
                code = (await x.get_response()).text.strip()
                if code and not code.startswith("+"):
                    code = "+" + code
                c = find_country(cid, code)
                if not c:
                    await x.send_message("- هذه الدولة غير موجودة في القسم ده.")
                    return True
                pick = "bn" if m == "n" else "bs"
                await x.send_message(
                    f"- الدولة : {c['name']}\n- رمز الدولة : {c['calling_code']}\n- السعر : {display_money(c.get('price', 0), user_id)}",
                    buttons=[[Button.inline(accent("شراء 🛒"), data=f"{pick}:{cid}:{c['calling_code']}")]])
        return True

    # ----- الأدمن -----
    if data == "export_users":
        if not _is_admin_user(user_id):
            await event.answer("❌ للأدمن فقط.", alert=True)
            return True
        await export_users_file(event)
        return True
    if data.startswith("tg:"):
        if not _is_admin_user(user_id):
            await event.answer("❌ للأدمن فقط.", alert=True)
            return True
        kind = data.split(":", 1)[1]
        if kind == "bot":
            on = _toggle_bot_state()
            await event.answer("تم تفعيل البوت ✅" if on else "تم تعطيل البوت ❌")
            section = "set"
        elif kind in ("stars", "manual"):
            key = "stars_charge_enabled" if kind == "stars" else "manual_charge_enabled"
            on = not _flag(key)
            db.set(key, on)
            name = "الدفع بالنجوم" if kind == "stars" else "الشحن المانيوال"
            await event.answer(f"تم تفعيل {name} ✅" if on else f"تم تعطيل {name} ❌")
            section = "pay"
        else:
            return True
        text, buttons = admin_section(section)
        await l_edit(event, text, parse_mode='markdown', buttons=buttons)
        return True
    if data.startswith("bk:"):
        if not _is_admin_user(user_id):
            await event.answer("❌ للأدمن فقط.", alert=True)
            return True
        await backup_router(event, data.split(":", 1)[1], user_id)
        return True
    if data.startswith("adm:"):
        if not _is_admin_user(user_id):
            await event.answer("❌ للأدمن فقط.", alert=True)
            return True
        text, buttons = admin_section(data.split(":", 1)[1])
        await l_edit(event, text, parse_mode='markdown', buttons=buttons)
        return True
    if not data.startswith("ca:"):
        return False
    if not _is_admin_user(user_id):
        await event.answer("❌ للأدمن فقط.", alert=True)
        return True
    parts = data.split(":")
    action = parts[1]
    if action == "pick":
        await show_admin_category_pick(event, parts[2] if len(parts) > 2 else "num")
    elif action == "set":
        cid, ret = parts[2], (parts[3] if len(parts) > 3 else "num")
        if not get_category(cid):
            await event.answer("❌ القسم غير موجود.", alert=True)
            return True
        db.set(f"admin_cat_{user_id}", cid)
        CUR_CAT.set(cid)
        if ret == "prc":
            await render_prices_panel(event)
        elif ret == "add":
            await show_add_number_countries(event, cid)
        elif ret == "bulk":
            await bulk_sessions_start(event)
        elif ret == "country":
            await start_add_country(event, cid)
        else:
            await render_numbers_panel(event)
    elif action == "mgr":
        await _show_cat_mgr(event)
    elif action == "add":
        async with bot.conversation(event.chat_id) as x:
            await x.send_message("✏️ أرسل اسم القسم الجديد (مثال: سليم، VIP، قديم ...)")
            name = (await x.get_response()).text.strip()
            if not name or len(name) > 30:
                await x.send_message("❌ الاسم لازم يكون من 1 إلى 30 حرف.")
                return True
            if any(c['name'].lower() == name.lower() for c in get_categories()):
                await x.send_message("❌ فيه قسم بنفس الاسم بالفعل.")
                return True
            cid = "k" + secrets.token_hex(3)
            cats = get_categories()
            cats.append({"id": cid, "name": name})
            db.set("categories", cats)
            db.set(countries_key(cid), [])
            db.set(f"admin_cat_{user_id}", cid)
            await x.send_message(f"✅ تم إضافة القسم «{name}» وده بقى القسم الحالي.\nدلوقتي من «اعدادات الارقام ← اضافة دولة» ضيف دوله وأسعاره.",
                                 buttons=[[Button.inline(accent("📂 إدارة الأقسام"), data="ca:mgr")]])
    elif action == "ren" and len(parts) == 2:
        await l_edit(event, "✏️ اختر القسم اللي تعدّل اسمه:", buttons=_cat_list_buttons("ca:rn"))
    elif action == "rn":
        cid = parts[2]
        cat = get_category(cid)
        if not cat:
            await event.answer("❌ القسم غير موجود.", alert=True)
            return True
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"✏️ أرسل الاسم الجديد للقسم «{cat['name']}»")
            name = (await x.get_response()).text.strip()
            if not name or len(name) > 30:
                await x.send_message("❌ الاسم لازم يكون من 1 إلى 30 حرف.")
                return True
            cats = get_categories()
            for c in cats:
                if c['id'] == cid:
                    c['name'] = name
            db.set("categories", cats)
            await x.send_message(f"✅ تم تغيير الاسم إلى «{name}».",
                                 buttons=[[Button.inline(accent("📂 إدارة الأقسام"), data="ca:mgr")]])
    elif action == "del" and len(parts) == 2:
        await l_edit(event, "🗑 اختر القسم اللي تحذفه (لازم يكون من غير دول):", buttons=_cat_list_buttons("ca:dl"))
    elif action == "dl":
        cid = parts[2]
        if not get_category(cid):
            await event.answer("❌ القسم غير موجود.", alert=True)
            return True
        if len(get_categories()) <= 1:
            await event.answer("❌ لازم يفضل قسم واحد على الأقل.", alert=True)
            return True
        if _dbget(countries_key(cid), []):
            await event.answer("❌ القسم فيه دول. احذف دوله الأول من «اعدادات الارقام ← حذف دولة».", alert=True)
            return True
        db.set("categories", [c for c in get_categories() if c['id'] != cid])
        if cid != DEFAULT_CAT and db.exists(countries_key(cid)):
            db.delete(countries_key(cid))
        await event.answer("✅ تم حذف القسم.")
        await _show_cat_mgr(event)
    return True


@client.on(events.callbackquery.CallbackQuery())
async def start_lis(event):
    data = event.data.decode('utf-8')
    user_id = event.chat_id
    bans = get_banned_ids()
    global new_password
    # الأدمن بيشتغل على القسم اللي اختاره؛ المستخدم العادي بيحدد القسم من الزرار نفسه
    CUR_CAT.set(get_admin_cat(user_id) if _is_admin_user(user_id) else None)
    await _remember_username(event)

    # Banned users and users who have not completed forced subscription
    # cannot use callback-based sections. Admins are exempt.
    if user_id in bans:
        await event.answer("🚫 أنت محظور من استخدام البوت.", alert=True)
        return

    if (not is_bot_enabled() and not _is_admin_user(user_id)
            and data != "check_force" and not data.startswith(("get:", "delsess:", "captcha_"))):
        await l_edit(event, MAINT_TEXT)
        return

    admins_now = db.get("admins") if db.exists("admins") else []
    if user_id != admin and user_id not in admins_now and data != "check_force":
        force_ok, missing_force = await check_force_subscription(user_id)
        if not force_ok:
            await l_edit(event, 
                "🔒 **الاشتراك الإجباري**\n\n"
                "اشترك في القنوات المطلوبة ثم اضغط «تحقق من الاشتراك»."
                , buttons=force_buttons(missing_force)
            )
            return

    if data == "check_force":
        force_ok, missing_force = await check_force_subscription(user_id)
        if force_ok:
            await event.answer("✅ تم التحقق من اشتراكك.", alert=True)
            if not db.get(f"captcha_verified_{user_id}"):
                await show_captcha_buttons(event)
            else:
                coins = db.get(f"user_{user_id}").get("coins", 0) if db.exists(f"user_{user_id}") else 0
                await l_edit(event, client_main_panel(user_id), parse_mode='markdown', buttons=buttonsr(user_id))
        else:
            await event.answer("❌ لم تكتمل كل الاشتراكات المطلوبة.", alert=True)
            await l_edit(event, 
                "🔒 **الاشتراك الإجباري**\n\n"
                "اشترك في القنوات المطلوبة ثم اضغط «تحقق من الاشتراك»."
                , buttons=force_buttons(missing_force)
            )
        return
    if await category_router(event, data, user_id):
        return

    if data == "change_sell_price":
        countries = db.get(countries_key())
        buttons = []
        row = []
        for code in countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['sell_price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name} : {price} $"), data=f"chs_{calling_code}_{name}_{price}"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name} : {price} $"), data=f"chs_{calling_code}_{name}_{price}")]
        if row:
            buttons.append(row)
        
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="adm:num")])
        await l_edit(event, f"- اختر البلد الذي تريد تغيير سعره \n سعر البيع هو السعر المجاور لاسم الدولة\n🗂 القسم : {cat_label()}", parse_mode='markdown', buttons=buttons)
        return 
    
    if data == "confirms_broadcast":
        pending = db.get(f"pending_broadcast_{admin}") if db.exists(f"pending_broadcast_{admin}") else None
        if not pending or not pending.get("message"):
            await event.answer("❌ لا توجد رسالة إذاعة معلقة.", alert=True)
            return
        message = pending["message"]
        db.delete(f"pending_broadcast_{admin}")
        await broadcast_message(event, message)
        return

    if data == "cancel_broadcast":
        db.delete(f"pending_broadcast_{admin}")
        await l_respond(event, "❌ | تم إلغاء الإرسال.")
        return
    
    if data.startswith("chs_"):
        calling_code = data.split('_')[1]
        name = data.split('_')[2]
        price = data.split('_')[3]
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"- أرسل الآن سعر البيع الجديد الذي تريد تعيينه لدولة {name}")
            ch = await x.get_response()
            try:
                price = float(ch.text)
            except:
                await x.send_message(f"- الرجاء ارسال السعر ارقام فقط")
                return
            countries = db.get(countries_key())
            for i in countries:
                if calling_code == i['calling_code']:
                    i['sell_price'] = price
                    db.set(countries_key(), countries)
                    await x.send_message(f"- تم تغيير سعر دولة {name} الى {price}$")
                    return
            await x.send_message(f"- حدث خطأ أثناء تغيير سعر الدولة ❌")
    
    if data.startswith("captcha_"):
        selected_code = data.split("_")[1]
        
        correct_code = db.get(f"captcha_{user_id}")
        
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        if selected_code == correct_code:
            db.set(f"captcha_verified_{user_id}", True)
            if not db.exists(f"user_{user_id}"):
                pending_ref = db.get(f"pending_referral_{user_id}") if db.exists(f"pending_referral_{user_id}") else None
                db.set(f"user_{user_id}", {"coins": 0.0, "id": user_id, "password": "default_password", "joined_at": time.time()})
                if pending_ref:
                    await process_referral_for_user(user_id, pending_ref)
                if db.exists(f"pending_referral_{user_id}"):
                    db.delete(f"pending_referral_{user_id}")
                await l_edit(event, client_main_panel(user_id), parse_mode='markdown', buttons=buttonsr(user_id))
            else:
                coins = db.get(f"user_{user_id}").get("coins", 0)
                await l_edit(event, client_main_panel(user_id), parse_mode='markdown', buttons=buttonsr(user_id))
            await event.answer("✅ تم التحقق بنجاح، أهلاً بك!", alert=True)
        else:
            await event.answer("❌ الكود غير صحيح!", alert=True)
            await show_captcha_buttons(event)
        return
    
    if data == "login":
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        if db.exists(f"user_{user_id}"):
            await l_respond(event, "🔑 | الرجاء إدخال كلمة سر حسابك")
            response = await event.get_response()
            password = response.text
            user_data = db.get(f"user_{user_id}")
            if "password" in user_data and user_data["password"] == password:
                await l_respond(event, "✅ | تم تسجيل الدخول بنجاح")
            else:
                await l_respond(event, "❌ | كلمة السر غير صحيحة")
        else:
            await l_respond(event, "❌ | ليس لديك حساب، الرجاء إنشاء حساب أولاً.")
    
    if data == "create_account":
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
            
        await show_captcha_buttons(event)
    
    if data == "change_code":
        verification_code = generate_verification_code()
        generate_verification_image(verification_code)
        await l_respond(event, "📷 | تم تغيير كود التحقق، الرجاء إدخال الكود الجديد:", file="verification_code.png")
        response = await event.get_response()
        if response.text == verification_code:
            db.set(f"user_{user_id}", {"coins": 0, "id": user_id, "password": "default_password", "joined_at": time.time()})
            await l_respond(event, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
            await l_respond(event, "✅ | تم إنشاء حسابك بنجاح\n\n شكرًا لاستخدامك بوت عفروتو.")
        else:
            await l_respond(event, "❌ | كود التحقق غير صحيح!")
    
    if data == "change_price":
        countries = db.get(countries_key())
        buttons = []
        row = []
        for code in countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name} : {price} $"), data=f"chg_{calling_code}_{name}_{price}"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name} : {price} $"), data=f"chg_{calling_code}_{name}_{price}")]
        if row:
            buttons.append(row)
        
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="adm:num")])
        await l_edit(event, f"- اختر الدولة الذي تريد تغيير سعرها\n🗂 القسم : {cat_label()}", parse_mode='markdown', buttons=buttons)
        return 
    
    if data.startswith("chg_"):
        calling_code = data.split('_')[1]
        name = data.split('_')[2]
        price = data.split('_')[3]
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"- أرسل الآن السعر الجديد الذي تريد تعيينه لدولة {name}")
            ch = await x.get_response()
            try:
                price = float(ch.text)
            except:
                await x.send_message(f"- ارسل السعر ارقام فقط")
                return
            countries = db.get(countries_key())
            for i in countries:
                if calling_code == i['calling_code']:
                    i['price'] = price
                    db.set(countries_key(), countries)
                    await x.send_message(f"- تم تغيير سعر دولة {name} الى {price}")
                    return
            await x.send_message(f"- حدث خطأ أثناء تغيير سعر الدولة ❌")
            
    if data == "ttrt":
        countries = db.get(countries_key()) or []

        blockeds = [
            (code['calling_code'], code['name'])
            for code in countries
            if db.get(stops_key(code['calling_code']))
        ]

        if not blockeds:
            await event.answer("🚫 لا توجد دول محظورة حاليًا.", alert=True)
            return

        buttons = [
            Button.inline(accent(f"{name}"), data=f"selectyu_{calling_code}")
            for calling_code, name in blockeds
        ]

        buttons = [buttons[i:i+2] for i in range(0, len(buttons), 2)]

        buttons.append([Button.inline(accent("🔙 رجوع"), data="adm:num")])

        await l_edit(event, 
            "📋 **قائمة الدول المحظور استقبال أرقام منها**\n\n"
            "- اضغط على زر الدولة للتحكم بها",
            buttons=buttons
        )

    if data.startswith("selectyu_"):
        calling_code = data.replace("selectyu_", "")
        country_name = next(
            (code['name'] for code in db.get(countries_key()) if code['calling_code'] == calling_code),
            None
        )

        if country_name:
            buttons = [
                [Button.inline(accent("تفعيل استقبال الأرقام"), data=f"activatery_{calling_code}")],
                [Button.inline(accent("🔙 رجوع"), data="ttrt")]
            ]
            await l_edit(event, 
                f"**اختر ما تريد تنفيذه على الدولة المحظورة**\n\n- اسم الدولة : {country_name}\n\n",
                buttons=buttons
            )
        else:
            await event.answer("❌ الدولة غير ة في القائمة.", alert=True)

    if data.startswith("activatery_"):
        calling_code = data.replace("activatery_", "")
        country_name = next(
            (code['name'] for code in db.get(countries_key()) if code['calling_code'] == calling_code),
            None
        )

        if country_name:
            db.set(stops_key(calling_code), False)
            await l_edit(event, 
                f"✅ **تم تفعيل استقبال الأرقام من دولة {country_name} بنجاح **",
                buttons=[
                    [Button.inline(accent("🔙 رجوع"), data="ttrt")]
                ]
            )
        else:
            await event.answer("❌ الدولة غير ة في القائمة.", alert=True)
            
    if data == "add_force":
        async with bot.conversation(event.chat_id) as x:
            force = db.get("force")
            await x.send_message(f"- ارسل يوزر او رابط القناة.")
            ch = await x.get_response()
            channel = ch.text.replace('https://t.me/', '').replace('@', '').replace(" ", "")
            if channel in force:
                await x.send_message(f"- هذه القناة مضافة بالفعل.")
                return
            force.append(channel)
            db.set("force", force)
            await x.send_message(f"- تم اضافة القناة للاشتراك الاجباري بنجاح ✅")
            return
            
    if data == "del_force":
        async with bot.conversation(event.chat_id) as x:
            force = db.get("force")
            await x.send_message(f"- ارسل يوزر او رابط القناة المراد حذفها من الاشتراك الاجباري")
            ch = await x.get_response()
            channel = ch.text.replace('https://t.me/', '').replace('@', '').replace(" ", "")
            if channel not in force:
                await x.send_message(f"- هذه القناة غير مضافة بالفعل")
                return
            force.remove(channel)
            db.set("force", force)
            await x.send_message(f"- تم حذف القناة بنجاح 🗑")
            return

    if data == "sell":
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        await l_edit(event, f"-")
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"• ارسل الان رقم الهاتف الذي تريد بيعه\n• مثال: \n+201211000000")
            ch = await x.get_response()
            phone_number = ch.text.replace("+", "").replace(" ", "")
            if "+" not in ch.text:
                message = "- ارسل رقم الهاتف بشكل صحيح مع +"
                await x.send_message(message)
            else:
                countries = db.get(countries_key())
                for code in countries:
                    if ch.text.startswith(code['calling_code']):
                        calling_code = code['calling_code']
                        name = code["name"]
                        sell_price = code["sell_price"]
                        
                        if db.exists(stops_key(calling_code)): 
                            message = f"- عذراً، غير مسموح ببيع أرقام من دولة {name} ❌"
                            await x.send_message(message)
                            return
                        else:
                            data = f"next_sell:+{phone_number}"
                            await x.send_message(f"تم العثور على الدولة 💎\n\n• الدولة : {name}\n• السعر : {sell_price}")
                            break
                else:
                    message = "- عذراً، غير مسموح ببيع ارقام لهذه الدولة .. راسل المالك لاضافتها ⚠️"
                    await x.send_message(message)
                    return
    
    if isinstance(data, str) and data.startswith("next_sell:"):
        async with bot.conversation(event.chat_id) as x:
            phone_number = data.split(':')[1]
            countries = db.get(countries_key())
            for code in countries:
                if phone_number.startswith(code['calling_code']):
                    calling_code = code['calling_code']
                    name = code["name"]
                    sell_price = code["sell_price"]
                    
                    if db.exists(stops_key(calling_code)): 
                        message = f"- عذراً، غير مسموح ببيع أرقام من دولة {name} ❌"
                        await x.send_message(message)
                        return
                    
                    app = TelegramClient(StringSession(), api_id=API_ID, api_hash=API_HASH)
                    await app.connect()
                    password = None
                    try:
                        code = await app.send_code_request(phone_number)
                    except (ApiIdInvalidError):
                        await x.send_message("ʏᴏᴜʀ **ᴀᴩɪ_ɪᴅ** ᴀɴᴅ **ᴀᴩɪ_ʜᴀsʜ** ᴄᴏᴍʙɪɴᴀᴛɪᴏɴ ᴅᴏᴇsɴ'ᴛ ᴍᴀᴛᴄʜ ᴡɪᴛʜ ᴛᴇʟᴇɢʀᴀᴍ ᴀᴩᴩs sʏsᴛᴇᴍ.")
                        return
                    except (PhoneNumberInvalidError):
                        await x.send_message("ᴛʜᴇ **ᴩʜᴏɴᴇ_ɴᴜᴍʙᴇʀ** ʏᴏᴜ'ᴠᴇ sᴇɴᴛ ᴅᴏᴇsɴ'ᴛ ʙᴇʟᴏɴɢ ᴛᴏ ᴀɴʏ ᴛᴇʟᴇɢʀᴀᴍ ᴀᴄᴄᴏᴜɴᴛ.")
                        return
                    await x.send_message("- تم ارسال كود التحقق الخاص بك على حسابك على تليجرام.\n\n- ارسل الكود بالتنسيق التالي: 1 2 3 4 5")
                    txt = await x.get_response()
                    code = txt.text.replace(" ", "")
                    try:
                        await app.sign_in(phone_number, code, password=None)
                        string_session = app.session.save()
                        _ok, _pw, _note = await unify_two_step(string_session, [])
                        data = {"phone_number": phone_number, "two-step": (_pw or ""), "session": string_session}
                        accounts = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
                        accounts.append(data)
                        db.set(accounts_key(calling_code), accounts)
                        if not _ok:
                            print(f"2FA unify failed for +{phone_number}: {_note}")
                        buttons = [
                            [
                                Button.inline(accent("تحقق ✅"), data=f"check:{phone_number}:{calling_code}"),
                            ]
                        ]
                        await l_respond(event, f"**• تم التحقق من صحة الكود **\n\n- الآن الخطوة التالية هي تسجيل الخروج من جميع جلسات الحساب ماعدا جلسة البوت الأساسية، ثم اضغط زر **تحقق** ", buttons=buttons)
                    
                    except (PhoneCodeInvalidError):
                        await x.send_message("ᴛʜᴇ ᴏᴛᴩ ʏᴏᴜ'ᴠᴇ sᴇɴᴛ ɪs **ᴡʀᴏɴɢ.**")
                        return
                    except (PhoneCodeExpiredError):
                        await x.send_message("ᴛʜᴇ ᴏᴛᴩ ʏᴏᴜ'ᴠᴇ sᴇɴᴛ ɪs **ᴇxᴩɪʀᴇᴅ.**")
                        return
                    except (SessionPasswordNeededError):
                        await x.send_message("- ارسل رمز التحقق بخطوتين الخاص بالحساب")
                        txt = await x.get_response()
                        password = txt.text
                        try:
                            await app.sign_in(password=password)
                        except (PasswordHashInvalidError):
                            await x.send_message("ᴛʜᴇ ᴩᴀssᴡᴏʀᴅ ʏᴏᴜ'ᴠᴇ sᴇɴᴛ ɪs �ᴡʀᴏɴɢ.")
                            return
                        string_session = app.session.save()
                        _ok, _pw, _note = await unify_two_step(string_session, [password])
                        data = {"phone_number": phone_number, "two-step": (_pw or password), "session": string_session}
                        accounts = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
                        accounts.append(data)
                        db.set(accounts_key(calling_code), accounts)
                        if not _ok:
                            print(f"2FA unify failed for +{phone_number}: {_note}")
                        buttons = [
                            [
                                Button.inline(accent("تحقق ✅"), data=f"check:{phone_number}:{calling_code}"),
                            ]
                        ]
                        await l_respond(event, f"**• تم التحقق من صحة الكود**\n\n- الآن الخطوة التالية هي تسجيل الخروج من جميع جلسات الحساب ماعدا جلسة البوت الأساسية، ثم اضغط زر **تحقق** ", buttons=buttons)
                    
    if isinstance(data, str) and data.startswith("check:"):
        await l_edit(event, f"-")
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        async with bot.conversation(event.chat_id) as x:
            phone_number = data.split(':')[1]
            calling_code = data.split(':')[2]
            countries = db.get(countries_key())
            
            for code in countries:
                if phone_number.startswith(code['calling_code']):
                    calling_code = code['calling_code']
                    name = code["name"]
                    sell_price = code["sell_price"]
                    accounts = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
                    
                    for i in accounts:
                        if phone_number == i["phone_number"]:
                            ses = i["session"]
                            xx = await count_ses(ses)
                            mkk = isinstance(xx, list)
                            
                            if mkk is False:
                                await x.send_message(f"• حدث خطأ ما، رجاء إعادة تسجيل الرقم مرة أخرى\n\n{xx}")
                                return
                            
                            xv = len(xx)
                            
                            if xv == 1:
                                user_accounts_key = f"user_accounts_{event.chat_id}"
                                user_accounts = db.get(user_accounts_key) if db.exists(user_accounts_key) else []
                                user_accounts.append(phone_number)
                                db.set(user_accounts_key, user_accounts)
                                num_accounts = len(user_accounts)
                                
                                message = f"**• تم استلام الرقم بنجاح ✅**\n\nإجمالي حساباتك: {num_accounts}"
                                buttons = [Button.inline(accent("بيع حساب آخر"), data="sell")]
                                await l_respond(event, message, buttons=buttons)
                                
                                update_user_balance(event.chat_id, float(sell_price))
                                
                                await x.send_message(message)
                                
                                admin_message = f"**- قام مستخدم بتسليم حساب إلى البوت.**\n• أي دي المستخدم: {event.chat_id}\n\n- الرقم: {phone_number}\n- الدولة: {name}\n- إجمالي حساباته: {num_accounts}"
                                await l_client_send_message(client, admin, admin_message)
                            
                            else:
                                bm = ""
                                for i in xx:
                                    bm += f"• {i}\n"
                                
                                xxx = f"""**• فشل في إكمال عملية التحقق ❌**
        
        - مازالت هناك بعض الجلسات يجب تسجيل الخروج منها.
        
        {bm}
        
        ⚠️- تذكر: قم بحذف جميع الجلسات ماعدا جلسة البوت ثم اضغط **تحقق**"""
                                
                                buttons = [
                                    [
                                        Button.inline(accent("تحقق ✅"), data=f"check:{phone_number}:{calling_code}"),
                                    ]
                                ]
                                await x.send_message(xxx, buttons=buttons)
                                
    if data == "set_trust_channel":
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"- ارسل معرف او رابط قناة اثباتات التسليم.")
            ch = await x.get_response()
            channel = ch.text.replace('https://t.me/', '').replace('@', '').replace(" ", "")
            try:
                message = "- تم تفعيل قناة اثباتات التسليم بنجاح ✅"
                await l_client_send_message(client, channel, message)
            except:
                message = "- حدث خطأ ❌، تأكد من رفع البوت ادمن في قناتك مع صلاحية ارسال الرسائل"
                await x.send_message(message)
                return
            message = "- تم تفعيل قناة اثباتات التسليم بنجاح ✅"
            await x.send_message(message)
            db.set("trust_channel", channel)
                    
    if data == "sharh":
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
            
        await l_edit(event, 
            f"**• مرحبا بك عزيزي في قسم التعليمات والشروط.**\n\n"
            f"• شروط البوت : ↘️\n\n"
            f"- هذا البوت يقوم بجلب حسابات تيليجرام جاهزة.\n"
            f"- البوت لا يتحمل مسؤولية الأرقام في حالة أنها انحظرت او انسرقت، بمعنى البوت لا يتحمل مسؤولية الرقم بعد شرائه.\n"
            f"- فضلاً يرجى عدم استعمال الأرقام في أشياء قد تغضب الله عز وجل أو الانحراف الإسلامي كالإختراقات وغيره.\n\n"
            f"• تعليمات عن كيفية استعمال البوت : ↘️\n\n"
            f"⚠️ - ملاحظة مهمة: هذا البوت مخصص لشراء حسابات تيليجرام جاهزة، بمجرد الضغط على ( ✅ شراء ✅ )، سيتم إعطاؤك رقم، ويتم خصم قيمة الرقم تلقائياً، ولا يمكنك الإلغاء بعد أن يتم الشراء ⚠️.\n\n"
            f"- البوت يوفر حسابات تيليجرام جاهزة\n"
            f"• للاستفسار تواصل معنا:  @J_D_D_M ",
            buttons=[
                [Button.inline(accent("الرجوع"), data="bhoht")]
            ]
        )
    
    if data == "bhoht":
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        await l_edit(event, 
            f"**• 👨‍✈️ ⁞ مرحبا بك عزيزي المستخدم**\n"
            f"✅ - في - بوت ارقام صلاح\n"
            f"📮 - لخدمات الحسابات التيلجرام الجاهزة .\n\n"
            f"1⃣ - أكثر من 200 دولة في المخزون مع إضافات جديدة .\n"
            f"2⃣ - سرعة العمل، أرخص بوت لبيع الأرقام الجاهزة \n"
            f"3⃣ - سعر الرقم يبدأ من 0.28 سنت ويحتوي على أكثر دول العالم\n"
            f"4⃣ - يحتوي على الأرقام النادرة .\n\n"
            f"- يرجى بدء الاستخدام بإنشاء حساب، إذا لديك حساب من قبل، قم بالضغط على زر تسجيل الدخول ✅.\n\n"
            f"الإدارة والدعم الفني: @J_D_D_M ",
            buttons=[
                [Button.inline(accent("📌- انشاء حساب."), data="create_account"), Button.inline(accent("✅- تسجيل الدخول."), data="login")],
                [Button.inline(accent("📃- شرح البوت."), data="sharh")],  
                [Button.url(accent("⚠️- فريق الدعم"), url="t.me/J_D_D_M")],
            ]
        )
    
    if data == "passwd":
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        if db.exists(f"user_{user_id}"):
            user_data = db.get(f"user_{user_id}")
            old_password = user_data.get("password", "غير معروف")
    
            new_ppassword = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    
            user_data["password"] = new_ppassword
            db.set(f"user_{user_id}", user_data)
    
            await l_respond(event, 
                f"✅ | تم تغيير كلمة السر بنجاح!\n\n"
                f"- كلمة السر القديمة: {old_password}\n"
                f"- كلمة السر الجديدة: `{new_ppassword}`"
            )
        else:
            await l_respond(event, "❌ | ليس لديك حساب، الرجاء إنشاء حساب أولاً.")
    
    if data == "SUPEGY":
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        await l_edit(event, 
            "**⚙️| اختر الإعدادات من الأسفل.**",
            buttons=[
                [Button.inline(accent("🔐 تعديل كلمة سر الحساب."), data="passwd")],
                [Button.inline(accent("📊- احصائيات البوت.."), data="successful_accounts")],
                [Button.inline(accent("الرجوع"), data="main")]
            ]
        )
    
    if data == "successful_accounts":
        successful_accounts_count = db.get("successful_accounts_count") if db.exists("successful_accounts_count") else 0
        await l_edit(event, 
            f"**📈- عدد الأرقام المكتملة: **{successful_accounts_count}",
            buttons=[[Button.inline(accent("رجوع"), data="SUPEGY")]]
        )
    
    if data == "transfer":
        user_info = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {"coins": 0, "coind": 0}
        
        await l_edit(event, 
            f"**⚜️ | هنا قائمة تحويل من محفظة السحب الى حسابك في البوت لإستخدام الأموال في الشراء داخل البوت .**\n\n"
            f"➖ الرصيد| {money(user_info.get('coind', 0))}$",
            buttons=[
                [Button.inline(accent("تحويل الكل"), data="transferdr")],
                [Button.inline(accent("الرجوع"), data="main")]
            ]
        )
                                                        
    if data == "transferdr":
        user_info = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {"coins": 0, "coind": 0}
        
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        await l_edit(event, 
            f"**❕ هل انت متأكد من تحويل جميع المحفظة للسحب الى حسابك في البوت ؟ حيث انه لا يمكن عكس العملية...**\n\n"
            f"➖ الرصيد| {money(user_info.get('coind', 0))}$",
            buttons=[
                [Button.inline(accent("✅تأكيد✅"), data="trcoind")],
                [Button.inline(accent("الرجوع"), data="transfer")]
            ]
        )

    if data == "trcoind":
        coind_balance = get_user_coind(user_id)
        
        user_info = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {"coins": 0, "coind": 0}
        
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        if coind_balance <= 0:
            await event.answer("- رصيدك غير كافي ❗", alert=True)
            return
        
        user_info['coins'] += coind_balance
        db.set(f"user_{user_id}", user_info)
        
        update_user_coind(user_id, 0)
        
        await l_edit(event, 
            f"**- تم تحويل {display_money(coind_balance, user_id)} من محفظة السحب إلى حسابك في البوت بنجاح ✅**\n\n"
            f"• رصيدك الآن : {display_money(user_info['coins'], user_id)}",
            buttons=[
                [Button.inline(accent("الرجوع"), data="main")]
            ]
        )
    if data == "add_coins":
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"- ارسل ايدي المستخدم الذي تريد اضافة الرصيد له")
            id = await x.get_response()
            if not db.exists(f"user_{id.text}"):
                await x.send_message(f"- لم ينضم للبوت بعد")
                return
            info = db.get(f"user_{id.text}")
            await x.send_message(f"- المستخدم : {id.text}\n- رصيده : {money(info['coins'])} $\n\n- ارسل المبلغ الذي تريد اضافته للمستخدم")
            count = await x.get_response()
            try:
                info['coins'] = money_value(info['coins'] + money_value(count.text))
            except:
                await x.send_message(f"- ارسل المبلغ ارقام فقط")
                return
            db.set(f"user_{id.text}", info)
            await x.send_message(f"- تم اضافة الرصيد للمستخدم بنجاح.\n\n- المبلغ : {money(info['coins'])} $")
            message = f"- تم اضافة {money_value(count.text):.2f}$ الى رصيدك ✅\n\n- رصيدك الان : {money(info['coins'])}$"
            await l_client_send_message(client, int(id.text), message)
            return 
    if data == "del_coins":
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"- ارسل ايدي المستخدم المراد خصم رصيد منه")
            id = await x.get_response()
            if not db.exists(f"user_{id.text}"):
                await x.send_message(f"- المستخدم لم ينضم للبوت بعد")
                return
            info = db.get(f"user_{id.text}")
            await x.send_message(f"- المستخدم : {id.text}\n- رصيده : {money(info['coins'])} $\n\n- ارسل المبلغ المراد خصمه من رصيد المستخدم")
            count = await x.get_response()
            try:
                info['coins'] = money_value(info['coins'] - money_value(count.text))
            except:
                await x.send_message(f"- ارسل المبلغ ارقام فقط")
                return
            db.set(f"user_{id.text}", info)
            await x.send_message(f"- تم خصم الرصيد بنجاح.\n- الرصيد الان : {money(info['coins'])} $")
            return
        
    if data == "ban":
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"- ارسل ايدي المستخدم المراد حظره")
            id = await x.get_response()
            try:
                i = int(id.text)
            except:
                await x.send_message(f"- ارسل ايدي بشكل صحيح")
                return
            bans = get_banned_ids()
            if i in bans:
                await x.send_message(f"- هذا المستخدم محظور بالفعل")
                return
            bans.append(i)
            db.set("bad_guys", bans)
            await x.send_message(f"- تم حظر المستخدم من البوت بنجاح.")
            return 
    
    if data == "unban":
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"- ارسل ايدي المستخدم المراد الغاء حظره من البوت")
            id = await x.get_response()
            try:
                i = int(id.text)
            except:
                await x.send_message(f"- ارسل ايدي المستخدم بشكل صحيح.")
                return
            bans = get_banned_ids()
            if i not in bans:
                await x.send_message(f"- هذا المستخدم ليس محظور بالفعل")
                return
            bans.remove(i)
            db.set("bad_guys", bans)
            await x.send_message(f"- تم الغاء حظر المستخدم بنجاح")
            return 
    
    if data == "wkala":
        user_info = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {"coins": 0, "coind": 0}
    
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        await l_edit(event, 
            f"**- مرحباً بك في قسم الوكلاء، هنا قائمة بوكلاء البوت الذين تم اعتمادهم من الإدارة شخصياً.**\n\n"
            f"✅ يمكنك شحن البوت عبرهم بكل ثقة وأمان، وبضمان من الإدارة رسميًا.\n"
            f"⚠️ في حال لاحظت من أحدهم أي تصرف غير لائق، يرجى إبلاغنا فورًا.",
            buttons=[
                [Button.inline(accent("AFROTO"), data="#"), Button.url(accent("الدخول للمحادثة"), url="t.me/J_D_D_M")],
                [Button.inline(accent("رجوع"), data="main")],
            ]
        )
    
    if data == "all_of_number":
        cur = CUR_CAT.get() or DEFAULT_CAT
        total_all = sum(category_stock(c['id']) for c in get_categories())
        return await event.answer(f"- حسابات قسم {get_category_name(cur)} : {category_stock(cur)}\n- كل الأقسام : {total_all}", alert=True)
        
    if data == "change_language":
        lang = user_language(user_id)
        buttons = [
            [Button.inline(accent("🇪🇬 العربية"), data="set_language:ar"), Button.inline(accent("🇬🇧 English"), data="set_language:en")],
            [Button.inline(accent("🔙 رجوع") if lang == "ar" else "🔙 Back", data="main")]
        ]
        await l_edit(event, "🌐 اختر اللغة / Choose language:", buttons=buttons)
        return

    if data.startswith("set_language:"):
        new_lang = data.split(":",1)[1]
        set_user_pref(user_id, "language", "en" if new_lang == "en" else "ar")
        await event.answer("Language changed to English" if new_lang == "en" else "تم تغيير اللغة إلى العربية")
        await l_edit(event, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
        return

    if data == "change_currency":
        cur = user_currency(user_id)
        rate = get_global_exchange_rate()
        buttons = [
            [Button.inline(accent("🇺🇸 USD ($)"), data="set_currency:USD"), Button.inline(accent("🇪🇬 EGP (ج.م)"), data="set_currency:EGP")],
            [Button.inline(accent("⚙️ سعر الصرف / Exchange rate"), data="show_exchange_rate")],
            [Button.inline(accent("🔙 رجوع"), data="main")]
        ]
        if user_language(user_id) == "en":
            currency_text = f"💱 Currency: {cur}\n📊 1 USD = {rate:.2f} EGP\n\nChoose your display currency:"
        else:
            currency_text = f"💱 العملة الحالية: {cur}\n📊 1 دولار = {rate:.2f} جنيه\n\nاختر العملة التي تريد عرض الأسعار بها:"
        await l_edit(event, currency_text, buttons=buttons)
        return

    if data == "show_exchange_rate":
        await event.answer(f"1 USD = {get_global_exchange_rate():.2f} EGP", alert=True)
        return

    if data.startswith("set_currency:"):
        new_cur = data.split(":",1)[1]
        set_user_pref(user_id, "currency", "EGP" if new_cur == "EGP" else "USD")
        await event.answer("Currency changed to EGP" if new_cur == "EGP" else "Currency changed to USD")
        await l_edit(event, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
        return

    if data == "set_exchange_rate":
        admins = db.get("admins") if db.exists("admins") else []
        if int(event.sender_id) != int(admin) and int(event.sender_id) not in [int(x) for x in (admins or [])]:
            await event.answer("❌ الأمر مخصص للأدمن فقط.", alert=True)
            return
        current_rate = get_global_exchange_rate()
        db.set(f"exchange_rate_pending_{event.sender_id}", True)
        await l_edit(
            event,
            f"💱 تغيير سعر الصرف\n\n📊 السعر الحالي: 1$ = {current_rate:.2f} ج.م\n\nأرسل السعر الجديد بالجنيه المصري.\nمثال: 50 أو 52.50",
            buttons=[[Button.inline(accent("❌ إلغاء"), data="cancel_exchange_rate")]]
        )
        return

    if data == "cancel_exchange_rate":
        db.delete(f"exchange_rate_pending_{event.sender_id}") if db.exists(f"exchange_rate_pending_{event.sender_id}") else None
        await l_edit(event, client_main_panel(event.sender_id), parse_mode='markdown', buttons=main_menu_buttons(event.sender_id))
        return

    if data == "set_star_price":
        admins = db.get("admins") if db.exists("admins") else []
        if int(event.sender_id) != int(admin) and int(event.sender_id) not in [int(x) for x in (admins or [])]:
            await event.answer("❌ الأمر مخصص للأدمن فقط.", alert=True)
            return
        current = get_star_usd_value()
        db.set(f"star_price_pending_{event.sender_id}", True)
        await l_edit(
            event,
            f"⭐ تغيير سعر النجمة\n\n💵 السعر الحالي: ⭐ = {current:g}$\n\nأرسل قيمة النجمة بالدولار.\nمثال: 0.01 = 1 سنت\nمثال: 0.02 = 2 سنت",
            buttons=[[Button.inline(accent("❌ إلغاء"), data="cancel_star_price")]]
        )
        return

    if data == "cancel_star_price":
        if db.exists(f"star_price_pending_{event.sender_id}"):
            db.delete(f"star_price_pending_{event.sender_id}")
        await l_edit(event, client_main_panel(event.sender_id), parse_mode='markdown', buttons=main_menu_buttons(event.sender_id))
        return

    if data == "main":
        await l_edit(event, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
        return
        
    if data == "admin_panel":
        await l_edit(event, msgs['ADMIN_MESSAGE'], buttons=admin_main_keyboard())
        return 
        
    if data == "buy" or data == "back" or data.startswith("page_") or data == "search_country":
        countries = db.get(countries_key())
    
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        if data == "search_country":
            async with bot.conversation(event.chat_id) as x:
                await x.send_message("🔎 | حسنًا ، يمكنك الآن إرسال أي شيء يشير إلى البلد ... على سبيل المثال رمز الدولة +20.")
                search_code = await x.get_response()
                search_code = search_code.text.strip() 
                
                found_country = None
                for country in countries:
                    if country['calling_code'] == search_code:
                        found_country = country
                        break
            
                if found_country:
                    message_text = (
                        f"- الدولة : {found_country['name']}\n"
                        f"- رمز الدولة : {found_country['calling_code']}\n"
                        f"- سعر الحساب : {found_country['price']} $\n"
                        "------------------------------------------------------"
                    )
                
                    buttons = [
                        [Button.inline(accent("شراء حساب"), f"buy_{found_country['calling_code']}_{found_country['name']}_{found_country['price']}")]
                    ]
                
                    await l_edit(event, message_text, buttons=buttons)
                    return
                else:
                    await event.answer("- هذه الدولة غير ه.", alert=True)
                    return
    
        buttons_per_page = 20 
        total_pages = (len(countries) + buttons_per_page - 1) // buttons_per_page
    
        current_page = int(data.split('_')[1]) if data.startswith("page_") else 0
    
        if current_page < 0:
            current_page = 0
        elif current_page >= total_pages:
            current_page = total_pages - 1
    
        start_index = current_page * buttons_per_page
        end_index = start_index + buttons_per_page
        page_countries = countries[start_index:end_index]
    
        buttons = []
        row = []
        for code in page_countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name} : {price} $"), data=f"countries_{calling_code}_{name}_{price}"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name} : {price} $"), data=f"countries_{calling_code}_{name}_{price}")]
        if row:
            buttons.append(row)
    
        navigation_buttons = []
    
        if current_page > 0:
            navigation_buttons.append(Button.inline(text=accent("⏪"), data=f"page_{current_page - 1}"))
    
        if current_page < total_pages - 1:
            navigation_buttons.append(Button.inline(text=accent("⏩"), data=f"page_{current_page + 1}"))
    
        if navigation_buttons:
            buttons.append(navigation_buttons)
    
        buttons.append([Button.inline(text=accent("AUTO 🔍"), data="search_country")])
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="main")])
    
        await l_edit(event, msgs['COUNTRY_LIST'], parse_mode='markdown', buttons=buttons)
        return
        
    if data.startswith("countries_"):
        calling_code = data.split('_')[1]
        name = data.split('_')[2]
        price = data.split('_')[3]
        coins = get_user_record(user_id)['coins']
        if float(coins) < float(price):
            return await event.answer("- رصيدك لا يكفي لشراء اي ارقام من هذه الدولة.", alert=True)
        accounts = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
        if accounts == []:
            return await event.answer("- لا توجد أي حسابات في هذه الدولة.", alert=True)
        keyboard = [
            [
                Button.inline(accent("الغاء ❌"), data="back"),
                Button.inline(accent("تأكيد ✅"), data=f"buy_{calling_code}_{name}_{price}")
            ],
        ]
        await l_edit(event, msgs['BUY_MESSAGE'].format(name, price), parse_mode='markdown', buttons=keyboard)
        return
        
    if data == "buysr":
        countries = db.get(countries_key())
        buttons = []
        row = []
        for code in countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['sell_price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name} : {price} $"), data=f"#"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name} : {price} $"), data=f"#")]
        if row:
            buttons.append(row)
    
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="seller")])
        await l_edit(event, msgs['COUNTRY_LIST'], parse_mode='markdown', buttons=buttons)
        return
        
    if data == "delaccount" or data.startswith("paget_"):
        countries = db.get(countries_key())
    
        buttons_per_page = 10
        total_pages = (len(countries) + buttons_per_page - 1) // buttons_per_page
    
        if data.startswith("paget_"):
            try:
                current_page = int(data.split('_')[3]) 
            except (IndexError, ValueError):
                current_page = 0
        else:
            current_page = 0
    
        if current_page < 0:
            current_page = 0
        elif current_page >= total_pages:
            current_page = total_pages - 1
    
        start_index = current_page * buttons_per_page
        end_index = start_index + buttons_per_page
        page_countries = countries[start_index:end_index]
    
        buttons = []
        row = []
        for code in page_countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name} : {price} $"), data=f"show_{calling_code}_{name}_{price}"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name} : {price} $"), data=f"show_{calling_code}_{name}_{price}")]
        if row:
            buttons.append(row)
    
        navigation_buttons = []
    
        if current_page > 0:
            navigation_buttons.append(Button.inline(text=accent("⏪"), data=f"paget_{current_page - 1}"))
    
        if current_page < total_pages - 1:
            navigation_buttons.append(Button.inline(text=accent("⏩"), data=f"paget_{current_page + 1}"))
    
        if navigation_buttons:
            buttons.append(navigation_buttons)
    
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="adm:num")])
    
        await l_edit(event, 
            f"- اختر الدولة الذي تريد حذف رقم منها\n🗂 القسم : {cat_label()}",
            parse_mode='markdown',
            buttons=buttons
        )
        return
    
    if data.startswith("show_") or data.startswith("pagevv_"):
        if data.startswith("show_"):
            calling_code = data.split('_')[1]
            name = data.split('_')[2]
            price = data.split('_')[3]
            current_page = 0
        else:
            calling_code = data.split('_')[2]
            name = data.split('_')[3]
            price = data.split('_')[4]
            current_page = int(data.split('_')[1]) 

        accounts = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
    
        if accounts == []:
            return await event.answer("- There are no accounts in this country.", alert=True)
    
        accounts_per_page = 10  
        total_pages = (len(accounts) + accounts_per_page - 1) // accounts_per_page
    
        if current_page < 0:
            current_page = 0
        elif current_page >= total_pages:
            current_page = total_pages - 1
    
        start_index = current_page * accounts_per_page
        end_index = start_index + accounts_per_page
        page_accounts = accounts[start_index:end_index]
    
        buttons = [
            [Button.inline(accent(f"{count + start_index}: {i['phone_number']}"), data=f"v:{i['phone_number']}:{calling_code}:{name}:{price}")]
            for count, i in enumerate(page_accounts, 1)
        ]
    
        navigation_buttons = []
    
        if current_page > 0:
            navigation_buttons.append(Button.inline(text=accent("⏪"), data=f"pagevv_{current_page - 1}_{calling_code}_{name}_{price}"))
    
        if current_page < total_pages - 1:
            navigation_buttons.append(Button.inline(text=accent("⏩"), data=f"pagevv_{current_page + 1}_{calling_code}_{name}_{price}"))
    
        if navigation_buttons:
            buttons.append(navigation_buttons)
    
        buttons.append([Button.inline(accent("حذف كل الحسابات"), data=f"deleteall:{calling_code}:{name}")])
    
        buttons.append([Button.inline(accent("رجوع ↪️"), data=f"delaccount")])
    
        await l_edit(event, 
            f"- اليك قائمة بالحسابات المضافة لدولة {name}",
            parse_mode='markdown',
            buttons=buttons
        )
        return
    
    if data.startswith("deleteall"):
        calling_code = data.split(':')[1]
        name = data.split(':')[2]
    
        db.delete(accounts_key(calling_code))
    
        await event.answer(f"- هل تريد حذف جميع الحسابات من دولة {name} من البوت ؟.", alert=True)
        await l_edit(event, "- تم حذف جميع الأرقام بنجاح .", buttons=[[Button.inline(accent("رجوع ↪️"), data=f"delaccount")]])
        return
        
    if data.startswith("v:"):
        phone_number = data.split(':')[1]
        calling_code = data.split(':')[2]
        name = data.split(':')[3]
        price = data.split(':')[4]
        info = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
        for i in info:
            if i['phone_number'] == phone_number:
                _pw_shown = acc_pw(i) or 'غير متاح'
                text = f"- الرقم : `{i['phone_number']}`\n- التحقق بخطوتين المخزّن : `{_pw_shown}`\n\n**• اختر ما تريد فعله في الحساب**"
        keyboard = [
            [
                Button.inline(accent("جلب الكود 💬"), data=f"get:{phone_number}:{calling_code}:{name}:{price}"),
            ],
            [
            Button.inline(accent(f"حذف الحساب 🗑"), data=f"del:{phone_number}:{calling_code}:{name}"), 
            ],
            [
            Button.inline(accent("رجوع ↪️"), data=f"show_{calling_code}_{name}_{price}")
            ]
        ]
        await l_edit(event, text, parse_mode='markdown', buttons=keyboard)
        return
        
    if data.startswith("del:"):
        phone_number = data.split(':')[1]
        calling_code = data.split(':')[2]
        name = data.split(':')[3]
        text = f"- الرقم : `{phone_number}`\n\n**- هل تريد حذفه من البوت ؟**"
        keyboard = [
            [
            Button.inline(accent("رجوع ↪️"), data=f"v:{phone_number}:{calling_code}:{name}"),
            Button.inline(accent("حذف ❌"), data=f"del_done:{phone_number}:{calling_code}:{name}")
            ]
        ]
        await l_edit(event, text, parse_mode='markdown', buttons=keyboard)
        return
        
    if data.startswith("del_done:"):
        phone_number = data.split(':')[1]
        calling_code = data.split(':')[2]
        name = data.split(':')[3]
        keyboard = [
            [
            Button.inline(accent("رجوع ↪️"), data="adm:num")
            ]
        ]
        
        info = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
        for i in info:
            if i['phone_number'] == phone_number:
                info.remove(i)
                db.set(accounts_key(calling_code), info)
                await l_edit(event, f"- تم حذف الرقم بنجاح 🗑", parse_mode='markdown', buttons=keyboard)
                return
        await l_edit(event, f"- حدث خطأ اثناء حذف الرقم ❌", parse_mode='markdown', buttons=keyboard)
        return 
        
    if data == "add":
        countries = db.get(countries_key())
        buttons = []
        row = []
        for code in countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name} : {price} $"), data=f"rig_{calling_code}_{name}_{price}"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name} : {price} $"), data=f"rig_{calling_code}_{name}_{price}")]
        if row:
            buttons.append(row)
        
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="main")])
        await l_edit(event, f"- اختر دولة الرقم الذي تريد اضافته\n🗂 القسم : {cat_label()}", parse_mode='markdown', buttons=buttons)
        return 
        
    if data.startswith("rig_"):
        _parts = data.split('_')
        calling_code = _parts[1]
        name = _parts[2]
        price = _parts[3]
        _rig_cid = _parts[4] if len(_parts) > 4 else get_admin_cat(user_id)
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(f"🗂 القسم : {get_category_name(_rig_cid)}\n- أرسل رقم الحساب مع الرمز الدولي +")
            txt = await x.get_response()
            phone_number = txt.text.replace("+", "").replace(" ", "")
            app = TelegramClient(StringSession(), api_id=API_ID, api_hash=API_HASH)
            await app.connect()
            password=None
            try:
                code = await app.send_code_request(phone_number)
            except (ApiIdInvalidError):
                await x.send_message("ʏᴏᴜʀ **ᴀᴩɪ_ɪᴅ** ᴀɴᴅ **ᴀᴩɪ_ʜᴀsʜ** ᴄᴏᴍʙɪɴᴀᴛɪᴏɴ ᴅᴏᴇsɴ'ᴛ ᴍᴀᴛᴄʜ ᴡɪᴛʜ ᴛᴇʟᴇɢʀᴀᴍ ᴀᴩᴩs sʏsᴛᴇᴍ.")
                return
            except (PhoneNumberInvalidError):
                await x.send_message("ᴛʜᴇ **ᴩʜᴏɴᴇ_ɴᴜᴍʙᴇʀ** ʏᴏᴜ'ᴠᴇ sᴇɴᴛ ᴅᴏᴇsɴ'ᴛ ʙᴇʟᴏɴɢ ᴛᴏ ᴀɴʏ ᴛᴇʟᴇɢʀᴀᴍ ᴀᴄᴄᴏᴜɴᴛ.")
                return
            await x.send_message("- تم ارسال كود تحقق الى الحساب .. ارسله لي\n\n- ارسله هكذا : 1 2 3 4 5")
            txt = await x.get_response()
            code = txt.text.replace(" ", "")
            try:
                await app.sign_in(phone_number, code, password=None)
                string_session = app.session.save()
                _ok, _pw, _note = await unify_two_step(string_session, [])
                acc_data = {"phone_number": phone_number, "two-step": (_pw or ""), "session": string_session}
                accounts = db.get(accounts_key(calling_code, _rig_cid)) if db.exists(accounts_key(calling_code, _rig_cid)) else []
                accounts.append(acc_data)
                db.set(accounts_key(calling_code, _rig_cid), accounts)
                _note_txt = "" if _ok else f"\n⚠️ تعذّر توحيد التحقق بخطوتين: {_note}"
                await x.send_message(f"- تم اضافة الحساب الى دولة {name} (قسم {get_category_name(_rig_cid)})\n- اجمالي حسابات هذه الدولة : {len(accounts)}{_note_txt}")
            except (PhoneCodeInvalidError):
                await x.send_message("ᴛʜᴇ ᴏᴛᴩ ʏᴏᴜ'ᴠᴇ sᴇɴᴛ ɪs **ᴡʀᴏɴɢ.**")
                return
            except (PhoneCodeExpiredError):
                await x.send_message("ᴛʜᴇ ᴏᴛᴩ ʏᴏᴜ'ᴠᴇ sᴇɴᴛ ɪs **ᴇxᴩɪʀᴇᴅ.**")
                return
            except (SessionPasswordNeededError):
                await x.send_message("- ارسل التحقق بخطوتين الخاص بالحساب")
                txt = await x.get_response()
                password = txt.text
                try:
                    await app.sign_in(password=password)
                except (PasswordHashInvalidError):
                    await x.send_message("ᴛʜᴇ ᴩᴀssᴡᴏʀᴅ ʏᴏᴜ'ᴠᴇ sᴇɴᴛ ɪs ᴡʀᴏɴɢ.")
                    return
                string_session = app.session.save()
                _ok, _pw, _note = await unify_two_step(string_session, [password])
                acc_data = {"phone_number": phone_number, "two-step": (_pw or password), "session": string_session}
                accounts = db.get(accounts_key(calling_code, _rig_cid)) if db.exists(accounts_key(calling_code, _rig_cid)) else []
                accounts.append(acc_data)
                db.set(accounts_key(calling_code, _rig_cid), accounts)
                _note_txt = "" if _ok else f"\n⚠️ تعذّر توحيد التحقق بخطوتين: {_note}"
                await x.send_message(f"- تم اضافة الحساب الى دولة {name} (قسم {get_category_name(_rig_cid)})\n- اجمالي هحسابات هذه الدولة : {len(accounts)}{_note_txt}")
        return 
        
    if data == 'zip_all':
        folder_path = f"./database"
        zip_file_name = f"database.zip"
        zip_file_nam = f"database"
        try:
            shutil.make_archive(zip_file_nam, 'zip', folder_path)
            with open(zip_file_name, 'rb') as zip_file:
                await client.send_file(user_id, zip_file, attributes=[DocumentAttributeFilename(file_name="database.zip")])
            os.remove(zip_file_name)
        except Exception as a:
            print(a)

    if data.startswith("get:"):
        phone_number = data.split(':')[1]
        calling_code = data.split(':')[2]
        name_cb = data.split(':')[3] if len(data.split(':')) > 3 else ""
        price = data.split(':')[4] if len(data.split(':')) > 4 else ""
        reserved = db.get(f"reserved_account_{phone_number}")
        if not reserved and _is_admin_user(user_id):
            # الأدمن بيقدر يطلب كود لأي رقم لسه في المخزون (مباع لسه ولا)، كأداة متابعة/استرجاع لأرقامه هو،
            # من غير ما يتحجز أو يتشال من المخزون ومن غير ما نلمس أي تحقق بخطوتين.
            for cat in get_categories():
                accs = db.get(accounts_key(calling_code, cat["id"])) if db.exists(accounts_key(calling_code, cat["id"])) else []
                for a in accs:
                    if a.get("phone_number") == phone_number:
                        reserved = dict(a)
                        reserved.setdefault("type", cat["name"])
                        reserved.setdefault("country_name", name_cb)
                        reserved["cid"] = cat["id"]
                        break
                if reserved:
                    break
        if not reserved:
            keyboard = [[Button.inline(accent("رجــوع ↪️"), data="main")]]
            async with bot.conversation(event.chat_id) as x:
                await x.send_message("- هذا الرقم لم يعد محجوزًا لك. ❌", buttons=keyboard)
            return
        i = reserved
        # نعتمد على البيانات المحفوظة مع الحجز نفسه، ولو ناقصة (حجوزات قديمة) نحاول نكتشفها من رقم الهاتف
        acc_type = i.get("type") or "أرقام سبام"
        name = i.get("country_name") or name_cb
        if not name:
            _match = match_country_by_phone(phone_number, db.get(countries_key(i.get("cid"))) or db.get(countries_key()) or [])
            name = (_match or {}).get("name", "غير محدد")
        if not price:
            price = "-"
        has_pw = await get_real_two_step_status(i['session'])
        two_step_status = _two_step_display(has_pw, acc_pw(i))
        code = await get_code(i['session'])
        keyboard = [
            [Button.inline(accent("طلب كود مرة أخرى 📩"), data=f"get:{phone_number}:{calling_code}:{name_cb}:{price}")],
            [Button.inline(accent("إزالة جلسة البوت من الرقم ❌"), data=f"delsess:{phone_number}:{calling_code}:{name_cb}:{price}")],
            [Button.inline(accent("القائمة الرئيسية 🏠"), data="main")],
        ]
        try:
            cd = int(code)
            text = (
                f"🌍 الدولة: {name}\n"
                f"🔥 النوع: {acc_type}\n"
                f"📱 الرقم: +{phone_number}\n"
                f"🔑 الكود: `{code}`\n"
                f"🔒 التحقق بخطوتين: {two_step_status}\n"
                f"💰 السعر: {price}\n\n"
                "تم استلام الكود بنجاح ✅"
            )
            now = datetime.datetime.now()
            current_time = now.strftime("%Y-%m-%d %H:%M:%S")
            await post_trust_number(name, phone_number, price, user_id, code)
        except Exception as a:
            print(a)
            text = (
                f"🌍 الدولة: {name}\n"
                f"🔥 النوع: {acc_type}\n"
                f"📱 الرقم: +{phone_number}\n"
                f"🔒 التحقق بخطوتين: {two_step_status}\n\n"
                "لم يتم العثور على كود جديد حاليًا، جرب تاني بعد شوية. ❌"
            )
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(text, buttons=keyboard, parse_mode="markdown")
        return

    if data.startswith("delsess:"):
        phone_number = data.split(':')[1]
        calling_code = data.split(':')[2]
        reserved = db.get(f"reserved_account_{phone_number}")
        keyboard = [[Button.inline(accent("القائمة الرئيسية 🏠"), data="main")]]
        if not reserved:
            return await event.answer("- هذا الرقم لم يعد محجوزًا لك. ❌", alert=True)
        try:
            app = TelegramClient(StringSession(reserved['session']), api_id=API_ID, api_hash=API_HASH)
            await app.connect()
            await app.log_out()
            await app.disconnect()
        except Exception as ex:
            print("delsess error:", ex)
        db.delete(f"reserved_account_{phone_number}")
        await l_edit(event, "✅ تم إزالة جلسة البوت من الرقم، الحساب بقى ملكك بالكامل الآن.", buttons=keyboard)
        return
    if data.startswith("addctry:"):
        await start_add_country(event, data.split(":", 1)[1])
        return
    
    if data == "del_country":
        countries = db.get(countries_key())
        buttons = []
        row = []
        for code in countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name} : {price} $"), data=f"delete_{calling_code}_{name}_{price}"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name} : {price} $"), data=f"delete_{calling_code}_{name}_{price}")]
        if row:
            buttons.append(row)
        
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="ajxjao")])
        await l_edit(event, f"- اختر الدولة الذي تريد حذفها من البوت\n🗂 القسم : {cat_label()}", parse_mode='markdown', buttons=buttons)
    
    if data.startswith("delete_"):
        calling_code = data.split('_')[1]
        name = data.split('_')[2]
        price = data.split('_')[3]
        countries = db.get(countries_key())
        buttons = [
            [
            Button.inline(accent("رجوع ↪️"), data="del_country")
            ]
        ]
        for data in countries:
            if data["calling_code"] == calling_code:
                countries.remove(data)
                await l_edit(event, "- تم حذف الدولة من البوت بنجاح ✅", parse_mode='markdown', buttons=buttons)
                db.set(countries_key(), countries)
                return
        await l_edit(event, "- حدث خطأ اثناء حذف الدولة ❌", parse_mode='markdown', buttons=buttons)
        
    if data == "stopcountry":
        countries = db.get(countries_key())
        buttons = []
        row = []
        for code in countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name}"), data=f"shower_options_{calling_code}"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name}"), data=f"shower_options_{calling_code}")]
        if row:
            buttons.append(row)
        
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="adm:num")])
        await l_edit(event, f"- اختر الدولة الذي تريد ايقاف استقبال ارقام منها\n🗂 القسم : {cat_label()}", parse_mode='markdown', buttons=buttons)
    
    if isinstance(data, str) and data.startswith("shower_options_"):
        calling_code = data.split('_')[2] 
        country_name = next((code['name'] for code in db.get(countries_key()) if code['calling_code'] == calling_code), None)
        
        if country_name:
            buttons = [
                [Button.inline(accent(f"إيقاف استلام حسابات"), data=f"stops_{calling_code}")],
                [Button.inline(accent("رجوع ↪️"), data="del_country")]
            ]
            await l_edit(event, f"اختر الإجراء الذي تريد تنفيذه على دولة {country_name}:", buttons=buttons)
    
    if isinstance(data, str) and data.startswith("stops_"):
        calling_code = data.split('_')[1] 
        country_name = next((code['name'] for code in db.get(countries_key()) if code['calling_code'] == calling_code), None)
        
        if country_name:
    
            db.set(stops_key(calling_code), True)
            await l_edit(event, f"تم إيقاف استقبال الأرقام من دولة {country_name} ✅")
        
    if data.startswith("buy_"):
        calling_code = data.split('_')[1]
        name = data.split('_')[2]
        price = data.split('_')[3]
        
        acc = get_user_record(user_id)
        if float(acc['coins']) < float(price):
            return await event.answer("- رصيدك لا يكفي لشراء حساب من هذه الدولة.", alert=True)
        
        # بنحجز الرقم ونشيله من المخزون فورًا، عشان ميتباعش لأكتر من شخص
        i = take_random_account(calling_code)
        if i is None:
            return await event.answer("-  لا توجد حسابات في هذه الدولة", alert=True)
        
        acc['coins'] -= float(price)
        db.set(f"user_{user_id}", acc)
        
        i = dict(i)
        i.update({"type": "أرقام سبام", "country_name": name})
        db.set(f"reserved_account_{i['phone_number']}", i)
        await post_trust_number_purchase(name, i['phone_number'], price, user_id)
        await reward_referral_for_purchase(user_id)
        
        text = f"- الرقم : `{i['phone_number']}`\n\n**• حاول تسجيل الدخول بالرقم في تطبيق تليجرام ثم اضغط على زر جلب الكود**"
        keyboard = [
            [
                Button.inline(accent("جلب الكود"), data=f"get:{i['phone_number']}:{calling_code}:{_short_cb(name)}:{price}"),
            ]
        ]
        await l_edit(event, text, buttons=keyboard)

    if data == "bsbuy" or data.startswith("bspage_") or data == "bssearch_country":
        countries = db.get(countries_key())

        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return

        if data == "bssearch_country":
            async with bot.conversation(event.chat_id) as x:
                await x.send_message("🔎 | ابعت رمز الدولة ... مثال +20.")
                search_code = await x.get_response()
                search_code = search_code.text.strip()

                found_country = None
                for country in countries:
                    if country['calling_code'] == search_code:
                        found_country = country
                        break

                if found_country:
                    message_text = (
                        f"- الدولة : {found_country['name']}\n"
                        f"- رمز الدولة : {found_country['calling_code']}\n"
                        f"- سعر السيشن : {found_country['price']} $\n"
                        "------------------------------------------------------"
                    )
                    buttons = [
                        [Button.inline(accent("شراء سيشن 💾"), f"bscountries_{found_country['calling_code']}_{found_country['name']}_{found_country['price']}")]
                    ]
                    await l_edit(event, message_text, buttons=buttons)
                    return
                else:
                    await event.answer("- هذه الدولة غير موجودة.", alert=True)
                    return

        buttons_per_page = 20
        total_pages = (len(countries) + buttons_per_page - 1) // buttons_per_page

        current_page = int(data.split('_')[1]) if data.startswith("bspage_") else 0

        if current_page < 0:
            current_page = 0
        elif current_page >= total_pages:
            current_page = total_pages - 1

        start_index = current_page * buttons_per_page
        end_index = start_index + buttons_per_page
        page_countries = countries[start_index:end_index]

        buttons = []
        row = []
        for code in page_countries:
            calling_code = code['calling_code']
            name = code['name']
            price = code['price']
            if len(row) < 2:
                row.append(Button.inline(text=accent(f"{name} : {price} $"), data=f"bscountries_{calling_code}_{name}_{price}"))
            else:
                buttons.append(row)
                row = [Button.inline(text=accent(f"{name} : {price} $"), data=f"bscountries_{calling_code}_{name}_{price}")]
        if row:
            buttons.append(row)

        navigation_buttons = []
        if current_page > 0:
            navigation_buttons.append(Button.inline(text=accent("⏪"), data=f"bspage_{current_page - 1}"))
        if current_page < total_pages - 1:
            navigation_buttons.append(Button.inline(text=accent("⏩"), data=f"bspage_{current_page + 1}"))
        if navigation_buttons:
            buttons.append(navigation_buttons)

        buttons.append([Button.inline(text=accent("AUTO 🔍"), data="bssearch_country")])
        buttons.append([Button.inline(text=accent("رجوع ↪️"), data="main")])

        await l_edit(event, "**💾 شراء سيشن جاهز**\n\n- اختر الدولة اللي عايز تشتري سيشن منها، هتستلم ملف السيشن (.session) فورًا بعد الدفع.", parse_mode='markdown', buttons=buttons)
        return

    if data.startswith("bscountries_"):
        calling_code = data.split('_')[1]
        name = data.split('_')[2]
        price = data.split('_')[3]
        coins = get_user_record(user_id)['coins']
        if float(coins) < float(price):
            return await event.answer("- رصيدك لا يكفي لشراء سيشن من هذه الدولة.", alert=True)
        accounts = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
        if not accounts:
            return await event.answer("- لا توجد أي حسابات في هذه الدولة.", alert=True)
        keyboard = [
            [
                Button.inline(accent("الغاء ❌"), data="bsbuy"),
                Button.inline(accent("تأكيد ✅"), data=f"bspurchase_{calling_code}_{name}_{price}")
            ],
        ]
        await l_edit(event,
            f"**✅︙هل انت متاكد من شراء سيشن ؟**\n⌁︙معلومات الحساب :-\n\n🌎︙ الدولة: {name}\n💰︙ سعر السيشن : {price} $\n\n"
            "**🚀︙اذا تريد الشراء اضغط (تأكيد ✅) و اذا لا تريد اضغط (الغاء ❌)**",
            parse_mode='markdown', buttons=keyboard)
        return

    if data.startswith("bspurchase_"):
        calling_code = data.split('_')[1]
        name = data.split('_')[2]
        price = data.split('_')[3]

        acc = get_user_record(user_id)
        if float(acc['coins']) < float(price):
            return await event.answer("- رصيدك لا يكفي لشراء سيشن من هذه الدولة.", alert=True)

        i = take_random_account(calling_code)
        if i is None:
            return await event.answer("- لا توجد حسابات في هذه الدولة.", alert=True)

        acc['coins'] -= float(price)
        db.set(f"user_{user_id}", acc)

        await event.answer("⏳ جاري تجهيز ملف السيشن...")

        tmp_dir = "database/tmp_sessions"
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_base = os.path.join(tmp_dir, f"deliver_{uuid.uuid4().hex}")
        try:
            fresh = await asyncio.wait_for(make_fresh_session(i['session'], _pw_candidates(i)), 90)
            file_path = string_session_to_file(fresh, tmp_base)
            now = datetime.datetime.now()
            current_time = now.strftime("%Y-%m-%d %H:%M:%S")

            caption = f"**💾 تم شراء السيشن بنجاح ✅**\nالرقم : `{i['phone_number']}`\nالدولة : {name}\nالسعر : {price} $"
            if acc_pw(i):
                caption += f"\n🔑 التحقق بخطوتين : `{acc_pw(i)}`"
            await client.send_file(user_id, file_path, caption=caption, parse_mode='markdown',
                                    attributes=[DocumentAttributeFilename(file_name=f"{i['phone_number']}.session")])

            keyboard = [[Button.inline(accent("رجــوع ↪️"), data="main")]]
            await l_edit(event, "✅ تم إرسال ملف السيشن لك في الخاص.", buttons=keyboard)

            await post_trust_session(name, price, user_id, phone=i['phone_number'])
        except Exception as ex:
            print("session delivery error:", ex)
            # فشل التجهيز: نرجّع الحساب للمخزون ونرد له فلوسه
            accounts = db.get(accounts_key(calling_code)) if db.exists(accounts_key(calling_code)) else []
            accounts.append(i)
            db.set(accounts_key(calling_code), accounts)
            acc = get_user_record(user_id)
            acc['coins'] += float(price)
            db.set(f"user_{user_id}", acc)
            await event.answer("❌ حصل خطأ أثناء تجهيز السيشن، تم استرجاع رصيدك.", alert=True)
        finally:
            for ext in (".session", ".session-journal"):
                try:
                    if os.path.exists(tmp_base + ext):
                        os.remove(tmp_base + ext)
                except Exception:
                    pass
        return

    if data == "add_admin":
        async with bot.conversation(event.chat_id) as x:
            await x.send_message("-ارسل ايدي المستخدم الذي تريد رفعه ادمن في البوت")
            name = await x.get_response()
            try:
                id = int(name.text)
            except:
                return await x.send_message("- ارسل الايدي ارقام فقط")
            admins = db.get("admins")
            if id in admins:
                return await x.send_message("- هذا المستخدم ادمن بالفعل ❗")
            admins.append(id)
            db.set("admins", admins)
            await x.send_message("- تم رفع المستخدم ادمن في البوت ✅")
            
    if data == "del_admin":
        async with bot.conversation(event.chat_id) as x:
            await x.send_message("- ارسل ايدي المستخدم المراد تنزيله من الادمنيه")
            name = await x.get_response()
            try:
                id = int(name.text)
            except:
                return await x.send_message("- ارسل الايدي ارقام فقط")
            admins = db.get("admins")
            if id not in admins:
                return await x.send_message("- المستخدم ليس ادمن بالفعل ❗")
            admins.remove(id)
            db.set("admins", admins)
            await x.send_message("- تم تنزيل المستخدم من الادمن بنجاح ✅")

    if data == "referral":
        user_info = db.get(f"user_{user_id}") if db.exists(f"user_{user_id}") else {"coins": 0, "coind": 0}
        
        if not is_bot_enabled():
            await l_edit(event, "⚠️ البوت حاليا تحت الصيانة سيتم اعادة تشغيله في الساعات القادمه")
            return
        
        bot_info = await client.get_me()
        bot_username = bot_info.username
        
        referral_amount = get_referral_reward()
        
        referral_link = f"https://t.me/{bot_username}?start={user_id}"
        
        reward_display = display_usd_price(referral_amount, user_id)
        referral_balance_display = display_usd_price(user_info.get('referral_balance', 0), user_id)
        if user_language(user_id) == "en":
            referral_text = (f"🤑 Earn free balance by sharing your referral link.\n\n"
                             f"Reward per referral: {reward_display}\n\n"
                             f"Your referral link:\n\n`{referral_link}`\n\n"
                             f"🎁 Referral balance: {referral_balance_display}\n👥 Referrals: {user_info.get('referrals_count', 0)}")
        else:
            referral_text = (f"🤑 ⌯ إربح رصيدًا مجانًا عبر مشاركة رابط البوت مع أصدقائك 👥\n\n"
                             f"المكافأة لكل إحالة: {reward_display}\n"
                             f"📌 المكافأة تُضاف بعد أول عملية شراء لرقم يقوم بها صديقك.\n\n"
                             f"☑️ رابط الدعوة الخاص بك:\n\n`{referral_link}`\n\n"
                             f"🎁 رصيد الإحالات: {referral_balance_display}\n👥 عدد الإحالات: {user_info.get('referrals_count', 0)}")
        await l_edit(event, referral_text, buttons=[[Button.inline(accent("🔙 Back") if user_language(user_id)=="en" else "رجوع", data="main")]])

async def start_countdown(chat_id, message_id, currency, remaining_time):
    while remaining_time > 0:
        minutes = remaining_time // 60
        seconds = remaining_time % 60

        address = pending_deposits[chat_id]['address']
        network = pending_deposits[chat_id]['network']

        buttons = [[Button.inline(accent('ارسلت المبلغ ✅'), f'enter_txid_{currency}')]]
        try:
            await client.edit_message(
                chat_id,
                message_id,
                f'ارسل المبلغ الذي تريد ايداعه في حسابك الى العنوان التالي ثم اضغط على زر ارسلت المبلغ 💵\n\n'
                f'🖇 العنوان :\n `{address}`\n\n🪩 الشبكة : `{network}`\n\n'
                f'⏳ وقت العملية : 60:00',
                buttons=buttons
            )
        except Exception as e:
            print(f"Error updating message: {e}")

        await asyncio.sleep(1)
        remaining_time -= 1

    await client.edit_message(chat_id, message_id, "Time is up. The deposit window has closed.")
    del pending_deposits[chat_id]

async def cancel_start(event):
    user_id = event.chat_id
    await l_respond(event, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
    return

async def create_account(event):
    user_id = event.chat_id
    if not db.exists(f"user_{user_id}"):
        db.set(f"user_{user_id}", {"coins": 0, "id": user_id, "password": "default_password", "joined_at": time.time()})
        
        await l_respond(event, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
        await l_respond(event, "✅ | تم إنشاء حسابك بنجاح \n\n شكرًا لاستخدامك بوت عفروتو.")
    else:
        await l_respond(event, client_main_panel(user_id), parse_mode='markdown', buttons=main_menu_buttons(user_id))
        await l_respond(event, "✅ | تم إنشاء حسابك بنجاح \n\n شكرًا لاستخدامك بوت فوكس.")

async def broadcast_message(event, message):
    users = db.keys('user_%') 
    users = [key[0] if isinstance(key, tuple) else key for key in users]

    total_users = len(users)
    success, failed = 0, 0

    progress_message = await l_respond(event, 
        f"📢 **جارٍ اذاعة الرسالة**\n\n"
        f"✅ **تم الإرسال :** {success}\n"
        f"❌ **فشل الإرسال :** {failed}\n\n"
        f"👥 **إجمالي المستخدمين :** {total_users}"
    )

    for index, user_key in enumerate(users):
        user_id = user_key.replace("user_", "") 
        try:
            await l_client_send_message(client, int(user_id), message)
            success += 1
        except Exception as e:
            print(f"⚠️ فشل إرسال الرسالة إلى {user_id}: {e}")
            failed += 1

        if index % 10 == 0 or index == total_users - 1:
            await progress_message.edit(
                f"📢 **جارٍ اذاعة الرسالة**\n\n"
                f"✅ **تم الإرسال :** {success}\n"
                f"❌ **فشل الإرسال :** {failed}\n\n"
                f"👥 **إجمالي المستخدمين :** {total_users}"
            )
        await asyncio.sleep(0.1)

    await progress_message.edit(
        f"🎉 **اكتملت عملية الاذاعة**\n\n"
        f"✅ **تم الإرسال :** {success}\n"
        f"❌ **فشل الإرسال :** {failed}\n\n"
        f"👥 **إجمالي المستخدمين :** {total_users}"
    )
    
#غير الحقوق واثبت انك فاشل اذا تريد تنقل اذكر اسمي او اسم قناتي #

#====================#
#CH : @J_D_D_M 
#DEV : @J_D_D_M
#====================#
    
async def count_ses(session):
    api_hash='e0e2a70a885d1497c8feb47815bb3e36'
    api_id=24188127
    try:
        app = TelegramClient(StringSession(session), api_id=API_ID, api_hash=API_HASH)
        await app.connect()
        try:
            resulkt = await app(functele.auth.ResetAuthorizationsRequest())
        except:
            pass
        unauthorized_attempts = await app(GetAuthorizationsRequest())
        listt = []
        for i in unauthorized_attempts.authorizations:
        	mod = listt.append(i.device_model)
        return listt
    except Exception as a:
        print(str(a))
        return str(a)

# ============================================================
#  النسخ الاحتياطي التلقائي (كل 6 ساعات) + الاستعادة
#  النسخة = كل ملفات مجلد database (الأرقام، الجلسات، المستخدمين، الأرصدة، الإعدادات... إلخ)
#  بتتحفظ في مجلد backups (برا database عشان متتكررش جوا نفسها) وبتتبعت للأدمن.
# ============================================================
import zipfile
import tempfile

BACKUP_DIR = "backups"
BACKUP_INTERVAL = 6 * 3600
BACKUP_KEEP = 12
DB_DIR = "database"

def _backup_files():
    if not os.path.isdir(BACKUP_DIR):
        return []
    files = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
             if f.startswith("backup_") and f.endswith(".zip")]
    files.sort(key=lambda f: os.path.getmtime(f))
    return files

def _last_backup_text():
    files = _backup_files()
    if not files:
        return "لا توجد"
    return datetime.datetime.fromtimestamp(os.path.getmtime(files[-1])).strftime("%Y-%m-%d %H:%M")

def _is_sqlite_file(path):
    try:
        with open(path, "rb") as f:
            return f.read(16).startswith(b"SQLite format 3")
    except Exception:
        return False

def _make_backup_zip(prefix="backup"):
    """بيعمل zip كامل لمجلد database (لقطة سليمة لقواعد sqlite حتى لو البوت شغال)."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(BACKUP_DIR, f"{prefix}_{stamp}.zip")
    tmp = tempfile.mkdtemp(prefix="bk_")
    try:
        with zipfile.ZipFile(out + ".part", "w", zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(DB_DIR):
                dirs[:] = [d for d in dirs if not d.startswith("tmp")]
                for fn in files:
                    if fn.endswith(("-wal", "-shm", "-journal")):
                        continue
                    src = os.path.join(root, fn)
                    rel = os.path.relpath(src, DB_DIR)
                    use = src
                    if _is_sqlite_file(src):
                        snap = os.path.join(tmp, uuid.uuid4().hex)
                        try:
                            a = sqlite3.connect(src)
                            b = sqlite3.connect(snap)
                            a.backup(b)
                            b.close(); a.close()
                            use = snap
                        except Exception as ex:
                            print("backup snapshot fallback:", ex)
                    z.write(use, rel)
        os.replace(out + ".part", out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            if os.path.exists(out + ".part"):
                os.remove(out + ".part")
        except Exception:
            pass
    if prefix == "backup":
        for old in _backup_files()[:-BACKUP_KEEP]:
            try:
                os.remove(old)
            except Exception:
                pass
    return out

def _restore_backup_zip(zip_path):
    """بيرجّع قاعدة البيانات من النسخة. بيعمل نسخة أمان قبل الاستبدال."""
    global db
    with zipfile.ZipFile(zip_path) as z:
        if z.testzip() is not None:
            raise RuntimeError("ملف النسخة تالف")
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not names:
            raise RuntimeError("النسخة فاضية")
        for n in names:
            if os.path.isabs(n) or ".." in n.replace("\\", "/").split("/"):
                raise RuntimeError("مسار غير آمن داخل النسخة")
    _make_backup_zip(prefix="prerestore")
    tmp = tempfile.mkdtemp(prefix="rs_")
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        try:
            if hasattr(db, "close"):
                db.close()
        except Exception:
            pass
        for n in names:
            src = os.path.join(tmp, n)
            dst = os.path.join(DB_DIR, n)
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            for ext in ("-wal", "-shm", "-journal"):
                try:
                    if os.path.exists(dst + ext):
                        os.remove(dst + ext)
                except Exception:
                    pass
            staged = dst + ".restoring"
            shutil.copyfile(src, staged)
            os.replace(staged, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        db = uu('database/FFJFF5.ss', 'bot')

def _all_admin_ids():
    ids = {int(admin)}
    try:
        for x in (db.get("admins") if db.exists("admins") else []) or []:
            ids.add(int(x))
    except Exception:
        pass
    return ids

async def _send_backup_to_admins(path, caption, only=None):
    size_mb = os.path.getsize(path) / (1024 * 1024)
    targets = [only] if only else sorted(_all_admin_ids())
    for aid in targets:
        try:
            if size_mb > 49:
                await client.send_message(aid, f"⚠️ النسخة الاحتياطية حجمها {size_mb:.1f}MB (أكبر من حد تيليجرام). محفوظة على السيرفر في: {path}")
            else:
                await client.send_file(aid, path, caption=caption,
                                       attributes=[DocumentAttributeFilename(file_name=os.path.basename(path))])
        except Exception as ex:
            print("backup send error:", ex)

RESTORE_WAIT = set()      # أدمنية مستنيين يبعتوا ملف قاعدة
RESTORE_PENDING = {}      # uid -> مسار zip جاهز للاستعادة بعد التأكيد

def _prepare_restore_upload(path):
    """بيتأكد إن الملف المرفوع قاعدة صالحة (zip نسخة احتياطية أو ملف FFJFF5.ss) وبيرجّع مسار zip جاهز."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                raise RuntimeError("ملف الـ zip تالف")
            names = [n.replace("\\", "/") for n in z.namelist() if not n.endswith("/")]
            if "FFJFF5.ss" not in names:
                raise RuntimeError("الـ zip مفيهوش قاعدة البوت (FFJFF5.ss)")
            with z.open([n for n in z.namelist() if n.replace("\\", "/") == "FFJFF5.ss"][0]) as f:
                if not f.read(16).startswith(b"SQLite format 3"):
                    raise RuntimeError("FFJFF5.ss جوه الـ zip مش قاعدة صالحة")
        return path
    if _is_sqlite_file(path):
        z_path = path + ".zip"
        with zipfile.ZipFile(z_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(path, "FFJFF5.ss")
        return z_path
    raise RuntimeError("الملف مش zip ولا قاعدة SQLite صالحة")

async def backup_router(event, action, user_id):
    back = [Button.inline(accent("🔙 رجوع"), data="adm:set")]
    if action == "now":
        await event.answer("⏳ جاري عمل النسخة...")
        try:
            path = await asyncio.to_thread(_make_backup_zip)
            await _send_backup_to_admins(path, f"🗄 نسخة احتياطية كاملة — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", only=user_id)
            text, buttons = admin_section("set")
            await l_edit(event, text, parse_mode='markdown', buttons=buttons)
        except Exception as ex:
            print("backup error:", ex)
            await l_edit(event, f"❌ فشل عمل النسخة: {ex}", buttons=[back])
    elif action == "upload":
        RESTORE_WAIT.add(int(user_id))
        await l_edit(event,
            "**📥 استعمال قاعدة بيانات**\n\nابعتلي دلوقتي ملف القاعدة:\n"
            "• ملف النسخة الاحتياطية (.zip) اللي البوت بعتهولك\n• أو ملف القاعدة نفسه (FFJFF5.ss)\n\n"
            "هتتأكد من الملف الأول وبعدين تأكد الاستعادة.",
            parse_mode='markdown',
            buttons=[[Button.inline(accent("❌ إلغاء"), data="bk:cancel")]])
    elif action == "cancel":
        RESTORE_WAIT.discard(int(user_id))
        RESTORE_PENDING.pop(int(user_id), None)
        text, buttons = admin_section("set")
        await l_edit(event, text, parse_mode='markdown', buttons=buttons)
    elif action == "go":
        path = RESTORE_PENDING.pop(int(user_id), None)
        if not path or not os.path.exists(path):
            await event.answer("❌ مفيش ملف جاهز للاستعادة. ابدأ من جديد.", alert=True)
            return
        await event.answer("⏳ جاري الاستعادة...")
        try:
            await asyncio.to_thread(_restore_backup_zip, path)
            await l_edit(event, "✅ تم استرجاع قاعدة البيانات بنجاح.\n🔄 لو لاحظت أي حاجة غير طبيعية أعد تشغيل البوت.", buttons=[back])
        except Exception as ex:
            print("restore error:", ex)
            await l_edit(event, f"❌ فشلت الاستعادة: {ex}", buttons=[back])

@client.on(events.NewMessage(func=lambda e: e.is_private and e.document is not None and e.sender_id in RESTORE_WAIT))
async def restore_file_receiver(event):
    uid = int(event.sender_id)
    if not _is_admin_user(uid):
        RESTORE_WAIT.discard(uid)
        return
    os.makedirs(os.path.join(BACKUP_DIR, "incoming"), exist_ok=True)
    name = os.path.basename(event.file.name or "upload.bin")
    path = os.path.join(BACKUP_DIR, "incoming", f"in_{uuid.uuid4().hex}_{name}")
    msg = await event.respond("⏳ جاري تنزيل الملف والتحقق منه...")
    try:
        await event.download_media(file=path)
        ready = await asyncio.to_thread(_prepare_restore_upload, path)
    except Exception as ex:
        await msg.edit(f"❌ الملف غير صالح: {ex}\n\nابعت ملف تاني أو اضغط إلغاء.",
                       buttons=[[Button.inline(accent("❌ إلغاء"), data="bk:cancel")]])
        return
    RESTORE_WAIT.discard(uid)
    RESTORE_PENDING[uid] = ready
    await msg.edit("✅ الملف سليم.\n\n⚠️ الاستعادة هتستبدل كل بيانات البوت الحالية (الأرقام، الجلسات، الأرصدة، المستخدمين، الإعدادات) بالملف ده. بيتعمل نسخة أمان قبلها.",
                   buttons=[[Button.inline(accent("✅ تأكيد الاستعادة"), data="bk:go")],
                            [Button.inline(accent("❌ إلغاء"), data="bk:cancel")]])

async def auto_backup_loop():
    await asyncio.sleep(30)
    while True:
        try:
            files = _backup_files()
            last = os.path.getmtime(files[-1]) if files else 0
            if time.time() - last >= BACKUP_INTERVAL:
                path = await asyncio.to_thread(_make_backup_zip)
                await _send_backup_to_admins(path, f"🗄 نسخة احتياطية تلقائية (كل 6 ساعات) — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        except Exception as ex:
            print("auto backup error:", ex)
        await asyncio.sleep(300)

# ============================================================
#  الهدية اليومية + روابط النقاط
# ============================================================
_BOT_UNAME = None
async def _bot_username():
    global _BOT_UNAME
    if not _BOT_UNAME:
        _BOT_UNAME = (await client.get_me()).username
    return _BOT_UNAME

@client.on(events.CallbackQuery(data=b'pl:new'))
async def points_link_new(event):
    if not _is_admin_user(event.sender_id):
        return await event.answer("❌ للأدمن فقط.", alert=True)
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("💵 أرسل عدد النقاط (الرصيد بالدولار) اللي كل شخص ياخدها من الرابط، مثال: 0.10")
        try:
            amount = float((await x.get_response()).text.strip().replace(",", "."))
            if amount <= 0:
                raise ValueError
        except Exception:
            return await x.send_message("❌ المبلغ غير صحيح.")
        await x.send_message("👥 أرسل أقصى عدد أشخاص يقدروا يستخدموا الرابط، مثال: 20")
        try:
            uses = int((await x.get_response()).text.strip())
            if uses <= 0:
                raise ValueError
        except Exception:
            return await x.send_message("❌ العدد غير صحيح.")
        links = _dbget("points_links", {}) or {}
        for _ in range(20):
            token = secrets.token_urlsafe(6)
            if token not in links:
                break
        links[token] = {"amount": round(amount, 8), "remaining": uses, "max_uses": uses, "used_by": []}
        db.set("points_links", links)
        uname = await _bot_username()
        await x.send_message(
            f"✅ **تم إنشاء رابط النقاط**\n\n🔗 `https://t.me/{uname}?start=pl_{token}`\n"
            f"💵 القيمة: {money(amount)}$ لكل شخص\n👥 عدد الاستخدامات: {uses}\n\nكل شخص يستخدمه مرة واحدة بس.",
            parse_mode='markdown', buttons=[[Button.inline(accent("🔙 رجوع"), data="adm:free")]])

@client.on(events.CallbackQuery(data=b'pl:list'))
async def points_link_list(event):
    if not _is_admin_user(event.sender_id):
        return await event.answer("❌ للأدمن فقط.", alert=True)
    links = _dbget("points_links", {}) or {}
    uname = await _bot_username()
    if not links:
        text = "🔗 لا توجد روابط نقاط."
    else:
        lines = ["🔗 **روابط النقاط**", ""]
        for token, info in list(links.items())[-15:]:
            lines.append(f"`https://t.me/{uname}?start=pl_{token}`\n💵 {money(info.get('amount', 0))}$ — متبقي {int(info.get('remaining', 0))}/{int(info.get('max_uses', 0))}\n")
        text = "\n".join(lines)
    await l_edit(event, text, parse_mode='markdown', buttons=[[Button.inline(accent("🔙 رجوع"), data="adm:free")]])

async def _claim_points_link(uid, token):
    links = _dbget("points_links", {}) or {}
    info = links.get(token)
    if not info:
        return "❌ رابط النقاط غير صحيح."
    if int(info.get("remaining", 0)) <= 0:
        return "❌ رابط النقاط ده خلص عدد استخداماته."
    if int(uid) in [int(i) for i in info.get("used_by", [])]:
        return "❌ استخدمت الرابط ده قبل كده."
    amount = money_value(info.get("amount", 0))
    user = get_user_record(uid)
    user["coins"] = money_value(float(user.get("coins", 0) or 0) + amount)
    info["remaining"] = int(info.get("remaining", 0)) - 1
    info["used_by"] = list(info.get("used_by", [])) + [int(uid)]
    links[token] = info
    db.set(f"user_{uid}", user)
    db.set("points_links", links)
    return f"🎁 مبروك! تمت إضافة {display_usd_price(amount, uid)} لرصيدك.\n💰 رصيدك الآن: {display_usd_price(user['coins'], uid)}"

# ============================================================
#  بيع الحسابات للبوت + أسعار الدول + رصيد البائعين + سحب الأرباح
#  (القسم ده مستقل عن باقي الكود وبيستخدم مفاتيح قاعدة بيانات خاصة بيه)
# ============================================================
SELL_TYPES = {
    "clean": "حسابات سليمة",
    "temp": "حسابات محظورة مؤقت",
    "spam": "حسابات سبام",
}
SELL_TYPES_SHORT = {
    "clean": "سليم",
    "temp": "محظور مؤقت",
    "spam": "سبام",
}
SELLER_PRICES_KEY = "seller_account_prices"
SELLER_PRICE_CURRENCY_KEY = "seller_price_currency"
SELLER_BALANCE_CURRENCY_KEY = "seller_balance_currency"
SELLER_MIN_WITHDRAW_KEY = "seller_min_withdraw"
_SELLER_LOCKS = {}
_SESSION_CHECK_LOCKS = {}


def _norm_currency(value):
    return "EGP" if str(value).upper() == "EGP" else "USD"


def _price_currency():
    try:
        return _norm_currency(db.get(SELLER_PRICE_CURRENCY_KEY) if db.exists(SELLER_PRICE_CURRENCY_KEY) else "USD")
    except Exception:
        return "USD"


def _seller_currency():
    try:
        value = db.get(SELLER_BALANCE_CURRENCY_KEY) if db.exists(SELLER_BALANCE_CURRENCY_KEY) else "EGP"
        return _norm_currency(value)
    except Exception:
        return "EGP"


def _seller_min_withdraw():
    try:
        return max(0.0, float(db.get(SELLER_MIN_WITHDRAW_KEY) if db.exists(SELLER_MIN_WITHDRAW_KEY) else 5))
    except Exception:
        return 5.0


def _cur_symbol(cur):
    return "ج.م" if _norm_currency(cur) == "EGP" else "$"


def _fmt_cur(value, cur):
    try:
        value = float(value)
    except Exception:
        value = 0.0
    return f"{value:.2f} {_cur_symbol(cur)}"


def _convert_amount(value, src, dst):
    """تحويل بين الدولار والجنيه بسعر الصرف اللي الأدمن محدده."""
    try:
        value = float(value)
    except Exception:
        value = 0.0
    src, dst = _norm_currency(src), _norm_currency(dst)
    if src == dst:
        return value
    rate = get_global_exchange_rate()
    if src == "USD" and dst == "EGP":
        return value * rate
    if src == "EGP" and dst == "USD":
        return value / rate if rate else value
    return value


def _seller_prices():
    data = _dbget(SELLER_PRICES_KEY, {}) or {}
    return data if isinstance(data, dict) else {}


def _save_seller_prices(data):
    db.set(SELLER_PRICES_KEY, data)


def _country_meta(digits):
    """بيرجّع (الاسم, العلم) لرمز دولة من جدول CALLING_CODES."""
    digits = str(digits or "").lstrip("+")
    raw = CALLING_CODES.get(digits)
    if raw:
        parts = str(raw).split(" ", 1)
        if len(parts) == 2:
            return parts[1].strip(), parts[0].strip()
        return str(raw), "🌍"
    # أطول رمز متطابق (مثال 1 و 12 و 123)
    for size in range(min(4, len(digits)), 0, -1):
        raw = CALLING_CODES.get(digits[:size])
        if raw:
            parts = str(raw).split(" ", 1)
            if len(parts) == 2:
                return parts[1].strip(), parts[0].strip()
    return f"دولة +{digits}", "🌍"


def _match_seller_country(digits):
    """أطول رمز دولة متطابق مع الدول اللي الأدمن ضاف أسعارها."""
    digits = re.sub(r"\D", "", str(digits or ""))
    prices = _seller_prices()
    best = None
    for item in prices.values():
        code = re.sub(r"\D", "", str(item.get("code", "")))
        if code and digits.startswith(code) and (best is None or len(code) > len(best.get("code", ""))):
            best = item
    return best


def _find_price_entry(digits):
    digits = re.sub(r"\D", "", str(digits or ""))
    prices = _seller_prices()
    if digits in prices:
        return prices[digits]
    return _match_seller_country(digits)


def _display_price_for_user(value, uid):
    dst = user_currency(uid)
    return _fmt_cur(_convert_amount(value, _price_currency(), dst), dst)


def _add_seller_country(digits):
    digits = re.sub(r"\D", "", str(digits or ""))
    if not digits:
        return None
    prices = _seller_prices()
    if digits in prices:
        return prices[digits]
    name, flag = _country_meta(digits)
    entry = {"code": "+" + digits, "digits": digits, "name": name, "flag": flag,
             "clean": 0.0, "temp": 0.0, "spam": 0.0}
    prices[digits] = entry
    _save_seller_prices(prices)
    return entry


def _prices_panel_payload():
    prices = _seller_prices()
    cur = _price_currency()
    seller_cur = _seller_currency()
    lines = [
        "**💵 أسعار الحسابات (التي يشتريها البوت من المستخدمين)**",
        "",
        f"💱 عملة الأسعار: **{cur}**",
        f"💰 عملة رصيد البائعين: **{seller_cur}**",
        f"🏧 أدنى حد للسحب: **{_fmt_cur(_seller_min_withdraw(), seller_cur)}**",
        "",
    ]
    if not prices:
        lines.append("لا توجد دول مضافة حاليًا. اضغط «➕ إضافة دولة» وابعت رمز الدولة.")
    else:
        for item in list(prices.values())[:20]:
            lines.append(f"{item.get('flag', '🌍')} {item.get('name', '')} {item.get('code', '')}")
            lines.append(
                f"   ✅ {SELL_TYPES_SHORT['clean']}: {_fmt_cur(item.get('clean', 0), cur)}"
                f" | ⏳ {SELL_TYPES_SHORT['temp']}: {_fmt_cur(item.get('temp', 0), cur)}"
                f" | 🚫 {SELL_TYPES_SHORT['spam']}: {_fmt_cur(item.get('spam', 0), cur)}"
            )
        if len(prices) > 20:
            lines.append(f"\n... و{len(prices) - 20} دولة أخرى")
    buttons = [
        [Button.inline(accent("➕ إضافة دولة"), data="spr:add")],
        [Button.inline(accent("✏️ تعديل أسعار دولة"), data="spr:list")],
        [Button.inline(accent(f"💱 عملة الأسعار: {cur} (تغيير)"), data="spr:cur"),
         Button.inline(accent(f"💰 عملة البائعين: {seller_cur} (تغيير)"), data="spr:sellercur")],
        [Button.inline(accent("🏧 تحديد أدنى حد للسحب"), data="spr:min")],
        [Button.inline(accent("🔙 رجوع"), data="admin_panel")],
    ]
    return "\n".join(lines), buttons


def _price_edit_payload(digits):
    entry = _find_price_entry(digits)
    if not entry:
        return "❌ الدولة غير موجودة.", [[Button.inline(accent("🔙 رجوع"), data="spr:home")]]
    cur = _price_currency()
    lines = [
        f"**{entry.get('flag', '🌍')} {entry.get('name', '')} — {entry.get('code', '')}**",
        "",
        "اضغط على النوع اللي عايز تحدد سعره:",
    ]
    buttons = []
    for key in ("clean", "temp", "spam"):
        buttons.append([Button.inline(
            accent(f"{SELL_TYPES_SHORT[key]}: {_fmt_cur(entry.get(key, 0), cur)}"),
            data=f"spr:price:{entry.get('digits', digits)}:{key}")])
    buttons.append([Button.inline(accent("🗑 حذف الدولة"), data=f"spr:del:{entry.get('digits', digits)}")])
    buttons.append([Button.inline(accent("🔙 رجوع"), data="spr:home")])
    return "\n".join(lines), buttons


def _target_category(type_key):
    """بيرجّع قسم المخزون اللي الحساب هيتضاف فيه حسب نوعه."""
    wanted = SELL_TYPES_SHORT.get(type_key, "سبام")
    if type_key == "spam" and get_category(DEFAULT_CAT):
        return DEFAULT_CAT
    for c in get_categories():
        name = str(c.get("name", ""))
        if c.get("id") == ("slim" if type_key == "clean" else "tempbanned"):
            return c["id"]
        if name.strip() == wanted or wanted in name:
            return c["id"]
    desired_id = {"clean": "slim", "temp": "tempbanned", "spam": DEFAULT_CAT}.get(type_key, DEFAULT_CAT)
    if get_category(desired_id):
        return desired_id
    cats = get_categories()
    cats.append({"id": desired_id, "name": wanted})
    db.set("categories", cats)
    db.set(countries_key(desired_id), [])
    return desired_id


def _seller_balance(user_id):
    rec = get_user_record(user_id)
    try:
        return float(rec.get("seller_balance", 0) or 0)
    except Exception:
        return 0.0


def _add_seller_balance(user_id, amount):
    rec = get_user_record(user_id)
    try:
        current = float(rec.get("seller_balance", 0) or 0)
    except Exception:
        current = 0.0
    rec["seller_balance"] = money_value(current + float(amount))
    try:
        rec["seller_earnings_total"] = money_value(float(rec.get("seller_earnings_total", 0) or 0) + float(amount))
    except Exception:
        rec["seller_earnings_total"] = money_value(float(amount))
    db.set(f"user_{int(user_id)}", rec)
    return rec["seller_balance"]


def _phone_in_stock(phone):
    """هل الرقم موجود بالفعل في مخزون أي قسم؟ (عشان مندفعش مرتين لنفس الرقم)."""
    phone = str(phone or "").lstrip("+")
    if not phone:
        return False
    for c in get_categories():
        for country in _dbget(countries_key(c.get("id")), []):
            for a in _dbget(accounts_key(country.get("calling_code"), c.get("id")), []):
                if str(a.get("phone_number", "")).lstrip("+") == phone:
                    return True
    return False


async def _spambot_status(app):
    """بيفحص حالة الرقم من @SpamBot باستخدام جلسة الحساب نفسه."""
    try:
        await app.send_message("SpamBot", "/start")
        text = ""
        for _ in range(5):
            await asyncio.sleep(2)
            messages = await app.get_messages("SpamBot", limit=4)
            incoming = [m.message for m in messages if m and not getattr(m, "out", False) and getattr(m, "message", None)]
            if incoming:
                text = incoming[0]
                break
        low = (text or "").lower()
        if not low.strip():
            return None, text
        if any(k in low for k in ("no limits", "free as a bird", "good news", "لا توجد قيود", "حسابك خالي")):
            return "clean", text
        if any(k in low for k in ("limited until", "temporarily", "temporary", "مؤقت", "حتى")):
            return "temp", text
        if any(k in low for k in ("limited", "spam", "restricted", "محدود", "سبام", "محظور")):
            return "spam", text
        return None, text
    except Exception as ex:
        return None, str(ex)


async def _only_bot_session(session_str):
    """هل الحساب بقى فيه جلسة البوت بس؟
    بترجّع True لو تمام، False لو لسه فيه جلسات، و"dead" لو جلسة البوت نفسها اتقفلت."""
    # قفل لكل جلسة عشان الفحص التلقائي واليدوي ميتصلوش بنفس الجلسة في نفس اللحظة.
    lock_key = str(session_str or "")[:24] or "empty"
    lock = _SESSION_CHECK_LOCKS.setdefault(lock_key, asyncio.Lock())
    async with lock:
        app = None
        try:
            app = TelegramClient(StringSession(session_str), api_id=int(API_ID), api_hash=API_HASH)
            await app.connect()
            if not await app.is_user_authorized():
                return "dead"
            result = await app(GetAuthorizationsRequest())
            return len(result.authorizations) <= 1
        except Exception as ex:
            print("seller logout check error:", type(ex).__name__, ex)
            return False
        finally:
            try:
                if app:
                    await app.disconnect()
            except Exception:
                pass


async def _finalize_seller_account(uid, pending):
    """بيضيف الحساب للمخزون ويحسب رصيد البائع بعد ما يتأكد إنه خرج من كل الجلسات."""
    uid = int(uid)
    phone = str(pending.get("phone", "")).lstrip("+")
    if not phone:
        return False
    done_key = f"shp_done_{phone}"
    if db.exists(done_key):
        return False
    lock = _SELLER_LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        if db.exists(done_key):
            return False
        type_key = pending.get("type", "spam")
        cid = _target_category(type_key)
        code = str(pending.get("code", ""))
        if code and not code.startswith("+"):
            code = "+" + code
        name = pending.get("name") or _country_meta(code)[0]
        flag = pending.get("flag") or _country_meta(code)[1]
        if _phone_in_stock(phone):
            # الرقم موجود عندنا بالفعل: منضيفوش تاني ومنحسبش فلوس للبائع.
            if db.exists(f"shp_pending_{uid}"):
                db.delete(f"shp_pending_{uid}")
            try:
                await l_client_send_message(client, uid,
                    f"⚠️ الرقم `+{phone}` موجود بالفعل في مخزون البوت، فمش هيتضاف تاني ومش هيتحسب له رصيد.\n"
                    "لو عندك رقم تاني تقدر تبيعه من قسم «بيع الحسابات».",
                    parse_mode='markdown')
            except Exception as ex:
                print("seller duplicate message error:", ex)
            return False
        country_missing = find_country(cid, code) is None
        # مهم: مش بنضيف دولة جديدة بسعر صفر (كانت هتخلي الحساب يتباع مجانًا).
        # لو الدولة مش مضافة في القسم، بنخزّن الحساب والأدمن بيضيف الدولة بسعر البيع عشان يظهر.
        accounts = _dbget(accounts_key(code, cid), [])
        if not any(str(a.get("phone_number")) == phone for a in accounts):
            accounts.append({
                "phone_number": phone,
                "two-step": pending.get("two_step", ""),
                "session": pending.get("session", ""),
                "api_id": int(API_ID),
                "added_at": int(time.time()),
                "has_2fa": bool(pending.get("two_step")),
            })
            db.set(accounts_key(code, cid), accounts)
        # رصيد البائع: السعر محفوظ بعملة الأسعار وبيتحول لعملة رصيد البائعين
        price = float(pending.get("price", 0) or 0)
        seller_amount = money_value(_convert_amount(price, pending.get("currency", _price_currency()), _seller_currency()))
        new_balance = _add_seller_balance(uid, seller_amount)
        db.set(done_key, {"uid": uid, "phone": phone, "type": type_key, "at": time.time()})
        if db.exists(f"shp_pending_{uid}"):
            db.delete(f"shp_pending_{uid}")
        try:
            db.set("successful_accounts_count", int(_dbget("successful_accounts_count", 0) or 0) + 1)
        except Exception:
            pass
        seller_cur = _seller_currency()
        try:
            await l_client_send_message(client, uid,
                f"✅ **تم إضافة رصيدك بنجاح**\n\n"
                f"📱 الرقم: `+{phone}`\n"
                f"🗂 النوع: {SELL_TYPES.get(type_key, type_key)}\n"
                f"💵 المبلغ المضاف: {_fmt_cur(seller_amount, seller_cur)}\n"
                f"💰 رصيد البائعين الآن: {_fmt_cur(new_balance, seller_cur)}\n\n"
                "تقدر تسحب أرباحك من قسم «رصيد البائعين» في قائمة بيع الحسابات.",
                parse_mode='markdown',
                buttons=[[Button.inline(accent("💰 رصيد البائعين"), data="shp:wallet")],
                         [Button.inline(accent("🏠 القائمة الرئيسية"), data="main")]])
        except Exception as ex:
            print("seller credit message error:", ex)
        try:
            await l_client_send_message(client, admin,
                f"🛒 **تم بيع حساب جديد للبوت**\n\n"
                f"👤 البائع: `{uid}`\n📱 الرقم: `+{phone}`\n"
                f"🗂 النوع: {SELL_TYPES.get(type_key, type_key)}\n"
                f"🌍 الدولة: {flag} {name} {pending.get('code', '')}\n"
                f"💵 السعر: {_fmt_cur(price, pending.get('currency', _price_currency()))}\n"
                f"💰 رصيد البائع: {_fmt_cur(new_balance, seller_cur)}\n\n"
                + ("⚠️ **الدولة دي مش مضافة في قسم الشراء**، فالحساب متخزن في المخزون بس مش ظاهر للعملاء.\n"
                   "ضيف الدولة بسعر البيع من لوحة الأدمن عشان الحساب يظهر ويتباع.\n\n" if country_missing else "")
                + "✅ الحساب اتضاف تلقائيًا لقسم الشراء.",
                parse_mode='markdown')
        except Exception as ex:
            print("seller admin notify error:", ex)
        return True


async def _seller_wait_logout(uid, phone):
    """فحص تلقائي كل 10 ثواني لحد ما البائع يسجل خروج من كل الأجهزة."""
    for _ in range(36):
        await asyncio.sleep(10)
        if not db.exists(f"shp_pending_{uid}"):
            return
        pending = db.get(f"shp_pending_{uid}")
        if not isinstance(pending, dict) or str(pending.get("phone", "")).lstrip("+") != phone:
            return
        state = await _only_bot_session(pending.get("session", ""))
        if state is True:
            if await _finalize_seller_account(uid, pending):
                return
        elif state == "dead":
            if db.exists(f"shp_pending_{uid}"):
                db.delete(f"shp_pending_{uid}")
            try:
                await l_client_send_message(client, uid,
                    "⚠️ **توقف الفحص**\n\n"
                    "جلسة البوت اللي كنا بنتابع بيها الرقم اتقفلت (اتعمل خروج منها).\n"
                    "لو عايز تبيع الرقم ابدأ العملية من جديد من قسم «بيع الحسابات».",
                    parse_mode='markdown')
            except Exception as ex:
                print("seller dead session notify error:", ex)
            return


async def _seller_start_flow(event, uid, type_key):
    prices = _seller_prices()
    available = [p for p in prices.values() if float(p.get(type_key, 0) or 0) > 0]
    if not available:
        return await l_respond(event, "⚠️ لا توجد دول متاحة للبيع من النوع ده حاليًا.")
    async with bot.conversation(event.chat_id) as x:
        await x.send_message(
            "📱 ابعت رقم الهاتف مع رمز الدولة.\n"
            "مثال: +201012345678\n\n"
            "لإلغاء العملية اكتب: إلغاء"
        )
        resp = await x.get_response()
        raw = (getattr(resp, "text", "") or "").strip()
        if raw in ("الغاء", "إلغاء", "cancel", "Cancel"):
            await x.send_message("❌ تم إلغاء العملية.")
            return
        phone = re.sub(r"\D", "", raw)
        if len(phone) < 6:
            await x.send_message("❌ رقم غير صحيح. ابعت الرقم مع رمز الدولة، مثال: +201012345678")
            return
        entry = _match_seller_country(phone)
        price = float(entry.get(type_key, 0) or 0) if entry else 0.0
        if not entry or price <= 0:
            await x.send_message("❌ الدولة دي غير متاحة للبيع حاليًا. راسل الإدارة لإضافتها.")
            return
        if db.exists(f"shp_done_{phone}") or _phone_in_stock(phone):
            await x.send_message("⚠️ الرقم ده اتقبل قبل كده أو موجود بالفعل في مخزون البوت، فمش هينفع تبيعه تاني.")
            return
        await x.send_message("⏳ جاري إرسال كود التحقق للرقم...")
        app = TelegramClient(StringSession(), api_id=int(API_ID), api_hash=API_HASH,
                             device_model="Bot Session", system_version="1.0", app_version="1.0")
        try:
            await app.connect()
            await app.send_code_request(phone)
        except PhoneNumberInvalidError:
            await x.send_message("❌ رقم الهاتف غير صحيح أو غير مسجل في تيليجرام.")
            try:
                await app.disconnect()
            except Exception:
                pass
            return
        except Exception as ex:
            print("seller send code error:", type(ex).__name__, ex)
            await x.send_message(f"❌ حصل خطأ أثناء إرسال الكود: {type(ex).__name__}\nحاول تاني بعد شوية.")
            try:
                await app.disconnect()
            except Exception:
                pass
            return
        await x.send_message("📩 تم إرسال كود التحقق.\nابعت الكود بالشكل ده: 1 2 3 4 5")
        code_resp = await x.get_response()
        code = re.sub(r"\D", "", (getattr(code_resp, "text", "") or ""))
        password = ""
        try:
            await app.sign_in(phone, code, password=None)
        except SessionPasswordNeededError:
            await x.send_message("🔐 الحساب عليه التحقق بخطوتين. ابعت الباسورد:")
            pw_resp = await x.get_response()
            password = (getattr(pw_resp, "text", "") or "").strip()
            try:
                await app.sign_in(password=password)
            except PasswordHashInvalidError:
                await x.send_message("❌ الباسورد غلط. ابدأ العملية من جديد.")
                try:
                    await app.log_out()
                except Exception:
                    pass
                return
            except Exception as ex:
                await x.send_message(f"❌ فشل تسجيل الدخول: {type(ex).__name__}")
                return
        except PhoneCodeInvalidError:
            await x.send_message("❌ الكود غلط. ابدأ العملية من جديد.")
            return
        except PhoneCodeExpiredError:
            await x.send_message("❌ الكود انتهت صلاحيته. ابدأ العملية من جديد.")
            return
        except Exception as ex:
            print("seller sign in error:", type(ex).__name__, ex)
            await x.send_message(f"❌ فشل تسجيل الدخول: {type(ex).__name__}")
            return

        session_str = app.session.save()
        await x.send_message("⏳ ثواني بنفحص حالة الرقم في @SpamBot...")
        status, details = await _spambot_status(app)
        if status is None:
            try:
                await app.log_out()
            except Exception:
                pass
            await x.send_message(
                "❌ تعذر فحص حالة الرقم في @SpamBot حاليًا.\n"
                "اتأكد إن الرقم شغال وعليه نت، وجرب تاني بعد شوية."
            )
            return
        if status != type_key:
            try:
                await app.log_out()
            except Exception:
                pass
            await x.send_message(
                f"❌ **تم رفض المعاملة**\n\n"
                f"الرقم طلع: **{SELL_TYPES.get(status, status)}**\n"
                f"وانت اخترت: **{SELL_TYPES.get(type_key, type_key)}**\n\n"
                "لازم يكون نوع الحساب مطابق للنوع اللي اخترته. تقدر تحاول تاني من القسم الصح."
            )
            return
        pending = {
            "uid": int(uid),
            "phone": phone,
            "code": entry.get("code", ""),
            "name": entry.get("name", ""),
            "flag": entry.get("flag", ""),
            "type": type_key,
            "price": price,
            "currency": _price_currency(),
            "session": session_str,
            "two_step": password,
            "at": time.time(),
        }
        db.set(f"shp_pending_{uid}", pending)
        try:
            await app.disconnect()
        except Exception:
            pass
        await x.send_message(
            f"✅ **الرقم مطابق للنوع المطلوب**\n\n"
            f"📱 الرقم: `+{phone}`\n"
            f"🗂 النوع: {SELL_TYPES.get(type_key, type_key)}\n"
            f"💵 السعر: {_fmt_cur(price, pending['currency'])}\n\n"
            "🔐 **دلوقتي سجل خروج من كل الأجهزة والجلسات على الرقم، وخلي جلسة البوت بس.**\n"
            "من تيليجرام: الإعدادات ← الأجهزة ← إنهاء كل الجلسات التانية.\n"
            "⚠️ جلسة البوت اسمها **Bot Session** في قائمة الأجهزة — دي اللي متعملهاش خروج.\n\n"
            "أول ما تخلص اضغط الزرار تحت، أو البوت هيفحص تلقائيًا كل شوية.",
            parse_mode='markdown',
            buttons=[[Button.inline(accent("✅ تم سحلت خروج من كل التلفونات — افحص الآن"), data="shp:check")],
                     [Button.inline(accent("🏠 القائمة الرئيسية"), data="main")]])
    asyncio.create_task(_seller_wait_logout(int(uid), phone))


def _seller_hub_text():
    return (
        "**🛒 بيع الحسابات**\n\n"
        "اختار نوع الحساب اللي عايز تبيعه للبوت.\n"
        "البوت بيفحص الرقم بنفسه في @SpamBot قبل القبول."
    )


def _seller_hub_buttons():
    return [
        [Button.inline(accent("بيع حسابات سليمة"), data="shp:type:clean")],
        [Button.inline(accent("بيع حسابات محظورة مؤقت"), data="shp:type:temp")],
        [Button.inline(accent("بيع حسابات سبام"), data="shp:type:spam")],
        [Button.inline(accent("💰 رصيد البائعين وسحب الأرباح"), data="shp:wallet")],
        [Button.inline(accent("🏠 القائمة الرئيسية"), data="main")],
    ]


def _seller_type_payload(type_key):
    prices = _seller_prices()
    cur = _price_currency()
    rows = []
    for item in prices.values():
        value = float(item.get(type_key, 0) or 0)
        if value > 0:
            rows.append(f"{item.get('flag', '🌍')} {item.get('name', '')} {item.get('code', '')}  "
                        f"{_fmt_cur(value, cur)}")
    if not rows:
        rows = ["لا توجد دول متاحة للنوع ده حاليًا."]
    text = (
        f"<b>اسعار بيع {SELL_TYPES.get(type_key, type_key)}:</b>\n\n"
        "<blockquote>" + "\n".join(rows) + "</blockquote>\n\n"
        "─────────────────\n\n"
        "<b>[بيع حساب]</b>\n\n"
        "ارسل رقم الهاتف مع رمز الدولة مثل: +201012345678\n\n"
        f"النوع: {SELL_TYPES.get(type_key, type_key)}\n\n"
        "في حاله اختارت اضافه حساب غير مطابق للنوع، سيتم رفض المعامله."
    )
    return text, [
        [Button.inline(accent("بيع حساب"), data=f"shp:go:{type_key}")],
        [Button.inline(accent("💰 رصيد البائعين"), data="shp:wallet")],
        [Button.inline(accent("🔙 رجوع"), data="shp:hub")],
    ]


def _seller_wallet_payload(uid):
    cur = _seller_currency()
    bal = _seller_balance(uid)
    minimum = _seller_min_withdraw()
    text = (
        f"**💰 رصيد البائعين**\n\n"
        f"💵 رصيدك من بيع الأرقام: **{_fmt_cur(bal, cur)}**\n"
        f"🏧 أدنى حد للسحب: **{_fmt_cur(minimum, cur)}**\n\n"
        "تقدر تسحب أرباحك، والسحب بيتحول لعملة رصيد البائعين المحددة من الإدارة."
    )
    buttons = []
    if bal > 0 and (minimum <= 0 or bal >= minimum):
        buttons.append([Button.inline(accent("🏧 سحب الرصيد"), data="shp:withdraw")])
    buttons.append([Button.inline(accent("🛒 أسعار بيع الحسابات"), data="shp:hub")])
    buttons.append([Button.inline(accent("🏠 القائمة الرئيسية"), data="main")])
    return text, buttons


async def _seller_withdraw_flow(event, uid):
    rec = get_user_record(uid)
    cur = _seller_currency()
    balance = _seller_balance(uid)
    minimum = _seller_min_withdraw()
    if balance <= 0:
        return await event.answer("❌ مفيش رصيد متاح للسحب حاليًا.", alert=True)
    if minimum > 0 and balance < minimum:
        return await event.answer(f"❌ أدنى حد للسحب {_fmt_cur(minimum, cur)} ورصيدك {_fmt_cur(balance, cur)}.", alert=True)
    async with bot.conversation(event.chat_id) as x:
        await x.send_message(
            f"💰 رصيدك المتاح: {_fmt_cur(balance, cur)}\n"
            f"🏧 أدنى حد للسحب: {_fmt_cur(minimum, cur) if minimum > 0 else 'بدون حد أدنى'}\n\n"
            "اكتب عملة/شبكة السحب (مثال: USDT-TRC20 أو TRX أو غيرها):"
        )
        network = (getattr(await x.get_response(), "text", "") or "").strip()
        if not network:
            await x.send_message("❌ الشبكة مطلوبة. ابدأ من جديد.")
            return
        await x.send_message("📮 ابعت عنوان المحفظة:")
        address = (getattr(await x.get_response(), "text", "") or "").strip()
        if not address:
            await x.send_message("❌ العنوان مطلوب. ابدأ من جديد.")
            return
        await x.send_message(f"💵 ابعت المبلغ اللي عايز تسحبه ({cur}):")
        amount_text = (getattr(await x.get_response(), "text", "") or "").strip().replace(",", ".")
        try:
            amount = float(amount_text)
            if amount <= 0:
                raise ValueError
        except Exception:
            await x.send_message("❌ المبلغ غير صحيح. ابدأ من جديد.")
            return
        if amount < minimum:
            await x.send_message(f"❌ أدنى حد للسحب {_fmt_cur(minimum, cur)}.")
            return
        if amount > balance:
            await x.send_message(f"❌ المبلغ أكبر من رصيدك ({_fmt_cur(balance, cur)}).")
            return
        rec = get_user_record(uid)
        rec["seller_balance"] = money_value(float(rec.get("seller_balance", 0) or 0) - amount)
        db.set(f"user_{int(uid)}", rec)
        rid = uuid.uuid4().hex[:12]
        db.set(f"seller_withdraw_{rid}", {
            "uid": int(uid), "amount": amount, "currency": cur,
            "network": network, "address": address, "status": "pending", "at": time.time(),
        })
        try:
            await l_bot_send_message(bot, admin,
                f"🏧 **طلب سحب رصيد بائع جديد**\n\n"
                f"👤 البائع: `{uid}`\n"
                f"💵 المبلغ: {_fmt_cur(amount, cur)} (~{_fmt_cur(_convert_amount(amount, cur, 'USD'), 'USD')})\n"
                f"🌐 الشبكة: {network}\n"
                f"📮 المحفظة: `{address}`\n"
                f"💰 رصيده المتبقي: {_fmt_cur(rec.get('seller_balance', 0), cur)}",
                parse_mode='markdown',
                buttons=[[Button.inline(accent("✅ تأكيد السحب"), data=f"sw:ok:{rid}"),
                          Button.inline(accent("❌ رفض"), data=f"sw:no:{rid}")]])
        except Exception as ex:
            print("seller withdraw admin notify error:", ex)
        await x.send_message(
            f"✅ تم تسجيل طلب السحب بمبلغ {_fmt_cur(amount, cur)}.\n"
            "هيتم مراجعته من الإدارة، وهيوصلك إشعار أول ما يتم."
        )


@client.on(events.CallbackQuery(pattern=b"(shp:|spr:|sw:|selladm)"))
async def seller_features_router(event):
    data = event.data.decode("utf-8")
    uid = int(event.sender_id)
    if uid in get_banned_ids():
        return await event.answer("🚫 أنت محظور من استخدام البوت.", alert=True)
    if not is_bot_enabled() and not _is_admin_user(uid):
        await l_edit(event, MAINT_TEXT)
        return
    # الاشتراك الإجباري بيتطبق على أقسام المستخدمين بس (مش أزرار الأدمن).
    if not _is_admin_user(uid):
        try:
            _force_ok, _missing_force = await check_force_subscription(uid)
        except Exception:
            _force_ok, _missing_force = True, []
        if not _force_ok:
            await l_edit(event,
                "🔒 **الاشتراك الإجباري**\n\n"
                "اشترك في القنوات المطلوبة ثم اضغط «تحقق من الاشتراك».",
                buttons=force_buttons(_missing_force))
            return

    # ---------- لوحة أسعار الحسابات (أدمن) ----------
    if data == "selladm" or data == "spr:home":
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        text, buttons = _prices_panel_payload()
        return await l_edit(event, text, parse_mode='markdown', buttons=buttons)

    if data == "spr:add":
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        async with bot.conversation(event.chat_id) as x:
            await x.send_message("📱 ابعت رمز الدولة اللي عايز تضيف سعرها.\nمثال: +963 أو +20")
            resp = await x.get_response()
            digits = re.sub(r"\D", "", (getattr(resp, "text", "") or ""))
            if not digits:
                return await x.send_message("❌ رمز الدولة غير صحيح. ابدأ من جديد.")
            entry = _add_seller_country(digits)
            text, buttons = _price_edit_payload(digits)
            await x.send_message(text, parse_mode='markdown', buttons=buttons)
        return

    if data == "spr:list":
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        prices = _seller_prices()
        if not prices:
            return await event.answer("⚠️ مفيش دول مضافة. اضغط «إضافة دولة».", alert=True)
        buttons = []
        for item in list(prices.values())[:40]:
            buttons.append([Button.inline(
                accent(f"{item.get('flag', '🌍')} {item.get('name', '')} {item.get('code', '')}"),
                data=f"spr:edit:{item.get('digits')}")])
        buttons.append([Button.inline(accent("🔙 رجوع"), data="spr:home")])
        return await l_edit(event, "✏️ اختر الدولة اللي تعدل أسعارها:", buttons=buttons)

    if data.startswith("spr:edit:"):
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        digits = data.split(":", 2)[2]
        text, buttons = _price_edit_payload(digits)
        return await l_edit(event, text, parse_mode='markdown', buttons=buttons)

    if data.startswith("spr:price:"):
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        _, _, digits, type_key = data.split(":", 3)
        if type_key not in SELL_TYPES:
            return await event.answer("❌ نوع غير معروف.", alert=True)
        entry = _find_price_entry(digits)
        if not entry:
            return await event.answer("❌ الدولة غير موجودة.", alert=True)
        cur = _price_currency()
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(
                f"💵 اكتب سعر {SELL_TYPES[type_key]} لدولة {entry.get('name')} بعملة {cur}.\n"
                f"السعر الحالي: {_fmt_cur(entry.get(type_key, 0), cur)}\n\n"
                "مثال: 0.4 أو 20\nلإلغاء اكتب: إلغاء"
            )
            resp = await x.get_response()
            text = (getattr(resp, "text", "") or "").strip().replace(",", ".")
            if text in ("الغاء", "إلغاء", "cancel", "Cancel"):
                return await x.send_message("❌ تم إلغاء التعديل.")
            try:
                price = float(text)
                if price <= 0:
                    raise ValueError
            except Exception:
                return await x.send_message("❌ السعر غير صحيح. لازم يكون رقم أكبر من صفر.")
            prices = _seller_prices()
            digits = str(entry.get("digits", digits))
            if digits not in prices:
                prices[digits] = entry
            prices[digits][type_key] = round(price, 8)
            _save_seller_prices(prices)
            text, buttons = _price_edit_payload(digits)
            await x.send_message(
                f"✅ تم حفظ سعر {SELL_TYPES[type_key]}: {_fmt_cur(price, cur)}\n\n" + text,
                parse_mode='markdown', buttons=buttons)
        return

    if data.startswith("spr:del:"):
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        digits = data.split(":", 2)[2]
        prices = _seller_prices()
        if digits in prices:
            prices.pop(digits, None)
            _save_seller_prices(prices)
        text, buttons = _prices_panel_payload()
        return await l_edit(event, "🗑 تم حذف الدولة.\n\n" + text, parse_mode='markdown', buttons=buttons)

    if data == "spr:cur":
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        db.set(SELLER_PRICE_CURRENCY_KEY, "EGP" if _price_currency() == "USD" else "USD")
        text, buttons = _prices_panel_payload()
        return await l_edit(event, text, parse_mode='markdown', buttons=buttons)

    if data == "spr:sellercur":
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        db.set(SELLER_BALANCE_CURRENCY_KEY, "EGP" if _seller_currency() == "USD" else "USD")
        text, buttons = _prices_panel_payload()
        return await l_edit(event, text, parse_mode='markdown', buttons=buttons)

    if data == "spr:min":
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        cur = _seller_currency()
        async with bot.conversation(event.chat_id) as x:
            await x.send_message(
                f"🏧 أدنى حد للسحب الحالي: {_fmt_cur(_seller_min_withdraw(), cur)}\n\n"
                f"ابعت الحد الأدنى الجديد بعملة {cur}.\nمثال: 5 أو 10\n"
                "اكتب 0 لإلغاء الحد الأدنى (هيسمح بسحب أي مبلغ)."
            )
            resp = await x.get_response()
            text = (getattr(resp, "text", "") or "").strip().replace(",", ".")
            try:
                value = float(text)
                if value < 0:
                    raise ValueError
            except Exception:
                return await x.send_message("❌ قيمة غير صحيحة.")
            db.set(SELLER_MIN_WITHDRAW_KEY, round(value, 8))
            text, buttons = _prices_panel_payload()
            await x.send_message(f"✅ تم ضبط أدنى حد للسحب على {_fmt_cur(value, cur)}", buttons=buttons)
        return

    # ---------- قسم بيع الحسابات (مستخدم) ----------
    if data == "shp:hub":
        return await l_edit(event, _seller_hub_text(), parse_mode='markdown', buttons=_seller_hub_buttons())

    if data.startswith("shp:type:"):
        type_key = data.split(":", 2)[2]
        if type_key not in SELL_TYPES:
            return await event.answer("❌ نوع غير معروف.", alert=True)
        text, buttons = _seller_type_payload(type_key)
        return await l_edit(event, text, parse_mode='html', buttons=buttons)

    if data.startswith("shp:go:"):
        type_key = data.split(":", 2)[2]
        if type_key not in SELL_TYPES:
            return await event.answer("❌ نوع غير معروف.", alert=True)
        return await _seller_start_flow(event, uid, type_key)

    if data == "shp:wallet":
        text, buttons = _seller_wallet_payload(uid)
        return await l_edit(event, text, parse_mode='markdown', buttons=buttons)

    if data == "shp:withdraw":
        return await _seller_withdraw_flow(event, uid)

    if data == "shp:check":
        if not db.exists(f"shp_pending_{uid}"):
            return await event.answer("⚠️ مفيش عملية بيع معلقة. ابدأ من جديد.", alert=True)
        pending = db.get(f"shp_pending_{uid}")
        if not isinstance(pending, dict):
            return await event.answer("⚠️ مفيش عملية بيع معلقة. ابدأ من جديد.", alert=True)
        await event.answer("⏳ جاري فحص الجلسات...")
        state = await _only_bot_session(pending.get("session", ""))
        if state is True:
            ok = await _finalize_seller_account(uid, pending)
            if ok:
                return await l_edit(event,
                    "✅ تم التأكد إن الحساب بقى فيه جلسة البوت بس.\n"
                    "تم إضافة الحساب للمخزون وإضافة رصيدك تلقائيًا.",
                    buttons=[[Button.inline(accent("💰 رصيد البائعين"), data="shp:wallet")],
                             [Button.inline(accent("🏠 القائمة الرئيسية"), data="main")]])
        if state == "dead":
            if db.exists(f"shp_pending_{uid}"):
                db.delete(f"shp_pending_{uid}")
            return await l_edit(event,
                "⚠️ جلسة البوت اللي كنا بنتابع بيها الرقم اتقفلت (اتعمل خروج منها).\n"
                "ابدأ العملية من جديد من قسم «بيع الحسابات».",
                buttons=[[Button.inline(accent("🛒 بيع الحسابات"), data="shp:hub")],
                         [Button.inline(accent("🏠 القائمة الرئيسية"), data="main")]])
        return await l_edit(event,
            "⚠️ لسه فيه جلسات تانية مفتوحة على الرقم.\n"
            "سجل خروج من كل الأجهزة ما عدا جلسة البوت، وبعدين اضغط الزرار تاني.",
            buttons=[[Button.inline(accent("✅ افحص الآن"), data="shp:check")],
                     [Button.inline(accent("🏠 القائمة الرئيسية"), data="main")]])

    # ---------- قرارات سحب رصيد البائعين (أدمن) ----------
    if data.startswith("sw:ok:") or data.startswith("sw:no:"):
        if not _is_admin_user(uid):
            return await event.answer("❌ للأدمن فقط.", alert=True)
        action, rid = data.split(":", 1)[1].split(":", 1)
        req = db.get(f"seller_withdraw_{rid}") if db.exists(f"seller_withdraw_{rid}") else None
        if not isinstance(req, dict):
            return await event.answer("❌ الطلب غير موجود.", alert=True)
        if req.get("status") != "pending":
            return await event.answer("⚠️ الطلب تمت معالجته بالفعل.", alert=True)
        seller_id = int(req.get("uid"))
        amount = float(req.get("amount", 0) or 0)
        cur = _norm_currency(req.get("currency", _seller_currency()))
        if action == "ok":
            req["status"] = "approved"
            db.set(f"seller_withdraw_{rid}", req)
            try:
                await l_bot_send_message(bot, seller_id,
                    f"✅ **تم تأكيد سحب رصيدك**\n\n"
                    f"💵 المبلغ: {_fmt_cur(amount, cur)}\n"
                    f"🌐 الشبكة: {req.get('network')}\n"
                    f"📮 المحفظة: `{req.get('address')}`\n\n"
                    "تم إرسال المبلغ، تحقق من محفظتك.",
                    parse_mode='markdown')
            except Exception:
                pass
            return await l_edit(event, f"✅ تم تأكيد السحب وإشعار البائع.\n💵 {_fmt_cur(amount, cur)}")
        req["status"] = "rejected"
        db.set(f"seller_withdraw_{rid}", req)
        rec = get_user_record(seller_id)
        rec["seller_balance"] = money_value(float(rec.get("seller_balance", 0) or 0) + amount)
        db.set(f"user_{seller_id}", rec)
        try:
            await l_bot_send_message(bot, seller_id,
                f"❌ **تم رفض طلب السحب**\n\n"
                f"💵 المبلغ: {_fmt_cur(amount, cur)}\n"
                "تم إرجاع المبلغ لرصيد البائعين بتاعك.",
                parse_mode='markdown')
        except Exception:
            pass
        return await l_edit(event, f"❌ تم رفض السحب وإرجاع المبلغ لرصيد البائع.\n💵 {_fmt_cur(amount, cur)}")

    return


async def _style_selftest():
    """اختبار صغير عند التشغيل: لو تيليجرام رفض الأزرار الملونة نرجع للأزرار العادية بدل ما البوت يبوظ."""
    global STYLE_MODE
    if STYLE_MODE != "tl":
        return
    try:
        m0 = await client.send_message(admin, "🎨 test", buttons=[[_orig_inline("test", b"noop")]])
        await m0.delete()
    except Exception as ex:
        print("style selftest skipped:", ex)
        return
    try:
        m1 = await client.send_message(admin, "🎨 test", buttons=[[_styled_inline("✅ test", b"noop")]])
        await m1.delete()
        print("🎨 اختبار الأزرار الملونة: ناجح")
    except Exception as ex:
        print("🎨 تيليجرام رفض الأزرار الملونة، هتتعطل:", type(ex).__name__, ex)
        STYLE_MODE = None

try:
    client.loop.run_until_complete(_style_selftest())
except Exception as _ex:
    print("style selftest error:", _ex)

client.loop.create_task(stars_payment_polling())
client.loop.create_task(auto_backup_loop())
client.loop.create_task(_pay_recover_pending())
client.run_until_disconnected()
