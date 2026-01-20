import discord
from discord import app_commands, Activity, ActivityType
from discord.ext import tasks
from datetime import datetime, timedelta, timezone
import json
import os
import logging
from itertools import cycle

# ================== CONFIG ==================

# DISCORD TOKEN (ENV VARIABLE)
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set")

# ROLE ID allowed to manage events
EVENTS_ADMIN_ROLE_ID = 1269705468876554421  # CHANGE THIS

# CHANNEL where announcements/reminders are sent
ANNOUNCEMENT_CHANNEL_ID = 1463247462952337510  # CHANGE THIS

EVENTS_FILE = "events.json"

EMBED_COLOR = 0x08B4CA
FOOTER_TEXT = "TRvACC Helper • Made by Alex - 1715580 for Türkiye vACC (VATSIM)"

# ================== LOGGING ==================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ================== DISCORD SETUP ==================

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ================== STATUS ROTATION ==================

STATUSES = [
    Activity(type=ActivityType.watching, name="TRvACC events"),
    Activity(type=ActivityType.watching, name="Turkish airspace"),
    Activity(type=ActivityType.playing, name="on VATSIM Türkiye"),
    Activity(type=ActivityType.listening, name="event briefings"),
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
    embed = discord.Embed(
        title=f"{prefix}: {event['name']}",
        description=event["description"],
        color=EMBED_COLOR
    )
    embed.add_field(name="🕒 Start (UTC)", value=event["start"], inline=True)
    embed.add_field(name="🕓 End (UTC)", value=event["end"], inline=True)
    embed.add_field(name="🆔 Event ID", value=str(event["id"]), inline=True)
    embed.set_footer(text=FOOTER_TEXT)
    embed.timestamp = datetime.now(timezone.utc)
    return embed

# ================== REMINDER TASK ==================

@tasks.loop(minutes=1)
async def reminder_task():
    now = datetime.now(timezone.utc)
    events = load_events()
    updated = False

    for event in events:
        if event.get("cancelled"):
            continue

        start = datetime.fromisoformat(event["start"])
        delta = start - now

        if not event.get("reminded_24h") and timedelta(hours=23, minutes=59) < delta < timedelta(hours=24, minutes=1):
            await announce_event(event, "⏰ Event starts in 24 hours")
            event["reminded_24h"] = True
            updated = True

        if not event.get("reminded_1h") and timedelta(minutes=59) < delta < timedelta(hours=1, minutes=1):
            await announce_event(event, "⏰ Event starts in 1 hour")
            event["reminded_1h"] = True
            updated = True

    if updated:
        save_events(events)

async def announce_event(event, prefix):
    channel = client.get_channel(ANNOUNCEMENT_CHANNEL_ID)
    if channel:
        await channel.send(embed=make_event_embed(event, prefix))

# ================== EVENTS ==================

@client.event
async def on_ready():
    logging.info(f"Logged in as {client.user}")

    if not rotate_status.is_running():
        rotate_status.start()

    if not reminder_task.is_running():
        reminder_task.start()

    await tree.sync()
    logging.info("Slash commands synced")

# ================== SLASH COMMANDS ==================

@tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🏓 Pong! `{round(client.latency * 1000)}ms`"
    )

@tree.command(name="help", description="Show bot help")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "**TRvACC Events Assistant**\n"
        "`/event_list` – List upcoming events\n"
        "`/event_info <id>` – Event details\n"
        "`/event_create` – Create event (Events Dept)\n"
        "`/event_delete <id>` – Delete event (Events Dept)\n"
        "`/ping` – Bot latency"
    )

@tree.command(name="event_list", description="List upcoming events")
async def event_list(interaction: discord.Interaction):
    events = [e for e in load_events() if not e.get("cancelled")]
    if not events:
        await interaction.response.send_message("No upcoming events.")
        return

    text = "\n".join(f"**{e['id']}** — {e['name']} ({e['start']} UTC)" for e in events)
    await interaction.response.send_message(text)

@tree.command(name="event_info", description="Get event details")
async def event_info(interaction: discord.Interaction, event_id: int):
    for event in load_events():
        if event["id"] == event_id:
            await interaction.response.send_message(embed=make_event_embed(event))
            return
    await interaction.response.send_message("❌ Event not found.")

@tree.command(name="event_create", description="Create an event (Events Dept only)")
async def event_create(
    interaction: discord.Interaction,
    name: str,
    start_utc: str,
    end_utc: str,
    description: str
):
    if not is_events_admin(interaction):
        await interaction.response.send_message("❌ You are not allowed to do this.", ephemeral=True)
        return

    events = load_events()
    event_id = max([e["id"] for e in events], default=0) + 1

    event = {
        "id": event_id,
        "name": name,
        "start": start_utc,
        "end": end_utc,
        "description": description,
        "cancelled": False
    }

    events.append(event)
    save_events(events)

    await interaction.response.send_message(
        "✅ Event created successfully.",
        embed=make_event_embed(event)
    )

@tree.command(name="event_delete", description="Delete an event (Events Dept only)")
async def event_delete(interaction: discord.Interaction, event_id: int):
    if not is_events_admin(interaction):
        await interaction.response.send_message("❌ You are not allowed to do this.", ephemeral=True)
        return

    events = load_events()
    events = [e for e in events if e["id"] != event_id]
    save_events(events)

    await interaction.response.send_message("🗑️ Event deleted.")

# ================== ERROR HANDLER ==================

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    logging.error(error)
    if interaction.response.is_done():
        await interaction.followup.send("❌ An error occurred.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ An error occurred.", ephemeral=True)

# ================== RUN ==================

client.run(BOT_TOKEN)
