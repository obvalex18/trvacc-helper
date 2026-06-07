import discord
from discord import app_commands, Activity, ActivityType
from discord.ext import tasks
from datetime import datetime, timedelta, timezone
import json
import os
import logging
from itertools import cycle
import aiohttp

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set")

EVENTS_ADMIN_ROLE_ID = 1269705468876554421
ANNOUNCEMENT_CHANNEL_ID = 1463247462952337510
EVENTS_FILE = "events.json"

EMBED_COLORS = [0x08B4CA, 0x1A5DAB, 0xBC9B6A, 0x4A90E2]
FOOTER_TEXT = "TRvACC Helper • Made by Alex - 1715580 for Türkiye vACC (VATSIM)"

AISWEB_API_KEY = "1695390440"
AISWEB_API_PASS = "7cfa2aa4-ee67-11f0-a4e0-0050569ac2e1"
AISWEB_BASE = "http://www.aisweb.aer.mil.br/api"
# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ================== DISCORD ==================
intents = discord.Intents.default()
intents.guilds = True
intents.members = False
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ================== STATUS ROTATION ==================
STATUSES = [
    Activity(type=ActivityType.watching, name="TRvACC events"),
    Activity(type=ActivityType.playing, name="with controllers"),
    Activity(type=ActivityType.listening, name="event briefings"),
    Activity(type=ActivityType.watching, name="VATSIM Türkiye"),
]
status_cycle = cycle(STATUSES)

@tasks.loop(minutes=5)
async def rotate_status():
    await client.change_presence(activity=next(status_cycle))

# ================== STORAGE ==================
def load_events():
    if not os.path.exists(EVENTS_FILE):
        return []
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_events(events):
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2)

# ================== HELPERS ==================
def is_events_admin(interaction: discord.Interaction) -> bool:
    return any(role.id == EVENTS_ADMIN_ROLE_ID for role in interaction.user.roles)

