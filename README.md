# Python Discord Bot

A starter Discord bot built with Python and `discord.py`. It currently includes:

- `/ping` for gateway latency
- `/hello` for a user greeting
- `/serverinfo` for basic server details
- Moderation commands: `/timeout`, `/untimeout`, `/warn`, `/warnings`, `/clear`, and
  `/lockdown`
- Automatic invite detection, mass-mention protection, duplicate-message detection, and
  timed spam protection
- CAPTCHA verification, account-age gating, scam-link blocking, and anti-nuke audit-log
  monitors
- Slash-command syncing for either one development server or all servers
- Structured logs and clear startup errors

## Run it

The bot reads its token from the `DISCORD_BOT_TOKEN` Replit Secret.

```bash
python main.py
```

The configured project workflow runs `python main.py` automatically.

## Dashboard access

The Discord bot serves its dashboard on port `8099`, and the API service exposes
it through the public project domain at `/api/dashboard/`.

Open:

```text
https://<REPLIT_DEV_DOMAIN>/api/dashboard/
```

The root URL redirects there automatically. Select **تسجيل الدخول عبر ديسكورد**
and sign in with an account that owns the server or has the Administrator
permission.

For Discord OAuth login, add this exact callback URL under **OAuth2 → Redirects**:

```text
https://<REPLIT_DEV_DOMAIN>/api/dashboard/api/auth/callback
```

The dashboard uses `CLIENT_ID`, `CLIENT_SECRET`, and `REDIRECT_URI` for OAuth
login. `CLIENT_SECRET` must be stored as a Replit Secret; never put it in the
bot invite URL or source code.

## Discord setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **Bot**, create the bot user, and copy its raw token into the `DISCORD_BOT_TOKEN` Replit Secret. Do not use the Application ID, public key, client secret, or an OAuth URL.
3. In **Bot → Privileged Gateway Intents**, enable **Server Members Intent** and
   **Message Content Intent**, then save the change. The security cog uses the members
   intent for account-age gating and ban/join events, and the message-content intent for
   scam-link blocking. Presence Intent is not required.
4. Open **OAuth2 → URL Generator**.
5. Select the `bot` and `applications.commands` scopes.
6. Select the permissions the bot needs, including **View Channel**, **Send Messages**,
   **Embed Links**, **Add Reactions**, **Read Message History**, **Manage Messages**,
   and **Manage Channels**. The community commands use Embed Links for suggestions and
   polls, Add Reactions for suggestion voting, and Manage Channels for live counters.
   Add these security permissions where the corresponding feature is enabled:

   | Permission | Used by |
   | --- | --- |
   | View Audit Log | Anti-nuke channel, role, and mass-ban monitors |
   | Moderate Members | Scam-link timeout |
   | Manage Roles | CAPTCHA role assignment and anti-nuke quarantine |
   | Kick Members | Account-age gate for accounts younger than three days |
   | Mention Everyone | Emergency anti-nuke alert in the system channel |

   Do not grant these permissions to unrelated bots or roles. The bot must also be
   above the verified CAPTCHA role and any roles it may quarantine.
7. Open the generated URL and add the bot to your server.

Existing installations must grant **Embed Links** and **Add Reactions** to the bot role
in the channels where community commands are used, or reauthorize the bot with an
updated OAuth URL containing those permissions.

For fast slash-command updates during development, add a non-secret `DISCORD_GUILD_ID`
environment variable containing the server ID. Without it, commands sync globally, which
can take longer to appear.

After the bot connects and commands finish syncing, an administrator can run
`/setup_captcha` and choose the role that verified members should receive. The CAPTCHA
button remains active while the bot is running and each server's button is bound to its
own selected role.

## Extend the bot

Add additional slash commands next to the starter commands in the loaded cogs. Keep
privileged intents disabled unless a feature explicitly needs them, and request only the
permissions required by that feature. The security and moderation cogs are loaded from
`main.py` before slash commands are synced.

## PRIME Leveling

The leveling system is managed from the authenticated dashboard at `/api/dashboard/`. PRIME separates lifetime leveling from scheduled Daily/Weekly/Monthly TOP periods.

Manual commands include `/rank`, `/lvl`, `لفل`, and `rank`; `/rank` defaults to a single PRIME PNG card. `/top` and `توب` show the lifetime server leaderboard with Text/Voice modes. Scheduled Daily/Weekly/Monthly TOP use only XP earned inside their configured period and do not reset lifetime XP or levels.

Dashboard notification settings are stored in PRIME controls and are applied through the leveling settings API. Legacy leveling fields remain as compatibility storage for existing installations.

Runtime SQLite files are ignored by Git via `*.db`, `*.sqlite`, and `*.sqlite3`. Existing deployments that intentionally use a tracked legacy database should migrate that data to their persistent `DB_PATH` before removing the legacy file from the repository; do not delete a live production database blindly.
