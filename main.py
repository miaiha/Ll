import os
import asyncio
from collections import Counter
from aiohttp import web
from telethon import TelegramClient, events, Button, utils
from telethon.tl.types import ChannelParticipantsAdmins, User
from telethon.tl.functions.messages import GetCommonChatsRequest
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError
)

# جلب البيانات من متغيرات البيئة
API_ID = int(os.getenv("API_ID", "35387422"))
API_HASH = os.getenv("API_HASH", "c3ab2cdac591ab2963d10080725d4dfa")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8641805003:AAFTxgxVjOLL7Cn2MH9jh46LoZRwX0-kaCo")
PORT = int(os.getenv("PORT", 8080))

# تهيئة بوت الإدارة
bot = TelegramClient("bot_session", API_ID, API_HASH)

user_clients = {}
user_states = {}

# --- خادم ويب مصغر لمنصة Render لضمان بقاء الخدمة نشطة ---
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# دالة مساعدة لتجزئة الرسائل الطويلة وإرسالها بالتتابع
async def send_chunked_report(event, text):
    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3500:
            await event.respond(chunk, parse_mode="md")
            chunk = line + "\n"
        else:
            chunk += line + "\n"
    if chunk.strip():
        await event.respond(chunk, parse_mode="md")

# --- واجهات البوت ---

@bot.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    chat_id = event.chat_id
    buttons = [
        [Button.inline("تسجيل الدخول بالحساب 📱", b"login_start")],
        [Button.inline("عرض مجموعاتي 📋", b"list_groups")]
    ]
    await event.respond(
        "👋 **أهلاً بك في بوت استخراج وتحليل الأعضاء المتفاعلين.**\n\n"
        "للبدء، يرجى تسجيل الدخول بحساب التيليجرام المطلوب عبر الزر أدناه.",
        buttons=buttons
    )

@bot.on(events.CallbackQuery(data=b"login_start"))
async def login_start(event):
    chat_id = event.chat_id
    user_states[chat_id] = {"step": "AWAIT_PHONE"}
    await event.respond("📞 يرجى إرسال رقم الهاتف مع الرمز الدولي (مثال: `+9647700000000`):")

@bot.on(events.CallbackQuery(data=b"list_groups"))
async def show_groups(event):
    chat_id = event.chat_id
    client = user_clients.get(chat_id)
    if not client or not await client.is_user_authorized():
        await event.respond("⚠️ لم تقم بتسجيل الدخول بالحساب بعد! اضغط على تسجيل الدخول أولاً.")
        return

    await event.respond("⏳ جاري جلب المجموعات التي انضم إليها الحساب...")
    buttons = []
    async for dialog in client.iter_dialogs(limit=25):
        if dialog.is_group:
            btn_data = f"grp_{dialog.id}".encode()
            name = (dialog.name[:20] + "..") if len(dialog.name) > 20 else dialog.name
            buttons.append([Button.inline(name, btn_data)])

    if not buttons:
        await event.respond("لم يتم العثور على مجموعات في هذا الحساب، أو يمكنك إرسال رابط المجموعة مباشرة.")
    else:
        await event.respond("اختر المجموعة المراد فحصها من القائمة:", buttons=buttons)

