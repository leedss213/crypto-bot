import discord
from discord.ext import commands
import os
import asyncio
import aiohttp
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REPORT_HOUR_WIB = int(os.getenv("REPORT_HOUR_WIB", "8"))

WIB = timezone(timedelta(hours=7))
ORANGE = 0xFFA500

# ─── FETCH FUNCTIONS ───

async def fetch_json(url, session):
    try:
        async with session.get(url, timeout=15) as r:
            return await r.json()
    except Exception as e:
        print(f"[ERROR] fetch {url}: {e}")
        return None

async def get_binance_data(session):
    data = {}
    try:
        ticker = await fetch_json("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", session)
        if ticker:
            data["price"] = float(ticker["lastPrice"])
            data["change_24h"] = float(ticker["priceChangePercent"])
            data["high_24h"] = float(ticker["highPrice"])
            data["low_24h"] = float(ticker["lowPrice"])
            data["volume"] = float(ticker["volume"])
            data["quote_volume"] = float(ticker["quoteVolume"])
        ob = await fetch_json("https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=5", session)
        if ob:
            data["bids"] = [[float(p), float(q)] for p, q in ob["bids"]]
            data["asks"] = [[float(p), float(q)] for p, q in ob["asks"]]
        klines = await fetch_json("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=7", session)
        if klines:
            data["klines"] = [{
                "date": k[0], "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])
            } for k in klines]
    except Exception as e:
        print(f"[ERROR] binance: {e}")
    return data

async def get_coingecko_data(session):
    data = {}
    try:
        global_data = await fetch_json("https://api.coingecko.com/api/v3/global", session)
        if global_data:
            gd = global_data["data"]
            data["total_market_cap"] = gd["total_market_cap"]["usd"]
            data["total_volume_24h"] = gd["total_volume_24h"]["usd"]
            data["btc_dominance"] = gd["market_cap_percentage"]["btc"]
            data["eth_dominance"] = gd["market_cap_percentage"]["eth"]
            data["active_coins"] = gd["active_cryptocurrencies"]
            data["market_change_24h"] = gd["market_cap_change_percentage_24h_usd"]
        fear_greed = await fetch_json("https://api.alternative.me/fng/?limit=1", session)
        if fear_greed and fear_greed.get("data"):
            fg = fear_greed["data"][0]
            data["fear_greed_value"] = int(fg["value"])
            data["fear_greed_label"] = fg["value_classification"]
        trending = await fetch_json("https://api.coingecko.com/api/v3/search/trending", session)
        if trending:
            data["trending"] = [c["item"]["name"] for c in trending.get("coins", [])[:7]]
    except Exception as e:
        print(f"[ERROR] coingecko: {e}")
    return data

async def get_dxy(session):
    try:
        rates = await fetch_json("https://open.er-api.com/v6/latest/USD", session)
        if not rates or "rates" not in rates:
            return None
        r = rates["rates"]
        dxy = 50.14348112 * (1/r["EUR"])**(-0.576056) * (1/r["JPY"])**(0.033094) * (1/r["GBP"])**(-0.090662)
        return round(dxy, 2)
    except Exception as e:
        print(f"[ERROR] dxy: {e}")
        return None

async def get_news(session):
    try:
        url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest&limit=10"
        data = await fetch_json(url, session)
        if not data or "Data" not in data:
            return []
        articles = []
        for item in data["Data"][:10]:
            published = datetime.fromtimestamp(item.get("published_on", 0), tz=WIB)
            articles.append({
                "title": item.get("title", "No title"),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "published": published.strftime("%d/%m %H:%M WIB"),
                "categories": item.get("categories", "")
            })
        return articles
    except Exception as e:
        print(f"[ERROR] news: {e}")
        return []

# ─── GROQ ANALYSIS ───

