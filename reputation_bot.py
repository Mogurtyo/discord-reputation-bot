import discord
import os
import json
import uuid
import re
import traceback
import aiofiles
import logging
from dotenv import load_dotenv
from discord.ext import commands
from discord import app_commands, Interaction
from datetime import datetime, timezone

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Initialize bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ===== Reputation System =====
# Regex patterns for token addresses
ETHEREUM_PATTERN = re.compile(r'0x[a-fA-F0-9]{40}')
SOLANA_PATTERN = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')

# Reputation data structures
bot.reputation = {}  # {user_id: {"good": int, "bad": int, "tokens": {token_address: {"good": int, "bad": int, "goodvoters": [], "badvoters": [], "symbol": str}}}
bot.reputation_log = {}  # {vote_id: vote_data}
bot.current_votes = {}  # {vote_id: vote_info}
bot.disabled_voters = set()
bot.reputation_log_channels = {}
bot.token_messages = {}  # {message_id: {"author": user_id, "token_address": str, "token_symbol": str}}

# Fixed special user ID
SPECIAL_USER_ID = int(os.getenv('SPECIAL_USER_ID', '1081815963990761542'))

# ===== Helper Functions =====
def extract_token_address(embed: discord.Embed) -> str:
    """Extract token address from embed content"""
    text = ""
    if embed.title:
        text += embed.title
    if embed.description:
        text += embed.description
    for field in embed.fields:
        text += field.name + (field.value or "")
    if embed.footer and embed.footer.text:
        text += embed.footer.text
    
    # Look for Solana address first (base58, 44 characters)
    sol_match = SOLANA_PATTERN.search(text)
    if sol_match:
        return sol_match.group(0)
    
    # Then look for Ethereum address
    eth_match = ETHEREUM_PATTERN.search(text)
    if eth_match:
        return eth_match.group(0)
    
    return "unknown"

def extract_token_symbol(text: str) -> str:
    """Improved token symbol extraction with dollar sign and markdown support"""
    if not text:
        return ""
    
    # Look for bolded dollar sign pattern (**$SYMBOL**)
    bold_dollar_match = re.search(r'\*\*\$([A-Za-z0-9 ]{2,20})\*\*', text)
    if bold_dollar_match:
        return bold_dollar_match.group(1).strip().upper()
    
    # Look for dollar sign pattern ($SYMBOL) - now allows spaces
    dollar_match = re.search(r'\$([A-Za-z0-9 ]{2,20})', text)
    if dollar_match:
        return dollar_match.group(1).upper()
    
    # Look for token symbol in markdown links [SYMBOL]
    markdown_match = re.search(r'\[([A-Za-z0-9 ]{2,20})\]\(', text)
    if markdown_match:
        return markdown_match.group(1).strip().upper()
    
    # Then look for token symbol in parentheses
    paren_match = re.search(r'\(([A-Za-z0-9 ]{2,20})\)', text)
    if paren_match:
        return paren_match.group(1).strip().upper()
    
    # Then try to extract from text content
    clean_text = re.sub(r'^[\s\U00010000-\U0010ffff]+', '', text)
    parts = re.split(r'[\s\-–—]+', clean_text)
    if parts:
        symbol = parts[0].strip('.,:;!?*$')
        if symbol:
            return symbol[:20].upper()
    
    return ""

def create_simple_rep_embed(member: discord.Member) -> discord.Embed:
    """Create simplified reputation embed for special responses"""
    user_id = member.id
    user_data = bot.reputation.get(str(user_id), {"good": 0, "bad": 0})
    total_good = user_data.get("good", 0)
    total_bad = user_data.get("bad", 0)
    total_score = total_good - total_bad
    total_votes = total_good + total_bad
    reputation_percent = (total_good / total_votes * 100) if total_votes > 0 else 0.0

    embed = discord.Embed(
        description=(
            f"{member.mention} has "
            f"`{total_good}` 🟢 | `{total_bad}` 🔴 | "
            f"**Score:** `{total_score}` 🪙 | "
            f"**Reputation:** `{reputation_percent:.1f}%`"
        ),
        color=0x000000  # Black color
    )
    return embed