# استقبال النصوص لتسجيل الدخول والروابط
@bot.on(events.NewMessage)
async def message_handler(event):
    if event.text.startswith("/"):
        return

    chat_id = event.chat_id
    state = user_states.get(chat_id, {})
    step = state.get("step")

    # استلام رقم الهاتف
    if step == "AWAIT_PHONE":
        phone = event.text.strip()
        client = TelegramClient(f"user_session_{chat_id}", API_ID, API_HASH)
        await client.connect()
        try:
            sent_code = await client.send_code_request(phone)
            user_states[chat_id] = {
                "step": "AWAIT_CODE",
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
                "client": client
            }
            await event.respond(
                "📩 تم إرسال كود التحقق إلى حسابك.\n\n"
                "⚠️ **تنبيه هام:**\n"
                "أرسل الكود **مفصولاً بمسافات بين كل رقم** حتى لا يحرقه تيليجرام:\n"
                "مثال: إذا كان الكود `58291`، أرسله هكذا: `5 8 2 9 1`"
            )
        except Exception as e:
            await event.respond(f"❌ حدث خطأ أثناء إرسال الكود: {str(e)}")

    # استلام كود التحقق
    elif step == "AWAIT_CODE":
        raw_text = event.text.strip()
        code = "".join(filter(str.isdigit, raw_text))

        if not code or len(code) < 5:
            await event.respond("⚠️ الكود غير صالح. يرجى إرساله مع وضع مسافة بين كل رقم (مثال: `5 8 2 9 1`).")
            return

        client = state["client"]
        phone = state["phone"]
        phone_code_hash = state["phone_code_hash"]
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            user_clients[chat_id] = client
            user_states[chat_id] = {"step": "LOGGED_IN"}
            await event.respond("✅ تم تسجيل الدخول بنجاح!\nاضغط على 'عرض مجموعاتي' أو أرسل رابط مجموعة مباشرة.")
        except SessionPasswordNeededError:
            user_states[chat_id]["step"] = "AWAIT_2FA"
            await event.respond("🔐 هذا الحساب محمي بكلمة مرور (2FA). يرجى إرسال كلمة المرور:")
        except Exception as e:
            await event.respond(f"❌ خطأ أثناء تسجيل الدخول: {str(e)}")

    # استلام كلمة مرور التحقق بخطوتين
    elif step == "AWAIT_2FA":
        client = state["client"]
        password = event.text.strip()
        try:
            await client.sign_in(password=password)
            user_clients[chat_id] = client
            user_states[chat_id] = {"step": "LOGGED_IN"}
            await event.respond("✅ تم فك القفل بنجاح!\nيمكنك الآن استعراض المجموعات عبر /start.")
        except Exception as e:
            await event.respond(f"❌ كلمة المرور غير صحيحة أو حدث خطأ: {str(e)}")

    # استلام رابط المجموعة مباشرة
    elif "t.me/" in event.text or event.text.startswith("@"):
        client = user_clients.get(chat_id)
        if not client or not await client.is_user_authorized():
            await event.respond("⚠️ يرجى تسجيل الدخول أولاً قبل إرسال الروابط.")
            return

        link = event.text.strip()
        try:
            entity = await client.get_entity(link)
            user_states[chat_id] = {"selected_group": entity.id}
            await prompt_message_limit(event, entity.title)
        except Exception as e:
            await event.respond(f"❌ تعذر الوصول للمجموعة: {str(e)}")

@bot.on(events.CallbackQuery(pattern=b"grp_"))
async def group_selected(event):
    chat_id = event.chat_id
    group_id = int(event.data.decode().split("_")[1])
    user_states[chat_id] = {"selected_group": group_id}
    await prompt_message_limit(event, "المحددة")

async def prompt_message_limit(event, group_name):
    buttons = [
        [Button.inline("1,000 رسالة", b"limit_1000"), Button.inline("5,000 رسالة", b"limit_5000")],
        [Button.inline("10,000 رسالة", b"limit_10000"), Button.inline("20,000 رسالة", b"limit_20000")]
    ]
    await event.respond(
        f"📊 اختر نطاق الفحص للمجموعة ({group_name}):\n"
        "سيتم فحص التفاعل وتصنيف المتواجدين والمغادرين وإرسال جدول مباشر.",
        buttons=buttons
    )

