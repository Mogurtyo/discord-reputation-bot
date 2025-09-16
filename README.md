# Discord Reputation Bot

A Discord bot that manages a reputation system for token trading communities. Users can vote on each other's token recommendations using green (🟢) and red (🔴) reactions.

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Variables**:
   Create a `.env` file with the following variables:
   ```
   DISCORD_TOKEN=your_bot_token_here
   SPECIAL_USER_ID=1081815963990761542 (for example, this is to extract the token address from rickbot)
   ```

3. **Run the Bot**:
   ```bash
   python reputation_bot.py
   ```

## Commands

### User Commands
- `/rep [user]` - Check a user's reputation
- `/repboard` - Show reputation leaderboard

### Admin Commands
- `/repadd <user> <amount> <vote_type>` - Add reputation votes
- `/repremove <vote_ids>` - Remove votes by ID
- `/repdisable <user>` - Disable/enable user voting
- `/replogs <channel>` - Set logging channel
- `/repmanager` - Review recent votes

## How It Works

1. When a special bot posts token information, this bot creates a reputation embed
2. Users can react with 🟢 (good) or 🔴 (bad) to vote on the recommendation
3. Votes are tracked per user and per token
4. Admins can manage votes and review the system

## Data Files

The bot creates several JSON files to store data:
- `reputation.json` - User reputation data
- `reputation_log.json` - Complete vote history
- `disabled_voters.json` - List of disabled users
- `current_votes.json` - Active votes
- `reputation_log_channels.json` - Logging channel settings

## Security

- Users cannot vote on their own recommendations
- Admin commands require administrator permissions
