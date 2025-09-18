import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import random
import re
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io

# --- BOT SETUP ---
# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass # dotenv is not required for production if vars are set in environment

TOKEN = os.getenv("TOKEN")
GUILD_ID = discord.Object(id=int(os.getenv("GUILD_ID")))
WELCOME_CHANNEL_ID = 1410541883884568688

# Define intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True

# Bot instance
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Role IDs for Team Reporting ---
TEAM_ROLE_IDS_1 = [
    1410618414891929681, 1410618481757393008, 1410618720820269186, 1410624457776828477,
    1410917154533933157, 1410917253972496467, 1410917375548592260, 1410917442301071390,
    1410917500035665940, 1410917542075174943, 1410917593614651454, 1410917639685148755,
    1410986563416101028, 1412412917285916743, 1412413014115749931, 1412413091328692384
]
TEAM_ROLE_IDS_2 = [
    1412413185612316673, 1412413282828156938, 1412413352042561617, 1412413444652531872,
    1412593735518388334, 1412593810982043678, 1412593861875990619, 1412593968452993055,
    1412594065018720267, 1412594134429991044, 1412594216219185152, 1412594288453226606,
    1412594355860013077, 1412594436877189250, 1412594499946942627, 1412594553575182498
]
ALL_TEAM_ROLE_IDS = set(TEAM_ROLE_IDS_1 + TEAM_ROLE_IDS_2)

# Dictionary to store active timers
active_timers = {}

