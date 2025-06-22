import discord
from discord.ext import commands
from flask import Flask, request, jsonify
import threading
import asyncio
import os

TOKEN = os.getenv("USER_TOKEN")  # Your user token here

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", self_bot=True, intents=intents)

app = Flask(__name__)

@bot.event
async def on_ready():
    print(f"Selfbot ready as {bot.user}")

# Command implementations
async def cmd_join(invite_code):
    try:
        invite = await bot.fetch_invite(invite_code)
        await invite.accept()
        return "Joined server successfully."
    except Exception as e:
        return f"Error joining server: {e}"

async def cmd_listall():
    return [f"{guild.name} ({guild.id})" for guild in bot.guilds]

async def cmd_leaveall():
    try:
        for guild in bot.guilds:
            await guild.leave()
        return "Left all servers."
    except Exception as e:
        return f"Error leaving servers: {e}"

async def cmd_leave(guild_id):
    try:
        guild = bot.get_guild(int(guild_id))
        if guild:
            await guild.leave()
            return f"Left server: {guild.name}"
        else:
            return "Guild not found."
    except Exception as e:
        return f"Error leaving server: {e}"

# Flask API endpoint to receive commands
@app.route("/command", methods=["POST"])
def command():
    data = request.json
    cmd = data.get("cmd")
    params = data.get("params", {})

    loop = bot.loop

    async def execute_command():
        if cmd == "join":
            invite = params.get("invite")
            return await cmd_join(invite)
        elif cmd == "listall":
            return await cmd_listall()
        elif cmd == "leaveall":
            return await cmd_leaveall()
        elif cmd == "leave":
            guild_id = params.get("guild_id")
            return await cmd_leave(guild_id)
        else:
            return "Unknown command"

    future = asyncio.run_coroutine_threadsafe(execute_command(), loop)
    try:
        result = future.result(timeout=30)
    except Exception as e:
        result = f"Command error: {e}"

    if isinstance(result, list):
        return jsonify({"result": result})
    else:
        return jsonify({"result": str(result)})

def run_flask():
    app.run(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN, bot=False)
