import os
import io
import asyncio
from collections import Counter
from aiohttp import web
from telethon import TelegramClient, events, Button
from telethon.tl.types import ChannelParticipantsAdmins, User
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    UserNotParticipantError
)

# جلب البيانات من متغيرات البيئة (مع إمكانية القراءة المباشرة إذا تم ضبطها)
API_ID = int(os.getenv("API_ID", "35387422"))
API_HASH = os.getenv("API_HASH", "c3ab2cdac591ab2963d10080725d4dfa")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8641805003:AAFTxgxVjOLL7Cn2MH9jh46LoZRwX0-kaCo")
PORT = int(os.getenv("PORT", 8080))

# تهيئة بوت الإدارة
bot = TelegramClient("bot_session", API_ID, API_HASH)

# قواميس لحفظ جلسات وحالات المستخدمين
user_clients = {}
user_states = {}

# --- خادم ويب مصغر لمنصة Render لضمان عدم توقف الخدمة ---
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

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
            # تقصير الاسم لتفادي تجاوز الحد المسموح للأزرار (64 بايت)
            name = (dialog.name[:20] + "..") if len(dialog.name) > 20 else dialog.name
            buttons.append([Button.inline(name, btn_data)])

    if not buttons:
        await event.respond("لم يتم العثور على مجموعات في هذا الحساب، أو يمكنك إرسال رابط المجموعة مباشرة.")
    else:
        await event.respond("اختر المجموعة المراد فحصها من القائمة:", buttons=buttons)

# استقبال النصوص لتسجيل الدخول (الرقم، الكود، كلمة المرور، أو رابط مجموعة)
@bot.on(events.NewMessage)
async def message_handler(event):
    if event.text.startswith("/"):
        return

    chat_id = event.chat_id
    state = user_states.get(chat_id, {})
    step = state.get("step")

    # الخطوة 1: استلام رقم الهاتف
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
            await event.respond("📩 تم إرسال كود التحقق. يرجى إرسال الكود هنا:")
        except Exception as e:
            await event.respond(f"❌ حدث خطأ أثناء إرسال الكود: {str(e)}")

    # الخطوة 2: استلام كود التحقق
    elif step == "AWAIT_CODE":
        code = event.text.strip().replace(" ", "")
        client = state["client"]
        phone = state["phone"]
        phone_code_hash = state["phone_code_hash"]
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            user_clients[chat_id] = client
            user_states[chat_id] = {"step": "LOGGED_IN"}
            await event.respond("✅ تم تسجيل الدخول بالحساب بنجاح!\nاضغط الآن على زر 'عرض مجموعاتي' أو أرسل رابط مجموعة مباشرة.")
        except SessionPasswordNeededError:
            user_states[chat_id]["step"] = "AWAIT_2FA"
            await event.respond("🔐 هذا الحساب محمي بالتحقق بخطوتين (2FA). يرجى إرسال كلمة المرور:")
        except Exception as e:
            await event.respond(f"❌ خطأ أثناء تسجيل الدخول: {str(e)}")

    # الخطوة 2 (مكرر): استلام كلمة مرور التحقق بخطوتين
    elif step == "AWAIT_2FA":
        client = state["client"]
        password = event.text.strip()
        try:
            await client.sign_in(password=password)
            user_clients[chat_id] = client
            user_states[chat_id] = {"step": "LOGGED_IN"}
            await event.respond("✅ تم فك القفل وتسجيل الدخول بنجاح!\nيمكنك الآن استعراض المجموعات عبر /start.")
        except Exception as e:
            await event.respond(f"❌ كلمة المرور غير صحيحة أو حدث خطأ: {str(e)}")

    # إمكانية إرسال رابط المجموعة مباشرة
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

# استلام خيار المجموعة من الأزرار
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
        "سيتم قراءة الرسائل وتحديد المتفاعلين واستبعاد المشرفين والبوتات.",
        buttons=buttons
    )