async def generate_welcome_banner(member: discord.Member) -> io.BytesIO:
    """Generates a personalized welcome banner for a new member."""
    # --- 1. Setup and Load Assets ---
    # Load the background image
    try:
        background = Image.open("assets/welcome_bg.png").convert("RGBA")
    except FileNotFoundError:
        print("Error: assets/welcome_bg.png not found. Cannot generate banner.")
        return None

    # Load a font. A .ttf font file is required for resizing.
    try:
        font_path = "assets/times.ttf" # Use the new Times New Roman font
        main_font = ImageFont.truetype(font_path, 40) # Larger initial font size
    except FileNotFoundError:
        print("Error: assets/times.ttf not found. Using default font.")
        main_font = ImageFont.load_default()

    # --- 2. Process Avatar ---
    # Get user's avatar
    avatar_data = await member.display_avatar.read()
    avatar_image = Image.open(io.BytesIO(avatar_data)).convert("RGBA")

    # Resize avatar to 230x230
    avatar_image = avatar_image.resize((230, 230))

    # Create a circular mask
    mask = Image.new("L", avatar_image.size, 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.ellipse((0, 0) + avatar_image.size, fill=255)

    # --- 3. Composite Image ---
    # The banner canvas is a copy of the background
    banner = background.copy()

    # Paste the avatar onto the banner using the circular mask
    # Position: center (487, 301), radius 115px
    avatar_position = (487 - 115, 301 - 115)
    banner.paste(avatar_image, avatar_position, mask)

    # --- 4. Process and Draw Username ---
    draw = ImageDraw.Draw(banner)
    username = member.display_name

    # Define username placement rectangle
    rect_top_left = (640, 346)
    rect_dims = (184, 40)

    # Auto-scale font size for long names if a ttf font was loaded
    if isinstance(main_font, ImageFont.FreeTypeFont):
        while main_font.getbbox(username)[2] > rect_dims[0] and main_font.size > 1:
            main_font = ImageFont.truetype(font_path, main_font.size - 1)
    else: # Fallback for default font: Truncate
        text_width = draw.textbbox((0, 0), username, font=main_font)[2]
        if text_width > rect_dims[0]:
            while draw.textbbox((0,0), username + '...')[2] > rect_dims[0] and len(username) > 0:
                username = username[:-1]
            username += '...'

    # Recalculate text dimensions after potential truncation/scaling
    text_bbox = draw.textbbox((0,0), username, font=main_font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]

    text_x = rect_top_left[0] + (rect_dims[0] - text_width) / 2
    text_y = rect_top_left[1] + (rect_dims[1] - text_height) / 2

    # Draw main text (shadow removed)
    main_color = (0, 0, 0) # Black
    draw.text((text_x, text_y), username, font=main_font, fill=main_color)

    # --- 5. Save and Return ---
    # Save the final image to an in-memory buffer
    buffer = io.BytesIO()
    banner.save(buffer, format="PNG")
    buffer.seek(0)

    return buffer


class TimerView(discord.ui.View):
    """
    A view for timer controls (Pause/Resume and End).
    """
    def __init__(self, timer_id, interaction, duration_seconds, user):
        super().__init__(timeout=None)  # No timeout for persistent views
        self.timer_id = timer_id
        self.interaction = interaction
        self.user = user
        self.initial_duration = duration_seconds
        self.duration_seconds = duration_seconds
        self.end_time = datetime.now() + timedelta(seconds=duration_seconds)
        self.paused = False
        self.pause_start_time = None
        self.message = None

    async def update_embed(self):
        """Updates the timer embed with the current time remaining."""
        if not self.message:
            return

        if self.paused:
            remaining_seconds = self.duration_seconds
            status = "Paused ⏸️"
            color = 0xffa500 # Orange
        else:
            remaining_seconds = (self.end_time - datetime.now()).total_seconds()
            status = "Running ⏳"
            color = 0x2ecc71 # Green

        if remaining_seconds <= 0:
            remaining_seconds = 0
            status = "Finished ✅"
            color = 0xe74c3c # Red

        embed = self.create_timer_embed(remaining_seconds, status, color)
        try:
            await self.message.edit(embed=embed)
        except discord.NotFound:
            # Message was deleted, stop the timer
            self.stop_timer()

    def create_timer_embed(self, remaining_seconds, status, color):
        """Creates the timer embed."""
        minutes, seconds = divmod(int(remaining_seconds), 60)
        hours, minutes = divmod(minutes, 60)
        time_str = f"{hours:02}:{minutes:02}:{seconds:02}"

        # Create a dynamic progress bar
        progress_percentage = max(0, remaining_seconds) / self.initial_duration if self.initial_duration > 0 else 0
        bar_length = 25 # Increased bar length
        filled_length = int(bar_length * progress_percentage)
        progress_bar = '█' * filled_length + '░' * (bar_length - filled_length)

        embed = discord.Embed(
            title=f"Speech Timer For {self.user.display_name}",
            description=f"**Time Remaining:**\n# {time_str}\n`{progress_bar}`",
            color=color
        )
        embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.add_field(name="Status", value=status, inline=False)
        embed.set_footer(text=f"Timer started by {self.interaction.user.display_name}", icon_url=self.interaction.user.display_avatar.url)
        return embed

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸️", custom_id="pause_button")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.paused:
            # Resume
            self.paused = False
            pause_duration = datetime.now() - self.pause_start_time
            self.end_time += pause_duration
            button.label = "Pause"
            button.emoji = "⏸️"
            active_timers[self.timer_id]['paused'] = False
            await interaction.response.send_message("Timer resumed!", ephemeral=True)
        else:
            # Pause
            self.paused = True
            self.pause_start_time = datetime.now()
            button.label = "Resume"
            button.emoji = "▶️"
            active_timers[self.timer_id]['paused'] = True
            await interaction.response.send_message("Timer paused!", ephemeral=True)

        # We must edit the original message view, not create a new one.
        await interaction.message.edit(view=self)


    @discord.ui.button(label="End", style=discord.ButtonStyle.danger, emoji="⏹️", custom_id="end_button")
    async def end_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop_timer()
        await interaction.response.send_message("Timer ended.", ephemeral=True)
        # Disable buttons after ending
        for item in self.children:
            item.disabled = True

        # Update the embed to show "Ended" status
        ended_embed = self.create_timer_embed(0, "Ended ⏹️", 0x717d7e) # Grey color
        await self.message.edit(embed=ended_embed, view=self)


    def stop_timer(self):
        """Stops the timer and removes it from the active list."""
        if self.timer_id in active_timers:
            active_timers[self.timer_id]['task'].cancel()
            del active_timers[self.timer_id]

# --- Views for Team Reporting ---
class TeamSelect(discord.ui.Select):
    def __init__(self, placeholder, roles, sort_options: bool = True):
        options = []
        # Sort roles alphabetically only if sort_options is True
        role_list = sorted(roles, key=lambda r: r.name) if sort_options else roles

        for role in role_list:
            options.append(discord.SelectOption(label=role.name, value=str(role.id)))
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        selected_role_id = int(self.values[0])
        new_role = guild.get_role(selected_role_id)

        if not new_role:
            await interaction.followup.send("Error: Could not find the selected role.", ephemeral=True)
            return

        roles_to_remove = [role for role_id in ALL_TEAM_ROLE_IDS if (role := guild.get_role(role_id)) and role in member.roles and role.id != selected_role_id]

        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Team selection changed")

        if new_role not in member.roles:
            await member.add_roles(new_role, reason="Team selection")

        await interaction.followup.send(f"🎯 Successfully reported as {new_role.mention} ✅", ephemeral=True)

class TeamSelectionView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        roles1 = [guild.get_role(role_id) for role_id in TEAM_ROLE_IDS_1 if guild.get_role(role_id)]
        roles2 = [guild.get_role(role_id) for role_id in TEAM_ROLE_IDS_2 if guild.get_role(role_id)]

        if roles1:
            # First dropdown is NOT sorted, uses the order from TEAM_ROLE_IDS_1
            self.add_item(TeamSelect("Dropdown 1: 🎯 Pick Team (First Half) 🔽", roles1, sort_options=False))
        if roles2:
            # Second dropdown IS sorted alphabetically (default behavior)
            self.add_item(TeamSelect("Dropdown 2: 🎯 Pick Team (Second Half) 🔽", roles2))


# --- EVENTS ---
@bot.event
async def on_ready():
    """Event triggered when the bot is ready."""
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    print('------')
    await bot.tree.sync(guild=GUILD_ID)
    if not timer_update_loop.is_running():
        timer_update_loop.start()

@bot.event
async def on_member_join(member):
    """Event triggered when a new member joins the server."""
    welcome_channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if not welcome_channel:
        return

    # Define the new embed
    color_hex = "#EDD6B1"
    color_int = int(color_hex.lstrip('#'), 16)
    description_text = (
        f"**We warmly welcome our newest voice {member.mention}**\n\n"
        "Your voice, ideas, and passion for reasoning are now part of our mission to foster "
        "thoughtful dialogue and intellectual growth.\n\n"
        "We believe your presence will contribute to the growth of debate and enrich our "
        "round-table discussions. ✨"
    )

    embed = discord.Embed(
        title="🏛️Opening the Floor to Our Newest Voice",
        description=description_text,
        color=color_int,
        timestamp=datetime.now()
    )

    # FIX 2: Set thumbnail to the new member's avatar
    embed.set_thumbnail(url=member.display_avatar.url)

    embed.set_footer(text=member.guild.name, icon_url=member.guild.icon.url if member.guild.icon else None)


    # Generate the welcome banner
    banner_buffer = await generate_welcome_banner(member)

    if banner_buffer:
        # If banner generation was successful
        welcome_file = discord.File(banner_buffer, filename="welcome_banner.png")
        embed.set_image(url="attachment://welcome_banner.png")
        await welcome_channel.send(embed=embed, file=welcome_file)
    else:
        # Fallback to sending the embed without the banner image if banner fails
        await welcome_channel.send(embed=embed)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Event triggered when a member is updated, checks for role changes."""
    # Define the role to watch for
    role_id_to_watch = 1410554605494079498
    role = after.guild.get_role(role_id_to_watch)

    # Do nothing if the role doesn't exist or if the update is for a bot
    if not role or after.bot:
        return

    # Check if the role was added
    if role not in before.roles and role in after.roles:
        # The target channel for the notification
        target_channel_id = 1417850104806637670
        channel = after.guild.get_channel(target_channel_id)

        if channel:
            try:
                message_content = f"📢 Hear hear! {after.mention}"
                # FIX 1: Send the message and have it automatically delete after 60 seconds (1 minute)
                await channel.send(message_content, delete_after=60)
            except discord.Forbidden:
                # This can happen if the bot doesn't have permissions to send messages in the channel
                print(f"Could not send message to channel ID {target_channel_id}. Check my permissions.")
            except Exception as e:
                print(f"An error occurred while trying to send a temporary message: {e}")
        else:
            print(f"Could not find the target channel with ID: {target_channel_id}")


# --- PREFIX COMMANDS ---
@bot.command(name="report")
async def report(ctx: commands.Context):
    """Sends a message with dropdowns for team role selection."""
    color_hex = "#EDD6B1"
    color_int = int(color_hex.lstrip('#'), 16)

    embed = discord.Embed(
        title="📣 Hear! Hear! Debaters — Team Reporting",
        color=color_int
    )

    description_text = (
        "Dear <@&1410554605494079498>,\n\n"
        "Please report your team by selecting your **team name **from the dropdown menu. ⚠️ Choose carefully — all names are arranged in alphabetical order (A → Z).\n\n"
        "✅ Once you select, you’ll be assigned your team role automatically.\n"
        "🚫 Do not select more than one team.\n\n"
        "Thank you for reporting on time — it helps us keep the event organized and smooth. 🏆"
    )

    embed.description = description_text
    embed.set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

    view = TeamSelectionView(ctx.guild)

    await ctx.send(embed=embed, view=view)
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass # Bot doesn't have permissions to delete messages
    except discord.NotFound:
        pass # Message was already deleted


# --- SLASH COMMANDS ---

@bot.tree.command(name="teams", description="Displays a list of all teams and their members.", guild=GUILD_ID)
async def teams(interaction: discord.Interaction):
    """Creates and sends an embed listing all teams and their members."""
    await interaction.response.defer()

    guild = interaction.guild

    # Fetch roles in the specified order from the original ID lists, not alphabetically
    roles_part1 = [guild.get_role(role_id) for role_id in TEAM_ROLE_IDS_1]
    roles_part2 = [guild.get_role(role_id) for role_id in TEAM_ROLE_IDS_2]
    ordered_roles = [role for role in roles_part1 + roles_part2 if role is not None]

    # Split roles into two chunks based on the specified order
    teams_part1 = ordered_roles[:16]
    teams_part2 = ordered_roles[16:]

    # --- First Embed ---
    embed1 = discord.Embed(
        title="🏆 Team List (Part 1/2) 🏆",
        description="Here are the first 16 registered teams and their members.",
        color=discord.Color.red(),
        timestamp=datetime.now()
    )
    if guild.icon:
        embed1.set_thumbnail(url=guild.icon.url)

    for role in teams_part1:
        members = role.members
        member_list = ' '.join(m.mention for m in members) if members else "👻 No members have reported to this team yet."
        if len(member_list) > 1024:
            member_list = f"{len(members)} members (list too long to display)."
        embed1.add_field(name=f"**{role.name}**", value=member_list, inline=False)

    embed1.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

    # Send the first embed
    await interaction.followup.send(embed=embed1)

    # --- Second Embed (if there are more teams) ---
    if teams_part2:
        embed2 = discord.Embed(
            title="🏆 Team List (Part 2/2) 🏆",
            description="Here are the remaining registered teams and their members.",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        if guild.icon:
            embed2.set_thumbnail(url=guild.icon.url)

        for role in teams_part2:
            members = role.members
            member_list = ' '.join(m.mention for m in members) if members else "👻 No members have reported to this team yet."
            if len(member_list) > 1024:
                member_list = f"{len(members)} members (list too long to display)."
            embed2.add_field(name=f"**{role.name}**", value=member_list, inline=False)

        embed2.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

        # Send the second embed
        await interaction.followup.send(embed=embed2)


@bot.tree.command(name="content", description="Create a custom embed message.", guild=GUILD_ID)
@app_commands.describe(
    title="The title of the embed.",
    main_text="The main text. Use // for line breaks and {{Title}} Text for fields.",
    color="The color of the embed in hex format (e.g., #DBBE93).",
    image="Optional main image attachment.",
    thumbnail="Optional thumbnail image attachment."
)
async def content(
    interaction: discord.Interaction,
    title: str,
    main_text: str,
    color: str = None,
    image: discord.Attachment = None,
    thumbnail: discord.Attachment = None
):
    """Creates a custom embed message."""
    # Defer interaction to prevent timeout, especially when handling files.
    await interaction.response.defer(ephemeral=False)

    try:
        color_hex = color or "#EDD6B1"

        try:
            # Convert hex color string to integer
            color_int = int(color_hex.lstrip('#'), 16)
        except (ValueError, TypeError):
            await interaction.followup.send("Invalid hex color format. Please use a format like `#DBBE93`.", ephemeral=True)
            return

        # --- Main Embed Setup ---
        main_embed = discord.Embed(
            title=title,
            color=color_int,
            timestamp=datetime.now()
        )
        main_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        main_embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        # --- Process Text and Fields ---
        description_content = main_text
        fields_content = ""
        first_field_match = re.search(r"\{\{", description_content)
        if first_field_match:
            split_index = first_field_match.start()
            fields_content = description_content[split_index:]
            description_content = description_content[:split_index]

        main_embed.description = description_content.replace('//', '\n').strip()

        if fields_content:
            fields = re.findall(r"\{\{(.*?)\}\}\s*(.*?)(?=\{\{|$)", fields_content, re.DOTALL)
            for field_title, field_value in fields:
                processed_value = field_value.replace('//', '\n').strip()
                if field_title.strip() and processed_value:
                    main_embed.add_field(name=field_title.strip(), value=processed_value, inline=False)

        # --- Prepare Image and Files ---
        files_to_send = []

        if image:
            files_to_send.append(await image.to_file())
            main_embed.set_image(url=f"attachment://{image.filename}")

        if thumbnail:
            files_to_send.append(await thumbnail.to_file())
            main_embed.set_thumbnail(url=f"attachment://{thumbnail.filename}")

        # Send the message, handling the case where there are no files
        if files_to_send:
            await interaction.followup.send(embed=main_embed, files=files_to_send)
        else:
            await interaction.followup.send(embed=main_embed)

    except Exception as e:
        print(f"Error in /content command: {e}")
        if not interaction.is_expired():
            await interaction.followup.send("An unexpected error occurred. Please check your command and try again.", ephemeral=True)


def parse_duration(duration_str: str) -> int:
    """Parses a duration string (e.g., '1h 20m 30s') into seconds."""
    total_seconds = 0
    matches = re.findall(r"(\d*\.?\d+)\s*(h|m|s)", duration_str, re.IGNORECASE)
    for value, unit in matches:
        value = float(value)
        if unit.lower() == 'h':
            total_seconds += value * 3600
        elif unit.lower() == 'm':
            total_seconds += value * 60
        elif unit.lower() == 's':
            total_seconds += value
    return int(total_seconds)

@bot.tree.command(name="time", description="Starts a timer for a debate or speech.", guild=GUILD_ID)
@app_commands.describe(
    duration="Duration of the timer (e.g., '1h 20m 30s').",
    user="The user to start the timer for (optional)."
)
async def time(interaction: discord.Interaction, duration: str, user: discord.Member = None):
    """Starts a timer."""
    if user is None:
        user = interaction.user

    duration_seconds = parse_duration(duration)
    if duration_seconds <= 0:
        await interaction.response.send_message("Please provide a valid duration.", ephemeral=True)
        return

    timer_id = f"{interaction.guild_id}-{interaction.channel_id}-{user.id}"
    if timer_id in active_timers:
        await interaction.response.send_message(f"A timer is already active for {user.mention}.", ephemeral=True)
        return

    view = TimerView(timer_id, interaction, duration_seconds, user)
    embed = view.create_timer_embed(duration_seconds, "Starting...", 0x3498db) # Blue for starting
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()
    view.message = message

    # Start the background task
    task = asyncio.create_task(timer_task(timer_id, user, duration_seconds, interaction.channel, view))
    active_timers[timer_id] = {'task': task, 'view': view, 'paused': False}

async def timer_task(timer_id, user, duration_seconds, channel, view):
    """The background task for the timer."""
    end_time = datetime.now() + timedelta(seconds=duration_seconds)
    one_minute_warning_sent = False

    while True:
        if not active_timers[timer_id]['paused']:
            remaining_seconds = (end_time - datetime.now()).total_seconds()

            if remaining_seconds <= 60 and not one_minute_warning_sent:
                await channel.send(f"📢 Hear Hear! 1 minute left, {user.mention}")
                one_minute_warning_sent = True

            if remaining_seconds <= 0:
                await channel.send(f"⏰ Time’s up, {user.mention}!")
                view.stop_timer()
                # Disable buttons
                for item in view.children:
                    item.disabled = True
                await view.message.edit(view=view)
                break

        # Adjust end_time if paused
        if active_timers[timer_id]['paused']:
             end_time += timedelta(seconds=1)

        await asyncio.sleep(1)


@tasks.loop(seconds=1)
async def timer_update_loop():
    """Periodically updates the embeds of active timers."""
    for timer_id, timer_data in list(active_timers.items()):
        view = timer_data['view']
        await view.update_embed()

@bot.tree.command(name="coinflip", description="Flips a virtual coin.", guild=GUILD_ID)
async def coinflip(interaction: discord.Interaction):
    """Flips a coin."""
    # Send initial "flipping" message
    flipping_embed = discord.Embed(
        title="Flipping a coin...",
        description="🪙 The coin is in the air!",
        color=0x3498db  # Blue
    )
    await interaction.response.send_message(embed=flipping_embed)

    await asyncio.sleep(1.5)  # Wait for a "flipping" animation effect

    result = random.choice(["Heads", "Tails"])
    result_emoji = "👑" if result == "Heads" else "🪙"
    color = 0xFFD700 if result == "Heads" else 0xC0C0C0 # Gold for heads, Silver for tails

    final_embed = discord.Embed(
        title="Coin Flip Result",
        description=f"# {result_emoji} {result} {result_emoji}",
        color=color
    )
    final_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    final_embed.set_footer(text=f"Flipped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    await interaction.edit_original_response(embed=final_embed)


@bot.tree.command(name="guide", description="Shows the user guide for this bot.", guild=GUILD_ID)
async def guide(interaction: discord.Interaction):
    """Shows the user guide."""
    embed = discord.Embed(
        title="Bot User Guide",
        description="Here's how to use the bot's commands:",
        color=0x3498db
    )
    embed.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)

    embed.add_field(
        name="👋 Welcome Messages",
        value="The bot automatically welcomes new members in a designated channel.",
        inline=False
    )
    embed.add_field(
        name="`!report` (Prefix Command)",
        value="Use this command to post the team selection dropdowns.",
        inline=False
    )
    embed.add_field(
        name="`/teams`",
        value="Displays a list of all teams and the members currently assigned to them.",
        inline=False
    )
    embed.add_field(
        name="`/purge`",
        value="Deletes a specified number of recent messages (you must have 'Manage Messages' permission).",
        inline=False
    )
    embed.add_field(
        name="`/content`",
        value=(
            "Create custom embeds.\n"
            "**Example:** `/content title:My Event main_text:Join us!//{{Details}} At 8 PM sharp. color:#DBBE93`\n"
            "- `//` creates a new line.\n"
            "- `{{Title}} Value` creates a new field."
        ),
        inline=False
    )
    embed.add_field(
        name="`/time`",
        value=(
            "Start a timer for yourself or another user.\n"
            "**Example:** `/time duration:1m 30s user:@somebody`\n"
            "- Buttons to pause/resume and end the timer.\n"
            "- Alerts at 1 minute remaining and when time is up."
        ),
        inline=False
    )
    embed.add_field(
        name="`/coinflip`",
        value="Flips a virtual coin and shows the result.",
        inline=False
    )

    embed.set_footer(text="Enjoy using the bot!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- NEW FEATURE ---
@bot.tree.command(name="purge", description="Deletes a specified number of messages from the channel.", guild=GUILD_ID)
@app_commands.describe(count="The number of messages to delete (up to 100).")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, count: app_commands.Range[int, 1, 100]):
    """Deletes a specified number of messages."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    # Purge messages from the channel the command was used in
    deleted = await interaction.channel.purge(limit=count)
    await interaction.followup.send(f"✅ Successfully deleted {len(deleted)} message(s).", ephemeral=True)

# Error handler for the /purge command
@purge.error
async def on_purge_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("🚫 You do not have the required permissions (Manage Messages) to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message("An unexpected error occurred. Please try again later.", ephemeral=True)
        print(f"Error in /purge command: {error}")


# --- BOT RUN ---
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("Bot token not found. Please set the TOKEN environment variable.")


