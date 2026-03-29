import asyncio
import re

import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import db
import shortio
from rss import fetch_episodes
from podlink import build_podlink_url
from handlers.auth import restricted

# ── States ─────────────────────────────────────────────────────────────────
(
    PICK_LINK_TYPE,
    PICK_PODCAST,
    PICK_EPISODE,
    ASK_SLUG_PODCAST,
    ASK_URL,
    PICK_DOMAIN,
    ASK_SLUG_URL,
) = range(7)

PAGE_SIZE = 5
_MENU_TEXTS = filters.Text(["🔗 New link", "☰ Menu"])


def _truncate(text: str, length: int) -> str:
    return text[:length] + ("…" if len(text) > length else "")


# ── Helpers ────────────────────────────────────────────────────────────────

def _episode_buttons(episodes, page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    chunk = episodes[start : start + PAGE_SIZE]
    rows = [
        [InlineKeyboardButton(ep.title, callback_data=f"ep:{start + i}")]
        for i, ep in enumerate(chunk)
    ]
    nav = []
    if start + PAGE_SIZE < len(episodes):
        nav.append(InlineKeyboardButton("Show 5 more ↓", callback_data=f"page:{page + 1}"))
    if page > 0:
        nav.append(InlineKeyboardButton("↑ Back", callback_data=f"page:{page - 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


# ── Entry ──────────────────────────────────────────────────────────────────

async def _fetch_page_title(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        match = re.search(r"<title[^>]*>([^<]+)</title>", resp.text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return None


async def _sync_domain_links(domain: dict) -> None:
    try:
        links = await shortio.fetch_links(domain["api_key"], domain["shortio_domain_id"])
        db.sync_links(domain["id"], links)
    except Exception:
        pass


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    sync_tasks = [asyncio.create_task(_sync_domain_links(d)) for d in db.list_domains()]
    context.user_data["sync_task"] = asyncio.gather(*sync_tasks, return_exceptions=True)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎧 Podcast episode", callback_data="type:podcast")],
        [InlineKeyboardButton("🔗 Any URL", callback_data="type:url")],
    ])
    if cq := update.callback_query:
        await cq.answer()
        await cq.edit_message_text("What are you shortlinking?", reply_markup=keyboard)
    else:
        await update.message.reply_text("What are you shortlinking?", reply_markup=keyboard)
    return PICK_LINK_TYPE


# ── Branch selector ────────────────────────────────────────────────────────

async def pick_link_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]

    if choice == "podcast":
        podcasts = db.list_podcasts()
        if not podcasts:
            await query.edit_message_text(
                "No podcasts saved yet. Use /podcasts to add one first."
            )
            return ConversationHandler.END

        rows = [
            [InlineKeyboardButton(p["name"], callback_data=f"pod:{p['id']}")]
            for p in podcasts
        ]
        rows.append([InlineKeyboardButton("➕ Add a podcast", callback_data="pod:add")])
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
        await query.edit_message_text(
            "Pick a podcast:", reply_markup=InlineKeyboardMarkup(rows)
        )
        return PICK_PODCAST

    else:  # url
        await query.edit_message_text("Paste the URL you want to shorten:")
        return ASK_URL


# ── Podcast branch ─────────────────────────────────────────────────────────

async def pick_podcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    if query.data == "pod:add":
        await query.edit_message_text(
            "Use /podcasts to add a podcast, then tap 🔗 New link to start."
        )
        return ConversationHandler.END

    podcast_id = int(query.data.split(":")[1])
    podcast = db.get_podcast(podcast_id)
    if not podcast:
        await query.edit_message_text("Podcast not found.")
        return ConversationHandler.END

    context.user_data["podcast"] = dict(podcast)
    await query.edit_message_text(f"Fetching episodes for *{podcast['name']}*…", parse_mode="Markdown")

    try:
        episodes = await fetch_episodes(podcast["rss_url"])
    except Exception as e:
        await query.edit_message_text(f"❌ Failed to fetch RSS feed:\n`{e}`\n\nTry again.", parse_mode="Markdown")
        return ConversationHandler.END

    if not episodes:
        await query.edit_message_text("No episodes found in the RSS feed.")
        return ConversationHandler.END

    context.user_data["episodes"] = episodes
    context.user_data["ep_page"] = 0

    await query.edit_message_text(
        f"*{podcast['name']}* — pick an episode:",
        reply_markup=_episode_buttons(episodes, 0),
        parse_mode="Markdown",
    )
    return PICK_EPISODE


async def pick_episode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    if query.data.startswith("page:"):
        page = int(query.data.split(":")[1])
        context.user_data["ep_page"] = page
        episodes = context.user_data["episodes"]
        podcast = context.user_data["podcast"]
        await query.edit_message_text(
            f"*{podcast['name']}* — pick an episode:",
            reply_markup=_episode_buttons(episodes, page),
            parse_mode="Markdown",
        )
        return PICK_EPISODE

    idx = int(query.data.split(":")[1])
    episode = context.user_data["episodes"][idx]
    context.user_data["episode"] = episode

    podcast = context.user_data["podcast"]
    podlink_url = build_podlink_url(podcast["apple_id"], episode)
    context.user_data["final_url"] = podlink_url

    domain_id = podcast["domain_id"]
    suggested = episode.suggested_slug

    if suggested and db.slug_exists_on_domain(domain_id, suggested):
        existing = db.find_link_by_slug(domain_id, suggested)
        await query.edit_message_text(
            f"⚠️ Slug `{suggested}` already exists on this domain:\n{existing['short_url']}\n\nType a different slug:",
            parse_mode="Markdown",
        )
        context.user_data["domain_id"] = domain_id
        return ASK_SLUG_PODCAST

    context.user_data["domain_id"] = domain_id
    context.user_data["suggested_slug"] = suggested

    if suggested:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✓ Use {suggested}", callback_data="slug:confirm")],
        ])
        await query.edit_message_text(
            f"Suggested slug: `{suggested}`\n\nConfirm or type a different one:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            f"Episode: *{episode.title}*\n\nNo season/episode number detected. Type your desired slug:",
            parse_mode="Markdown",
        )
    return ASK_SLUG_PODCAST