async def get_groq_analysis(binance_data, coingecko_data, dxy, news):
    news_text = ""
    if news:
        for i, n in enumerate(news[:8], 1):
            news_text += f"{i}. {n['title']} ({n['source']}, {n['published']}) - {n['url']}\n"
    else:
        news_text = "Tidak ada berita tersedia saat ini."

    klines_text = ""
    if "klines" in binance_data:
        for k in binance_data["klines"]:
            dt = datetime.fromtimestamp(k["date"]/1000, tz=WIB)
            klines_text += f"- {dt.strftime('%d/%m')}: O={k['open']:.0f} H={k['high']:.0f} L={k['low']:.0f} C={k['close']:.0f} V={k['volume']:.0f}\n"

    prompt = f"""Kamu adalah analis crypto profesional. Buat laporan analisis pasar crypto harian dalam Bahasa Indonesia.

**DATA PASAR:**
- BTC/USDT: ${binance_data.get("price", 0):,.2f} ({binance_data.get("change_24h", 0):+.2f}% 24h)
- High/Low 24h: ${binance_data.get("high_24h", 0):,.2f} / ${binance_data.get("low_24h", 0):,.2f}
- Volume 24h: ${binance_data.get("quote_volume", 0):,.0f}
- DXY Index: {dxy if dxy else "N/A"}

**MARKET GLOBAL:**
- Total Market Cap: ${coingecko_data.get("total_market_cap", 0):,.0f}
- Volume 24h Global: ${coingecko_data.get("total_volume_24h", 0):,.0f}
- BTC Dominance: {coingecko_data.get("btc_dominance", 0):.1f}%
- ETH Dominance: {coingecko_data.get("eth_dominance", 0):.1f}%
- Fear & Greed Index: {coingecko_data.get("fear_greed_value", 0)} ({coingecko_data.get("fear_greed_label", "N/A")})
- Market Change 24h: {coingecko_data.get("market_change_24h", 0):+.2f}%
- Trending: {", ".join(coingecko_data.get("trending", []))}
- Aktif Coins: {coingecko_data.get("active_coins", 0)}

**KLINE 7 HARI BTC/USDT:**
{klines_text}

**BERITA TERKINI:**
{news_text}

**FORMAT OUTPUT — WAJIB IKUTI:**
Buat analisis dengan section-section berikut. Gunakan bullet strip (-) untuk setiap poin. Jangan gunakan emoji di dalam body/isi teks. Emoji hanya boleh di judul section. Setiap poin maksimal 1 kalimat pendek.

📊 **Ringkasan Pasar**
(2 poin ringkasan)

₿ **Analisis Teknikal BTC/USDT**
(3 poin analisis)

🧠 **Market Psychology**
(2 poin sentimen)

📰 **Implikasi Berita**
(2 poin dampak berita)

🎯 **Rekomendasi Strategi**
(2 poin rekomendasi + level SR)

⚠️ **Disclaimer**
Tulis disclaimer singkat NFA/DYOR."""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 1200
                },
                timeout=30
            ) as resp:
                result = await resp.json()
                return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[ERROR] groq: {e}")
        return "Gagal mendapatkan analisis AI."

# ─── BUILD EMBED ───

def parse_sections(text):
    sections = []
    current_title = "Analisis"
    current_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("**") and any(c in stripped[:3] for c in "📊₿🧠📰🎯⚠️"):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            title = stripped.replace("**", "").strip()
            current_title = title
            current_lines = []
        elif stripped.startswith("- ") or stripped.startswith("* "):
            current_lines.append(stripped)
        elif stripped and not stripped.startswith("```"):
            current_lines.append(stripped)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))
    return sections