def create_rep_embed(target_user: discord.Member) -> discord.Embed:
    user_id_str = str(target_user.id)
    user_data = bot.reputation.get(user_id_str, {"good": 0, "bad": 0, "tokens": {}})
    
    embed = discord.Embed(
        title=f"Reputation Profile of {target_user.display_name}",
        color=0x000000  # Black color
    )
    embed.set_thumbnail(url=target_user.display_avatar.url)
    
    # Total stats
    total_good = user_data["good"]
    total_bad = user_data["bad"]
    total_score = total_good - total_bad
    
    embed.add_field(
        name="Total Reputation",
        value=(
            f"**Score:** `{total_score}`\n"
            f"**Upvotes:** `{total_good}`\n"
            f"**Downvotes:** `{total_bad}`"
        ),
        inline=False
    )
    
    # Per-token stats
    token_entries = list(user_data.get("tokens", {}).items())
    
    # Sort tokens by total votes (good + bad) descending
    token_entries.sort(key=lambda x: x[1]["good"] + x[1]["bad"], reverse=True)
    
    if token_entries:
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        token_fields = []
        for idx, (token_address, data) in enumerate(token_entries[:5]):
            medal = medals[idx] if idx < len(medals) else f"{idx+1}."
            token_score = data["good"] - data["bad"]
            token_symbol = data.get("symbol", token_address[:6] + "...")
            
            # Escape markdown but preserve links
            token_symbol_escaped = discord.utils.escape_markdown(token_symbol)
            
            if token_address == "unknown" or not token_symbol:
                display = token_symbol_escaped
            else:
                # Create clickable link
                display = f"[{token_symbol_escaped}](https://axiom.trade/t/{token_address}/@monki)"
            
            token_field = (
                f"{medal} {display}\n"
                f"Up: `{data['good']}` Down: `{data['bad']}` Score: `{token_score}`\n"
            )
            token_fields.append(token_field)
        
        embed.add_field(
            name="Top Tokens:",
            value="\n".join(token_fields),
            inline=False
        )
    
    return embed

# ===== Data Management =====
async def save_data():
    """Save all bot data to files"""
    try:
        # Save reputation data
        async with aiofiles.open('reputation.json', 'w') as f:
            await f.write(json.dumps(bot.reputation, indent=2))
        async with aiofiles.open('reputation_log.json', 'w') as f:
            await f.write(json.dumps(bot.reputation_log, indent=2, default=str))
        async with aiofiles.open('disabled_voters.json', 'w') as f:
            await f.write(json.dumps(list(bot.disabled_voters), indent=2))
        async with aiofiles.open('reputation_log_channels.json', 'w') as f:
            await f.write(json.dumps(bot.reputation_log_channels, indent=2))
        async with aiofiles.open('current_votes.json', 'w') as f:
            await f.write(json.dumps(bot.current_votes, indent=2, default=str))
    except Exception as e:
        print(f"Error saving data: {str(e)}")

def load_data():
    """Load all bot data from files"""
    try:
        # Load reputation data
        try:
            with open('reputation.json', 'r') as f:
                bot.reputation = json.load(f)
            with open('reputation_log.json', 'r') as f:
                bot.reputation_log = json.load(f)
            with open('disabled_voters.json', 'r') as f:
                bot.disabled_voters = set(json.load(f))
            with open('reputation_log_channels.json', 'r') as f:
                bot.reputation_log_channels = json.load(f)
            with open('current_votes.json', 'r') as f:
                bot.current_votes = json.load(f)
            
            # Add missing 'symbol' field to old token entries
            for user_id, user_data in bot.reputation.items():
                tokens = user_data.get("tokens", {})
                for token_address, token_data in tokens.items():
                    if "symbol" not in token_data:
                        token_data["symbol"] = token_address[:6] + "..." if token_address != "unknown" else "unknown"
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Error loading reputation data: {str(e)}")
    except Exception as e:
        print(f"Error in data loading: {e}")

# ===== Commands =====
@bot.tree.command(name="rep", description="Check a user's reputation")
async def rep(interaction: discord.Interaction, user: discord.Member = None):
    target_user = user or interaction.user
    embed = create_rep_embed(target_user)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="repboard", description="Show reputation leaderboard")