async def ask_slug_podcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if sync_task := context.user_data.get("sync_task"):
        await sync_task
        context.user_data["sync_task"] = None
    slug = update.message.text.strip()
    domain_id = context.user_data["domain_id"]
    if db.slug_exists_on_domain(domain_id, slug):
        existing = db.find_link_by_slug(domain_id, slug)
        await update.message.reply_text(
            f"⚠️ Slug `{slug}` already exists on this domain:\n{existing['short_url']}\n\nType a different slug:",
            parse_mode="Markdown",
        )
        return ASK_SLUG_PODCAST
    return await _create_link(
        update,
        context,
        slug=slug,
        original_url=context.user_data["final_url"],
        domain_id=domain_id,
        title=context.user_data["episode"].title,
    )


async def confirm_slug_podcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if sync_task := context.user_data.get("sync_task"):
        await sync_task
        context.user_data["sync_task"] = None
    query = update.callback_query
    await query.answer()
    slug = context.user_data["suggested_slug"]
    await query.edit_message_text(f"Using slug `{slug}`…", parse_mode="Markdown")
    return await _create_link(
        update,
        context,
        slug=slug,
        original_url=context.user_data["final_url"],
        domain_id=context.user_data["domain_id"],
        title=context.user_data["episode"].title,
    )


# ── URL branch ─────────────────────────────────────────────────────────────