# بدء عملية الفحص واستخراج الإحصائيات
@bot.on(events.CallbackQuery(pattern=b"limit_"))
async def start_analysis(event):
    chat_id = event.chat_id
    limit = int(event.data.decode().split("_")[1])
    state = user_states.get(chat_id, {})
    group_id = state.get("selected_group")

    client = user_clients.get(chat_id)
    if not client or not group_id:
        await event.respond("⚠️ حدث خطأ في استرجاع بيانات المجموعة أو الجلسة.")
        return

    status_msg = await event.respond(f"⏳ جاري قراءة الرسائل وتحديد المتفاعلين...")

    try:
        # 1. معرفات القروب الممكنة لمطابقتها في المجموعات المشتركة
        possible_group_ids = {group_id, abs(group_id)}
        raw_str = str(abs(group_id))
        if raw_str.startswith("100") and len(raw_str) > 5:
            possible_group_ids.add(int(raw_str[3:]))

        # 2. جلب قائمة المشرفين لاستبعادهم
        admins = set()
        try:
            async for admin in client.iter_participants(group_id, filter=ChannelParticipantsAdmins):
                admins.add(admin.id)
        except Exception:
            pass

        # 3. قراءة الرسائل واحتساب التفاعل
        user_counts = Counter()
        users_info = {}
        users_entities = {}

        count = 0
        async for msg in client.iter_messages(group_id, limit=limit):
            count += 1
            if not msg.sender_id or msg.sender_id in admins:
                continue

            sender = msg.sender
            if isinstance(sender, User):
                if sender.bot:
                    continue
                if sender.id not in users_info:
                    raw_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip() or "بدون اسم"
                    # تنظيف الاسم من الرموز التي قد تفسد تنسيق الرسالة
                    clean_name = raw_name.replace("*", "").replace("_", "").replace("`", "")
                    users_info[sender.id] = {
                        "name": clean_name,
                        "username": sender.username
                    }
                    users_entities[sender.id] = sender

                user_counts[msg.sender_id] += 1

        total_users = len(user_counts)
        await status_msg.edit(f"⏳ تم العثور على {total_users} عضواً متفاعلاً.\nجاري فحص المتواجدين والمغادرين عبر المجموعات المشتركة...")

        current_members = []
        left_members = []

        sorted_users = user_counts.most_common()

        # 4. فحص هل العضو لا يزال في المجموعة أم غادر باستخدام GetCommonChatsRequest
        for idx, (user_id, msg_cnt) in enumerate(sorted_users, 1):
            info = users_info.get(user_id, {"name": "مستخدم", "username": None})
            sender_entity = users_entities.get(user_id)

            if idx % 10 == 0 or idx == total_users:
                try:
                    await status_msg.edit(f"⏳ جاري فحص حالة الأعضاء ({idx}/{total_users})...")
                except Exception:
                    pass

            is_present = False
            try:
                target = sender_entity if sender_entity else user_id
                res = await client(GetCommonChatsRequest(user_id=target, max_id=0, limit=100))
                common_chat_ids = {c.id for c in res.chats}

                # إذا كانت المجموعة ضمن المجموعات المشتركة فهو متواجد، وإلا فقد غادر
                if any(gid in common_chat_ids for gid in possible_group_ids):
                    is_present = True
                else:
                    is_present = False

            except FloodWaitError as f:
                await asyncio.sleep(f.seconds)
                try:
                    res = await client(GetCommonChatsRequest(user_id=target, max_id=0, limit=100))
                    common_chat_ids = {c.id for c in res.chats}
                    is_present = any(gid in common_chat_ids for gid in possible_group_ids)
                except Exception:
                    is_present = False
            except Exception:
                is_present = False

            record = {
                "id": user_id,
                "name": info["name"],
                "username": info["username"],
                "count": msg_cnt
            }

            if is_present:
                current_members.append(record)
            else:
                left_members.append(record)

            await asyncio.sleep(0.08)

        # 5. بناء التقرير النصي المرتب بجدول مباشر
        report_text = f"📊 **إحصائيات تفاعل أعضاء المجموعة**\n"
        report_text += f"🔹 الرسائل المفحوصة: `{count:,}`\n"
        report_text += f"🟢 المتواجدون: `{len(current_members)}` | 🔴 المغادرون: `{len(left_members)}`\n"
        report_text += "━━━━━━━━━━━━━━━━━━━━━\n\n"

        report_text += "🟢 **[ الأعضاء المتواجدون حالياً بالقروب ]**\n"
        report_text += "الترتيب ▫️ الاسم ▫️ المعرف / الآيدي ▫️ التفاعل\n"
        report_text += "─────────────────────\n"

        if current_members:
            for idx, u in enumerate(current_members, 1):
                user_ident = f"@{u['username']}" if u['username'] else f"`{u['id']}`"
                report_text += f"{idx}. **{u['name']}** ▫️ {user_ident} ▫️ 💬 `{u['count']} رسالة`\n"
        else:
            report_text += "لا يوجد أعضاء متواجدون حالياً.\n"

        report_text += "\n━━━━━━━━━━━━━━━━━━━━━\n"
        report_text += "🔴 **[ الأعضاء الذين غادروا القروب ]**\n"
        report_text += "الترتيب ▫️ الاسم ▫️ المعرف / الآيدي ▫️ التفاعل\n"
        report_text += "─────────────────────\n"

        if left_members:
            for idx, u in enumerate(left_members, 1):
                user_ident = f"@{u['username']}" if u['username'] else f"`{u['id']}`"
                report_text += f"{idx}. **{u['name']}** ▫️ {user_ident} ▫️ 💬 `{u['count']} رسالة`\n"
        else:
            report_text += "لا يوجد أعضاء مغادرون ضمن الرسائل المفحوصة.\n"

        await status_msg.delete()

        # إرسال النتيجة كرسائل مرتبة
        await send_chunked_report(event, report_text)

    except Exception as e:
        await event.respond(f"❌ حدث خطأ أثناء التحليل: {str(e)}")

# --- تشغيل البوت وخادم الويب معاً ---
async def main():
    await start_web_server()
    await bot.start(bot_token=BOT_TOKEN)
    print("Bot and Web Server are running...")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())