async def repboard(interaction: discord.Interaction):
    await interaction.response.defer()
    guild = interaction.guild
    
    rep_list = []
    for user_id_str, user_data in bot.reputation.items():
        try:
            user_id = int(user_id_str)
            member = guild.get_member(user_id)
            if not member:
                continue

            good = user_data.get("good", 0)
            bad = user_data.get("bad", 0)
            if good == 0 and bad == 0:
                continue

            rep_list.append({
                "member": member,
                "score": good - bad,
                "good": good,
                "bad": bad
            })
        except Exception:
            continue

    # Sort descending by score
    rep_list.sort(key=lambda x: (-x["score"], -x["good"], x["bad"]))
    
    # Build embed
    embed = discord.Embed(title="🏆 Reputation Leaderboard", color=0x000000)  # Black color
    if rep_list:
        top_member = rep_list[0]["member"]
        embed.set_thumbnail(url=top_member.display_avatar.url)

    description_lines = []
    
    if rep_list:
        for idx, entry in enumerate(rep_list[:10]):
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            medal = medals[idx] if idx < len(medals) else f"{idx+1}."
            description_lines.append(
                f"{medal} {entry['member'].mention}\n - Score: {entry['score']} (↑{entry['good']} ↓{entry['bad']})"
            )
        description_lines.append(f"\nTotal participants: {len(rep_list)}")
    else:
        description_lines.append("No one has reputation points yet!")
    
    embed.description = "\n".join(description_lines)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="repadd", description="Add reputation votes to a user (Admin only)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    user="The user to add votes to",
    amount="The number of votes to add",
    vote_type="Type of votes to add"
)
@app_commands.choices(vote_type=[
    app_commands.Choice(name="Good", value="good"),
    app_commands.Choice(name="Bad", value="bad"),
])
async def repadd(interaction: discord.Interaction, 
                user: discord.Member, 
                amount: int, 
                vote_type: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Administrator permissions required", ephemeral=True)
        return

    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive", ephemeral=True)
        return

    vote_type_value = vote_type.value
    author_id = user.id
    voter_id = interaction.user.id

    # Initialize user data
    author_key = str(author_id)
    if author_key not in bot.reputation:
        bot.reputation[author_key] = {"good": 0, "bad": 0, "tokens": {}}

    # Generate votes
    vote_ids = []
    for _ in range(amount):
        vote_id = str(uuid.uuid4())
        vote_ids.append(vote_id)
        bot.reputation_log[vote_id] = {
            "vote_id": vote_id,
            "voter_id": voter_id,
            "author_id": author_id,
            "token_address": "admin_added",
            "vote_type": vote_type_value,
            "message_id": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reversed": False
        }
        bot.reputation[author_key][vote_type_value] += 1
        bot.current_votes[vote_id] = bot.reputation_log[vote_id]

    await save_data()

    # Log action
    log_channel_id = bot.reputation_log_channels.get(interaction.guild_id)
    if log_channel_id:
        log_channel = interaction.guild.get_channel(log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="Votes Added by Admin",
                description=(
                    f"**Admin:** {interaction.user.mention}\n"
                    f"**Target:** {user.mention}\n"
                    f"**Type:** {vote_type_value.capitalize()}\n"
                    f"**Amount:** `{amount}`\n"
                    f"**Vote IDs:** `{', '.join(vote_ids[:5])}`" + ("..." if len(vote_ids) > 5 else "")
                ),
                color=discord.Color.green() if vote_type_value == "good" else discord.Color.red()
            )
            await log_channel.send(embed=embed)

    await interaction.response.send_message(
        f"✅ Added `{amount}` {vote_type_value} votes to {user.mention}.",
        ephemeral=True
    )

@bot.tree.command(name="replogs", description="Set the channel for reputation logs")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="The channel to send reputation logs to")
async def replogs(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Administrator permissions required", ephemeral=True)
        return

    bot.reputation_log_channels[interaction.guild_id] = channel.id
    await save_data()
    await interaction.response.send_message(f"✅ Reputation logs will be sent to {channel.mention}", ephemeral=True)

@bot.tree.command(name="repdisable", description="Disable a user's ability to vote on reputation")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user="The user to disable/enable")
async def repdisable(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Administrator permissions required", ephemeral=True)
        return

    user_id = user.id
    if user_id in bot.disabled_voters:
        bot.disabled_voters.remove(user_id)
        action = "enabled"
    else:
        bot.disabled_voters.add(user_id)
        action = "disabled"

    await save_data()
    await interaction.response.send_message(f"✅ {user.mention} has been {action} from voting.", ephemeral=True)

@bot.tree.command(name="repremove", description="Remove reputation votes by IDs (Admin only)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(vote_ids="Comma-separated list of vote IDs to remove")
async def repremove(interaction: discord.Interaction, vote_ids: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Administrator permissions required", ephemeral=True)
        return

    raw_ids = [vid.strip() for vid in vote_ids.split(',')]
    results = {'success': [], 'invalid': [], 'reversed': []}
    affected_authors = set()

    for vid in raw_ids:
        vote = bot.reputation_log.get(vid)
        if not vote:
            results['invalid'].append(vid)
            continue
        if vote.get('reversed'):
            results['reversed'].append(vid)
            continue
            
        # Remove from counts
        author_key = str(vote['author_id'])
        vote_type = vote['vote_type']
        affected_authors.add(int(vote['author_id']))
        
        if author_key in bot.reputation:
            bot.reputation[author_key][vote_type] = max(0, bot.reputation[author_key].get(vote_type, 0) - 1)
            
            if vote['token_address'] != 'admin_added':
                token_data = bot.reputation[author_key].get("tokens", {}).get(vote['token_address'])
                if token_data:
                    token_data[vote_type] = max(0, token_data.get(vote_type, 0) - 1)
                    if vote['voter_id'] in token_data.get(f"{vote_type}voters", []):
                        token_data[f"{vote_type}voters"].remove(vote['voter_id'])
        
        vote['reversed'] = True
        if vid in bot.current_votes:
            del bot.current_votes[vid]
        results['success'].append(vid)

    await save_data()
    
    # Build response
    response = []
    if results['success']:
        response.append(f"✅ **Removed ({len(results['success'])})**:\n" + '\n'.join([f'- `{vid}`' for vid in results['success']]))
    if results['reversed']:
        response.append(f"⚠️ **Already Reversed ({len(results['reversed'])})**:\n" + '\n'.join([f'- `{vid}`' for vid in results['reversed']]))
    if results['invalid']:
        response.append(f"❌ **Invalid IDs ({len(results['invalid'])})**:\n" + '\n'.join([f'- `{vid}`' for vid in results['invalid']]))
    
    await interaction.response.send_message('\n\n'.join(response), ephemeral=True)

@bot.tree.command(name="repmanager", description="Manage and review reputation votes")
@app_commands.default_permissions(administrator=True)
async def repmanager(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Administrator permissions required", ephemeral=True)
        return

    await interaction.response.defer()
    
    # Get all votes sorted by timestamp (newest first)
    all_votes = sorted(
        bot.current_votes.values(),
        key=lambda x: x.get('timestamp', ''),
        reverse=True
    )

    if not all_votes:
        return await interaction.followup.send("❌ No votes found", ephemeral=True)

    embed = discord.Embed(
        title="Vote Manager",
        color=0x000000,  # Black color
        description="Most recent votes:"
    )
    
    for vote in all_votes[:10]:  # Show first 10
        vote_time = datetime.fromisoformat(vote['timestamp']).strftime("%Y-%m-%d %H:%M")
        token_symbol = "unknown"
        if vote['token_address'] != "admin_added":
            author_data = bot.reputation.get(str(vote['author_id']), {})
            token_data = author_data.get("tokens", {}).get(vote['token_address'], {})
            token_symbol = token_data.get("symbol", vote['token_address'][:6] + "...")
        
        embed.add_field(
            name=f"Vote ID: {vote['vote_id']}",
            value=(
                f"Type: `{vote['vote_type'].upper()}`\n"
                f"Author: <@{vote['author_id']}>\n"
                f"Voter: <@{vote['voter_id']}>\n"
                f"Token: `{token_symbol}` (`{vote['token_address'][:6]}...`)\n"
                f"Time: `{vote_time}`\n"
                f"Reversed: `{vote.get('reversed', False)}`"
            ),
            inline=False
        )
    
    await interaction.followup.send(embed=embed)

@bot.event
async def on_reaction_add(reaction, user):
    # Ignore bot reactions and disabled voters
    if user.bot or user.id in bot.disabled_voters:
        return

    message_id = reaction.message.id
    message_info = bot.token_messages.get(message_id)
    
    # Return early if no message info
    if not message_info:
        return
        
    # Prevent author from voting on their own reputation
    if user.id == message_info["author"]:
        try:
            # Remove author's reaction immediately
            await reaction.remove(user)
            
            # Send ephemeral explanation (try DM first, then channel)
            try:
                await user.send(
                    "❌ You cannot vote on your own reputation! Your reaction has been removed.",
                    delete_after=10.0
                )
            except discord.Forbidden:
                # If DMs are closed, send in channel with ephemeral message
                ctx = await bot.get_context(reaction.message)
                await ctx.send(
                    f"{user.mention} You cannot vote on your own reputation!",
                    delete_after=10.0,
                    ephemeral=True
                )
        except Exception as e:
            logging.error(f"Failed to remove author reaction: {e}")
        return

    # Check for valid voting emojis
    if str(reaction.emoji) not in ("🟢", "🔴"):
        return

    try:
        author_id = message_info["author"]
        token_address = message_info["token_address"]
        token_symbol = message_info["token_symbol"]
        voter_id = user.id
        new_vote = "good" if str(reaction.emoji) == "🟢" else "bad"

        # Initialize reputation data
        author_key = str(author_id)
        bot.reputation.setdefault(author_key, {"good": 0, "bad": 0, "tokens": {}})
        
        # Initialize token data with symbol
        if token_address not in bot.reputation[author_key]["tokens"]:
            bot.reputation[author_key]["tokens"][token_address] = {
                "good": 0,
                "bad": 0,
                "goodvoters": [],
                "badvoters": [],
                "symbol": token_symbol
            }
        else:
            # Ensure existing token data has symbol and update it
            token_data = bot.reputation[author_key]["tokens"][token_address]
            if "symbol" not in token_data or token_data["symbol"] == "unknown" or len(token_data["symbol"]) < 3:
                token_data["symbol"] = token_symbol
            # Always update symbol with latest extracted value
            token_data["symbol"] = token_symbol

        token_data = bot.reputation[author_key]["tokens"][token_address]

        # Remove existing votes
        existing_votes = False
        for vote_type in ["good", "bad"]:
            if voter_id in token_data[f"{vote_type}voters"]:
                existing_votes = True
                token_data[f"{vote_type}voters"].remove(voter_id)
                token_data[vote_type] -= 1
                bot.reputation[author_key][vote_type] -= 1

        # Add new vote
        token_data[f"{new_vote}voters"].append(voter_id)
        token_data[new_vote] += 1
        bot.reputation[author_key][new_vote] += 1

        # Create vote log
        vote_id = str(uuid.uuid4())
        vote_data = {
            "vote_id": vote_id,
            "voter_id": voter_id,
            "author_id": author_id,
            "token_address": token_address,
            "vote_type": new_vote,
            "message_id": reaction.message.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reversed": False
        }
        bot.reputation_log[vote_id] = vote_data
        bot.current_votes[vote_id] = vote_data

        # Log to channel if configured
        guild = reaction.message.guild
        log_channel_id = bot.reputation_log_channels.get(guild.id)
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title=f"Vote {'Switched' if existing_votes else 'Added'}",
                    color=discord.Color.green() if new_vote == "good" else discord.Color.red(),
                    description=(
                        f"**Voter:** {user.mention} (`{user.id}`)\n"
                        f"**Author:** <@{author_id}>\n"
                        f"**Type:** {new_vote}\n"
                        f"**Token:** `{token_symbol}` (`{token_address[:6]}...`)\n"
                        f"**Vote ID:** `{vote_id}`\n"
                        f"**Message:** [Jump]({reaction.message.jump_url})"
                    )
                )
                await log_channel.send(embed=embed)

        await save_data()
    except Exception as e:
        traceback.print_exc()

@bot.event
async def on_raw_reaction_remove(payload):
    guild = bot.get_guild(payload.guild_id)
    if not guild or payload.user_id in bot.disabled_voters:
        return

    try:
        member = await guild.fetch_member(payload.user_id)
    except discord.NotFound:
        return

    if member.bot:
        return

    message_id = payload.message_id
    message_info = bot.token_messages.get(message_id)
    
    # Prevent processing author's reaction removal
    if not message_info or member.id == message_info["author"]:
        return

    # Check for valid emojis
    emoji_name = payload.emoji.name if payload.emoji.is_unicode_emoji() else str(payload.emoji)
    if emoji_name not in ("🟢", "🔴"):
        return

    try:
        author_id = message_info["author"]
        token_address = message_info["token_address"]
        token_symbol = message_info["token_symbol"]
        voter_id = payload.user_id
        vote_type = "good" if emoji_name == "🟢" else "bad"

        author_key = str(author_id)
        if author_key not in bot.reputation:
            return

        token_data = bot.reputation[author_key].get("tokens", {}).get(token_address)
        if not token_data:
            return

        if voter_id in token_data.get(f"{vote_type}voters", []):
            # Remove vote
            token_data[f"{vote_type}voters"].remove(voter_id)
            token_data[vote_type] = max(0, token_data[vote_type] - 1)
            bot.reputation[author_key][vote_type] = max(0, bot.reputation[author_key][vote_type] - 1)

            # Update logs
            for vote_id, vote in list(bot.reputation_log.items()):
                if (vote['voter_id'] == voter_id and
                    vote['author_id'] == author_id and
                    vote['token_address'] == token_address and
                    vote['vote_type'] == vote_type and
                    vote['message_id'] == message_id and
                    not vote.get('reversed', False)):
            
                    vote['reversed'] = True
                    if vote_id in bot.current_votes:
                        del bot.current_votes[vote_id]

            # Log removal
            log_channel_id = bot.reputation_log_channels.get(guild.id)
            if log_channel_id:
                log_channel = guild.get_channel(log_channel_id)
                if log_channel:
                    embed = discord.Embed(
                        title="Vote Removed",
                        color=discord.Color.orange(),
                        description=(
                            f"**Voter:** {member.mention} (`{member.id}`)\n"
                            f"**Author:** <@{author_id}>\n"
                            f"**Type:** {vote_type}\n"
                            f"**Token:** `{token_symbol}` (`{token_address[:6]}...`)"
                        )
                    )
                    await log_channel.send(embed=embed)

            await save_data()
    except Exception as e:
        traceback.print_exc()

@bot.event
async def on_message(message):
    await bot.process_commands(message)

    # Process messages from the special bot
    if message.author.id == SPECIAL_USER_ID and message.embeds:
        for embed in message.embeds:
            if embed.footer and embed.footer.text:
                try:
                    # Extract username from footer
                    username = embed.footer.text.split()[0]
                    
                    # Find member by username
                    member = discord.utils.find(lambda m: m.name == username, message.guild.members)
                    if not member:
                        continue
                    
                    # Extract token address from embed
                    token_address = extract_token_address(embed)
                    
                    # Extract token symbol from message content
                    token_symbol = extract_token_symbol(message.content)
                    
                    # Handle unknown tokens
                    if token_address == "unknown" or not token_symbol:
                        token_symbol = token_address[:6] + "..." if token_address != "unknown" else "unknown"
                    
                    # Create simplified reputation embed
                    simple_embed = create_simple_rep_embed(member)
                    
                    # Send our embed
                    bot_message = await message.channel.send(embed=simple_embed)
                    
                    # Store our message info for reaction handling
                    bot.token_messages[bot_message.id] = {
                        "author": member.id,
                        "token_address": token_address,
                        "token_symbol": token_symbol
                    }
                    
                    # Add voting reactions
                    await bot_message.add_reaction("🟢")
                    await bot_message.add_reaction("🔴")
                    
                    return  # Process only first valid embed
                except Exception as e:
                    print(f"Error processing special bot message: {e}")
                    traceback.print_exc()

@bot.event
async def on_ready():
    load_data()
    print(f'Logged in as {bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Error syncing commands: {e}")

# ===== Run Bot =====
if __name__ == "__main__":
    # Load token from environment variable
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("No bot token on .env")
        exit(1)
    bot.run(token) 