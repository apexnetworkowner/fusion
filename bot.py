import discord
from discord.ext import commands
import asyncio
import requests
import re
import time
import os
import random
import string
import io
from PIL import Image

# ---- SmailPro Client ----
class SmailProClient:
    def __init__(self):
        self.base_url = "https://api.smailpro.com"
        self.email_address = None
        self.session_id = None

    def create_account(self, domain="smailpro.com"):
        """Create a new SmailPro temp email"""
        try:
            url = f"{self.base_url}/new-email"
            payload = {"domain": domain}
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()

            self.email_address = data.get("email")
            self.session_id = data.get("session_id")
            return self.email_address
        except Exception as e:
            print(f"Error creating SmailPro email: {e}")
            return None

    def get_messages(self):
        """Get inbox messages"""
        try:
            if not self.session_id:
                return []
            url = f"{self.base_url}/messages/{self.session_id}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("messages", [])
        except Exception as e:
            print(f"Error fetching messages: {e}")
            return []

    def get_message_content(self, message_id):
        """Get full message"""
        try:
            url = f"{self.base_url}/message/{self.session_id}/{message_id}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching message content: {e}")
            return None

    def wait_for_freedns_email(self, timeout=120):
        """Wait for FreeDNS activation email"""
        start = time.time()
        while time.time() - start < timeout:
            messages = self.get_messages()
            for msg in messages:
                sender = msg.get("from", "").lower()
                if "freedns" in sender or "afraid.org" in sender:
                    full_message = self.get_message_content(msg["id"])
                    if full_message:
                        body = full_message.get("text", "") + full_message.get("html", "")
                        code = self.extract_activation_code(body)
                        if code:
                            return code
            time.sleep(5)
        return None

    def extract_activation_code(self, content):
        """Extract FreeDNS activation code"""
        patterns = [
            r'activate\.php\?([a-zA-Z0-9]{20,})',
            r'activation[_-]?code[:\s]+([a-zA-Z0-9]{20,})',
            r'([a-zA-Z0-9]{25,})',
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return max(match.groups(), key=len)
        return None

    def delete_account(self):
        """No real delete on SmailPro (session auto-expires)"""
        self.session_id = None
        self.email_address = None
        return True


# ---- FreeDNS Bot ----
class FreeDNSBot:
    def __init__(self):
        import freedns
        self.client = freedns.Client()
        self.smail = SmailProClient()
        self.current_email = None
        self.max_captcha_retries = 3
        self.custom_domains = {}  # alias_name -> domain

    def generate_random_credentials(self):
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        return username, password

    def add_domain(self, alias_name: str, domain: str):
        """Add a custom domain under a name"""
        self.custom_domains[alias_name.lower()] = domain

    async def setup_temp_email(self, channel, domain="smailpro.com"):
        try:
            if self.current_email:
                await channel.send(f"📧 Reusing email: {self.current_email}")
                return self.current_email

            await channel.send(f"📧 Creating temporary SmailPro email with domain `{domain}`...")

            email_address = self.smail.create_account(domain=domain)
            if not email_address:
                await channel.send("❌ Failed to create SmailPro account")
                return None

            self.current_email = email_address
            await channel.send(f"✅ Email created: {email_address}")
            return email_address

        except Exception as e:
            await channel.send(f"❌ Error creating email: {e}")
            return None

    async def get_captcha_from_user(self, channel, user, captcha_bytes, purpose="account creation", retry_count=0):
        """Ask user to solve CAPTCHA"""
        try:
            image = Image.open(io.BytesIO(captcha_bytes))
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            buf.seek(0)
            file = discord.File(buf, filename="captcha.png")

            await channel.send(
                f"🔐 Solve CAPTCHA for {purpose} (Attempt {retry_count+1}/{self.max_captcha_retries}):",
                file=file
            )

            def check(msg):
                return msg.author == user and msg.channel == channel

            try:
                response = await bot.wait_for("message", timeout=120.0, check=check)
                code = re.sub(r"[^a-zA-Z0-9]", "", response.content.strip())
                return code
            except asyncio.TimeoutError:
                await channel.send("⌛ CAPTCHA timed out.")
                return None
        except Exception as e:
            await channel.send(f"❌ Error showing CAPTCHA: {e}")
            return None

    async def create_account(self, channel, user):
        """Create FreeDNS account with temp mail"""
        email = await self.setup_temp_email(channel)
        if not email:
            return None, None, "❌ Failed to set up email"

        for attempt in range(self.max_captcha_retries):
            captcha = self.client.get_captcha()
            code = await self.get_captcha_from_user(channel, user, captcha, "account creation", attempt)
            if not code:
                continue

            username, password = self.generate_random_credentials()
            try:
                self.client.create_account(
                    captcha_code=code,
                    firstname="Bot",
                    lastname="User",
                    username=username,
                    password=password,
                    email=email
                )
                return username, password, f"✅ Account created! Waiting for activation email..."
            except Exception as e:
                await channel.send(f"⚠️ Error: {e}")
                continue

        return None, None, "❌ Failed after retries"

    async def activate_account(self, channel):
        await channel.send("📨 Waiting for FreeDNS activation email...")
        code = self.smail.wait_for_freedns_email(timeout=120)
        if not code:
            await channel.send("❌ No activation email received.")
            return None

        try:
            self.client.activate_account(code)
            await channel.send("✅ Account activated!")
            return code
        except Exception as e:
            await channel.send(f"❌ Activation failed: {e}")
            return None

    async def create_subdomain(self, channel, user, ip, subdomain_text="mysub", domain_alias=None):
        """Create subdomain with optional custom domain"""
        try:
            if domain_alias and domain_alias.lower() in self.custom_domains:
                domain_str = self.custom_domains[domain_alias.lower()]
                domain_id = self.client.get_domain_id(domain_str)
            else:
                registry = self.client.get_registry()
                domain_choice = random.choice(registry["domains"])
                domain_str = domain_choice["domain"]
                domain_id = domain_choice["id"]

            for attempt in range(self.max_captcha_retries):
                captcha = self.client.get_captcha()
                code = await self.get_captcha_from_user(channel, user, captcha, f"subdomain {subdomain_text}", attempt)
                if not code:
                    continue

                try:
                    self.client.create_subdomain(
                        captcha_code=code,
                        record_type="A",
                        subdomain=subdomain_text,
                        domain_id=domain_id,
                        destination=ip
                    )
                    full = f"{subdomain_text}.{domain_str}"
                    await channel.send(f"✅ Subdomain created: `{full}` → {ip}")
                    return full
                except Exception as e:
                    await channel.send(f"⚠️ Error: {e}")
                    continue

            await channel.send("❌ Failed to create subdomain.")
            return None
        except Exception as e:
            await channel.send(f"❌ Error: {e}")
            return None


# ---- Discord Bot ----
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

dnsbot = FreeDNSBot()


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")


@bot.command()
async def adddomain(ctx, alias: str, domain: str):
    """Add a domain to use with a custom alias"""
    dnsbot.add_domain(alias, domain)
    await ctx.send(f"✅ Added domain `{domain}` with alias `{alias}`")


@bot.command()
async def createdomain(ctx, ip: str, subdomain: str, alias: str = None):
    """Create FreeDNS account + subdomain with custom text"""
    await ctx.send(f"🚀 Creating subdomain `{subdomain}` pointing to `{ip}`...")

    user = ctx.author
    username, password, msg = await dnsbot.create_account(ctx.channel, user)
    if not username:
        await ctx.send(msg)
        return

    await ctx.send(f"✅ FreeDNS account: `{username}` / `{password}`")

    code = await dnsbot.activate_account(ctx.channel)
    if not code:
        return

    dnsbot.client.login(username=username, password=password)

    await dnsbot.create_subdomain(ctx.channel, user, ip, subdomain_text=subdomain, domain_alias=alias)


# ---- Run Bot ----
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ Please set DISCORD_TOKEN as an environment variable!")
    else:
        bot.run(TOKEN)