# بدء عملية التحليل والفحص
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

    status_msg = await event.respond(f"⏳ جاري فحص آخر {limit:,} رسالة... يرجى الانتظار، هذه العملية قد تستغرق وقتاً تجنباً لقيود التيليجرام.")

    try:
        # 1. جلب قائمة المشرفين لاستبعادهم
        admins = set()
        try:
            async for admin in client.iter_participants(group_id, filter=ChannelParticipantsAdmins):
                admins.add(admin.id)
        except Exception:
            pass

        # 2. قراءة الرسائل واحتساب التفاعل وتجاهل البوتات والمشرفين
        user_counts = Counter()
        users_info = {}

        count = 0
        async for msg in client.iter_messages(group_id, limit=limit):
            count += 1
            if not msg.sender_id:
                continue

            # استبعاد المشرفين
            if msg.sender_id in admins:
                continue

            # استبعاد البوتات والتأكد من أنه مستخدم عادي
            sender = msg.sender
            if isinstance(sender, User):
                if sender.bot:
                    continue
                if sender.id not in users_info:
                    name = f"{sender.first_name or ''} {sender.last_name or ''}".strip() or "بدون اسم"
                    username = f"@{sender.username}" if sender.username else "لا يوجد"
                    users_info[sender.id] = {"name": name, "username": username}

                user_counts[msg.sender_id] += 1

        # 3. فرز المتواجدين حالياً والمغادرين
        current_members = []
        left_members = []

        # الترتيب حسب الأكثر تفاعلاً
        sorted_users = user_counts.most_common()

        for user_id, msg_cnt in sorted_users:
            info = users_info.get(user_id, {"name": "مستخدم", "username": "لا يوجد"})
            
            # فحص هل المستخدم لا يزال عضواً في المجموعة
            is_present = True
            try:
                perms = await client.get_permissions(group_id, user_id)
                if not perms.is_participant:
                    is_present = False
            except UserNotParticipantError:
                is_present = False
            except FloodWaitError as f:
                await asyncio.sleep(f.seconds)
            except Exception:
                is_present = True

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

            await asyncio.sleep(0.05)  # تأخير زمني طفيف لتفادي FloodWait

        # 4. تجهيز التقرير في ملف نصي UTF-8
        report = io.StringIO()
        report.write("=" * 60 + "\n")
        report.write(f"تقرير الأعضاء المتفاعلين في المجموعة\n")
        report.write(f"إجمالي الرسائل المحللة: {count}\n")
        report.write(f"عدد المتواجدين حالياً: {len(current_members)} | عدد المغادرين: {len(left_members)}\n")
        report.write("=" * 60 + "\n\n")

        report.write("--- [ 1. الأعضاء المتواجدون حالياً بالمجموعة (مرتبين حسب الأكثر تفاعلاً) ] ---\n")
        report.write("الترتيب | الآيدي (ID) | المعرف (Username) | عدد الرسائل | الاسم\n")
        report.write("-" * 60 + "\n")
        for idx, u in enumerate(current_members, 1):
            report.write(f"{idx:<3} | {u['id']:<11} | {u['username']:<15} | {u['count']:<4} رسالة | {u['name']}\n")

        report.write("\n\n" + "=" * 60 + "\n")
        report.write("--- [ 2. الأعضاء المتفاعلون الذين غادروا المجموعة ] ---\n")
        report.write("الترتيب | الآيدي (ID) | المعرف (Username) | عدد الرسائل | الاسم\n")
        report.write("-" * 60 + "\n")
        for idx, u in enumerate(left_members, 1):
            report.write(f"{idx:<3} | {u['id']:<11} | {u['username']:<15} | {u['count']:<4} رسالة | {u['name']}\n")

        report.seek(0)
        file_data = io.BytesIO(report.getvalue().encode("utf-8"))
        file_data.name = f"active_members_{group_id}.txt"

        await bot.send_file(
            chat_id,
            file_data,
            caption=f"✅ اكتمل الفحص لآخر {count:,} رسالة بنجاح.\nالملف مقسم لخانتي: المتواجدين حالياً والمغادرين."
        )
        await status_msg.delete()

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