def build_embed(binance_data, coingecko_data, dxy, news, analysis):
    now = datetime.now(WIB)
    embed = discord.Embed(
        title=f"📊 Laporan Pasar Crypto Harian — {now.strftime('%d %B %Y')}",
        color=ORANGE,
        timestamp=now
    )

    price = binance_data.get("price", 0)
    change = binance_data.get("change_24h", 0)
    emoji = "🟢" if change >= 0 else "🔴"

    market_info = (
        f"- BTC/USDT: **${price:,.2f}** ({change:+.2f}% 24h) {emoji}\n"
        f"- High/Low: **${binance_data.get('high_24h',0):,.2f}** / **${binance_data.get('low_24h',0):,.2f}**\n"
        f"- Volume 24h: **${binance_data.get('quote_volume',0):,.0f}**\n"
        f"- DXY Index: **{dxy if dxy else 'N/A'}**"
    )
    embed.add_field(name="📈 Data Pasar", value=market_info, inline=False)

    fg = coingecko_data.get("fear_greed_value", 0)
    fg_label = coingecko_data.get("fear_greed_label", "N/A")
    fg_emoji = "🟢" if fg >= 55 else ("🟡" if fg >= 40 else "🔴")

    global_info = (
        f"- Market Cap: **${coingecko_data.get('total_market_cap',0):,.0f}**\n"
        f"- Volume Global: **${coingecko_data.get('total_volume_24h',0):,.0f}**\n"
        f"- BTC Dom: **{coingecko_data.get('btc_dominance',0):.1f}%** | ETH Dom: **{coingecko_data.get('eth_dominance',0):.1f}%**\n"
        f"- Fear & Greed: **{fg}** ({fg_label}) {fg_emoji}\n"
        f"- Market 24h: **{coingecko_data.get('market_change_24h',0):+.2f}%**\n"
        f"- Trending: **{', '.join(coingecko_data.get('trending', [])[:5])}**"
    )
    embed.add_field(name="🌐 Market Global", value=global_info, inline=False)

    if news:
        news_lines = []
        for i, n in enumerate(news[:5], 1):
            news_lines.append(f"{i}. [{n['title']}]({n['url']}) — *{n['source']}, {n['published']}*")
        embed.add_field(name="📰 Berita Terkini", value="\n".join(news_lines), inline=False)
    else:
        embed.add_field(name="📰 Berita Terkini", value="Tidak ada berita tersedia saat ini.", inline=False)

    sections = parse_sections(analysis)
    budget = 3000
    used = sum(len(f.value) + len(f.name) for f in embed.fields)
    for title, content in sections:
        remaining = budget - used
        if remaining <= 100:
            break
        if len(content) > min(remaining - len(title) - 10, 1024):
            content = content[:min(remaining - len(title) - 13, 1011)] + "..."
        if len(title) > 256:
            title = title[:253] + "..."
        embed.add_field(name=title, value=content, inline=False)
        used += len(content) + len(title)

    embed.set_footer(text="⚡ Binance | CoinGecko | CryptoCompare | Groq AI\n⚠️ NFA — Not Financial Advice | DYOR")
    return embed

# ─── GENERATE REPORT ───

async def generate_report():
    print(f"[{datetime.now(WIB).strftime('%H:%M:%S')}] Generating report...")
    async with aiohttp.ClientSession() as session:
        binance_data, coingecko_data, dxy, news = await asyncio.gather(
            get_binance_data(session),
            get_coingecko_data(session),
            get_dxy(session),
            get_news(session)
        )
    print(f"  - BTC: ${binance_data.get('price', 'N/A')}")
    print(f"  - DXY: {dxy}")
    print(f"  - News: {len(news)} articles")
    analysis = await get_groq_analysis(binance_data, coingecko_data, dxy, news)
    print("  - AI Analysis done")
    embed = build_embed(binance_data, coingecko_data, dxy, news, analysis)
    return embed

# ─── MODE: --once (buat scheduled task) ───

async def send_once():
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"✅ Logged in as {client.user}")
        channel = client.get_channel(CHANNEL_ID)
        if not channel:
            print(f"❌ Channel {CHANNEL_ID} not found!")
            await client.close()
            return
        try:
            embed = await generate_report()
            await channel.send(embed=embed)
            print(f"✅ Report sent!")
        except Exception as e:
            print(f"❌ Error: {e}")
        await client.close()

    async with client:
        await client.start(DISCORD_BOT_TOKEN)

# ─── MODE: bot (interaktif, support $report) ───

def run_bot():
    intents = discord.Intents.default()
    intents.message_content = True
    bot2 = commands.Bot(command_prefix="$", intents=intents)

    @bot2.command()
    async def report(ctx):
        msg = await ctx.send("⏳ Generating report...")
        try:
            embed = await generate_report()
            await msg.delete()
            await ctx.send(embed=embed)
        except Exception as e:
            await msg.edit(content=f"❌ Error: {e}")

    @bot2.event
    async def on_ready():
        print(f"✅ Bot online as {bot2.user}")
        print("💡 Ketik $report di Discord untuk test manual")

    bot2.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    if "--once" in sys.argv:
        asyncio.run(send_once())
    else:
        run_bot()