def make_event_embed(event, prefix="📅 Event"):
    color = EMBED_COLORS[event["id"] % len(EMBED_COLORS)]
    embed = discord.Embed(
        title=f"{prefix}: {event['name']}",
        description=event["description"],
        color=color
    )
    # Format dates nicely with day-of-week
    start_dt = datetime.fromisoformat(event["start"])
    end_dt = datetime.fromisoformat(event["end"])
    start_str = start_dt.strftime("%a, %d %b %Y %H:%M UTC")
    end_str = end_dt.strftime("%a, %d %b %Y %H:%M UTC")
    embed.add_field(name="🕒 Start (UTC)", value=start_str, inline=True)
    embed.add_field(name="🕓 End (UTC)", value=end_str, inline=True)
    embed.add_field(name="🆔 Event ID", value=str(event["id"]), inline=True)
    if event.get("positions"):
        pos_text = "\n".join(f"{pos}: {user} {('(Note: ' + notes + ')') if notes else ''}" 
                             for pos, (user, notes) in event["positions"].items())
        embed.add_field(name="🧑‍✈️ Signups", value=pos_text or "No signups yet", inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    embed.timestamp = datetime.now(timezone.utc)
    return embed

async def fetch_weather(icao: str):
    headers = {
        "x-api-key": AISWEB_API_KEY,
        "x-api-pass": AISWEB_API_PASS
    }

    url = f"{AISWEB_BASE}/?icao={icao}&api=metar,taf"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
# ================== EVENTS ==================
@client.event
async def on_ready():
    logging.info(f"Logged in as {client.user}")
    if not rotate_status.is_running():
        rotate_status.start()
    if not reminder_check.is_running():
        reminder_check.start()
    await tree.sync()
    logging.info("Slash commands synced")

# ================== REMINDERS ==================
@tasks.loop(minutes=1)
async def reminder_check():
    events = load_events()
    now = datetime.now(timezone.utc)
    changed = False
    for event in events:
        if event.get("cancelled") or event.get("reminder_sent"):
            continue
        start_dt = datetime.fromisoformat(event["start"])
        if timedelta(minutes=29) < (start_dt - now) <= timedelta(minutes=30):
            # Send DM to each user
            for pos, (user_name, notes) in event.get("positions", {}).items():
                try:
                    user_obj = discord.utils.get(client.get_all_members(), name=user_name)
                    if user_obj:
                        msg = f"📢 Reminder: Event **{event['name']}** starts in 30 minutes. Your position: **{pos}**"
                        if notes:
                            msg += f"\n📝 Notes: {notes}"
                        await user_obj.send(msg)
                except Exception as e:
                    logging.warning(f"Failed to send reminder to {user_name}: {e}")
            event["reminder_sent"] = True
            changed = True
    if changed:
        save_events(events)

# ================== COMMANDS ==================
@tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{round(client.latency * 1000)}ms`")

@tree.command(name="help", description="Show bot help")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "**TRvACC Events Assistant**\n"
        "`/event_list` – List upcoming events\n"
        "`/event_info <id>` – Event details\n"
        "`/event_create` – Create event (Admin only)\n"
        "`/event_delete <id>` – Delete event (Admin only)\n"
        "`/event_signup <event_id> <position> [notes]` – Register for a position with optional notes\n"
        "`/event_remove <event_id> <position>` – Remove your signup\n"
        "`/ping` – Bot latency"
    )

@tree.command(name="event_list", description="List upcoming events")
async def event_list(interaction: discord.Interaction):
    events = [e for e in load_events() if not e.get("cancelled")]
    if not events:
        await interaction.response.send_message("No upcoming events. (ERR001)")
        return
    text = "\n".join(f"**{e['id']}** — {e['name']} ({datetime.fromisoformat(e['start']).strftime('%a, %d %b %Y %H:%M UTC')})" for e in events)
    await interaction.response.send_message(text)

@tree.command(name="event_info", description="Get event details")
async def event_info(interaction: discord.Interaction, event_id: int):
    for event in load_events():
        if event["id"] == event_id:
            await interaction.response.send_message(embed=make_event_embed(event))
            return
    await interaction.response.send_message("❌ Event not found. (ERR002)")

@tree.command(name="event_create", description="Create an event (Admin only)")
async def event_create(
    interaction: discord.Interaction,
    name: str,
    date: str,        # YYYY-MM-DD
    start_time: str,  # HH:MM UTC
    end_time: str,    # HH:MM UTC
    description: str
):
    if not is_events_admin(interaction):
        await interaction.response.send_message("❌ No permission. (ERR003)", ephemeral=True)
        return
    try:
        start_dt = datetime.fromisoformat(f"{date}T{start_time}:00+00:00")
        end_dt = datetime.fromisoformat(f"{date}T{end_time}:00+00:00")
        if end_dt <= start_dt:
            raise ValueError("End before start")
    except Exception:
        await interaction.response.send_message(
            "❌ Invalid date/time format. Use YYYY-MM-DD and HH:MM UTC. (ERR004)",
            ephemeral=True
        )
        return
    events = load_events()
    event_id = max([e["id"] for e in events], default=0) + 1
    event = {
        "id": event_id,
        "name": name,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "description": description,
        "cancelled": False,
        "positions": {},  # key=position, value=(username, notes)
        "reminder_sent": False
    }
    events.append(event)
    save_events(events)
    await interaction.response.send_message("✅ Event created.", embed=make_event_embed(event))

@tree.command(name="event_delete", description="Delete an event (Admin only)")
async def event_delete(interaction: discord.Interaction, event_id: int):
    if not is_events_admin(interaction):
        await interaction.response.send_message("❌ No permission. (ERR005)", ephemeral=True)
        return
    events = load_events()
    events = [e for e in events if e["id"] != event_id]
    save_events(events)
    await interaction.response.send_message("🗑️ Event deleted.")

@tree.command(name="event_signup", description="Sign up for a position in an event")
async def event_signup(interaction: discord.Interaction, event_id: int, position: str, notes: str = ""):
    events = load_events()
    for event in events:
        if event["id"] == event_id:
            event["positions"][position] = (interaction.user.display_name, notes)
            save_events(events)
            await interaction.response.send_message(f"✅ Registered **{interaction.user.display_name}** for **{position}**.", ephemeral=True)
            # Update persistent embed
            channel = client.get_channel(ANNOUNCEMENT_CHANNEL_ID)
            if channel:
                msg = await channel.fetch_message(event.get("announcement_msg_id")) if event.get("announcement_msg_id") else None
                embed = make_event_embed(event)
                if msg:
                    await msg.edit(embed=embed)
                else:
                    sent = await channel.send(embed=embed)
                    event["announcement_msg_id"] = sent.id
                    save_events(events)
            return
    await interaction.response.send_message("❌ Event not found. (ERR006)", ephemeral=True)

@tree.command(name="event_remove", description="Remove your signup from an event")
async def event_remove(interaction: discord.Interaction, event_id: int, position: str):
    events = load_events()
    for event in events:
        if event["id"] == event_id:
            if position in event.get("positions", {}) and event["positions"][position][0] == interaction.user.display_name:
                del event["positions"][position]
                save_events(events)
                await interaction.response.send_message(f"🗑️ Removed **{interaction.user.display_name}** from **{position}**.", ephemeral=True)
                # Update persistent embed
                channel = client.get_channel(ANNOUNCEMENT_CHANNEL_ID)
                if channel:
                    msg = await channel.fetch_message(event.get("announcement_msg_id")) if event.get("announcement_msg_id") else None
                    if msg:
                        await msg.edit(embed=make_event_embed(event))
                return
            else:
                await interaction.response.send_message("❌ You are not signed up for this position. (ERR007)", ephemeral=True)
                return
    await interaction.response.send_message("❌ Event not found. (ERR008)", ephemeral=True)

@tree.command(name="weather", description="Get METAR/TAF for an airport")
async def weather(interaction: discord.Interaction, icao: str):
    await interaction.response.defer()

    data = await fetch_weather(icao.upper())

    if not data:
        await interaction.followup.send(
            f"❌ Could not fetch weather for {icao.upper()} (ERR010)"
        )
        return

    # ---- extract safely (API structure may vary slightly) ----
    metar = data.get("metar", "No METAR available")
    taf = data.get("taf", "No TAF available")

    embed = discord.Embed(
        title=f"🌦️ Weather Report — {icao.upper()}",
        color=0x08B4CA
    )

    embed.add_field(name="METAR", value=f"```{metar}```", inline=False)
    embed.add_field(name="TAF", value=f"```{taf}```", inline=False)

    embed.set_footer(text=FOOTER_TEXT)

    await interaction.followup.send(embed=embed)

# ================== ERROR HANDLER ==================
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    logging.error(error)
    if interaction.response.is_done():
        await interaction.followup.send("❌ An error occurred. (ERR009)", ephemeral=True)
    else:
        await interaction.response.send_message("❌ An error occurred. (ERR009)", ephemeral=True)

# ================== RUN ==================
if __name__ == "__main__":
    client.run(BOT_TOKEN)
    