async def ask_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("That doesn't look like a URL. Try again:")
        return ASK_URL

    context.user_data["final_url"] = url
    context.user_data["title_task"] = asyncio.create_task(_fetch_page_title(url))

    domains = db.list_domains()
    if not domains:
        await update.message.reply_text("No domains saved. Use /domains to add one first.")
        return ConversationHandler.END

    rows = [
        [InlineKeyboardButton(d['nickname'], callback_data=f"dom:{d['id']}")]
        for d in domains
    ]
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    await update.message.reply_text(
        "Which domain?", reply_markup=InlineKeyboardMarkup(rows)
    )
    return PICK_DOMAIN


async def pick_domain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    domain_id = int(query.data.split(":")[1])
    context.user_data["domain_id"] = domain_id
    await query.edit_message_text("Type your desired slug:")
    return ASK_SLUG_URL


async def ask_slug_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if sync_task := context.user_data.get("sync_task"):
        await sync_task
        context.user_data["sync_task"] = None
    title = await context.user_data.pop("title_task", None)
    slug = update.message.text.strip()
    domain_id = context.user_data["domain_id"]
    if db.slug_exists_on_domain(domain_id, slug):
        existing = db.find_link_by_slug(domain_id, slug)
        await update.message.reply_text(
            f"⚠️ Slug `{slug}` already exists on this domain:\n{existing['short_url']}\n\nType a different slug:",
            parse_mode="Markdown",
        )
        return ASK_SLUG_URL
    return await _create_link(
        update,
        context,
        slug=slug,
        original_url=context.user_data["final_url"],
        domain_id=domain_id,
        title=title,
    )


# ── Shared link creation ───────────────────────────────────────────────────

async def _create_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    slug: str,
    original_url: str,
    domain_id: int,
    title: str | None,
) -> int:
    domain = db.get_domain(domain_id)
    if not domain:
        await update.effective_message.reply_text("Domain not found. Tap 🔗 New link to start over.")
        return ConversationHandler.END

    try:
        result = await shortio.create_link(
            api_key=domain["api_key"],
            hostname=domain["hostname"],
            original_url=original_url,
            slug=slug,
            title=title,
        )
    except Exception as e:
        error_msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                error_msg = e.response.json().get("error", error_msg)
            except Exception:
                error_msg = e.response.text or error_msg

        await update.effective_message.reply_text(
            f"❌ Short.io error: `{error_msg}`\n\nTry a different slug:",
            parse_mode="Markdown",
        )
        return ASK_SLUG_PODCAST if "episode" in context.user_data else ASK_SLUG_URL

    short_url = result.get("secureShortURL") or result.get("shortURL")
    db.save_link(
        domain_id=domain_id,
        original_url=original_url,
        short_url=short_url,
        slug=slug,
        title=title,
    )

    await update.effective_message.reply_text(
        f"✅ Done!\n\n{short_url}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📋 Click to copy", copy_text=CopyTextButton(text=short_url)),
        ]]),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Handler registration ───────────────────────────────────────────────────

def start_handler() -> ConversationHandler:
    from handlers.welcome import cancel_to_menu
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Text(["🔗 New link"]) & ~filters.COMMAND, start),
            CallbackQueryHandler(start, pattern="^menu:newlink$"),
        ],
        states={
            PICK_LINK_TYPE: [CallbackQueryHandler(pick_link_type, pattern="^type:")],
            PICK_PODCAST: [CallbackQueryHandler(pick_podcast, pattern="^(pod:|cancel)")],
            PICK_EPISODE: [CallbackQueryHandler(pick_episode, pattern="^(ep:|page:|cancel)")],
            ASK_SLUG_PODCAST: [
                CallbackQueryHandler(confirm_slug_podcast, pattern="^slug:confirm$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~_MENU_TEXTS, ask_slug_podcast),
            ],
            ASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~_MENU_TEXTS, ask_url)],
            PICK_DOMAIN: [CallbackQueryHandler(pick_domain, pattern="^(dom:|cancel)")],
            ASK_SLUG_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~_MENU_TEXTS, ask_slug_url)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Text(["☰ Menu"]), cancel_to_menu),
        ],
        per_message=False,
        allow_reentry=True,
    )
