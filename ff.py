#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
game.py - Full integrated bot:
- Tết mini (liixi, hoamai, phao, xongdat)
- TX (tài/xỉu) with buttons + PNG support
- Xổ số (/xoso, /chon, auto multi-results, /end)
- Bầu cua (/baucua)
- Free Fire simplified (ST & TC) with button-only UI
- System commands: /menu /help /dangky /diem /top /set /check /lich /tinhyeu /info
- All game results are posted to GROUP_ID
- Uses m.env for BOT_TOKEN and GROUP_ID
"""
import os
import re
import json
import random
import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
)

# --- Load env ---
load_dotenv("m.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID") or 0)
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in m.env")
if not GROUP_ID:
    print("Warning: GROUP_ID not set in m.env. Results will use the chat where command invoked.")

# --- Config & constants ---
IMAGES_DIR = "images"
SAVE_FILE = "game_data.json"
DEFAULT_TX_COUNTDOWN = 10
AUTO_CLOSE_AFTER_LAST_BET = 5
COUNTDOWN_EDIT_INTERVAL = 5
XOSO_DEFAULT_SESSION = 60
XOSO_MIN, XOSO_MAX = 1, 20
XOSO_MAX_CHOICES = 5

MATCHMAKING_MAX_WAIT = 50
LOBBY_SPAWN_SECONDS = 5  # as you wanted 5s into match
PLANE_WAIT = 30
BUY_SECONDS = 15
COMBAT_SECONDS = 75  # 1m15s as requested
MIN_ST_PLAYERS = 1  # you said no minimum

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gamebot")

# --- In-memory stores (persisted minimally to SAVE_FILE) ---
balances: Dict[int,int] = defaultdict(int)
user_names: Dict[int,str] = {}
leaderboard: Dict[int,int] = defaultdict(int)
tx_sessions = {}   # chat_id -> TxSession
xoso_sessions = {}
baucua_sessions = {}
ff_lobbies = {}

# --- Persistence helpers ---
def load_data():
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k,v in data.get("balances",{}).items():
            balances[int(k)] = v
        for k,v in data.get("user_names",{}).items():
            user_names[int(k)] = v
        for k,v in data.get("leaderboard",{}).items():
            leaderboard[int(k)] = v
        logger.info("Loaded data")
    except FileNotFoundError:
        logger.info("No save file")
    except Exception as e:
        logger.exception("Error loading data: %s", e)

def save_data():
    try:
        data = {
            "balances": {str(k): v for k,v in balances.items()},
            "user_names": {str(k): v for k,v in user_names.items()},
            "leaderboard": {str(k): v for k,v in leaderboard.items()},
        }
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception("Error saving data: %s", e)

# Register periodic save
async def periodic_save_task():
    while True:
        await asyncio.sleep(30)
        save_data()

# --- Utilities ---
AMOUNT_RE = re.compile(r"""^([0-9]+(?:[.,][0-9]+)?)\s*([kKmMtT]?)$""")
SUFFIX_MULT = {"":1,"k":1000,"K":1000,"m":1_000_000,"M":1_000_000,"t":1_000_000_000,"T":1_000_000_000}

def parse_amount(text: str) -> Optional[int]:
    if not text: return None
    s = text.replace(".", "").replace(",", ".").strip()
    m = AMOUNT_RE.match(s)
    if not m: return None
    try:
        num = float(m.group(1))
    except:
        return None
    mult = SUFFIX_MULT.get(m.group(2) or "",1)
    amt = int(num * mult)
    return amt if amt > 0 else None

def fmt_amount(v:int)->str:
    if v>=1_000_000_000: return f"{v/1_000_000_000:.2f}T"
    if v>=1_000_000: return f"{v/1_000_000:.2f}M"
    if v>=1000: return f"{v//1000}k"
    return str(v)

def image_path(name:str)->Optional[str]:
    p = os.path.join(IMAGES_DIR, name)
    return p if os.path.exists(p) else None

async def send_group_or_chat(context: ContextTypes.DEFAULT_TYPE, chat_id:int, text:str, **kwargs):
    """Send to GROUP_ID if set; else send to provided chat_id."""
    target = GROUP_ID or chat_id
    try:
        await context.bot.send_message(target, text, parse_mode=ParseMode.HTML, **kwargs)
    except Exception as e:
        logger.exception("send_group_or_chat error: %s", e)
        # fallback to chat where invoked
        try:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, **kwargs)
        except:
            pass

# --- Command: /menu /help ---
async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_names[uid] = update.effective_user.username or update.effective_user.full_name
    kb = [
        [InlineKeyboardButton("/tx Tài/Xỉu", callback_data="menu_tx"),
         InlineKeyboardButton("/xoso Xổ số", callback_data="menu_xoso")],
        [InlineKeyboardButton("/baucua Bầu Cua", callback_data="menu_baucua"),
         InlineKeyboardButton("/ff Free Fire", callback_data="menu_ff")],
        [InlineKeyboardButton("/liixi Lì xì", callback_data="menu_liixi"),
         InlineKeyboardButton("/hoamai Hoa mai", callback_data="menu_hoamai")],
        [InlineKeyboardButton("/dangky Đăng ký", callback_data="menu_dangky"),
         InlineKeyboardButton("/diem Điểm", callback_data="menu_diem")]
    ]
    await update.message.reply_text("🌸 <b>MENU</b> — chọn một mục", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "menu_tx":
        await q.edit_message_text("🎲 /tx <số tiền>  — Đặt Tài/Xỉu (sử dụng nút để chọn)")
    elif data == "menu_xoso":
        await q.edit_message_text("🎰 /xoso — mở phiên xổ số 60s. /chon để chọn.")
    elif data == "menu_baucua":
        await q.edit_message_text("🦀 /baucua — mở phiên bầu cua. Dùng nút chọn linh vật.")
    elif data == "menu_ff":
        await q.edit_message_text("🔥 /ff — mở menu Free Fire (button-only).")
    elif data == "menu_liixi":
        await q.edit_message_text("🧧 /liixi — nhận lì xì ngẫu nhiên.")
    elif data == "menu_hoamai":
        await q.edit_message_text("🌺 /hoamai — chúc Tết ngẫu nhiên.")
    elif data == "menu_dangky":
        await handle_dangky_private(q, context)
    elif data == "menu_diem":
        await handle_diem_private(q, context)
    else:
        await q.edit_message_text("Chức năng đang được triển khai.")

# --- dangky / diem / top ---
async def handle_dangky_private(q, context):
    uid = q.from_user.id
    if balances.get(uid,0)>0:
        await q.edit_message_text("Bạn đã đăng ký.")
        return
    balances[uid] = 100_000
    await q.edit_message_text(f"✅ Đăng ký: bạn nhận 100k. Số dư: {fmt_amount(balances[uid])}")
    save_data()

async def handle_diem_private(q, context):
    uid = q.from_user.id
    await q.edit_message_text(f"💼 Số dư: {fmt_amount(balances.get(uid,0))}")

async def dangky_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if balances.get(uid,0)>0:
        await update.message.reply_text("Bạn đã đăng ký trước đó.")
        return
    balances[uid]=100_000
    user_names[uid]=update.effective_user.username or update.effective_user.full_name
    await update.message.reply_text(f"Đăng ký thành công. Số dư: {fmt_amount(balances[uid])}")
    save_data()

async def diem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"Số dư: {fmt_amount(balances.get(uid,0))}")

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = sorted(leaderboard.items(), key=lambda kv: kv[1], reverse=True)[:10]
    if not items:
        await update.message.reply_text("Chưa có dữ liệu.")
        return
    lines = ["🏆 Top leaderboard:"]
    for uid, val in items:
        lines.append(f"{user_names.get(uid,uid)} — {fmt_amount(val)}")
    await update.message.reply_text("\n".join(lines))

# --- set / check / lich / tinhyeu / info ---
ADMINS = set()  # populate if needed

def is_admin(uid:int)->bool:
    return uid in ADMINS

async def set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("Bạn không có quyền.")
        return
    text = update.message.text or ""
    s_match = re.search(r"s=(\d+)", text)
    bias_match = re.search(r"bias=(\d+)", text)
    global DEFAULT_TX_COUNTDOWN
    if s_match:
        DEFAULT_TX_COUNTDOWN = int(s_match.group(1))
    await update.message.reply_text(f"Đã cập nhật: s={DEFAULT_TX_COUNTDOWN}")

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Cú pháp: /check @username")
        return
    uname = context.args[0].lstrip("@")
    try:
        chat = await context.bot.get_chat(f"@{uname}")
        await update.message.reply_text(f"@{uname} => id: {chat.id}")
        return
    except Exception:
        for uid, name in user_names.items():
            if name and name.lstrip("@").lower()==uname.lower():
                await update.message.reply_text(f"@{uname} => id: {uid} (cache)")
                return
    await update.message.reply_text("Không tìm thấy.")

async def lich_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    holidays = [("Tết Dương lịch","2025-01-01"),("Tết Nguyên đán","2025-01-29"),("Quốc khánh","2025-09-02")]
    await update.message.reply_text("\n".join([f"{n} — {d}" for n,d in holidays]))

async def tinhyeu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2:
        await update.message.reply_text("Cú pháp: /tinhyeu tên1 tên2")
        return
    pct = random.choice([10,45,0,98,77, random.randint(0,100)])
    await update.message.reply_text(f"💖 {context.args[0]} + {context.args[1]} = {pct}%")

async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        members = (await context.bot.get_chat(chat.id)).get_member_count()
    except:
        members = "unknown"
    await update.message.reply_text(f"Tên: {chat.title or 'private'}\nID: {chat.id}\nThành viên: {members}")

# ------------------------------
# --- TÀI / XỈU (button) ---
# ------------------------------
class TxSession:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.bets = []  # list of dict {uid, uname, choice, amount}
        self.end_time = None
        self.last_bet_time = None
        self.countdown = DEFAULT_TX_COUNTDOWN
        self.running = False
        self.previous_result = None
        self._last_edit = None
        self.message_id = None

active_tx: Dict[int, TxSession] = {}

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Cú pháp: /tx <tiền>")
        return
    amount = parse_amount(context.args[0])
    if amount is None:
        await update.message.reply_text("Số tiền không hợp lệ.")
        return
    uid = update.effective_user.id
    user_names[uid] = update.effective_user.username or update.effective_user.full_name
    context.user_data["pending_tx_amount"] = amount
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔵 Tài", callback_data=f"tx|t|{amount}"),
                                InlineKeyboardButton("🔴 Xỉu", callback_data=f"tx|x|{amount}")]])
    await update.message.reply_text(f"Đặt {fmt_amount(amount)} — chọn:", reply_markup=kb)

async def tx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    try:
        _, choice, amount = data.split("|")
        amount = int(amount)
    except:
        await q.edit_message_text("Dữ liệu không hợp lệ.")
        return
    uid = q.from_user.id
    uname = q.from_user.username or q.from_user.full_name
    user_names[uid] = uname
    if balances.get(uid,0) < amount:
        await q.edit_message_text("Bạn không đủ tiền.")
        return
    # deduct and register bet
    balances[uid] -= amount
    chat_id = q.message.chat_id
    session = active_tx.get(chat_id)
    if not session or not session.running:
        # create session
        m = await context.bot.send_message(chat_id, "🎲 Phiên TX bắt đầu — chờ cược...")
        session = TxSession(chat_id)
        session.running = True
        session.end_time = datetime.now() + timedelta(seconds=session.countdown)
        session._last_edit = datetime.now()
        session.message_id = m.message_id
        active_tx[chat_id] = session
        # spawn countdown loop
        asyncio.create_task(run_tx_countdown(context.application, session))
    session.bets.append({"uid":uid,"uname":uname,"choice":choice,"amount":amount})
    session.last_bet_time = datetime.now()
    await q.edit_message_text(f"✅ @{uname} cược {'Tài' if choice=='t' else 'Xỉu'} {fmt_amount(amount)}")
    save_data()

async def run_tx_countdown(app: Application, session: TxSession):
    chat_id = session.chat_id
    try:
        while session.running:
            now = datetime.now()
            if session.last_bet_time and (now - session.last_bet_time).total_seconds() >= AUTO_CLOSE_AFTER_LAST_BET and session.bets:
                break
            if session.end_time and now >= session.end_time:
                break
            if session._last_edit is None or (now - session._last_edit).total_seconds() >= COUNTDOWN_EDIT_INTERVAL:
                remaining = max(0, int((session.end_time - now).total_seconds())) if session.end_time else session.countdown
                if session.bets:
                    body = "\n".join([f"• {b['uname']}: {'Tài' if b['choice']=='t' else 'Xỉu'} {fmt_amount(b['amount'])}" for b in session.bets])
                else:
                    body = "Chưa có ai đặt cược."
                try:
                    await app.bot.edit_message_text(f"⏳ Phiên TX — còn {remaining}s\n{body}", chat_id=chat_id, message_id=session.message_id)
                except:
                    pass
                session._last_edit = now
            await asyncio.sleep(0.6)
    except Exception as e:
        logger.exception("tx countdown error: %s", e)
    await end_tx_session(app, session)

async def end_tx_session(app: Application, session: TxSession):
    session.running = False
    chat_id = session.chat_id
    # choose result (bias simple: 60% repeat previous)
    if session.previous_result and random.random() < 0.6:
        result = session.previous_result
    else:
        dice = [random.randint(1,6) for _ in range(3)]
        s = sum(dice)
        result = "t" if s>=11 else "x"
        session.previous_result = result
    # try send PNG
    png = image_path("tai.png") if result=="t" else image_path("xiu.png")
    if png:
        try:
            await send_group_or_chat(app, chat_id, f"🎉 Kết quả: {'Tài' if result=='t' else 'Xỉu'}")
            with open(png, "rb") as f:
                await app.bot.send_photo(GROUP_ID or chat_id, f)
        except:
            await send_group_or_chat(app, chat_id, f"🎉 KQ: {'Tài' if result=='t' else 'Xỉu'}")
    else:
        await send_group_or_chat(app, chat_id, f"🎉 KQ: {'Tài' if result=='t' else 'Xỉu'}")
    winners=[]; losers=[]
    for b in session.bets:
        if b['choice']==result:
            payout = b['amount']*2
            balances[b['uid']] += payout
            leaderboard[b['uid']] += (payout - b['amount'])
            winners.append((b['uname'], payout))
        else:
            leaderboard[b['uid']] -= b['amount']
            losers.append((b['uname'], b['amount']))
    lines = [f"🎉 Chi tiết: {'Tài' if result=='t' else 'Xỉu'}"]
    if winners:
        lines.append("🏆 Thắng:")
        lines += [f"• @{u} nhận {fmt_amount(p)}" for u,p in winners]
    else:
        lines.append("🏆 Thắng: Không ai")
    if losers:
        lines.append("😞 Thua:")
        lines += [f"• {u} mất {fmt_amount(a)}" for u,a in losers]
    await send_group_or_chat(app, chat_id, "\n".join(lines))
    active_tx.pop(chat_id, None)
    save_data()

# -----------------------
# --- XỔ SỐ
# -----------------------
class XoSoSession:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.picks = {}  # uid -> list of numbers
        self.end_time = None
        self.running = False
        self.message_id = None
        self._last_edit = None

active_xoso: Dict[int, XoSoSession] = {}

async def xoso_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_xoso and active_xoso[chat_id].running:
        await update.message.reply_text("Đã có phiên Xổ Số.")
        return
    m = await update.message.reply_text(f"🎰 Xổ số {XOSO_MIN}-{XOSO_MAX} — kéo dài {XOSO_DEFAULT_SESSION}s. /chon để tham gia")
    s = XoSoSession(chat_id)
    s.end_time = datetime.now() + timedelta(seconds=XOSO_DEFAULT_SESSION)
    s.running = True
    s.message_id = m.message_id
    s._last_edit = datetime.now()
    active_xoso[chat_id] = s
    asyncio.create_task(run_xoso_countdown(context.application, s))

async def chon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = active_xoso.get(chat_id)
    if not session or not session.running:
        await update.message.reply_text("Không có phiên xổ số.")
        return
    if not context.args:
        await update.message.reply_text("Cú pháp: /chon 1,5 100k")
        return
    nums_raw = context.args[0]
    nums = []
    for s in nums_raw.replace(" ", "").split(","):
        try:
            v = int(s)
            if XOSO_MIN <= v <= XOSO_MAX:
                nums.append(v)
        except:
            pass
    if not nums or len(nums) > XOSO_MAX_CHOICES:
        await update.message.reply_text("Số chọn không hợp lệ.")
        return
    amount = parse_amount(context.args[1]) if len(context.args)>1 else 0
    uid = update.effective_user.id
    user_names[uid] = update.effective_user.username or update.effective_user.full_name
    session.picks[uid] = nums
    await update.message.reply_text(f"✅ @{user_names[uid]} chọn {nums} với {fmt_amount(amount)}")

async def run_xoso_countdown(app: Application, session: XoSoSession):
    chat_id = session.chat_id
    try:
        while session.running:
            now = datetime.now()
            if now >= session.end_time:
                break
            if session._last_edit is None or (now - session._last_edit).total_seconds() >= COUNTDOWN_EDIT_INTERVAL:
                remaining = int((session.end_time - now).total_seconds())
                try:
                    await app.bot.edit_message_text(f"🎰 Xổ số còn {remaining}s — người đã chọn: {len(session.picks)}", chat_id=chat_id, message_id=session.message_id)
                except:
                    pass
                session._last_edit = now
            await asyncio.sleep(1)
    except Exception as e:
        logger.exception("xoso countdown error: %s", e)
    # results: 1..10 random numbers (can duplicate)
    results = [random.randint(XOSO_MIN, XOSO_MAX) for _ in range(random.randint(1,10))]
    winners=[]
    for uid, nums in session.picks.items():
        for r in results:
            if r in nums:
                winners.append((uid, r))
    lines=[f"🎉 KQ xổ số: {results}"]
    if winners:
        for uid,r in winners:
            lines.append(f"• @{user_names.get(uid,uid)} trúng {r}")
    else:
        lines.append("Không ai trúng.")
    await send_group_or_chat(app, chat_id, "\n".join(lines))
    session.running = False
    active_xoso.pop(chat_id, None)

# -----------------------
# --- BẦU CUA
# -----------------------
BAU_CUA = {"bau":"🍐","cua":"🦀","ca":"🐟","ga":"🐔","nai":"🦌","tom":"🦞"}
async def baucua_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    kb = [[InlineKeyboardButton(f"{v} {k}", callback_data=f"baucua|{k}") for k,v in BAU_CUA.items()]]
    await update.message.reply_text("Chọn linh vật để đặt:", reply_markup=InlineKeyboardMarkup(kb))

async def baucua_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("|")
    if len(parts)<2:
        await q.edit_message_text("Tương tác không hợp lệ.")
        return
    choice = parts[1]
    uid = q.from_user.id; uname = q.from_user.username or q.from_user.full_name
    user_names[uid]=uname
    # For demo, default bet 100k
    amt = 100_000
    balances[uid] = balances.get(uid,0) - amt
    await send_group_or_chat(context, q.message.chat_id, f"🦀 @{uname} đặt {choice} {fmt_amount(amt)}")
    await q.edit_message_text(f"✅ Bạn đã đặt {choice} {fmt_amount(amt)}")

# -----------------------
# --- TẾT MINI
# -----------------------
async def liixi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    amt = random.randint(10_000, 200_000)
    balances[uid] = balances.get(uid,0) + amt
    user_names[uid] = update.effective_user.username or update.effective_user.full_name
    await update.message.reply_text(f"🧧 Bạn nhận được lì xì {fmt_amount(amt)}")

async def hoamai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wishes = ["🌸 Vạn sự như ý","🎉 Tấn tài tấn lộc","💖 An khang thịnh vượng","🍀 Sức khỏe dồi dào"]
    await update.message.reply_text(random.choice(wishes))

async def phao_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧨 BOOM! Chúc mừng Năm Mới 🎆")

async def xongdat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not user_names:
        await update.message.reply_text("Chưa có ai.")
        return
    await update.message.reply_text(f"👑 Người xông đất: {random.choice(list(user_names.values()))}")

# -----------------------
# --- FREE FIRE (ST + TC) simplified with button-only UI
# -----------------------
class FFPlayer:
    def __init__(self, uid:int, uname:str):
        self.user_id = uid
        self.username = uname
        self.hp = 200
        self.medkits = 2
        self.keos = 0
        self.guns = []  # list of weapon keys
        self.pistol = None
        self.alive = True
        self.knocked = False
        self.kills = 0
        self.jumped = False
        self.team = None
        self.money = 0
        self.mp5_level = 0

class FFLobby:
    def __init__(self, chat_id:int, mode:str):
        self.chat_id = chat_id
        self.mode = mode  # 'st' or 'tc'
        self.players: Dict[int, FFPlayer] = {}
        self.started = False
        self.message_id = None
        self.matchmaking_seconds = 0
        self.lock = asyncio.Lock()
        self.map_name = None

ff_lobbies: Dict[int, FFLobby] = {}

def ff_lobby_kb(chat_id:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Tham gia", callback_data=f"ff_join|{chat_id}"),
         InlineKeyboardButton("❌ Rời", callback_data=f"ff_leave|{chat_id}")],
        [InlineKeyboardButton("▶️ Bắt đầu", callback_data=f"ff_start|{chat_id}")]
    ])

def ff_plane_kb(chat_id:int):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🪂 Nhảy", callback_data=f"ff_jump|{chat_id}")]])

def ff_action_kb(chat_id:int, uid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔫 Bắn", callback_data=f"ff_shoot|{chat_id}|{uid}"),
         InlineKeyboardButton("🏃 Chạy", callback_data=f"ff_run|{chat_id}|{uid}")],
        [InlineKeyboardButton("🧱 Keo", callback_data=f"ff_glue|{chat_id}|{uid}"),
         InlineKeyboardButton("❤️ Máu", callback_data=f"ff_med|{chat_id}|{uid}")]
    ])

async def ff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏝 Sinh Tồn", callback_data="ff_mode|st"),
         InlineKeyboardButton("⚔️ Tử Chiến", callback_data="ff_mode|tc")],
        [InlineKeyboardButton("⭐ Rank", callback_data="ff_rank")],
    ])
    await update.message.reply_text("🔥 Free Fire — chọn chế độ", reply_markup=kb)

async def ff_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("|")
    mode = parts[1]
    chat_id = q.message.chat.id
    # create lobby
    lobby = FFLobby(chat_id, mode)
    uid = q.from_user.id; uname = q.from_user.username or q.from_user.full_name
    lobby.players[uid] = FFPlayer(uid, uname)
    user_names[uid] = uname
    m = await context.bot.send_message(chat_id, f"🎮 Phòng FF ({'Sinh tồn' if mode=='st' else 'Tử chiến'}) đã tạo. Người chơi: 1", reply_markup=ff_lobby_kb(chat_id))
    lobby.message_id = m.message_id
    ff_lobbies[chat_id] = lobby

async def ff_lobby_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("|")
    action = parts[0]
    chat_id = int(parts[1])
    lobby = ff_lobbies.get(chat_id)
    if not lobby:
        await q.edit_message_text("Phòng không tồn tại.")
        return
    uid = q.from_user.id; uname = q.from_user.username or q.from_user.full_name
    async with lobby.lock:
        if action=="ff_join":
            if uid in lobby.players:
                await q.edit_message_text(f"Bạn đã ở trong phòng. Tổng: {len(lobby.players)}")
                return
            # assign team if tc
            if lobby.mode=="tc":
                teams = [p.team for p in lobby.players.values() if p.team is not None]
                t0 = teams.count(0); t1 = teams.count(1)
                team = 0 if t0<=t1 else 1
                p = FFPlayer(uid, uname); p.team = team
                lobby.players[uid] = p
            else:
                lobby.players[uid] = FFPlayer(uid, uname)
            user_names[uid]=uname
            await q.edit_message_text(f"✅ @{uname} tham gia. Tổng: {len(lobby.players)}", reply_markup=ff_lobby_kb(chat_id))
            return
        if action=="ff_leave":
            if uid in lobby.players:
                lobby.players.pop(uid,None)
                await q.edit_message_text(f"🚪 @{uname} rời phòng. Tổng: {len(lobby.players)}", reply_markup=ff_lobby_kb(chat_id))
                if not lobby.players:
                    ff_lobbies.pop(chat_id,None)
                return
            else:
                await q.edit_message_text("Bạn không ở trong phòng.")
                return
        if action=="ff_start":
            if lobby.started:
                await q.edit_message_text("Phòng đã bắt đầu.")
                return
            lobby.started = True
            await q.edit_message_text("⏳ Đang ghép trận... (random 1..50s)")
            # start matchmaking loop
            asyncio.create_task(ff_matchmaking(context.application, lobby))
            return

async def ff_matchmaking(app: Application, lobby: FFLobby):
    chat_id = lobby.chat_id
    # random wait 1..50
    wait = random.randint(1, MATCHMAKING_MAX_WAIT)
    await asyncio.sleep(wait)
    # proceed to lobby spawn (5s)
    await send_group_or_chat(app, chat_id, f"✅ Ghép thành công! Vào sảnh {LOBBY_SPAWN_SECONDS}s...")
    await asyncio.sleep(LOBBY_SPAWN_SECONDS)
    # plane stage
    lobby.map_name = random.choice(["Làng Thông","Tháp Đồng Hồ","Cổng Trời","Khu Trung Cư","Đảo Quân Sự"])
    await send_group_or_chat(app, chat_id, f"✈️ Máy bay — Map: {lobby.map_name}\n🪂 30s để nhảy (bấm nút nếu muốn)",)
    # send plane kb once
    try:
        await app.bot.send_message(chat_id, "🪂 Nhấn để nhảy", reply_markup=ff_plane_kb(chat_id))
    except:
        pass
    # simulate plane wait but do not spam: announce only a few milestones
    await asyncio.sleep(5)
    await send_group_or_chat(app, chat_id, "✈️ Máy bay — 25s còn lại")
    await asyncio.sleep(15)
    await send_group_or_chat(app, chat_id, "✈️ Máy bay — 10s còn lại")
    await asyncio.sleep(8)
    await send_group_or_chat(app, chat_id, "✈️ Máy bay — 2s còn lại")
    # auto jump those not jumped
    for p in lobby.players.values():
        if not p.jumped:
            p.jumped = True
    await send_group_or_chat(app, chat_id, "🪂 Tất cả đã đáp đất — bắt đầu tìm đồ")
    # loot
    for p in lobby.players.values():
        p.pistol = random.choice(["m500","g18"])
        if random.random()<0.85:
            p.guns.append(random.choice(list(["ak47","scar","m14","mp5","mp40"])))
        await asyncio.sleep(0.05)
    # announce loot summary batched
    lines=["🔎 Loot summary:"]
    for p in lobby.players.values():
        lines.append(f"• @{p.username}: {p.pistol.upper()}" + (f" + {p.guns[0].upper()}" if p.guns else ""))
    await send_group_or_chat(app, chat_id, "\n".join(lines))
    # combat phase (auto)
    await send_group_or_chat(app, chat_id, f"⚔️ Combat bắt đầu — {COMBAT_SECONDS}s")
    start = datetime.now()
    end = start + timedelta(seconds=COMBAT_SECONDS)
    while datetime.now() < end:
        alive = [pl for pl in lobby.players.values() if pl.alive and not pl.knocked]
        if len(alive)<=1:
            break
        attacker = random.choice(alive)
        targets = [t for t in lobby.players.values() if t.user_id!=attacker.user_id and t.alive and not t.knocked]
        if not targets:
            break
        target = random.choice(targets)
        # simulate shot
        dmg = random.randint(15,60)
        target.hp -= dmg
        attacker.kills += 1 if random.random()<0.2 else 0
        if target.hp<=0 and target.alive:
            target.alive=False
            await send_group_or_chat(app, chat_id, f"🔫 @{attacker.username} hạ @{target.username} — {dmg} dmg")
        await asyncio.sleep(random.uniform(0.5,1.2))
    # determine winner
    survivors = [pl for pl in lobby.players.values() if pl.alive]
    if survivors:
        winner = survivors[0]
        await send_group_or_chat(app, chat_id, f"🏆 Kết thúc — Người sống sót: @{winner.username}")
    else:
        await send_group_or_chat(app, chat_id, "Hòa. Không còn ai sống sót.")
    # cleanup
    ff_lobbies.pop(chat_id, None)

# -----------------------
# --- Callbacks / Routing
# -----------------------
async def global_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    data = q.data or ""
    # dispatch for menu
    if data.startswith("menu_"):
        return await menu_button(update, context)
    if data.startswith("tx|"):
        return await tx_callback(update, context)
    if data.startswith("baucua|"):
        return await baucua_callback(update, context)
    if data.startswith("ff_mode|"):
        return await ff_mode_callback(update, context)
    if data.startswith("ff_"):
        return await ff_lobby_callback(update, context)
    # fallback
    await q.answer("Tương tác không xử lý được hoặc đã hết hạn.", show_alert=False)

# -----------------------
# --- Startup / main
# -----------------------
def register_handlers(app: Application):
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CallbackQueryHandler(global_callback))
    # system
    app.add_handler(CommandHandler("dangky", dangky_cmd))
    app.add_handler(CommandHandler("diem", diem_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("set", set_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("lich", lich_cmd))
    app.add_handler(CommandHandler("tinhyeu", tinhyeu_cmd))
    app.add_handler(CommandHandler("info", info_cmd))
    # TX, Xoso, Baucua
    app.add_handler(CommandHandler("tx", tx_cmd))
    app.add_handler(CommandHandler("xoso", xoso_cmd))
    app.add_handler(CommandHandler("chon", chon_cmd))
    app.add_handler(CommandHandler("baucua", baucua_cmd))
    # Tết
    app.add_handler(CommandHandler("liixi", liixi_cmd))
    app.add_handler(CommandHandler("hoamai", hoamai_cmd))
    app.add_handler(CommandHandler("phao", phao_cmd))
    app.add_handler(CommandHandler("xongdat", xongdat_cmd))
    # FF
    app.add_handler(CommandHandler("ff", ff_cmd))
    # free text fallback to notify group
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), lambda u,c: None))

async def on_startup(app: Application):
    logger.info("Starting periodic save task")
    app.create_task(periodic_save_task())

def main():
    load_data()
    app = Application.builder().token(BOT_TOKEN).build()
    register_handlers(app)
    app.add_handler(CallbackQueryHandler(global_callback))
    app.post_init(on_startup)
    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()