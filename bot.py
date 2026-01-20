import discord
from discord import app_commands, Activity, ActivityType
from discord.ext import tasks
from datetime import datetime, timezone
import json
import os
import logging
from itertools import cycle

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set")

EVENTS_ADMIN_ROLE_ID = 123456789012345678  # CHANGE THIS
ANNOUNCEMENT_CHANNEL_ID = 123456789012345678  # CHANGE THIS
EVENTS_FILE = "events.json"

EMBED_COLORS = [0x08B4CA, 0x1A5DAB, 0xBC9B6A, 0x4A90E2]  # colors for embed rotation
FOOTER_TEXT = "TRvACC Helper • Made by Alex - 1715580 for Türkiye vACC (VATSIM)"

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ================== DISCORD ==================
intents = discord.Intents.default()
intents.guilds = True
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
    # Format dates nicely
    start_dt = datetime.fromisoformat(event["start"])
    end_dt = datetime.fromisoformat(event["end"])
    start_str = start_dt.strftime("%a, %d %b %Y %H:%M UTC")
    end_str = end_dt.strftime("%a, %d %b %Y %H:%M UTC")
    embed.add_field(name="🕒 Start (UTC)", value=start_str, inline=True)
    embed.add_field(name="🕓 End (UTC)", value=end_str, inline=True)
    embed.add_field(name="🆔 Event ID", value=str(event["id"]), inline=True)
    if event.get("positions"):
        pos_text = "\n".join(f"{pos}: {user}" for pos, user in event["positions"].items())
        embed.add_field(name="🧑‍✈️ Signups", value=pos_text or "No signups yet", inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    embed.timestamp = datetime.now(timezone.utc)
    return embed

# ================== EVENTS ==================
@client.event
async def on_ready():
    logging.info(f"Logged in as {client.user}")
    if not rotate_status.is_running():
        rotate_status.start()
    await tree.sync()
    logging.info("Slash commands synced")

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
        "`/event_signup <event_id> <position>` – Register for a position\n"
        "`/ping` – Bot latency"
    )

@tree.command(name="event_list", description="List upcoming events")
async def event_list(interaction: discord.Interaction):
    events = [e for e in load_events() if not e.get("cancelled")]
    if not events:
        await interaction.response.send_message("No upcoming events.")
        return
    text = "\n".join(f"**{e['id']}** — {e['name']} ({datetime.fromisoformat(e['start']).strftime('%d %b %Y %H:%M UTC')})" for e in events)
    await interaction.response.send_message(text)

@tree.command(name="event_info", description="Get event details")
async def event_info(interaction: discord.Interaction, event_id: int):
    for event in load_events():
        if event["id"] == event_id:
            await interaction.response.send_message(embed=make_event_embed(event))
            return
    await interaction.response.send_message("❌ Event not found.")

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
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    try:
        start_dt = datetime.fromisoformat(f"{date}T{start_time}:00+00:00")
        end_dt = datetime.fromisoformat(f"{date}T{end_time}:00+00:00")
        if end_dt <= start_dt:
            raise ValueError("End before start")
    except Exception:
        await interaction.response.send_message(
            "❌ Invalid date/time format.\nUse:\n`date`: YYYY-MM-DD\n`start_time`: HH:MM UTC\n`end_time`: HH:MM UTC",
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
        "positions": {}  # key=position, value=username
    }
    events.append(event)
    save_events(events)
    await interaction.response.send_message("✅ Event created.", embed=make_event_embed(event))

@tree.command(name="event_delete", description="Delete an event (Admin only)")
async def event_delete(interaction: discord.Interaction, event_id: int):
    if not is_events_admin(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    events = load_events()
    events = [e for e in events if e["id"] != event_id]
    save_events(events)
    await interaction.response.send_message("🗑️ Event deleted.")

@tree.command(name="event_signup", description="Sign up for a position in an event")
async def event_signup(interaction: discord.Interaction, event_id: int, position: str):
    events = load_events()
    for event in events:
        if event["id"] == event_id:
            # Register user
            event["positions"][position] = interaction.user.display_name
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
    await interaction.response.send_message("❌ Event not found.", ephemeral=True)

# ================== ERROR HANDLER ==================
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    logging.error(error)
    if interaction.response.is_done():
        await interaction.followup.send("❌ An error occurred.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ An error occurred.", ephemeral=True)

# ================== RUN ==================
if __name__ == "__main__":
    rotate_status.start()
    client.run(BOT_TOKEN)
